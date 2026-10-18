#!/usr/bin/env python3
"""Tiny microphone/capture-card tester for the R36S.

Records a few seconds from each ALSA capture card, plays it back and shows a
speech-likeness readout so you can find the card that actually hears you.
"""

import array
import ctypes
import re
import signal
import struct
import subprocess
import sys
import time
import wave
from ctypes import byref, c_char_p, c_int, c_uint8, c_uint32, c_void_p
from pathlib import Path

W, H = 640, 480
TMP = Path("/tmp/mictest")
TMP.mkdir(exist_ok=True)
SAMPLE_RATE = 16000
RECORD_SECONDS = 5
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

BG = (4, 8, 6, 255)
PANEL = (7, 15, 11, 255)
BORDER = (18, 66, 40, 255)
EDGE = (34, 124, 74, 255)
TEXT = (196, 240, 215, 255)
MUTED = (86, 146, 116, 255)
GREEN = (51, 255, 119, 255)
GREEN_DIM = (28, 176, 96, 255)
ERROR = (255, 82, 96, 255)
HIGHLIGHT = (51, 255, 119, 255)

SDL = ctypes.CDLL("libSDL2-2.0.so.0")
TTF = ctypes.CDLL("libSDL2_ttf-2.0.so.0")


class Rect(ctypes.Structure):
    _fields_ = [("x", c_int), ("y", c_int), ("w", c_int), ("h", c_int)]


class Color(ctypes.Structure):
    _fields_ = [("r", c_uint8), ("g", c_uint8), ("b", c_uint8), ("a", c_uint8)]


SDL.SDL_Init.argtypes = [c_uint32]
SDL.SDL_Init.restype = c_int
SDL.SDL_GetError.restype = c_char_p
SDL.SDL_CreateWindow.argtypes = [c_char_p, c_int, c_int, c_int, c_int, c_uint32]
SDL.SDL_CreateWindow.restype = c_void_p
SDL.SDL_CreateRenderer.argtypes = [c_void_p, c_int, c_uint32]
SDL.SDL_CreateRenderer.restype = c_void_p
SDL.SDL_SetRenderDrawColor.argtypes = [c_void_p, c_uint8, c_uint8, c_uint8, c_uint8]
SDL.SDL_RenderFillRect.argtypes = [c_void_p, ctypes.POINTER(Rect)]
SDL.SDL_RenderDrawRect.argtypes = [c_void_p, ctypes.POINTER(Rect)]
SDL.SDL_RenderPresent.argtypes = [c_void_p]
SDL.SDL_RenderClear.argtypes = [c_void_p]
SDL.SDL_PollEvent.argtypes = [c_void_p]
SDL.SDL_PollEvent.restype = c_int
SDL.SDL_Delay.argtypes = [c_uint32]
SDL.SDL_CreateTextureFromSurface.argtypes = [c_void_p, c_void_p]
SDL.SDL_CreateTextureFromSurface.restype = c_void_p
SDL.SDL_QueryTexture.argtypes = [c_void_p, c_void_p, c_void_p,
                                 ctypes.POINTER(c_int), ctypes.POINTER(c_int)]
SDL.SDL_RenderCopy.argtypes = [c_void_p, c_void_p, c_void_p, ctypes.POINTER(Rect)]
SDL.SDL_DestroyTexture.argtypes = [c_void_p]
SDL.SDL_FreeSurface.argtypes = [c_void_p]
SDL.SDL_NumJoysticks.restype = c_int
SDL.SDL_JoystickOpen.argtypes = [c_int]
SDL.SDL_JoystickOpen.restype = c_void_p
SDL.SDL_JoystickClose.argtypes = [c_void_p]
SDL.SDL_DestroyRenderer.argtypes = [c_void_p]
SDL.SDL_DestroyWindow.argtypes = [c_void_p]
SDL.SDL_Quit.argtypes = []

TTF.TTF_Init.restype = c_int
TTF.TTF_OpenFont.argtypes = [c_char_p, c_int]
TTF.TTF_OpenFont.restype = c_void_p
TTF.TTF_SizeUTF8.argtypes = [c_void_p, c_char_p, ctypes.POINTER(c_int), ctypes.POINTER(c_int)]
TTF.TTF_RenderUTF8_Blended.argtypes = [c_void_p, c_char_p, Color]
TTF.TTF_RenderUTF8_Blended.restype = c_void_p
TTF.TTF_CloseFont.argtypes = [c_void_p]
TTF.TTF_Quit.argtypes = []


def fill(renderer, x, y, w, h, color):
    if w <= 0 or h <= 0:
        return
    SDL.SDL_SetRenderDrawColor(renderer, *color)
    SDL.SDL_RenderFillRect(renderer, byref(Rect(int(x), int(y), int(w), int(h))))


def box(renderer, x, y, w, h, color):
    if w <= 0 or h <= 0:
        return
    SDL.SDL_SetRenderDrawColor(renderer, *color)
    SDL.SDL_RenderDrawRect(renderer, byref(Rect(int(x), int(y), int(w), int(h))))


def draw_text(renderer, font, text, x, y, color):
    surface = TTF.TTF_RenderUTF8_Blended(font, str(text).encode("utf-8", "replace"), Color(*color))
    if not surface:
        return 0, 0
    texture = SDL.SDL_CreateTextureFromSurface(renderer, surface)
    SDL.SDL_FreeSurface(surface)
    tw, th = c_int(), c_int()
    SDL.SDL_QueryTexture(texture, None, None, byref(tw), byref(th))
    SDL.SDL_RenderCopy(renderer, texture, None, byref(Rect(int(x), int(y), tw.value, th.value)))
    SDL.SDL_DestroyTexture(texture)
    return tw.value, th.value


def detect_devices():
    devices = []
    try:
        result = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.SubprocessError):
        return devices
    for raw in result.stdout.splitlines():
        match = re.match(r"card (\d+): (\S+) \[(.*?)\], device (\d+): (.*)", raw)
        if not match:
            continue
        card = int(match.group(1))
        index = int(match.group(4))
        name = (match.group(3) or match.group(2)).strip()
        node = Path(f"/dev/snd/pcmC{card}D{index}c")
        devices.append({
            "card": card, "index": index, "name": name,
            "arg": f"plughw:{card},{index}", "node": node.exists(),
        })
    return devices


def repair_wav(path):
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < 44:
        return
    try:
        with path.open("r+b") as handle:
            header = handle.read(64)
            idx = header.find(b"data")
            if idx < 0:
                return
            handle.seek(4)
            handle.write(struct.pack("<I", size - 8))
            handle.seek(idx + 4)
            handle.write(struct.pack("<I", size - (idx + 8)))
    except OSError:
        pass


def analyze(path):
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.readframes(min(handle.getnframes(), SAMPLE_RATE * 60))
            rate = handle.getframerate() or SAMPLE_RATE
    except (OSError, wave.Error, MemoryError):
        return None
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) - (len(frames) % 2)])
    count = len(samples)
    if count == 0:
        return {"peak": 0, "rms": 0.0, "cross": 0.0, "dur": 0.0}
    peak = max((abs(sample) for sample in samples), default=0)
    rms = (sum(sample * sample for sample in samples) / count) ** 0.5
    window = max(1, rate // 100)
    running = 0
    crossings = 0
    previous = 0
    for i in range(count):
        running += samples[i]
        if i >= window:
            running -= samples[i - window]
        value = samples[i] - running / window
        if i > 0 and (previous < 0) != (value < 0):
            crossings += 1
        previous = value
    duration = count / rate
    return {"peak": peak, "rms": rms, "cross": crossings / max(duration, 0.001),
            "dur": duration, "nframes": count}


def verdict(stats, device):
    if not device["node"]:
        return "UNAVAILABLE", ERROR
    if stats is None or stats["dur"] < 0.2:
        return "NO AUDIO", ERROR
    if stats["peak"] < 100:
        return "SILENT", MUTED
    if stats["cross"] < 300:
        return "RUMBLE / NOISE", ERROR
    if stats["cross"] < 1000:
        return "WEAK", MUTED
    return "SPEECH OK", GREEN


class Recorder:
    def __init__(self):
        self.proc = None
        self.path = None
        self.started = 0.0

    def start(self, device, path):
        self.path = Path(path)
        self.path.unlink(missing_ok=True)
        self.proc = subprocess.Popen(
            ["arecord", "-D", device, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
             "-c", "1", "-t", "wav", "-q", str(self.path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.started = time.monotonic()

    def elapsed(self):
        return time.monotonic() - self.started

    def stop(self):
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self.path is not None:
            repair_wav(self.path)


def play(path, device="default"):
    return subprocess.Popen(
        ["aplay", "-q", "-D", device, str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y = 1, 0, 2, 3
BUTTON_START, BUTTON_SELECT = 13, 12
BUTTON_UP, BUTTON_DOWN, BUTTON_LEFT, BUTTON_RIGHT = 8, 9, 10, 11


def main():
    if SDL.SDL_Init(0x20 | 0x200 | 0x2000) != 0 or TTF.TTF_Init() != 0:
        print("SDL init failed:", SDL.SDL_GetError())
        return 1
    window = SDL.SDL_CreateWindow(b"MIC TEST", 0, 0, W, H, 1)
    renderer = SDL.SDL_CreateRenderer(window, -1, 6) if window else None
    if not renderer:
        print("renderer failed:", SDL.SDL_GetError())
        return 1
    joystick = SDL.SDL_JoystickOpen(0) if SDL.SDL_NumJoysticks() > 0 else None

    font_big = TTF.TTF_OpenFont(FONT_BOLD.encode(), 30)
    font_mid = TTF.TTF_OpenFont(FONT_BOLD.encode(), 19)
    font_small = TTF.TTF_OpenFont(FONT.encode(), 15)

    devices = detect_devices()
    state = "menu"          # menu | recording | result | testing | summary
    selection = 0
    mode = ""               # "single" | "all"
    recorder = Recorder()
    player = None
    result = None
    per_device = []         # for test-all

    def current():
        return devices[selection] if devices else None

    def begin_single():
        nonlocal state, mode, result, player
        device = current()
        if device is None:
            return
        result = None
        mode = "single"
        recorder.start(device["arg"], TMP / f"c{device['card']}.wav")
        state = "recording"

    def begin_all():
        nonlocal state, mode, per_device, selection
        if not devices:
            return
        per_device = []
        mode = "all"
        state = "recording"
        start_index_recording(0)

    def start_index_recording(index):
        nonlocal selection
        selection = index
        recorder.start(devices[index]["arg"], TMP / f"c{devices[index]['card']}.wav")

    def finish_recording():
        nonlocal state, result, player, per_device
        recorder.stop()
        device = devices[selection]
        stats = analyze(recorder.path)
        player = play(recorder.path)
        if mode == "single":
            result = {"device": device, "stats": stats}
            state = "result"
        else:
            per_device.append({"device": device, "stats": stats})
            if selection + 1 < len(devices):
                start_index_recording(selection + 1)
            else:
                state = "summary"

    last_button = -1
    last_time = 0
    running = True
    event_buffer = (ctypes.c_uint8 * 64)()

    try:
        while running:
            now = time.monotonic()
            while SDL.SDL_PollEvent(event_buffer):
                etype = int.from_bytes(bytes(event_buffer[:4]), "little")
                button = None
                if etype == 0x100:
                    running = False
                    continue
                if etype == 0x300:
                    sym = int.from_bytes(bytes(event_buffer[20:24]), "little")
                    button = {27: BUTTON_B, 13: BUTTON_A, 32: BUTTON_A,
                              1073741906: BUTTON_UP, 1073741905: BUTTON_DOWN,
                              1073741904: BUTTON_LEFT, 1073741903: BUTTON_RIGHT,
                              114: BUTTON_START}.get(sym)
                elif etype == 0x602:
                    hat = event_buffer[13]
                    if hat & 1:
                        button = BUTTON_UP
                    elif hat & 4:
                        button = BUTTON_DOWN
                    elif hat & 8:
                        button = BUTTON_LEFT
                    elif hat & 2:
                        button = BUTTON_RIGHT
                elif etype == 0x603:
                    button = event_buffer[12]
                if button is None:
                    continue
                button_time = int.from_bytes(bytes(event_buffer[4:8]), "little") or (int(now * 1000) & 0xFFFFFFFF)
                if button == last_button and ((button_time - last_time) & 0xFFFFFFFF) < 140:
                    continue
                last_button, last_time = button, button_time

                if state == "menu":
                    if button == BUTTON_UP and devices:
                        selection = (selection - 1) % len(devices)
                    elif button == BUTTON_DOWN and devices:
                        selection = (selection + 1) % len(devices)
                    elif button == BUTTON_A and devices:
                        begin_single()
                    elif button == BUTTON_START and devices:
                        begin_all()
                    elif button == BUTTON_B:
                        running = False
                elif state == "recording":
                    if button == BUTTON_B:
                        recorder.stop()
                        result = None
                        state = "menu"
                elif state == "result":
                    if button in (BUTTON_A, BUTTON_X):
                        result_device = devices[selection]
                        player = play(TMP / f"c{result_device['card']}.wav")
                    elif button == BUTTON_B:
                        state = "menu"
                elif state == "summary":
                    if button == BUTTON_UP:
                        selection = (selection - 1) % max(1, len(per_device))
                    elif button == BUTTON_DOWN:
                        selection = (selection + 1) % max(1, len(per_device))
                    elif button in (BUTTON_A, BUTTON_X) and per_device:
                        player = play(TMP / f"c{per_device[selection]['device']['card']}.wav")
                    elif button == BUTTON_B:
                        state = "menu"

            if state == "recording":
                if recorder.elapsed() >= RECORD_SECONDS:
                    finish_recording()

            # ---- draw ----
            SDL.SDL_SetRenderDrawColor(renderer, *BG)
            SDL.SDL_RenderClear(renderer)
            fill(renderer, 0, 0, W, 52, PANEL)
            fill(renderer, 0, 50, W, 2, EDGE)
            draw_text(renderer, font_big, "MIC TEST", 16, 10, GREEN)
            draw_text(renderer, font_small, f"{len(devices)} capture card(s)", W - 190, 22, MUTED)

            if state == "menu":
                y = 74
                for i, device in enumerate(devices):
                    line_color = GREEN if i == selection else TEXT
                    tag = "" if device["node"] else "  [no pcm node]"
                    marker = ">" if i == selection else " "
                    draw_text(renderer, font_mid, f"{marker} card {device['card']}: {device['name']}{tag}", 16, y, line_color)
                    y += 34
                if not devices:
                    draw_text(renderer, font_mid, "No capture devices found.", 16, 80, ERROR)
                draw_text(renderer, font_small, "UP/DOWN choose   A: record+test   START: test all   B: exit", 16, H - 26, MUTED)

            elif state == "recording":
                device = devices[selection]
                draw_text(renderer, font_mid, f"Recording card {device['card']}: {device['name']}", 16, 80, TEXT)
                draw_text(renderer, font_big, ">>> SPEAK NOW <<<", 16, 120, GREEN)
                left = max(0.0, RECORD_SECONDS - recorder.elapsed())
                draw_text(renderer, font_big, f"{left:0.1f}s", 16, 170, TEXT)
                frac = min(1.0, recorder.elapsed() / RECORD_SECONDS)
                box(renderer, 16, 220, W - 32, 26, BORDER)
                fill(renderer, 18, 222, int((W - 36) * frac), 22, GREEN_DIM)
                if mode == "all":
                    draw_text(renderer, font_small,
                              f"device {selection + 1} of {len(devices)}   (B: cancel)", 16, 258, MUTED)
                else:
                    draw_text(renderer, font_small, "(B: cancel)", 16, 258, MUTED)

            elif state == "result" and result:
                device, stats = result["device"], result["stats"]
                label, color = verdict(stats, device)
                draw_text(renderer, font_mid, f"card {device['card']}: {device['name']}", 16, 74, TEXT)
                draw_text(renderer, font_big, label, 16, 108, color)
                if stats:
                    draw_text(renderer, font_mid, f"peak      {stats['peak']}", 16, 170, TEXT)
                    draw_text(renderer, font_mid, f"rms       {stats['rms']:0.0f}", 16, 202, TEXT)
                    draw_text(renderer, font_mid, f"crossings {stats['cross']:0.0f} /s", 16, 234, TEXT)
                    draw_text(renderer, font_mid, f"duration  {stats['dur']:0.1f} s", 16, 266, TEXT)
                draw_text(renderer, font_small, "playing back...   A: replay   B: back", 16, H - 26, MUTED)

            elif state == "summary":
                draw_text(renderer, font_mid, "TEST ALL - crossing rate (higher = speech-like)", 16, 70, GREEN)
                y = 108
                for i, entry in enumerate(per_device):
                    device, stats = entry["device"], entry["stats"]
                    label, color = verdict(stats, device)
                    cross = stats["cross"] if stats else 0
                    peak = stats["peak"] if stats else 0
                    marker = ">" if i == selection else " "
                    text = f"{marker} card {device['card']:<2} {device['name'][:12]:<12} peak {peak:<6} {cross:>6.0f}/s  {label}"
                    draw_text(renderer, font_small, text, 12, y, GREEN if i == selection else color)
                    y += 26
                draw_text(renderer, font_small, "UP/DOWN choose   A: replay   B: back", 16, H - 26, MUTED)

            SDL.SDL_RenderPresent(renderer)
            SDL.SDL_Delay(33)
    finally:
        recorder.stop()
        if player:
            player.terminate()
        if joystick:
            SDL.SDL_JoystickClose(joystick)
        for font in (font_big, font_mid, font_small):
            if font:
                TTF.TTF_CloseFont(font)
        SDL.SDL_DestroyRenderer(renderer)
        SDL.SDL_DestroyWindow(window)
        TTF.TTF_Quit()
        SDL.SDL_Quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
