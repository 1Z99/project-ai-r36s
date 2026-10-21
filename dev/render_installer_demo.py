#!/usr/bin/env python3
"""Capture the production PC installer renderer, never an installation.

Only synthetic cards/events are used. No bootstrap, worker, card discovery,
network, package setup, device input, or installer log is executed. SDL2/TTF
and ffmpeg must already be installed. --validate-only is read-only and SDL-free.
"""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import zlib


ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, FPS, DURATION = 640, 480, 12, 14
FONT = ROOT / "assets/DejaVuSansMono.ttf"
VIDEO = "installer-demo.mp4"
MIB = 1024 ** 2


def plan(skip_video=False):
    """Return fresh, deterministic metadata without importing the installer/SDL."""
    return {
        "width": WIDTH, "height": HEIGHT, "duration": DURATION, "fps": FPS,
        "video": None if skip_video else VIDEO, "hasAudio": False,
        "productionRenderer": True, "simulatedInstall": True,
        "simulatedDownloads": True, "packageChanges": False,
        "networkAccess": False, "deviceAccess": False, "localPC": True,
        "realWorker": False, "installerLogging": False,
        "font": "assets/DejaVuSansMono.ttf",
        "description": "PC-only scripted installer demo, not a real install or speed benchmark.",
        "frames": [
            {"name": "home.png", "time": 2,
             "description": "Main SD explicitly selected; Install, Update / Repair and Uninstall actions visible."},
            {"name": "confirm.png", "time": 4,
             "description": "Install confirmation for the synthetic main SD; YES highlighted but never executed."},
            {"name": "download.png", "time": 8,
             "description": "Simulated model download at 50 percent with sample transfer speed, elapsed time and ETA."},
            {"name": "success.png", "time": 12,
             "description": "Simulated install completion, explicitly naming the PC demo as its source."},
        ],
        "timeline": [
            {"id": "selection", "start": 0, "end": 1},
            {"id": "home", "start": 1, "end": 3},
            {"id": "confirm", "start": 3, "end": 5},
            {"id": "download", "start": 5, "end": 11},
            {"id": "success", "start": 11, "end": DURATION},
        ],
    }


def refuse_execution(*args, **kwargs):
    raise RuntimeError("PC demo refuses worker, thread and device execution")


class RefusingWorker:
    """Fail closed even if a future capture accidentally tries to start work."""

    __init__ = refuse_execution
    run = refuse_execution


def synthetic_cards():
    return [dict(path=path, label=label, available=True, free=free * 1024 ** 3,
                 total=64 * 1024 ** 3, installed=False, reason="PC demo; not probed")
            for path, label, free in (("/roms", "Main SD / SD1", 40),
                                      ("/roms2", "Second SD / SD2", 18))]


@contextmanager
def demo_ui(clock):
    """Import inert controller definitions, then deny every execution entry point."""
    # Also keep direct invocation (without -B) from writing installer bytecode.
    with patch.object(sys, "dont_write_bytecode", True), \
            patch.object(sys, "path", [str(ROOT / "installer")] + sys.path):
        import installer_ui as ui
        import installer_core as core
    with patch.object(ui, "discover_cards", side_effect=synthetic_cards), \
            patch.object(core, "discover_cards", side_effect=refuse_execution), \
            patch.object(core, "Installer", RefusingWorker), \
            patch.object(ui, "Installer", RefusingWorker), \
            patch.object(ui.Manager, "start", refuse_execution), \
            patch.object(ui, "threading", SimpleNamespace(Event=ui.threading.Event,
                                                          Thread=refuse_execution)), \
            patch.object(ui, "time", SimpleNamespace(monotonic=lambda: clock.now)):
        yield ui


def model_at(ui, seconds):
    """Seekable state: production selection/confirmation, synthetic progress/result."""
    if not 0 <= seconds < DURATION:
        raise ValueError("Frame time must lie within the demo timeline")
    model = ui.Manager(source="PC demo (simulated)", cards=synthetic_cards(),
                       worker=RefusingWorker, log=None)
    if seconds < 1:
        return model
    model.button("ok")  # Explicitly select Main SD; Install becomes ready.
    if seconds < 3:
        return model
    model.button("ok")  # Open confirmation, never confirm execution.
    if seconds >= 4:
        model.button("right")
    if seconds < 5:
        return model
    model.state = "working"
    model.started = 1005.0
    model.emit(dict(phase="download", label="PC DEMO: LFM2.5-350M-QAD-Q4_0.gguf",
                    done=round(214 * MIB * min(1, (seconds - 5) / 6)),
                    total=214 * MIB, speed=1.7 * MIB))
    model.history.extend(("PC demo: no card or network accessed",
                          "Sample speed and ETA; not a benchmark"))
    model.update()
    if seconds >= 11:
        model.emit(dict(phase="result", result="success",
                        operation=dict(outcome="installed", source="PC demo (simulated)", changes=[])))
        model.update()
        model.finished = 1011.0
    return model


def png_bytes(pixels):
    """Encode native RGBA rows losslessly using only the standard library."""
    if len(pixels) != WIDTH * HEIGHT * 4:
        raise ValueError("Expected one native 640x480 RGBA frame")

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    rows = b"".join(b"\0" + pixels[y * WIDTH * 4:(y + 1) * WIDTH * 4]
                    for y in range(HEIGHT))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def capture(display, model):
    import ctypes as C

    # draw() presents internally; read the backbuffer before that can discard it.
    present = display.sdl.SDL_RenderPresent
    with patch.object(display.sdl, "SDL_RenderPresent", lambda renderer: None):
        display.draw(model)
        pixels = (C.c_uint8 * (WIDTH * HEIGHT * 4))()
        # SDL_PIXELFORMAT_ABGR8888 is RGBA byte order on this little-endian PC.
        if display.sdl.SDL_RenderReadPixels(display.renderer, None, 0x16762004,
                                            pixels, WIDTH * 4) != 0:
            raise RuntimeError("Unable to read installer renderer pixels")
        result = bytes(pixels)
    present(display.renderer)
    return result


def render(output_dir, skip_video=False):
    import ctypes as C

    if sys.byteorder != "little":
        raise RuntimeError("This PC capture expects little-endian RGBA pixels")
    if not FONT.is_file():
        raise RuntimeError("Bundled regular font is missing: " + str(FONT))
    metadata = plan(skip_video)
    outputs = [output_dir / f["name"] for f in metadata["frames"]]
    outputs += [output_dir / "demo.json"]
    if not skip_video:
        outputs.append(output_dir / VIDEO)
    for path in [output_dir, *output_dir.parents, *outputs]:
        if path.is_symlink():
            raise RuntimeError("Refusing symlink output: " + str(path))
    output_dir.mkdir(parents=True, exist_ok=True)
    clock = SimpleNamespace(now=1000.0)
    environment = {"SDL_VIDEODRIVER": "dummy", "SDL_RENDER_DRIVER": "software",
                   "SDL_AUDIODRIVER": "dummy", "SDL_JOYSTICK_HIDAPI": "0",
                   "PROJECT_AI_FONT": str(FONT)}
    with patch.dict(os.environ, environment), demo_ui(clock) as ui:
        sdl = C.CDLL("libSDL2-2.0.so.0")
        ttf = C.CDLL("libSDL2_ttf-2.0.so.0")
        init = sdl.SDL_Init
        init.argtypes, init.restype = [C.c_uint32], C.c_int
        libraries = {"libSDL2-2.0.so.0": sdl, "libSDL2_ttf-2.0.so.0": ttf}
        # Display normally initializes joystick support. Initialize video ONLY;
        # never enumerate/open input devices, even on a PC with a pad attached.
        with patch.object(C, "CDLL", side_effect=lambda name: libraries[name]), \
                patch.object(sdl, "SDL_Init", lambda flags: init(0x20)), \
                patch.object(sdl, "SDL_NumJoysticks", lambda: 0), \
                patch.object(sdl, "SDL_JoystickOpen", refuse_execution):
            display = ui.Display(desktop=True)
            try:
                def frame(seconds):
                    clock.now = 1000.0 + seconds
                    return capture(display, model_at(ui, seconds))

                for item in metadata["frames"]:
                    (output_dir / item["name"]).write_bytes(png_bytes(frame(item["time"])))
                if not skip_video:
                    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                               "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", "640x480",
                               "-framerate", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
                               "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                               "-threads", "1", "-map_metadata", "-1", "-fflags", "+bitexact",
                               "-movflags", "+faststart", str(output_dir / VIDEO)]
                    with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
                        try:
                            for index in range(DURATION * FPS):
                                encoder.stdin.write(frame(index / FPS))
                        finally:
                            encoder.stdin.close()
                        if encoder.wait(timeout=60):
                            raise RuntimeError("ffmpeg failed to encode the installer demo")
            finally:
                display.close()
    (output_dir / "demo.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/media/previews/installer")
    parser.add_argument("--validate-only", action="store_true",
                        help="Print the pure capture plan; no installer/SDL imports or writes")
    parser.add_argument("--skip-video", action="store_true", help="Capture PNGs and metadata only")
    args = parser.parse_args(argv)
    metadata = plan(args.skip_video) if args.validate_only else render(args.output_dir, args.skip_video)
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
