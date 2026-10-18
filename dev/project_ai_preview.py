#!/usr/bin/env python3
"""Windowed, local-only UI preview using the production R36S renderer."""

import argparse
import ctypes
import os
from pathlib import Path
import re
import struct
import sys
import time
import zlib

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))

import project_ai_r36s as app
from project_ai_settings import SettingsPanel, contains


ASSETS = REPO_ROOT / "assets"
SCENES = ("chat", "keyboard", "models", "history", "waiting", "speech", "boot", "mic", "settings")
SETTINGS_PAGES = ("models", "prompt", "generation", "voice", "appearance", "system")


def screenshot(renderer, path):
    app.SDL.SDL_RenderReadPixels.argtypes = [app.c_void_p, app.c_void_p, app.c_uint32,
                                           app.c_void_p, app.c_int]
    pixels = (ctypes.c_uint8 * (app.W * app.H * 4))()
    if app.SDL.SDL_RenderReadPixels(renderer, None, 0x16762004, pixels, app.W * 4) != 0:
        raise RuntimeError(app.SDL.SDL_GetError().decode())
    raw = bytes(pixels)
    rows = b"".join(b"\0" + raw[y * app.W * 4:(y + 1) * app.W * 4] for y in range(app.H))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", app.W, app.H, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=SCENES, default="chat")
    parser.add_argument("--scale", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--screenshot", type=Path, help="Render one 640x480 PNG and exit")
    parser.add_argument("--skip-intro", action="store_true", help="Open the UI without the silent frame intro")
    parser.add_argument("--settings-page", choices=SETTINGS_PAGES, default="models")
    parser.add_argument("--edit-prompt", action="store_true", help="Open the prompt editor with its keyboard")
    args = parser.parse_args(argv)
    if args.screenshot and not args.screenshot.parent.is_dir():
        parser.error("Screenshot parent directory does not exist")
    for name in ("logo.rgba", "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"):
        if not (ASSETS / name).is_file():
            parser.error(f"Missing preview asset: {ASSETS / name}")

    # Never invoke the device engine, audio capture, or history/config writers.
    app.ROOT = ASSETS
    app.WORDS_FILE = ASSETS / "words.txt"
    app.WORD_LIST = app.load_words()
    app.SUGGEST_CACHE.clear()
    if app.SDL.SDL_Init(0x20 | 0x200) != 0 or app.TTF.TTF_Init() != 0:
        raise RuntimeError(app.SDL.SDL_GetError().decode())
    app.SDL.SDL_RenderSetLogicalSize.argtypes = [app.c_void_p, app.c_int, app.c_int]
    app.SDL.SDL_StartTextInput.argtypes = []
    app.SDL.SDL_StopTextInput.argtypes = []
    app.SDL.SDL_SetWindowTitle.argtypes = [app.c_void_p, app.c_char_p]
    scale = 1 if args.screenshot else args.scale
    window = app.SDL.SDL_CreateWindow(b"Project 227 Desktop Preview", 0x2FFF0000, 0x2FFF0000,
                                      app.W * scale, app.H * scale, 0x20)
    renderer = app.SDL.SDL_CreateRenderer(window, -1, 1) if window else None
    if not renderer:
        app.SDL.SDL_Quit()
        raise RuntimeError("Could not create the desktop preview window")
    app.SDL.SDL_RenderSetLogicalSize(renderer, app.W, app.H)
    app.SDL.SDL_ShowCursor(0)
    app.SDL.SDL_StartTextInput()
    joystick = app.SDL.SDL_JoystickOpen(0) if app.SDL.SDL_NumJoysticks() > 0 else None
    fonts = tuple(app.TTF.TTF_OpenFont(str(ASSETS / name).encode(), size)
                  for name, size in (("DejaVuSansMono-Bold.ttf", 16),
                                     ("DejaVuSansMono.ttf", 14),
                                     ("DejaVuSansMono.ttf", 11),
                                     ("DejaVuSansMono-Bold.ttf", 15)))
    cache = app.TextCache(renderer)
    logo = app.load_logo(renderer)
    background = app.load_background(renderer, ASSETS / "intro-background.rgba")
    if not all(fonts) or not logo:
        raise RuntimeError("Could not load preview fonts/logo")
    intro = app.IntroPlayer(renderer, ASSETS) if not (args.skip_intro or args.screenshot) else None

    messages = [
        {"role": "assistant", "content": "227 is ready for a new local session."},
        {"role": "user", "content": "can we customize the handheld interface on this PC?"},
        {"role": "assistant", "content":
         "Yes. This preview uses the exact same renderer, logo, fonts and 640x480 layout "
         "as your R36S. We can adjust the interface here first, then deploy only when "
         "you approve it. This is sample text, not a model-generated answer."},
    ]
    chats = [{"title": title, "updated": time.time() - i * 1800,
              "messages": messages[:2]} for i, title in enumerate(
                  ("customize the interface", "keep my notes private", "plan a weekend trip",
                   "explain local AI", "test the on-screen keyboard"))]
    choices = list(app.MODEL_PROFILES.items())
    draft = "tell me wh"
    scene = args.scene
    key_row = key_col = model_index = history_index = 0
    cursor_x, cursor_y = 320, 438
    scroll = 0.0
    started = time.monotonic()
    source = Path(app.__file__).resolve()
    files = (source, Path(__file__).resolve(), REPO_ROOT / "app" / "project_ai_settings.py")
    stamps = tuple(path.stat().st_mtime_ns for path in files)
    reload_requested = False
    running = True
    event = (ctypes.c_uint8 * 64)()
    settings_return = "chat"

    def choose_model(index):
        nonlocal model_index
        model_index = index

    def change_background(percent):
        nonlocal background
        replacement = app.load_background(renderer, ASSETS / "intro-background.rgba",
                                          opacity=round(percent * 255 / 100))
        if replacement is None:
            return False
        if background is not None:
            background.close()
        background = replacement
        return True

    settings = SettingsPanel(choices, on_model=choose_model, on_background=change_background)
    settings.page = SETTINGS_PAGES.index(args.settings_page)
    if args.edit_prompt:
        scene, settings.page = "settings", 1
        settings.edit_prompt()

    def switch(target):
        nonlocal scene, started, scroll
        scene, started, scroll = target, time.monotonic(), 0.0
        title = f"227 Preview [SAMPLE DATA] | {scene.upper()} | R2/F10 settings | Ctrl+R reload"
        app.SDL.SDL_SetWindowTitle(window, title.encode())

    def toggle_settings():
        nonlocal settings_return
        if scene == "settings":
            switch(settings_return)
        else:
            settings_return = scene
            settings.open(choices[model_index][0])
            switch("settings")

    def activate(key):
        nonlocal draft
        if key == "SPACE":
            draft = (draft + " ")[:500]
        elif key == "DEL":
            draft = draft[:-1]
        elif key == "CLOSE":
            switch("chat")
        elif key == "MIC":
            switch("mic" if scene != "mic" else "keyboard")
        elif key == "SEND":
            if draft.strip():
                messages[:] = [{"role": "user", "content": draft.strip()},
                               {"role": "assistant", "content":
                                "This is a preview response for styling the interface. "
                                "No AI model is running, and nothing is sent to the R36S."}]
                draft = ""
                switch("waiting")
        else:
            draft = (draft + key)[:500]

    switch(scene)
    print("Desktop preview: F1 chat, F2 keyboard, F3 models, F4 history, F5 waiting,")
    print("F6 speech highlight (silent demo), F7 boot, F8 mic (visual demo).")
    print("Type normally in the keyboard scene; Enter sends a sample reply. Esc closes.")
    print("F9 replays the silent frame intro. Enter, Space or a click skips it.")
    print("R2 (joystick button 7) or F10 opens/closes session-only preview settings.")
    try:
        while running:
            now = time.monotonic()
            while app.SDL.SDL_PollEvent(event):
                composing = scene in ("keyboard", "mic")
                kind = int.from_bytes(bytes(event[:4]), "little")
                if kind == 0x100:
                    running = False
                elif kind == 0x603 and event[12] == 7:
                    if intro is not None and intro.active:
                        intro.close()
                    toggle_settings()
                elif intro is not None and intro.active:
                    if kind == 0x300:
                        key = int.from_bytes(bytes(event[20:24]), "little", signed=True)
                        if key == 27:
                            running = False
                        elif key in (13, 32):
                            intro.close()
                    elif kind == 0x401:
                        intro.close()
                elif kind == 0x603 and scene == "settings":
                    if settings.controller_button(event[12]) == "close":
                        toggle_settings()
                elif kind == 0x300:
                    key = int.from_bytes(bytes(event[20:24]), "little", signed=True)
                    modifiers = int.from_bytes(bytes(event[24:26]), "little")
                    if key == 27:
                        if scene == "settings":
                            if settings.key(key, modifiers) == "close":
                                toggle_settings()
                        else:
                            running = False
                    elif key == ord("r") and modifiers & 0xC0:
                        reload_requested, running = True, False
                    elif key == 1073741891:
                        toggle_settings()
                    elif key == 1073741890:
                        if intro is not None:
                            intro.close()
                        if settings.values["intro"]:
                            intro = app.IntroPlayer(renderer, ASSETS)
                        else:
                            settings.note = "Intro disabled for this preview session."
                    elif 1073741882 <= key <= 1073741889:
                        switch(SCENES[key - 1073741882])
                    elif scene == "settings":
                        settings.key(key, modifiers)
                    elif key == 9:
                        switch("chat" if scene == "history" else "history")
                    elif key == 13:
                        if composing:
                            activate("SEND")
                        else:
                            switch("chat" if scene in ("models", "history", "speech") else "keyboard")
                    elif key == 8:
                        if composing:
                            draft = draft[:-1]
                        else:
                            switch("chat")
                    elif key in (1073741906, 1073741905, 1073741904, 1073741903):
                        direction = -1 if key in (1073741906, 1073741904) else 1
                        if scene == "models":
                            model_index = max(0, min(len(choices) - 1, model_index + direction))
                        elif scene == "history":
                            history_index = max(0, min(len(chats) - 1, history_index + direction))
                        elif composing:
                            if key in (1073741906, 1073741905):
                                key_row = max(0, min(len(app.KEYBOARD) - 1, key_row + direction))
                            else:
                                key_col += direction
                            key_col = max(0, min(len(app.KEYBOARD[key_row]) - 1, key_col))
                        else:
                            scroll = max(0.0, scroll + direction * 36)
                elif kind == 0x303:
                    text = bytes(event[12:44]).split(b"\0", 1)[0].decode("utf-8")
                    if scene == "settings":
                        settings.text_input(text)
                    elif composing:
                        draft = (draft + text)[:500]
                elif kind in (0x400, 0x401):
                    cursor_x = int.from_bytes(bytes(event[20:24]), "little", signed=True)
                    cursor_y = int.from_bytes(bytes(event[24:28]), "little", signed=True)
                    if scene == "settings":
                        if kind == 0x401 and event[16] == 1:
                            settings.click(cursor_x, cursor_y)
                        else:
                            settings.hover(cursor_x, cursor_y)
                        continue
                    if composing:
                        for row, keys in enumerate(app.KEYBOARD):
                            if app.KEY_TOP + row * app.KEY_STEP <= cursor_y < app.KEY_TOP + row * app.KEY_STEP + app.KEY_HEIGHT:
                                for column, (x, width) in enumerate(app.key_geometry(row)):
                                    if x <= cursor_x < x + width:
                                        key_row, key_col = row, column
                                        if kind == 0x401 and event[16] == 1:
                                            activate(keys[column])
                        if kind == 0x401 and app.SUGGEST_TOP <= cursor_y < app.SUGGEST_TOP + app.SUGGEST_H:
                            suggestions = app.word_suggestions(draft)
                            for i, (x, width) in enumerate(app.suggestion_geometry(len(suggestions))):
                                if x <= cursor_x < x + width:
                                    draft = draft[:draft.rfind(" ") + 1] + suggestions[i] + " "
                    elif scene in ("models", "history"):
                        row = (cursor_y - app.HISTORY_LIST_TOP) // (52 if scene == "models" else app.HISTORY_ROW_H)
                        count = len(choices) if scene == "models" else len(chats)
                        if app.HISTORY_LIST_LEFT <= cursor_x < app.HISTORY_LIST_LEFT + app.HISTORY_LIST_W and 0 <= row < count:
                            if scene == "models":
                                model_index = row
                            else:
                                history_index = row
                            if kind == 0x401 and event[16] == 1:
                                switch("chat")
                    elif kind == 0x401 and event[16] == 1:
                        if contains((app.CONTENT_X, 424, app.CONTENT_W, 34), cursor_x, cursor_y):
                            switch("keyboard")
                        elif contains((app.CONTENT_X, app.FRAME_TOP, app.CONTENT_W,
                                       app.INPUT_TOP - app.FRAME_TOP), cursor_x, cursor_y):
                            switch("speech")
                elif kind == 0x403:
                    wheel = int.from_bytes(bytes(event[20:24]), "little", signed=True)
                    if scene == "settings":
                        settings.wheel(wheel)
                    else:
                        scroll = max(0.0, scroll - wheel * 36)

            elapsed = 1.4 if args.screenshot else time.monotonic() - started
            composing = scene in ("keyboard", "mic")
            shown = messages
            if scene == "waiting" and elapsed < 3:
                shown = messages[:-1] + [{"role": "assistant", "content": "", "pending": True}]
            elif scene == "waiting":
                switch("chat")
            word = None
            if scene == "speech" and settings.values["highlight"]:
                words = list(re.finditer(r"\S+", messages[-1]["content"]))
                if words:
                    word = words[int(elapsed * settings.values["speech_rate"] / 0.32) % len(words)].span()
            visible = app.viewport_metrics(composing)[3]
            height = sum(app.message_layout(fonts[1], item)[2] + app.MSG_GAP for item in shown)
            scroll = min(scroll, max(0, height - visible))
            app.draw_ui(renderer, fonts, cache, shown, scroll, composing, draft,
                        key_row, key_col, "BOOTING" if scene == "boot" else "READY",
                        choices[model_index][1]["label"], "", "", "", cursor_x, cursor_y,
                        logo, int(now * 2) % 2 == 0,
                        overlay=scene if scene in ("models", "history", "settings") else None,
                        chats=chats, history_index=history_index,
                        boot_fraction=(elapsed % 5) / 5, scramble_salt=int(now / app.SCRAMBLE_TICK),
                        suggestions=app.word_suggestions(draft) if composing else [],
                        mic_state="recording" if scene == "mic" else "idle", mic_seconds=elapsed,
                        tts_index=len(shown) - 1 if scene == "speech" else -1,
                        tts_word=word, models=choices, model_index=model_index,
                        intro=intro, background=background, settings=settings)
            if args.screenshot:
                screenshot(renderer, args.screenshot)
                print(f"Saved {args.screenshot}")
                break
            if tuple(path.stat().st_mtime_ns for path in files) != stamps:
                reload_requested = True
                break
            app.SDL.SDL_Delay(16)
    finally:
        if intro is not None:
            intro.close()
        cache.close()
        if background:
            background.close()
        app.SDL.SDL_DestroyTexture(logo)
        for font in fonts:
            app.TTF.TTF_CloseFont(font)
        app.SDL.SDL_StopTextInput()
        if joystick:
            app.SDL.SDL_JoystickClose(joystick)
        app.SDL.SDL_DestroyRenderer(renderer)
        app.SDL.SDL_DestroyWindow(window)
        app.TTF.TTF_Quit()
        app.SDL.SDL_Quit()
    if reload_requested:
        command = [sys.executable, str(Path(__file__).resolve()),
                   "--scene", scene, "--scale", str(args.scale), "--skip-intro",
                   "--settings-page", SETTINGS_PAGES[settings.page]]
        if settings.editing:
            command.append("--edit-prompt")
        os.execv(sys.executable, command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
