"""PC capture safety/plan checks. Normal discovery needs neither SDL nor ffmpeg."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dev/render_installer_demo.py"
SPEC = importlib.util.spec_from_file_location("installer_demo", SOURCE)
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class InstallerDemoTests(unittest.TestCase):
    def test_stable_plan_and_truth_flags(self):
        plan = demo.plan()
        self.assertEqual((plan["width"], plan["height"], plan["fps"], plan["duration"]),
                         (640, 480, 12, 14))
        self.assertEqual(plan["video"], "installer-demo.mp4")
        for flag in ("productionRenderer", "simulatedInstall", "simulatedDownloads", "localPC"):
            self.assertIs(plan[flag], True)
        for flag in ("packageChanges", "networkAccess", "deviceAccess", "hasAudio",
                     "realWorker", "installerLogging"):
            self.assertIs(plan[flag], False)
        self.assertEqual([f["name"] for f in plan["frames"]],
                         ["home.png", "confirm.png", "download.png", "success.png"])
        self.assertTrue(all(f["description"] for f in plan["frames"]))
        plan["frames"][0]["name"] = "mutated"
        self.assertEqual(demo.plan()["frames"][0]["name"], "home.png")
        self.assertIsNone(demo.plan(skip_video=True)["video"])

    def test_contiguous_timeline_and_screenshot_times(self):
        plan = demo.plan()
        self.assertEqual([(c["id"], c["start"], c["end"]) for c in plan["timeline"]],
                         [("selection", 0, 1), ("home", 1, 3), ("confirm", 3, 5),
                          ("download", 5, 11), ("success", 11, 14)])
        for before, after in zip(plan["timeline"], plan["timeline"][1:]):
            self.assertEqual(before["end"], after["start"])
        for image, chapter in zip(plan["frames"], plan["timeline"][1:]):
            self.assertLessEqual(chapter["start"], image["time"])
            self.assertLess(image["time"], chapter["end"])

    def test_validate_only_cannot_render_or_write(self):
        output = io.StringIO()
        with patch.object(demo, "render", side_effect=AssertionError("render")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
                patch.object(Path, "write_text", side_effect=AssertionError("write")), \
                patch.object(Path, "write_bytes", side_effect=AssertionError("write")), \
                redirect_stdout(output):
            self.assertEqual(demo.main(["--validate-only", "--output-dir", "/nonexistent"]), 0)
        self.assertEqual(json.loads(output.getvalue()), demo.plan())

    def test_validate_subprocess_has_no_installer_sdl_imports_or_writes(self):
        # Audit hooks run before runpy: deny filesystem writes, dynamic libraries,
        # installer/app imports, sockets and child processes in a fresh interpreter.
        runner = """
import runpy, sys
def audit(event, args):
    if event == 'import' and args[0].startswith(('installer_', 'project_ai', 'ctypes', 'sdl')):
        raise AssertionError('forbidden import: ' + args[0])
    if event in ('ctypes.dlopen', 'os.mkdir', 'subprocess.Popen', 'socket.__new__'):
        raise AssertionError(event)
    if event == 'open' and ((isinstance(args[1], str) and any(c in args[1] for c in 'wax+'))
                            or args[2] & 0o3003):
        raise AssertionError('write: ' + str(args[0]))
sys.addaudithook(audit)
sys.argv = [sys.argv[1], '--validate-only', '--output-dir', sys.argv[2]]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "must-not-exist"
            result = subprocess.run([sys.executable, "-B", "-c", runner, str(SOURCE), str(target)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), demo.plan())
            self.assertFalse(target.exists())

    def test_worker_and_manager_execution_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "PC demo refuses"):
            demo.RefusingWorker("/roms")
        with self.assertRaisesRegex(RuntimeError, "PC demo refuses"):
            demo.RefusingWorker.run(object(), "install")
        with demo.demo_ui(SimpleNamespace(now=1004)) as ui:
            model = demo.model_at(ui, 4)
            self.assertIs(model.worker, demo.RefusingWorker)
            self.assertIsNone(model.log)
            self.assertIsNone(model.thread)
            with self.assertRaisesRegex(RuntimeError, "PC demo refuses"):
                model.button("ok")
            self.assertIsNone(model.thread)
            with self.assertRaisesRegex(RuntimeError, "PC demo refuses"):
                ui.threading.Thread(target=lambda: None)
            model.state = "home"
            model.button("refresh")
            self.assertEqual(model.cards, demo.synthetic_cards())
            self.assertEqual(ui.Manager(worker=demo.RefusingWorker).cards, demo.synthetic_cards())
            import installer_core
            with self.assertRaisesRegex(RuntimeError, "PC demo refuses"):
                installer_core.discover_cards()

    def test_production_controller_states_without_sdl(self):
        clock = SimpleNamespace(now=1000)
        with demo.demo_ui(clock) as ui:
            initial = demo.model_at(ui, 0)
            self.assertIsNone(initial.chosen)
            home = demo.model_at(ui, 2)
            self.assertEqual((home.state, home.chosen, home.focus), ("home", 0, 1))
            confirm = demo.model_at(ui, 3)
            self.assertEqual((confirm.state, confirm.action, confirm.confirm_yes),
                             ("confirm", "install", False))
            self.assertTrue(demo.model_at(ui, 4).confirm_yes)
            progress = []
            for seconds in (5, 6, 8, 10):
                clock.now = 1000 + seconds
                model = demo.model_at(ui, seconds)
                self.assertEqual(model.state, "working")
                self.assertIn("PC DEMO", model.progress["label"])
                self.assertGreater(model.speed, 0)
                self.assertIsNone(model.thread)
                self.assertIsNone(model.log)
                progress.append(model.progress["done"])
            self.assertEqual(progress, sorted(set(progress)))
            self.assertEqual(progress[2], 107 * demo.MIB)
            clock.now = 1012
            success = demo.model_at(ui, 12)
            self.assertEqual(success.state, "success")
            self.assertEqual(success.finished - success.started, 6)
            self.assertIn("PC demo (simulated)", " ".join(success.completion()[1]))
            for bad_time in (-1, demo.DURATION):
                with self.assertRaises(ValueError):
                    demo.model_at(ui, bad_time)

    def test_capture_reads_before_present_and_restores_on_failure(self):
        events = []
        present = lambda renderer: events.append("present")

        def read_pixels(renderer, rect, format_, pixels, pitch):
            self.assertEqual((format_, pitch), (0x16762004, 640 * 4))
            events.append("read")
            return 0

        sdl = SimpleNamespace(SDL_RenderPresent=present, SDL_RenderReadPixels=read_pixels)

        def draw(model):
            events.append("draw")
            sdl.SDL_RenderPresent(None)

        display = SimpleNamespace(sdl=sdl, renderer=None, draw=draw)
        self.assertEqual(len(demo.capture(display, object())), 640 * 480 * 4)
        self.assertEqual(events, ["draw", "read", "present"])
        self.assertIs(sdl.SDL_RenderPresent, present)
        events.clear()
        with patch.object(sdl, "SDL_RenderReadPixels", return_value=-1):
            with self.assertRaisesRegex(RuntimeError, "Unable to read"):
                demo.capture(display, object())
        self.assertEqual(events, ["draw"])
        self.assertIs(sdl.SDL_RenderPresent, present)

    def test_png_is_native_rgba_and_lossless(self):
        pixels = bytes((4, 8, 6, 255)) * (640 * 480)
        data = demo.png_bytes(pixels)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">IIBBBBB", data[16:29]), (640, 480, 8, 6, 0, 0, 0))
        offset, compressed = 8, b""
        while offset < len(data):
            length = struct.unpack(">I", data[offset:offset + 4])[0]
            kind, body = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
            crc = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
            self.assertEqual(crc, zlib.crc32(kind + body) & 0xffffffff)
            if kind == b"IDAT":
                compressed += body
            offset += 12 + length
        rows = zlib.decompress(compressed)
        self.assertEqual(rows, (b"\0" + pixels[:640 * 4]) * 480)
        with self.assertRaises(ValueError):
            demo.png_bytes(b"too short")


if __name__ == "__main__":
    unittest.main()
