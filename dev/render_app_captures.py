#!/usr/bin/env python3
"""Lossless native website captures, with scripted, memory-only PC fixtures.

Requires the existing system SDL2/SDL2_ttf libraries, not FFmpeg or a model.
--validate-only prints the pure capture plan without SDL imports or file I/O.
"""

import argparse
import builtins
from contextlib import contextmanager, ExitStack
import ctypes
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
import zlib


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
WIDTH, HEIGHT = 640, 480
FONTS = (("DejaVuSansMono-Bold.ttf", 16), ("DejaVuSansMono.ttf", 14),
         ("DejaVuSansMono.ttf", 11), ("DejaVuSansMono-Bold.ttf", 15))
ASSET_NAMES = ("logo.rgba", "intro-background.rgba", "words.txt",
               "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf")


def capture_plan():
    """Return fresh fixtures and provenance; no discovery, imports or file reads."""
    compose_messages = [
        {"role": "user", "content": "one packing tip?"},
        {"role": "assistant", "content": "Bring a rain jacket. Sample reply."},
    ]
    messages = [
        {"role": "user", "content": "explain local AI"},
        {"role": "assistant", "content":
         "Local AI runs on your own device, not in the cloud. "
         "Your questions stay with you. This is a scripted sample reply."},
        {"role": "user", "content": "give me a packing tip"},
        {"role": "assistant", "content":
         "Pack a light rain jacket and a refillable water bottle. "
         "Check the forecast before you leave. Sample reply, not live AI."},
    ]
    history = [
        {"title": "explain local AI", "updated": 1700000000, "messages": messages[:2]},
        {"title": "a weekend packing tip", "updated": 1699996400, "messages": messages[2:]},
        {"title": "draft a short note", "updated": 1699992800, "messages": [
            {"role": "user", "content": "draft a short note"},
            {"role": "assistant", "content": "See you at the park at noon. Sample note."},
        ]},
    ]
    return {
        "origin": "pc-demo", "productionRenderer": True, "sampleReplies": True,
        "simulatedMic": True, "liveModel": False, "microphoneAccess": False,
        "deviceAccess": False, "userPersistence": False,
        "userConfigRead": False, "userHistoryRead": False, "networkAccess": False,
        "hasAudio": False, "audioPlayback": False, "speechSynthesis": False,
        "width": WIDTH, "height": HEIGHT, "format": "PNG", "lossless": True,
        "pixelSource": "SDL_RenderReadPixels / RGBA32 / before present",
        "renderer": "app/project_ai_r36s.py:draw_ui + project_ai_settings.SettingsPanel",
        "fonts": [{"file": name, "size": size} for name, size in FONTS],
        "model": "fast", "highlightTiming": "synthetic-silent",
        "limitations": "Scripted PC states, not device captures, live answers, recordings or benchmarks.",
        "messages": messages, "history": history,
        "systemPrompt": "You are 227, a private offline assistant.\n"
                        "Keep replies short and useful.\n"
                        "If unsure, say so. Never invent facts.",
        "generation": {"context": 768, "max_tokens": 160, "temperature": 0.25},
        "captures": [
            {"id": "chat", "file": "chat.png", "description": "Two short scripted questions and complete sample replies."},
            {"id": "keyboard", "file": "keyboard.png", "draft": "what should I pa",
             "messages": compose_messages,
             "description": "Native compose keyboard, suggestions and explicit SEND."},
            {"id": "speech", "file": "speech.png", "highlight": "device,",
             "description": "Silent read-aloud illustration with a synthetic word highlight."},
            {"id": "voice", "file": "voice.png", "page": "VOICE",
             "description": "Native voice controls with an explicitly simulated mic test; no capture or playback."},
            {"id": "dictation", "file": "dictation.png", "draft": "suggest a short walk",
             "messages": compose_messages,
             "description": "Sample dictated draft ready for review and explicit SEND; no microphone used."},
            {"id": "history", "file": "history.png",
             "description": "Three in-memory sample conversations; no saved history is read."},
            {"id": "prompt", "file": "prompt.png", "page": "SYSTEM PROMPT",
             "description": "Native system prompt editor with MIC, APPLY and CANCEL keys."},
            {"id": "settings", "file": "settings.png", "page": "GENERATION",
             "description": "Actual context window, reply limit and temperature controls."},
        ],
    }


def forbidden(*args, **kwargs):
    raise RuntimeError("Capture attempted forbidden model/device/network/persistence I/O")


@contextmanager
def isolated_io():
    """Permit only bundled asset reads during production import and rendering.

    Production eagerly loads words from its data directory on import. Redirect
    that one read to the bundled dictionary, never the user's data directory.
    PNG/metadata writes happen only after this read-only rendering scope exits.
    """
    allowed = {ASSETS / name for name in ASSET_NAMES}
    original_words = ROOT / "app/project_ai/words.txt"
    builtin_open, io_open = builtins.open, io.open

    def asset_open(opener, file, mode="r", *args, **kwargs):
        if isinstance(file, int) or any(flag in mode for flag in "wax+"):
            return forbidden()
        path = Path(file).resolve()
        if path == original_words:
            path = ASSETS / "words.txt"
        if path not in allowed:
            return forbidden()
        return opener(path, mode, *args, **kwargs)

    with ExitStack() as guards:
        guards.enter_context(patch.object(builtins, "open", lambda *a, **k: asset_open(builtin_open, *a, **k)))
        guards.enter_context(patch.object(io, "open", lambda *a, **k: asset_open(io_open, *a, **k)))
        for module, names in (
            (subprocess, ("Popen", "run")),
            (socket, ("socket", "create_connection")),
            (os, ("open", "system", "popen", "mkdir", "unlink", "remove", "rename", "replace")),
            (threading.Thread, ("start",)),
        ):
            for name in names:
                guards.enter_context(patch.object(module, name, forbidden))
        yield


@contextmanager
def production_guards(app, settings_module):
    clock = SimpleNamespace(monotonic=lambda: 10.0, time=lambda: 1700000030.0)
    with ExitStack() as guards:
        for module in (app, settings_module):
            guards.enter_context(patch.object(module, "time", clock))
        for name in ("LocalEngine", "read_config", "save_config", "load_history", "save_history",
                     "load_chats", "save_chats", "detect_capture_devices", "enable_capture_input",
                     "capture_node_exists", "available_model_profiles", "resolve_whisper_model",
                     "synthesize_speech", "tts_output_devices", "read_device_metrics", "mic_log"):
            guards.enter_context(patch.object(app, name, forbidden))
        for name in ("SDL_NumJoysticks", "SDL_JoystickOpen", "SDL_GameControllerOpen",
                     "SDL_OpenAudio", "SDL_OpenAudioDevice", "SDL_GetNumAudioDevices",
                     "SDL_InitSubSystem"):
            guards.enter_context(patch.object(app.SDL, name, forbidden))
        guards.enter_context(patch.object(settings_module, "MicTester", lambda: SimpleNamespace(
            state="idle", progress=0.0, status=lambda: "SIMULATED / NO CAPTURE",
            busy=lambda: False, start=forbidden, update=forbidden, cancel=lambda: None)))
        yield


def png_bytes(raw):
    if len(raw) != WIDTH * HEIGHT * 4:
        raise ValueError("Expected one native 640x480 RGBA frame")
    if len(set(struct.iter_unpack("4s", raw))) < 16:
        raise RuntimeError("Refusing to publish an empty/flat capture")

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    stride = WIDTH * 4
    rows = b"".join(b"\0" + raw[y:y + stride] for y in range(0, len(raw), stride))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def render(plan, app, settings_module):
    """Render fresh pixels directly, without an event loop, video or audio."""
    window = renderer = cache = logo = background = None
    fonts = []
    initialized = ttf_initialized = False
    captures = {}
    with production_guards(app, settings_module), ExitStack() as resources:
        resources.enter_context(patch.object(app, "ROOT", ASSETS))
        try:
            # Video only: no audio, joystick or controller SDL subsystems.
            if app.SDL.SDL_Init(0x20) != 0:
                raise RuntimeError(app.SDL.SDL_GetError().decode())
            initialized = True
            if app.TTF.TTF_Init() != 0:
                raise RuntimeError("Cannot initialize SDL_ttf")
            ttf_initialized = True
            app.SDL.SDL_RenderReadPixels.argtypes = [app.c_void_p, app.c_void_p, app.c_uint32,
                                                    app.c_void_p, app.c_int]
            window = app.SDL.SDL_CreateWindow(b"227 memory-only captures", 0, 0, WIDTH, HEIGHT, 8)
            renderer = app.SDL.SDL_CreateRenderer(window, -1, 1) if window else None
            if not renderer:
                raise RuntimeError("Cannot create dummy SDL software renderer")
            for name, size in FONTS:
                font = app.TTF.TTF_OpenFont(str(ASSETS / name).encode(), size)
                if not font:
                    raise RuntimeError("Cannot open bundled font: " + name)
                fonts.append(font)
            cache = app.TextCache(renderer)
            logo = app.load_logo(renderer)
            background = app.load_background(renderer, ASSETS / "intro-background.rgba")
            if not logo or not background:
                raise RuntimeError("Missing production logo or background")
            choices = [("fast", app.MODEL_PROFILES["fast"])]
            usb = {"id": "demo-usb", "label": "DEMO USB", "card": 1,
                   "arg": "SIMULATED-NOT-A-DEVICE", "node": True, "usb": True}
            pixels = (ctypes.c_uint8 * (WIDTH * HEIGHT * 4))()
            # draw_ui normally presents itself. Read the valid backbuffer first.
            with patch.object(app.SDL, "SDL_RenderPresent", lambda renderer: None):
                for capture in plan["captures"]:
                    scene = capture["id"]
                    composing = scene in ("keyboard", "dictation")
                    messages = plan["messages"][:2] if scene == "speech" else plan["messages"]
                    messages = capture.get("messages", messages)
                    settings = settings_module.SettingsPanel(
                        choices, config={**plan["generation"], "system_prompt": plan["systemPrompt"]},
                        audio_devices=lambda: [usb], preview=True)
                    overlay = None
                    if "page" in capture:
                        overlay = "settings"
                        settings.page = settings_module.PAGES.index(capture["page"])
                        if scene == "prompt":
                            settings.edit_prompt()
                            settings.keyboard_row = len(settings_module.PROMPT_KEYS) - 1
                            settings.keyboard_col = settings_module.PROMPT_ACTIONS.index("APPLY")
                            settings.note = "Sample prompt; APPLY/CANCEL affect memory only."
                        elif scene == "voice":
                            settings.values["audio_device"] = usb["id"]
                            settings.row, settings.detail_focus = 3, True
                            settings.note = "Simulated test; no audio recorded or played."
                    elif scene == "history":
                        overlay = "history"
                    word = None
                    if scene == "speech":
                        word = next(match.span() for match in re.finditer(r"\S+", messages[1]["content"])
                                    if match.group() == capture["highlight"])
                    height = sum(app.message_layout(fonts[1], item)[2] + app.MSG_GAP for item in messages)
                    if height > app.viewport_metrics(composing)[3]:
                        raise RuntimeError("Sample conversation would clip: " + scene)
                    app.draw_ui(
                        renderer, fonts, cache, messages, 0, composing, capture.get("draft", ""),
                        4 if composing else 0, 3 if composing else 0,
                        "READY", choices[0][1]["label"], "PC DEMO", "", "", -100, -100,
                        logo, False, overlay=overlay, chats=plan["history"], history_index=1,
                        suggestions=app.word_suggestions(capture["draft"]) if scene == "keyboard" else [],
                        mic_note="SAMPLE TRANSCRIPT / REVIEW THEN SEND" if scene == "dictation" else "",
                        tts_index=1 if scene == "speech" else -1, tts_word=word,
                        models=choices, settings=settings, background=background)
                    # SDL_PIXELFORMAT_ABGR8888 is byte-order RGBA on this little-endian host.
                    pixel_format = 0x16762004 if sys.byteorder == "little" else 0x16462004
                    if app.SDL.SDL_RenderReadPixels(renderer, None, pixel_format, pixels, WIDTH * 4) != 0:
                        raise RuntimeError(app.SDL.SDL_GetError().decode())
                    captures[capture["file"]] = png_bytes(bytes(pixels))
        finally:
            if cache:
                cache.close()
            if background:
                background.close()
            if logo:
                app.SDL.SDL_DestroyTexture(logo)
            for font in fonts:
                app.TTF.TTF_CloseFont(font)
            if renderer:
                app.SDL.SDL_DestroyRenderer(renderer)
            if window:
                app.SDL.SDL_DestroyWindow(window)
            if ttf_initialized:
                app.TTF.TTF_Quit()
            if initialized:
                app.SDL.SDL_Quit()
    return captures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/media/previews/app")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    plan = capture_plan()
    if args.validate_only:
        print(json.dumps(plan, indent=2))
        return 0
    for name in ASSET_NAMES:
        if not (ASSETS / name).is_file():
            parser.error("Missing bundled production asset: " + name)
    if not args.output_dir.parent.is_dir():
        parser.error("Output parent directory must already exist")
    # Force dummy/software even if the caller has real desktop/audio drivers set.
    with patch.dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_RENDER_DRIVER="software",
                    SDL_AUDIODRIVER="dummy"), patch.object(sys, "dont_write_bytecode", True):
        with patch.object(sys, "path", [str(ROOT / "app"), *sys.path]), isolated_io():
            app = importlib.import_module("project_ai_r36s")
            settings_module = importlib.import_module("project_ai_settings")
            captures = render(plan, app, settings_module)
    expected = {capture["file"] for capture in plan["captures"]}
    if set(captures) != expected:
        raise RuntimeError("Incomplete capture set; nothing published")
    # Stage the full set before replacing files; publish provenance last.
    args.output_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".captures-", dir=args.output_dir) as temporary:
        stage = Path(temporary)
        for capture in plan["captures"]:
            data = captures[capture["file"]]
            capture.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            (stage / capture["file"]).write_bytes(data)
        (stage / "captures.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        for name in [capture["file"] for capture in plan["captures"]] + ["captures.json"]:
            (stage / name).replace(args.output_dir / name)
    for capture in plan["captures"]:
        print("{}: {:,} bytes, lossless 640x480".format(args.output_dir / capture["file"], capture["bytes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
