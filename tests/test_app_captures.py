"""Stdlib plan, safety and lossless encoding tests; SDL is never imported."""

import builtins
from contextlib import redirect_stdout
import ctypes
import importlib.util
import io
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib


SOURCE = Path(__file__).resolve().parents[1] / "dev/render_app_captures.py"
SPEC = importlib.util.spec_from_file_location("app_captures", SOURCE)
captures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(captures)


class AppCaptureTests(unittest.TestCase):
    def test_plan_states_and_truth_flags(self):
        plan = captures.capture_plan()
        names = ["chat", "keyboard", "speech", "voice", "dictation", "history", "prompt", "settings"]
        self.assertEqual([c["id"] for c in plan["captures"]], names)
        self.assertEqual([c["file"] for c in plan["captures"]], [name + ".png" for name in names])
        self.assertEqual((plan["width"], plan["height"]), (640, 480))
        self.assertEqual(plan["origin"], "pc-demo")
        for flag in ("productionRenderer", "sampleReplies", "simulatedMic", "lossless"):
            self.assertIs(plan[flag], True)
        for flag in ("liveModel", "microphoneAccess", "deviceAccess", "userPersistence",
                     "userConfigRead", "userHistoryRead", "networkAccess", "hasAudio",
                     "audioPlayback", "speechSynthesis"):
            self.assertIs(plan[flag], False)
        self.assertNotIn("video", plan)
        self.assertEqual(plan["highlightTiming"], "synthetic-silent")
        self.assertGreaterEqual(len(plan["history"]), 2)
        for entry in plan["history"]:
            self.assertEqual([m["role"] for m in entry["messages"]], ["user", "assistant"])
        self.assertEqual(plan["captures"][-1]["page"], "GENERATION")
        self.assertEqual(set(plan["generation"]), {"context", "max_tokens", "temperature"})
        plan["history"][0]["messages"][0]["content"] = "changed"
        self.assertEqual(captures.capture_plan()["messages"][0]["content"], "explain local AI")

    def test_validate_only_does_not_import_probe_render_or_write(self):
        output = io.StringIO()
        with patch.object(captures.importlib, "import_module", side_effect=AssertionError("import")), \
                patch.object(ctypes, "CDLL", side_effect=AssertionError("SDL")), \
                patch.object(captures, "render", side_effect=AssertionError("render")), \
                patch.object(Path, "is_file", side_effect=AssertionError("probe")), \
                patch.object(Path, "is_dir", side_effect=AssertionError("probe")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")), \
                patch.object(builtins, "open", side_effect=AssertionError("open")), \
                patch.object(io, "open", side_effect=AssertionError("open")), \
                patch.object(captures.tempfile, "TemporaryDirectory", side_effect=AssertionError("stage")), \
                redirect_stdout(output):
            self.assertEqual(captures.main(["--validate-only", "--output-dir", "/nonexistent/path"]), 0)
        self.assertEqual(json.loads(output.getvalue()), captures.capture_plan())

    def test_io_scope_fails_closed_and_restores(self):
        original = builtins.open
        with captures.isolated_io():
            for path in ("/home/example/config.json", "/proc/meminfo", "/dev/snd/controlC0"):
                with self.assertRaisesRegex(RuntimeError, "forbidden"):
                    Path(path).read_text()
            with self.assertRaises(RuntimeError):
                (captures.ASSETS / "words.txt").write_text("no")
            with self.assertRaises(RuntimeError):
                builtins.open(1, "w")
            with self.assertRaises(RuntimeError):
                captures.subprocess.run(["false"])
            with self.assertRaises(RuntimeError):
                captures.socket.socket()
            with self.assertRaises(RuntimeError):
                captures.os.unlink("/never-delete")
            with self.assertRaises(RuntimeError):
                captures.threading.Thread(target=lambda: None).start()
        self.assertIs(builtins.open, original)

    def test_failed_or_incomplete_render_cannot_publish(self):
        for result in (RuntimeError("render failed"), {}):
            with self.subTest(result=result), \
                    patch.object(Path, "is_file", return_value=True), \
                    patch.object(Path, "is_dir", return_value=True), \
                    patch.object(captures.importlib, "import_module", return_value=SimpleNamespace()), \
                    patch.object(captures, "render") as render, \
                    patch.object(Path, "mkdir", side_effect=AssertionError("published directory")), \
                    patch.object(Path, "write_bytes", side_effect=AssertionError("published image")), \
                    patch.object(Path, "write_text", side_effect=AssertionError("published metadata")), \
                    patch.object(captures.tempfile, "TemporaryDirectory", side_effect=AssertionError("stage")):
                if isinstance(result, Exception):
                    render.side_effect = result
                else:
                    render.return_value = result
                with self.assertRaisesRegex(RuntimeError, "render failed|Incomplete capture set"):
                    captures.main([])

    def test_import_dictionary_redirect_is_bundled_only(self):
        reads = []

        def opener(path, mode="r", *args, **kwargs):
            reads.append((path, mode))
            return io.StringIO("pack packing park")

        with patch.object(builtins, "open", opener), captures.isolated_io():
            with builtins.open(captures.ROOT / "app/project_ai/words.txt") as handle:
                self.assertIn("packing", handle.read())
        self.assertEqual(reads, [(captures.ASSETS / "words.txt", "r")])

    def test_production_dependencies_are_blocked_and_injected(self):
        source_names = ("LocalEngine read_config save_config load_history save_history load_chats "
                        "save_chats detect_capture_devices enable_capture_input capture_node_exists "
                        "available_model_profiles resolve_whisper_model synthesize_speech "
                        "tts_output_devices read_device_metrics mic_log").split()
        sdl_names = ("SDL_NumJoysticks SDL_JoystickOpen SDL_GameControllerOpen SDL_OpenAudio "
                     "SDL_OpenAudioDevice SDL_GetNumAudioDevices SDL_InitSubSystem").split()
        app = SimpleNamespace(time=None, SDL=SimpleNamespace(**dict.fromkeys(sdl_names)),
                              **dict.fromkeys(source_names))
        settings = SimpleNamespace(time=None, MicTester=None)
        with captures.production_guards(app, settings):
            for name in source_names:
                with self.assertRaisesRegex(RuntimeError, "forbidden"):
                    getattr(app, name)()
            for name in sdl_names:
                with self.assertRaises(RuntimeError):
                    getattr(app.SDL, name)()
            mic = settings.MicTester()
            self.assertEqual(mic.status(), "SIMULATED / NO CAPTURE")
            with self.assertRaises(RuntimeError):
                mic.start({})
            self.assertEqual(app.time.time(), 1700000030)
        self.assertIsNone(app.read_config)

    def test_lossless_rgba_png_round_trip_and_flat_frame_rejection(self):
        raw = bytes(range(256)) * (640 * 480 * 4 // 256)
        png = captures.png_bytes(raw)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        offset, chunks = 8, {}
        while offset < len(png):
            length = struct.unpack_from(">I", png, offset)[0]
            kind = png[offset + 4:offset + 8]
            data = png[offset + 8:offset + 8 + length]
            self.assertEqual(struct.unpack_from(">I", png, offset + 8 + length)[0],
                             zlib.crc32(kind + data))
            chunks[kind] = data
            offset += length + 12
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[b"IHDR"]), (640, 480, 8, 6, 0, 0, 0))
        rows = zlib.decompress(chunks[b"IDAT"])
        self.assertTrue(all(rows[y] == 0 for y in range(0, len(rows), 2561)))
        self.assertEqual(b"".join(rows[y + 1:y + 2561] for y in range(0, len(rows), 2561)), raw)
        with self.assertRaises(ValueError):
            captures.png_bytes(b"short")
        with self.assertRaisesRegex(RuntimeError, "empty/flat"):
            captures.png_bytes(b"\0" * (640 * 480 * 4))


if __name__ == "__main__":
    unittest.main()
