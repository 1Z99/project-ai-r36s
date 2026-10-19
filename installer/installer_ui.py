#!/usr/bin/env python3
"""227 AI deployment manager. SDL stays on the main thread, work runs offscreen."""

import argparse
from collections import OrderedDict, deque
import ctypes as C
import json
import os
from pathlib import Path
import queue
import textwrap
import threading
import time
import traceback

from installer_core import Cancelled, Installer, discover_cards

BG = (4, 8, 6, 255)
PANEL = (7, 15, 11, 255)
GREEN = (51, 255, 119, 255)
TEXT = (196, 240, 215, 255)
MUTED = (86, 146, 116, 255)
EDGE = (34, 124, 74, 255)
RED = (255, 82, 96, 255)
VERSION = "1.2.0"


def size(value):
    if value is None:
        return "--"
    if value >= 1024 ** 3:
        return "{:.1f} GB".format(value / 1024 ** 3)
    if value >= 1024 ** 2:
        return "{:.1f} MB".format(value / 1024 ** 2)
    return "{:.0f} KB".format(value / 1024)


def duration(value):
    if value is None:
        return "--:--"
    seconds = max(0, int(value))
    return "{:02d}:{:02d}".format(seconds // 60, seconds % 60)


class Manager:
    """Controller state, deliberately independent of SDL for regression tests."""

    def __init__(self, source=None, cards=None, worker=Installer, log=None):
        self.source = source
        self.cards = discover_cards() if cards is None else cards
        self.worker = worker
        self.log = log
        self.state = "home"
        self.focus = 0
        self.card_focus = 0
        self.chosen = None
        self.action = None
        self.confirm_yes = False
        self.running = True
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.thread = None
        self.progress = {}
        self.history = deque(maxlen=5)
        self.details = []
        self.detail_scroll = 0
        self.error = ""
        self.started = 0
        self.finished = None
        self.speed = 0
        self.operation = {}

    def emit(self, event):
        self.events.put(event)

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        card = self.cards[self.chosen]
        self.cancel.clear()
        self.state = "working"
        self.progress = {}
        self.history.clear()
        self.error = ""
        self.details = []
        self.started = time.monotonic()
        self.finished = None
        self.speed = 0
        self.operation = {}

        def work():
            try:
                operation = self.worker(card["path"], source_dir=self.source,
                                        emit=self.emit, cancel=self.cancel).run(self.action)
                self.emit(dict(phase="result", result="success", operation=operation or {}))
            except Cancelled as error:
                self.emit(dict(phase="result", result="cancelled", detail=str(error)))
            except Exception as error:
                self.emit(dict(phase="result", result="failed", detail=str(error),
                               traceback=traceback.format_exc()))

        self.thread = threading.Thread(target=work, daemon=False)
        self.thread.start()

    def update(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if self.log is not None:
                self.log.write(json.dumps(event) + "\n")
                self.log.flush()
            if event["phase"] == "result":
                self.state = event["result"]
                self.operation = event.get("operation", {})
                self.finished = time.monotonic()
                self.error = event.get("detail", "")
                self.details = textwrap.wrap(self.error, 54, replace_whitespace=True) or ["No error details."]
                self.detail_scroll = 0
                continue
            self.progress = event
            if event.get("speed") is not None:
                speed = event["speed"]
                self.speed = self.speed * .6 + speed * .4 if self.speed else speed
            label = event.get("label", "")
            if not self.history or self.history[-1] != label:
                self.history.append(label)

    def button(self, button):
        if self.state == "home":
            if button == "back":
                self.running = False
            elif button == "refresh":
                self.cards = discover_cards()
                self.chosen = None
            elif button in ("up", "down"):
                self.focus = (self.focus + (-1 if button == "up" else 1)) % 4
            elif button in ("left", "right"):
                self.focus = 0
                self.card_focus = 1 - self.card_focus
            elif button == "ok":
                if self.focus == 0:
                    if self.cards[self.card_focus]["available"]:
                        self.chosen = self.card_focus
                        self.focus = 1
                elif self.chosen is None:
                    self.focus = 0
                else:
                    self.action = ("install", "update", "uninstall")[self.focus - 1]
                    self.state = "confirm"
                    self.confirm_yes = False
        elif self.state == "confirm":
            if button == "back":
                self.state = "home"

            elif button in ("left", "right", "up", "down"):
                self.confirm_yes = not self.confirm_yes
            elif button == "ok":
                if self.confirm_yes:
                    self.start()
                else:
                    self.state = "home"
        elif self.state == "working":
            if button == "back":
                self.state = "cancel_confirm"
                self.confirm_yes = False
        elif self.state == "cancel_confirm":
            if button == "back":
                self.state = "working"
            elif button in ("left", "right", "up", "down"):
                self.confirm_yes = not self.confirm_yes
            elif button == "ok":
                if self.confirm_yes:
                    self.cancel.set()
                self.state = "working"
        elif self.state == "details":
            if button in ("back", "ok"):
                self.state = "failed"
            elif button == "up":
                self.detail_scroll = max(0, self.detail_scroll - 1)
            elif button == "down":
                self.detail_scroll = min(max(0, len(self.details) - 12), self.detail_scroll + 1)
        elif self.state in ("failed", "cancelled", "success"):
            if button == "ok" and self.state != "success":
                self.start()
            elif button == "refresh" and self.state == "failed":
                self.state = "details"
            elif button in ("back", "ok"):
                self.cards = discover_cards()
                self.chosen = None
                self.focus = 0
                self.state = "home"

    def completion(self):
        outcome = self.operation.get("outcome")
        source = self.operation.get("source", "")
        checked = ("Checked: local test bundle (not GitHub)." if source == "Local bundle" else
                   "Checked: " + source if source else "")
        if outcome == "up_to_date":
            return "YOU'RE UP TO DATE", ["No updates or repairs were needed.",
                                        "Files, models, runtime and menus verified.",
                                        checked, "No app files were rewritten."]
        if self.action == "uninstall":
            return "UNINSTALL COMPLETE", ["227 AI was removed from the selected card.",
                                          "The manager remains in Tools for reinstalling."]
        if outcome in ("updated", "repaired"):
            title = "REPAIR COMPLETE" if outcome == "repaired" else "UPDATE / REPAIR COMPLETE"
            return title, ["Missing, changed or damaged items were fixed.",
                           self.operation.get("detail", ""), checked,
                           "Your existing settings and chats were kept.",
                           "Restart ES if its menu or artwork changed."]
        return "INSTALL COMPLETE", ["227 AI is ready. Microphone testing is built in.",
                                    checked,
                                    "Restart ES, then open 227 > 227 AI.",
                                    "Test your microphone in R2 > VOICE."]


class Rect(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("x", "y", "w", "h")]


class Color(C.Structure):
    _fields_ = [(name, C.c_uint8) for name in ("r", "g", "b", "a")]


class Display:
    def __init__(self, desktop=False):
        # dArkOSRE's GLVND EGL loader can be incompatible with its Mali GLES library.
        mali = "/usr/lib/aarch64-linux-gnu/libMali.so"
        if os.environ.get("SDL_VIDEODRIVER") == "kmsdrm" and Path(mali).is_file():
            os.environ.setdefault("SDL_VIDEO_EGL_DRIVER", mali)
            os.environ.setdefault("SDL_VIDEO_GL_DRIVER", mali)
        self.sdl = C.CDLL("libSDL2-2.0.so.0")
        self.ttf = C.CDLL("libSDL2_ttf-2.0.so.0")
        self.window = self.renderer = self.joystick = None
        self.fonts = {}
        self.textures = OrderedDict()
        p, i, u = C.c_void_p, C.c_int, C.c_uint32
        for lib, name, args, result in (
            (self.sdl, "SDL_Init", [u], i),
            (self.sdl, "SDL_CreateWindow", [C.c_char_p, i, i, i, i, u], p),
            (self.sdl, "SDL_CreateRenderer", [p, i, u], p),
            (self.sdl, "SDL_SetRenderDrawColor", [p] + [C.c_uint8] * 4, i),
            (self.sdl, "SDL_RenderFillRect", [p, C.POINTER(Rect)], i),
            (self.sdl, "SDL_RenderDrawRect", [p, C.POINTER(Rect)], i),
            (self.sdl, "SDL_RenderClear", [p], i),
            (self.sdl, "SDL_RenderPresent", [p], None),
            (self.sdl, "SDL_CreateTextureFromSurface", [p, p], p),
            (self.sdl, "SDL_QueryTexture", [p, p, p, C.POINTER(i), C.POINTER(i)], i),
            (self.sdl, "SDL_RenderCopy", [p, p, p, C.POINTER(Rect)], i),
            (self.sdl, "SDL_PollEvent", [p], i),
            (self.sdl, "SDL_JoystickOpen", [i], p),
            (self.sdl, "SDL_GetError", [], C.c_char_p),
            (self.sdl, "SDL_RenderReadPixels", [p, p, u, p, i], i),
            (self.ttf, "TTF_Init", [], i),
            (self.ttf, "TTF_OpenFont", [C.c_char_p, i], p),
            (self.ttf, "TTF_RenderUTF8_Blended", [p, C.c_char_p, Color], p),
        ):
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, result
        for name in ("SDL_DestroyTexture", "SDL_FreeSurface", "SDL_DestroyRenderer",
                     "SDL_DestroyWindow", "SDL_JoystickClose"):
            getattr(self.sdl, name).argtypes = [p]
        self.ttf.TTF_CloseFont.argtypes = [p]
        try:
            if self.sdl.SDL_Init(0x20 | 0x200) != 0 or self.ttf.TTF_Init() != 0:
                raise RuntimeError(self.sdl.SDL_GetError().decode())
            self.window = self.sdl.SDL_CreateWindow(b"227 AI // Deployment Manager", 0, 0, 640, 480,
                                                    0 if desktop else 1)
            if not self.window:
                raise RuntimeError(self.sdl.SDL_GetError().decode())
            self.renderer = self.sdl.SDL_CreateRenderer(self.window, -1, 1)
            if not self.renderer:
                raise RuntimeError(self.sdl.SDL_GetError().decode())
            base = Path(__file__).resolve().parent
            font_path = next((path for path in (
                Path(os.environ.get("PROJECT_AI_FONT", str(base / "fonts/DejaVuSansMono.ttf"))),
                base / "fonts/DejaVuSansMono.ttf",
                base / "bundle/assets/DejaVuSansMono.ttf",
                base.parent / "assets/DejaVuSansMono.ttf",
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
            ) if path.is_file()), None)
            for label, pixels in (("title", 28), ("body", 18), ("small", 14), ("number", 34)):
                font = self.ttf.TTF_OpenFont(os.fsencode(font_path), pixels) if font_path else None
                if not font:
                    raise RuntimeError("DejaVu Sans Mono font is missing")
                self.fonts[label] = font
            if self.sdl.SDL_NumJoysticks() > 0:
                self.joystick = self.sdl.SDL_JoystickOpen(0)
            self.sdl.SDL_ShowCursor(1 if desktop else 0)
        except Exception:
            self.close()
            raise

    def close(self):
        for texture, _, _ in self.textures.values():
            self.sdl.SDL_DestroyTexture(texture)
        self.textures.clear()
        for font in self.fonts.values():
            self.ttf.TTF_CloseFont(font)
        self.fonts.clear()
        if self.joystick:
            self.sdl.SDL_JoystickClose(self.joystick)
        if self.renderer:
            self.sdl.SDL_DestroyRenderer(self.renderer)
        if self.window:
            self.sdl.SDL_DestroyWindow(self.window)
        self.ttf.TTF_Quit()
        self.sdl.SDL_Quit()

    def rect(self, x, y, w, h, color, outline=False):
        self.sdl.SDL_SetRenderDrawColor(self.renderer, *color)
        fn = self.sdl.SDL_RenderDrawRect if outline else self.sdl.SDL_RenderFillRect
        fn(self.renderer, C.byref(Rect(int(x), int(y), int(w), int(h))))

    def text(self, value, x, y, color=TEXT, font="body", width=600):
        value = str(value).replace("\n", " ").replace("\r", " ")
        # Trim instead of distorting glyphs to fit the handheld display.
        limit = max(1, int(width / {"small": 8.5, "body": 11, "title": 17, "number": 21}[font]))
        value = value if len(value) <= limit else value[:max(0, limit - 3)] + "..."
        key = (value, color, font)
        if key not in self.textures:
            surface = self.ttf.TTF_RenderUTF8_Blended(self.fonts[font], value.encode(), Color(*color))
            if not surface:
                return
            texture = self.sdl.SDL_CreateTextureFromSurface(self.renderer, surface)
            self.sdl.SDL_FreeSurface(surface)
            if not texture:
                return
            w, h = C.c_int(), C.c_int()
            self.sdl.SDL_QueryTexture(texture, None, None, C.byref(w), C.byref(h))
            self.textures[key] = (texture, w.value, h.value)
            if len(self.textures) > 160:
                old, _, _ = self.textures.popitem(last=False)[1]
                self.sdl.SDL_DestroyTexture(old)
        self.textures.move_to_end(key)
        texture, w, h = self.textures[key]
        self.sdl.SDL_RenderCopy(self.renderer, texture, None, C.byref(Rect(x, y, w, h)))

    def panel(self, x, y, w, h, active=False, color=GREEN):
        self.rect(x, y, w, h, PANEL)
        self.rect(x, y, w, h, color if active else EDGE, True)
        if active:
            for cx, cy in ((x, y), (x + w - 9, y), (x, y + h - 2), (x + w - 9, y + h - 2)):
                self.rect(cx, cy, 9, 2, color)

    def bar(self, x, y, width, ratio):
        self.rect(x, y, width, 8, EDGE)
        if ratio is not None:
            self.rect(x, y, int(width * max(0, min(1, ratio))), 8, GREEN)
        else:
            self.rect(x + int((time.monotonic() % 2) / 2 * (width - 65)), y, 65, 8, GREEN)

    def draw(self, model):
        self.rect(0, 0, 640, 480, BG)
        self.text("227", 20, 12, GREEN, "number")
        self.text("AI // DEPLOYMENT MANAGER", 104, 15, TEXT, "body")
        self.text("LOCAL TEST BUILD" if model.source else "INSTALL / MAINTAIN", 105, 39, MUTED, "small")
        self.text("v" + VERSION, 552, 24, MUTED, "small", 80)
        self.rect(20, 65, 600, 1, EDGE)
        if model.state == "home":
            self.text("01  CHOOSE THE DESTINATION CARD", 20, 77, GREEN, "small")
            for index, card in enumerate(model.cards):
                x = 20 + index * 306
                active = model.focus == 0 and index == model.card_focus
                self.panel(x, 102, 294, 118, active)
                self.text(card["label"], x + 14, 112, GREEN if card["available"] else MUTED, width=266)
                self.text(card["path"], x + 14, 141, TEXT, "small")
                self.text(size(card["free"]) + " FREE" if card["available"] else "NOT MOUNTED / UNAVAILABLE",
                          x + 14, 163, MUTED, "small", 268)
                status = "[ SELECTED ]" if model.chosen == index else "APP PRESENT" if card["installed"] else "NO INSTALLATION"
                self.text(status, x + 14, 192, GREEN if model.chosen == index else MUTED, "small")
            self.text("02  CHOOSE AN ACTION", 20, 235, GREEN, "small")
            for index, (label, desc) in enumerate((("INSTALL", "Set up 227 AI on this card"),
                                                   ("UPDATE / REPAIR", "Keep chats, settings and models"),
                                                   ("UNINSTALL", "Remove app and data from selected card"))):
                y = 260 + index * 53
                active = model.focus == index + 1
                color = RED if index == 2 else GREEN
                self.panel(20, y, 600, 45, active, color)
                self.text(("> " if active else "  ") + label, 30, y + 10, color if active else TEXT, width=238)
                self.text(desc, 273, y + 14, MUTED, "small", 333)
            footer = "D-PAD choose   A select   X refresh   B exit"
        elif model.state in ("confirm", "cancel_confirm"):
            cancelling = model.state == "cancel_confirm"
            danger = model.action == "uninstall" or cancelling
            self.text("CONFIRM " + ("CANCELLATION" if cancelling else model.action.upper()), 20, 90,
                      RED if danger else GREEN, "title")
            card = model.cards[model.chosen]
            self.text(card["label"] + "  " + card["path"], 20, 144, TEXT)
            lines = (["Stop this operation?", "Downloaded partial files are kept for retry."] if cancelling else
                     ["Deletes 227 AI, models, settings and chats", "from THIS card only. This cannot be undone.",
                      "The installer manager stays available in Tools."] if danger else
                     ["Install only 227 AI with built-in microphone test.", "Settings and conversations are preserved.",
                      "Downloads resume after interruptions.", "Allow about 650 MB free for staging."])
            for index, line in enumerate(lines):
                self.text(line, 20, 192 + index * 28, MUTED, width=600)
            for index, label in enumerate(("NO / GO BACK", "YES / " + ("STOP" if cancelling else model.action.upper()))):
                selected = bool(index) == model.confirm_yes
                self.panel(20 + index * 306, 356, 294, 56, selected, RED if danger and index else GREEN)
                self.text(label, 36 + index * 306, 374, TEXT, width=260)
            footer = "D-PAD choose   A confirm   B back"
        elif model.state == "details":
            self.text("ERROR DETAILS", 20, 82, RED, "title")
            for index, line in enumerate(model.details[model.detail_scroll:model.detail_scroll + 12]):
                self.text(line, 20, 124 + index * 23, TEXT, width=600)
            footer = "UP/DOWN scroll   A / B back   Full log in manager"
        elif model.state == "working":
            event = model.progress
            self.text(model.action.upper() + " // " + model.cards[model.chosen]["path"], 20, 83, GREEN)
            self.text(event.get("phase", "prepare").upper(), 20, 117, MUTED, "small")
            label = event.get("label", "Preparing...")
            for index, line in enumerate(textwrap.wrap(label, 43)[:2]):
                self.text(line, 20, 143 + index * 23, TEXT)
            done, total = event.get("done", 0), event.get("total")
            ratio = min(1, done / total) if total else None
            self.text("{:3.0f}%".format(ratio * 100) if ratio is not None else " ...", 494, 188, GREEN, "number", 126)
            self.bar(20, 239, 600, ratio)
            self.text(size(done) + " / " + size(total) if total and total > 1 else "TASK PROGRESS", 20, 208, MUTED, "small", 440)
            self.panel(20, 269, 600, 65)
            elapsed = (model.finished or time.monotonic()) - model.started
            rate = size(model.speed) + "/s" if event.get("phase") == "download" else "--"
            eta = (total - done) / model.speed if total and model.speed and event.get("phase") == "download" else None
            for x, label, value in ((36, "TRANSFER", rate), (252, "ELAPSED", duration(elapsed)), (459, "FILE ETA", duration(eta))):
                self.text(label, x, 278, MUTED, "small", 160)
                self.text(value, x, 300, TEXT, width=160)
            for index, line in enumerate(list(model.history)[-3:]):
                self.text("> " + line, 20, 350 + index * 23, MUTED, "small", 600)
            footer = "B cancel safely   Progress is per task / file"
            if model.cancel.is_set():
                footer = ("Finalizing safely... please do not power off" if event.get("phase") in
                          ("commit", "rollback", "uninstall", "files", "es", "theme", "config", "dependencies") else
                          "Stopping safely... partial downloads will be kept")
        else:
            success = model.state == "success"
            cancelled = model.state == "cancelled"
            title, success_lines = model.completion() if success else (None, [])
            self.text(title if success else "STOPPED" if cancelled else "DOWNLOAD / INSTALL FAILED",
                      20, 90, GREEN if success or cancelled else RED, "title")
            self.text("ELAPSED " + duration((model.finished or time.monotonic()) - model.started), 20, 141, MUTED, "small")
            if success:
                lines = success_lines
            elif cancelled:
                lines = ["Operation stopped. Downloaded partials are kept.", "Press A to resume, or B to return to the menu."]
            else:
                lines = ["The manager stays open. Your downloads are kept.", "Check Wi-Fi, then press A to retry / resume.", "X shows the full error. B returns to the menu."]
            for index, line in enumerate(lines):
                self.text(line, 20, 195 + index * 29, TEXT, width=600)
            if not success:
                self.text(model.error.splitlines()[0] if model.error else "", 20, 310, RED, "small", 600)
            self.panel(20, 367, 600, 45, True)
            self.text("A  RETURN TO MENU" if success else "A  RETRY / RESUME", 36, 379, GREEN)
            footer = "B menu" if success else "A retry   X error details   B menu"
        self.rect(20, 442, 600, 1, EDGE)
        self.text(footer, 20, 454, MUTED, "small", 600)
        self.sdl.SDL_RenderPresent(self.renderer)


def main():
    parser = argparse.ArgumentParser(description="227 AI graphical install/update/uninstall manager")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--demo", choices=("home", "working", "failed", "confirm", "success", "up-to-date", "updated", "repaired"))
    parser.add_argument("--screenshot", type=Path, help="Write one 640x480 RGBA frame and exit")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    source = args.source or (base / "bundle" if (base / "bundle/app").is_dir() else None)
    demo_cards = [dict(path=path, label=label, available=True, free=free * 1024 ** 3,
                       total=64 * 1024 ** 3, installed=index == 1, reason="")
                  for index, (path, label, free) in enumerate((("/roms", "Main SD / SD1", 40), ("/roms2", "Second SD / SD2", 18)))]
    # Keep the log outside the app directory so uninstall cannot remove an open log.
    with (base / "installer.log").open("a", encoding="utf-8") as log:
        model = Manager(source, cards=demo_cards if args.demo else None, log=log)
        if args.demo:
            model.state = args.demo
            model.chosen, model.action = 0, "install"
            if args.demo in ("up-to-date", "updated", "repaired"):
                model.state, model.action = "success", "update"
                model.operation = dict(outcome="up_to_date" if args.demo == "up-to-date" else args.demo,
                                       source="Local bundle", changes=[] if args.demo == "up-to-date" else ["Example managed file"],
                                       detail="1 changed item(s); Local bundle")
            model.started = time.monotonic() - 82
            model.progress = dict(phase="download", label="Downloading LFM2.5-350M-QAD-Q4_0.gguf",
                                  done=126 * 1024 ** 2, total=214 * 1024 ** 2)
            model.speed = 1.7 * 1024 ** 2
            model.error = "Connection timed out. Partial downloads retained."
            model.history.extend(("Runtime verified", "Primary mirror connected", "Downloading language model"))
        display = Display(args.desktop)
        event = (C.c_uint8 * 64)()
        last_button, last_time = None, -1000
        try:
            while model.running:
                model.update()
                while display.sdl.SDL_PollEvent(event):
                    kind = int.from_bytes(bytes(event[:4]), "little")
                    button = None
                    if kind == 0x100:
                        if model.thread is not None and model.thread.is_alive():
                            model.cancel.set()
                        else:
                            model.running = False
                    elif kind == 0x603:
                        button = {0: "back", 1: "ok", 2: "refresh", 8: "up", 9: "down", 10: "left", 11: "right"}.get(event[12])
                    elif kind == 0x602:
                        button = next((name for bit, name in ((1, "up"), (4, "down"), (8, "left"), (2, "right")) if event[13] & bit), None)
                    elif kind == 0x300:
                        key = int.from_bytes(bytes(event[20:24]), "little")
                        button = {27: "back", 13: "ok", 32: "ok", 120: "refresh", 1073741906: "up",
                                  1073741905: "down", 1073741904: "left", 1073741903: "right"}.get(key)
                    if button:
                        timestamp = int.from_bytes(bytes(event[4:8]), "little")
                        if button != last_button or (timestamp - last_time) % 2 ** 32 >= 140:
                            model.button(button)
                            last_button, last_time = button, timestamp
                display.draw(model)
                if args.screenshot:
                    pixels = (C.c_uint8 * (640 * 480 * 4))()
                    if display.sdl.SDL_RenderReadPixels(display.renderer, None, 0x16762004, pixels, 640 * 4) != 0:
                        raise RuntimeError("Unable to read rendered frame")
                    args.screenshot.write_bytes(bytes(pixels))
                    break
                time.sleep(1 / 30)
        finally:
            model.cancel.set()
            if model.thread is not None:
                model.thread.join()
            display.close()


if __name__ == "__main__":
    main()
