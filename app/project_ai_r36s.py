#!/usr/bin/env python3
"""Controller-first offline LLM client for the R36S."""

import array
import ast
import ctypes
from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import struct
import subprocess
import sys
import threading
import time
import wave
import zipfile
from urllib.error import URLError
from urllib.request import Request, urlopen


W, H = 640, 480
ROOT = Path(__file__).resolve().parent / "project_ai"
RUNTIME = ROOT / "runtime"
MODELS = ROOT / "models"
CONFIG = ROOT / "config.json"
HISTORY = ROOT / "history.json"
CHATS = ROOT / "chats.json"
WORDS_FILE = ROOT / "words.txt"
WHISPER_BIN = ROOT / "whisper" / "whisper-cli"
WHISPER_MODELS = MODELS / "whisper"
WHISPER_THREADS = 4
MIC_WAV = Path("/tmp/project_227_mic.wav")
MIC_LOG = Path("/tmp/project_227_mic.log")
MIC_MAX_SECONDS = 30.0
TTS_WAV = Path("/tmp/project_227_tts.wav")
TTS_INTERNAL_DEVICE = "default"
TTS_RATE = 1.0
MAX_CHATS = 60
SERVER_URL = "http://127.0.0.1:39227"

MODEL_PROFILES = {
    "smart": {
        "file": "Qwen3-0.6B-Q4_K_M.gguf",
        "label": "QWEN 3 // 0.6B",
        "note": "balanced, general chat",
        "fallback": False,
    },
    "falcon": {
        "file": "Falcon-H1-0.5B-Instruct-Q4_K_M.gguf",
        "label": "FALCON H1 // 0.5B",
        "note": "hybrid transformer + mamba",
        "fallback": False,
    },
    "fast": {
        "file": "LFM2.5-350M-QAD-Q4_0.gguf",
        "label": "LFM 2.5 // 350M",
        "note": "smallest, quickest replies",
        "fallback": True,
    },
}

SYSTEM_PROMPT = (
    "You are 227, a private offline pocket assistant. Be concise, useful, and honest. "
    "Check constraints and arithmetic before answering. If uncertain, say so rather than inventing facts. "
    "Treat equal stated masses as equal. For deadlines, subtract travel and early-arrival time. "
    "Keep normal replies under 100 words unless the user asks for detail."
)

KEYBOARD = [
    list("1234567890-+*/=:"),
    list("qwertyuiop"),
    list("asdfghjkl"),
    list("zxcvbnm.,!?"),
    ["MIC", "SPACE", "DEL", "SEND", "CLOSE"],
]
KEY_TOP = 258
KEY_STEP = 38
KEY_HEIGHT = 30
KEY_GAP = 4
KEY_MAX_W = 200

HEADER_H = 56
FRAME_GAP = 8
FRAME_TOP = HEADER_H + FRAME_GAP
INPUT_TOP = 416
COMPOSE_TOP = 158
CONTENT_X = 12
CONTENT_W = W - 24
CHAT_PAD = 14
MSG_LEFT = CONTENT_X + CHAT_PAD
MSG_RIGHT = CONTENT_X + CONTENT_W - CHAT_PAD
LINE_H = 18
DRAFT_TOP = 164
DRAFT_H = 64
SUGGEST_TOP = 232
SUGGEST_H = 22
SUGGEST_GAP = 8
SUGGEST_W = (CONTENT_W - 2 * SUGGEST_GAP) // 3
MSG_GAP = 8
SPEAK_GUTTER = 16
LOGO_W, LOGO_H = 108, 36
LOGO_NAME = "logo.rgba"
BLOCK_CHAR = "\u2588"

HISTORY_ROW_H = 36
HISTORY_LIST_LEFT = CONTENT_X + CHAT_PAD
HISTORY_LIST_W = CONTENT_W - CHAT_PAD * 2
HISTORY_LIST_TOP = FRAME_TOP + 48
HISTORY_LIST_BOTTOM = H - 44
HISTORY_VISIBLE = max(1, (HISTORY_LIST_BOTTOM - HISTORY_LIST_TOP) // HISTORY_ROW_H)

BG = (4, 8, 6, 255)
PANEL = (7, 15, 11, 255)
PANEL_2 = (9, 20, 15, 255)
PANEL_3 = (12, 27, 20, 255)
BORDER = (18, 66, 40, 255)
EDGE = (34, 124, 74, 255)
TEXT = (196, 240, 215, 255)
MUTED = (86, 146, 116, 255)
GREEN = (51, 255, 119, 255)
GREEN_DIM = (28, 176, 96, 255)
ERROR = (255, 82, 96, 255)
SHADOW = (0, 0, 0, 255)


def load_words():
    try:
        with open(WORDS_FILE, "r", encoding="utf-8", errors="ignore") as handle:
            raw = handle.read().split()
    except OSError:
        return []
    words = []
    seen = set()
    for word in raw:
        word = word.strip().lower()
        if 2 <= len(word) <= 16 and word.isascii() and word.isalpha() and word not in seen:
            seen.add(word)
            words.append(word)
    return words


WORD_LIST = load_words()
SUGGEST_CACHE = {}


def current_prefix(draft):
    return draft.rsplit(" ", 1)[-1].lower()


def word_suggestions(draft, limit=3):
    prefix = current_prefix(draft)
    if not prefix:
        return []
    cached = SUGGEST_CACHE.get(prefix)
    if cached is not None:
        return cached
    found = []
    for word in WORD_LIST:
        if len(word) > len(prefix) and word.startswith(prefix):
            found.append(word)
            if len(found) == limit:
                break
    SUGGEST_CACHE[prefix] = found
    return found


def suggestion_geometry(count):
    return [(CONTENT_X + index * (SUGGEST_W + SUGGEST_GAP), SUGGEST_W)
            for index in range(count)]


def resolve_whisper_model():
    preferred = WHISPER_MODELS / "ggml-tiny.en-q5_1.bin"
    if preferred.exists():
        return preferred
    try:
        for candidate in sorted(WHISPER_MODELS.glob("ggml-*.bin")):
            return candidate
    except OSError:
        pass
    return preferred


def mic_log(message):
    try:
        with MIC_LOG.open("a") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


def capture_node_exists(card):
    return Path(f"/dev/snd/pcmC{card}D0c").exists()


def wav_peak(path):
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getsampwidth() != 2:
                return None
            data = handle.readframes(min(handle.getnframes(), 16000 * 60))
    except (OSError, wave.Error, MemoryError):
        return None
    if not data:
        return 0
    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    return max((abs(sample) for sample in samples), default=0)


def repair_wav_header(path):
    """Fix the RIFF/data sizes arecord leaves as a 2 GB placeholder when interrupted."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < 44:
        return
    try:
        with path.open("r+b") as handle:
            header = handle.read(64)
            data_index = header.find(b"data")
            if data_index < 0:
                return
            handle.seek(4)
            handle.write(struct.pack("<I", size - 8))
            handle.seek(data_index + 4)
            handle.write(struct.pack("<I", size - (data_index + 8)))
    except OSError:
        pass


def detect_capture_devices():
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
        card_id = match.group(2)
        label = match.group(3) or card_id
        haystack = f"{card_id} {label} {match.group(5)}".lower()
        devices.append({
            "arg": f"plughw:CARD={card_id},DEV={index}",
            "card": card,
            "index": index,
            "id": card_id,
            "label": label,
            "node": capture_node_exists(card),
            "usb": "usb" in haystack,
            "rk817": "rk817" in haystack or "rockchip" in haystack,
        })
    return devices


def resolve_capture_device(devices, wanted):
    """Pick the device the user saved, matched by stable card id (survives renumbering)."""
    if not wanted or wanted == "auto":
        return None
    usable = [device for device in devices if device["id"] == wanted and device["node"]]
    if usable:
        return usable[0]
    named = [device for device in devices if device["id"] == wanted]
    return named[0] if named else None


def choose_capture_device(devices):
    """Return the best available capture device when no explicit preference is set."""
    usable = [device for device in devices if device["node"]]
    for device in usable:
        if device["usb"]:
            return device
    if usable:
        return usable[0]
    for device in devices:
        if device["usb"]:
            return device
    return devices[0] if devices else None


def enable_capture_input(device):
    if not device:
        return
    card = str(device["card"])
    if device.get("rk817"):
        settings = [("Capture MIC Path", "Main Mic"), ("Record", "100%")]
    else:
        settings = [
            ("Mic Capture Switch", "on"),
            ("Mic Capture Volume", "75%"),
            ("Capture Switch", "on"),
            ("Capture Volume", "75%"),
        ]
    for control, value in settings:
        try:
            subprocess.run(["amixer", "-c", card, "cset", f"name={control}", value],
                           capture_output=True, text=True, timeout=6)
        except (OSError, subprocess.SubprocessError):
            pass


def clean_transcript(raw):
    text = (raw or "").replace("\r", " ")
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tts_output_devices():
    devices = [TTS_INTERNAL_DEVICE]
    for device in detect_capture_devices():
        if device["usb"] and device["arg"] not in devices:
            devices.append(device["arg"])
    return devices


def synthesize_speech(text, output):
    """Generate audio and word timestamps in an isolated synthesis process."""
    class EventID(ctypes.Union):
        _fields_ = [("number", ctypes.c_int), ("name", ctypes.c_char_p),
                    ("string", ctypes.c_char * 8)]

    class SpeechEvent(ctypes.Structure):
        _fields_ = [("type", ctypes.c_int), ("identifier", ctypes.c_uint),
                    ("text_position", ctypes.c_int), ("length", ctypes.c_int),
                    ("audio_position", ctypes.c_int), ("sample", ctypes.c_int),
                    ("user_data", ctypes.c_void_p), ("id", EventID)]

    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short),
                                    ctypes.c_int, ctypes.POINTER(SpeechEvent))
    lib = ctypes.CDLL("libespeak-ng.so.1")
    lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    lib.espeak_SetParameter.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.espeak_SetSynthCallback.argtypes = [callback_type]
    lib.espeak_Synth.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint,
                                ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p]
    rate = lib.espeak_Initialize(2, 0, None, 0x8000)
    if rate <= 0:
        raise RuntimeError("Cannot initialize speech engine")
    timings = []
    errors = []
    try:
        if lib.espeak_SetVoiceByName(b"en") != 0:
            raise RuntimeError("English speech voice unavailable")
        lib.espeak_SetParameter(1, round(175 * read_config()["speech_rate"]), 0)
        with wave.open(str(output), "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))

            @callback_type
            def receive(samples, count, events):
                try:
                    if samples and count:
                        wav.writeframesraw(ctypes.string_at(samples, count * 2))
                    i = 0
                    while events and events[i].type:
                        event = events[i]
                        if event.type == 1 and event.length > 0:
                            start = max(0, event.text_position - 1)
                            end = min(len(text), start + event.length)
                            if start < end:
                                timings.append((event.audio_position / 1000.0, start, end))
                        i += 1
                    return 0
                except Exception as exc:
                    errors.append(exc)
                    return 1

            lib.espeak_SetSynthCallback(receive)
            encoded = text.encode("utf-8")
            if lib.espeak_Synth(encoded, len(encoded) + 1, 0, 1, 0, 0x1001, None, None) != 0:
                raise RuntimeError("Speech synthesis failed")
            if errors:
                raise errors[0]
    finally:
        lib.espeak_Terminate()
    words = list(re.finditer(r"\S+", text))
    result = []
    for moment, start, end in timings:
        word = next((word for word in words if word.start() <= start < word.end()), None)
        if word is not None:
            start, end = word.span()
        if not result or result[-1][1:] != (start, end):
            result.append((moment, start, end))
    return result


def draw_speak_glyph(renderer, x, y, color):
    for row in range(9):
        width = max(1, 6 - abs(row - 4))
        fill(renderer, x, int(y) + row, width, 1, color)
        fill(renderer, x + width - 1, int(y) + row, 1, 1, color)


def message_blocks(font, messages, scroll):
    _, _, content_top, _ = viewport_metrics(False)
    y = content_top - scroll
    blocks = []
    for index, item in enumerate(messages):
        _, _, height = message_layout(font, item)
        blocks.append((index, y, height, item))
        y += height + MSG_GAP
    return blocks



c_int = ctypes.c_int
c_uint8 = ctypes.c_uint8
c_uint32 = ctypes.c_uint32
c_void_p = ctypes.c_void_p
c_char_p = ctypes.c_char_p
byref = ctypes.byref

SDL = ctypes.CDLL("libSDL2-2.0.so.0")
TTF = ctypes.CDLL("libSDL2_ttf-2.0.so.0")


class Rect(ctypes.Structure):
    _fields_ = [("x", c_int), ("y", c_int), ("w", c_int), ("h", c_int)]


class Color(ctypes.Structure):
    _fields_ = [("r", c_uint8), ("g", c_uint8), ("b", c_uint8), ("a", c_uint8)]


SDL.SDL_Init.argtypes = [c_uint32]
SDL.SDL_Init.restype = c_int
SDL.SDL_GetError.restype = c_char_p
SDL.SDL_NumJoysticks.restype = c_int
SDL.SDL_JoystickOpen.argtypes = [c_int]
SDL.SDL_JoystickOpen.restype = c_void_p
SDL.SDL_JoystickClose.argtypes = [c_void_p]
SDL.SDL_JoystickGetAxis.argtypes = [c_void_p, c_int]
SDL.SDL_JoystickGetAxis.restype = ctypes.c_int16
SDL.SDL_GameControllerOpen.argtypes = [c_int]
SDL.SDL_GameControllerOpen.restype = c_void_p
SDL.SDL_GameControllerClose.argtypes = [c_void_p]
SDL.SDL_GameControllerGetAxis.argtypes = [c_void_p, c_int]
SDL.SDL_GameControllerGetAxis.restype = ctypes.c_int16
SDL.SDL_CreateWindow.argtypes = [c_char_p, c_int, c_int, c_int, c_int, c_uint32]
SDL.SDL_CreateWindow.restype = c_void_p
SDL.SDL_CreateRenderer.argtypes = [c_void_p, c_int, c_uint32]
SDL.SDL_CreateRenderer.restype = c_void_p
SDL.SDL_SetRenderDrawColor.argtypes = [c_void_p, c_uint8, c_uint8, c_uint8, c_uint8]
SDL.SDL_RenderFillRect.argtypes = [c_void_p, ctypes.POINTER(Rect)]
SDL.SDL_RenderDrawLine.argtypes = [c_void_p, c_int, c_int, c_int, c_int]
SDL.SDL_RenderDrawRect.argtypes = [c_void_p, ctypes.POINTER(Rect)]
SDL.SDL_RenderPresent.argtypes = [c_void_p]
SDL.SDL_RenderClear.argtypes = [c_void_p]
SDL.SDL_PollEvent.argtypes = [c_void_p]
SDL.SDL_PollEvent.restype = c_int
SDL.SDL_Delay.argtypes = [c_uint32]
SDL.SDL_CreateTextureFromSurface.argtypes = [c_void_p, c_void_p]
SDL.SDL_CreateTextureFromSurface.restype = c_void_p
SDL.SDL_CreateRGBSurfaceFrom.argtypes = [c_void_p, c_int, c_int, c_int, c_int,
                                         c_uint32, c_uint32, c_uint32, c_uint32]
SDL.SDL_CreateRGBSurfaceFrom.restype = c_void_p
SDL.SDL_SetTextureBlendMode.argtypes = [c_void_p, c_int]
SDL.SDL_SetTextureBlendMode.restype = c_int
SDL.SDL_SetTextureAlphaMod.argtypes = [c_void_p, c_uint8]
SDL.SDL_QueryTexture.argtypes = [c_void_p, c_void_p, c_void_p,
                                 ctypes.POINTER(c_int), ctypes.POINTER(c_int)]
SDL.SDL_RenderCopy.argtypes = [c_void_p, c_void_p, c_void_p, ctypes.POINTER(Rect)]
SDL.SDL_RenderSetClipRect.argtypes = [c_void_p, ctypes.POINTER(Rect)]
SDL.SDL_RenderSetClipRect.restype = c_int
SDL.SDL_WarpMouseInWindow.argtypes = [c_void_p, c_int, c_int]
SDL.SDL_ShowCursor.argtypes = [c_int]
SDL.SDL_ShowCursor.restype = c_int
SDL.SDL_DestroyTexture.argtypes = [c_void_p]
SDL.SDL_FreeSurface.argtypes = [c_void_p]
SDL.SDL_RWFromConstMem.argtypes = [c_char_p, c_int]
SDL.SDL_RWFromConstMem.restype = c_void_p
SDL.SDL_LoadBMP_RW.argtypes = [c_void_p, c_int]
SDL.SDL_LoadBMP_RW.restype = c_void_p
SDL.SDL_CreateRGBSurfaceWithFormat.argtypes = [c_uint32, c_int, c_int, c_int, c_uint32]
SDL.SDL_CreateRGBSurfaceWithFormat.restype = c_void_p
SDL.SDL_FillRect.argtypes = [c_void_p, ctypes.POINTER(Rect), c_uint32]
SDL.SDL_SetSurfaceBlendMode.argtypes = [c_void_p, c_int]
SDL.SDL_SetSurfaceAlphaMod.argtypes = [c_void_p, c_uint8]
SDL.SDL_UpperBlit.argtypes = [c_void_p, ctypes.POINTER(Rect), c_void_p, ctypes.POINTER(Rect)]
SDL.SDL_DestroyRenderer.argtypes = [c_void_p]
SDL.SDL_DestroyWindow.argtypes = [c_void_p]
SDL.SDL_Delay.argtypes = [c_uint32]
SDL.SDL_Quit.argtypes = []

TTF.TTF_Init.restype = c_int
TTF.TTF_OpenFont.argtypes = [c_char_p, c_int]
TTF.TTF_OpenFont.restype = c_void_p
TTF.TTF_SizeUTF8.argtypes = [c_void_p, c_char_p, ctypes.POINTER(c_int), ctypes.POINTER(c_int)]
TTF.TTF_RenderUTF8_Blended.argtypes = [c_void_p, c_char_p, Color]
TTF.TTF_RenderUTF8_Blended.restype = c_void_p
TTF.TTF_CloseFont.argtypes = [c_void_p]
TTF.TTF_Quit.argtypes = []


def fill(renderer, x, y, width, height, color):
    if width <= 0 or height <= 0:
        return
    SDL.SDL_SetRenderDrawColor(renderer, *color)
    rect = Rect(int(x), int(y), int(width), int(height))
    SDL.SDL_RenderFillRect(renderer, byref(rect))


def fill_panel(renderer, x, y, width, height, color, background=None):
    if width <= 0 or height <= 0:
        return
    if background and background.draw(x, y, width, height, color):
        return
    fill(renderer, x, y, width, height, color)


def line(renderer, x1, y1, x2, y2, color):
    SDL.SDL_SetRenderDrawColor(renderer, *color)
    SDL.SDL_RenderDrawLine(renderer, int(x1), int(y1), int(x2), int(y2))


def box(renderer, x, y, width, height, color):
    if width <= 0 or height <= 0:
        return
    SDL.SDL_SetRenderDrawColor(renderer, *color)
    rect = Rect(int(x), int(y), int(width), int(height))
    SDL.SDL_RenderDrawRect(renderer, byref(rect))


def bracket(renderer, x, y, dx, dy, length, color):
    line(renderer, x, y, x + dx * length, y, color)
    line(renderer, x, y, x, y + dy * length, color)


def sanitize(value):
    value = str(value).replace("\r", "")
    return "".join(char if char == "\n" or ord(char) >= 32 else " " for char in value)


def measure(font, value):
    width, height = c_int(), c_int()
    encoded = str(value).encode("utf-8", "replace")
    if TTF.TTF_SizeUTF8(font, encoded, byref(width), byref(height)) != 0:
        return 0, 0
    return width.value, height.value


def wrap_pixels(font, value, max_width):
    lines = []
    for paragraph in sanitize(value).split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else current + " " + word
            if measure(font, candidate)[0] <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if measure(font, word)[0] <= max_width:
                current = word
                continue
            piece = ""
            for char in word:
                if piece and measure(font, piece + char)[0] > max_width:
                    lines.append(piece)
                    piece = char
                else:
                    piece += char
            current = piece
        lines.append(current)
    return lines or [""]


def wrap_pixels_indexed(font, value, max_width):
    text = sanitize(value)
    lines = []
    offset = 0
    for paragraph in text.split("\n"):
        start = offset
        offset = start + len(paragraph) + 1
        if not paragraph:
            lines.append(("", start))
            continue
        current = ""
        current_start = start
        pos = start
        for word in paragraph.split(" "):
            candidate = word if not current else current + " " + word
            if measure(font, candidate)[0] <= max_width:
                if not current:
                    current_start = pos
                current = candidate
                pos += len(word) + 1
                continue
            if current:
                lines.append((current, current_start))
                current = ""
            if measure(font, word)[0] <= max_width:
                current = word
                current_start = pos
                pos += len(word) + 1
                continue
            piece = ""
            piece_start = pos
            for char in word:
                if piece and measure(font, piece + char)[0] > max_width:
                    lines.append((piece, piece_start))
                    piece = ""
                    piece_start = pos
                piece += char
                pos += 1
            pos += 1
            current = piece
            current_start = piece_start
        lines.append((current, current_start))
    return lines or [("", 0)]


class TextCache:
    def __init__(self, renderer, limit=420):
        self.renderer = renderer
        self.limit = limit
        self.items = OrderedDict()

    def texture(self, font, value, color):
        key = (font, str(value), color)
        cached = self.items.get(key)
        if cached is not None:
            self.items.move_to_end(key)
            return cached
        surface = TTF.TTF_RenderUTF8_Blended(font, str(value).encode("utf-8", "replace"), Color(*color))
        if not surface:
            return None, 0, 0
        texture = SDL.SDL_CreateTextureFromSurface(self.renderer, surface)
        width, height = c_int(), c_int()
        if texture:
            SDL.SDL_QueryTexture(texture, None, None, byref(width), byref(height))
        SDL.SDL_FreeSurface(surface)
        cached = (texture, width.value, height.value)
        self.items[key] = cached
        while len(self.items) > self.limit:
            _, (old_texture, _, _) = self.items.popitem(last=False)
            if old_texture:
                SDL.SDL_DestroyTexture(old_texture)
        return cached

    def draw(self, font, value, x, y, color, max_width=None):
        texture, width, height = self.texture(font, value, color)
        if texture:
            if max_width and width > max_width:
                width = max_width
            target = Rect(int(x), int(y), int(width), int(height))
            SDL.SDL_RenderCopy(self.renderer, texture, None, byref(target))
        return width, height

    def close(self):
        for texture, _, _ in self.items.values():
            if texture:
                SDL.SDL_DestroyTexture(texture)
        self.items.clear()


class BackgroundCache:
    """Blend each panel color once, then copy opaque, renderer-native pixels."""
    def __init__(self, renderer, raw, opacity):
        self.renderer = renderer
        self.opacity = opacity
        self.textures = {}
        self.buffer = (c_uint8 * len(raw)).from_buffer_copy(raw)
        self.surface = SDL.SDL_CreateRGBSurfaceFrom(
            self.buffer, W, H, 32, W * 4, 0xFF, 0xFF00, 0xFF0000, 0xFF000000)
        if self.surface:
            SDL.SDL_SetSurfaceBlendMode(self.surface, 1)
            SDL.SDL_SetSurfaceAlphaMod(self.surface, opacity)
            for color in (BG, PANEL, PANEL_2, PANEL_3, (2, 5, 3, 255),
                          (11, 42, 26, 255), (23, 150, 82, 255)):
                self.texture(color)

    def texture(self, color):
        if color in self.textures:
            return self.textures[color]
        surface = SDL.SDL_CreateRGBSurfaceWithFormat(0, W, H, 32, 0x16362004)
        if not surface:
            return None
        r, g, b, a = color
        SDL.SDL_FillRect(surface, None, (a << 24) | (r << 16) | (g << 8) | b)
        SDL.SDL_UpperBlit(self.surface, None, surface, None)
        texture = SDL.SDL_CreateTextureFromSurface(self.renderer, surface)
        SDL.SDL_FreeSurface(surface)
        if texture:
            SDL.SDL_SetTextureBlendMode(texture, 0)
            self.textures[color] = texture
        return texture

    def draw(self, x, y, width, height, color):
        texture = self.texture(color)
        if not texture:
            return False
        rect = Rect(int(x), int(y), int(width), int(height))
        SDL.SDL_RenderCopy(self.renderer, texture, byref(rect), byref(rect))
        return True

    def close(self):
        for texture in self.textures.values():
            SDL.SDL_DestroyTexture(texture)
        self.textures.clear()
        if self.surface:
            SDL.SDL_FreeSurface(self.surface)
            self.surface = None
        self.buffer = None


class IntroPlayer:
    """Silent, preloaded bitmap animation: no decoder process or playback I/O."""
    def __init__(self, renderer, root, enabled=True):
        self.renderer = renderer
        self.texture = None
        self.textures = []
        self.active = False
        self.started = None
        self.frame_index = 0
        pack = root / "intro-frames.zip"
        if not enabled or not pack.is_file():
            return
        try:
            with zipfile.ZipFile(pack) as archive:
                if archive.getinfo("manifest.json").file_size > 65536:
                    raise ValueError("Oversized intro manifest")
                manifest = json.loads(archive.read("manifest.json"))
                if not isinstance(manifest, dict):
                    raise ValueError("Invalid intro manifest")
                names = manifest.get("frames")
                self.duration = float(manifest["duration"])
                self.fade_seconds = float(manifest.get("fade_seconds", 0.8))
                if (manifest.get("version") != 1 or manifest.get("width") != W
                        or manifest.get("height") != H or not isinstance(names, list)
                        or not 1 <= len(names) <= 128
                        or not math.isfinite(self.duration) or not 0 < self.duration <= 30
                        or not math.isfinite(self.fade_seconds)
                        or not 0 <= self.fade_seconds <= self.duration):
                    raise ValueError("Invalid intro dimensions or timing")
                for name in names:
                    info = archive.getinfo(name)
                    if not 54 <= info.file_size <= W * H * 4 + 4096:
                        raise ValueError("Invalid intro frame size")
                    data = archive.read(info)
                    width, height = struct.unpack_from("<ii", data, 18)
                    if data[:2] != b"BM" or width != W or abs(height) != H:
                        raise ValueError("Intro frame dimensions do not match the screen")
                    stream = SDL.SDL_RWFromConstMem(data, len(data))
                    if not stream:
                        raise RuntimeError("Cannot open intro frame")
                    surface = SDL.SDL_LoadBMP_RW(stream, 1)
                    if not surface:
                        raise RuntimeError("Cannot decode intro bitmap")
                    texture = SDL.SDL_CreateTextureFromSurface(renderer, surface)
                    SDL.SDL_FreeSurface(surface)
                    if not texture:
                        raise RuntimeError("Cannot create intro frame texture")
                    SDL.SDL_SetTextureBlendMode(texture, 0)
                    self.textures.append(texture)
            self.active = True
        except (OSError, ValueError, OverflowError, KeyError, TypeError, struct.error,
                zipfile.BadZipFile, RuntimeError) as exc:
            print(f"Intro unavailable: {exc}", file=sys.stderr)
            self.close()

    def position(self):
        if self.started is None:
            return 0.0
        return time.monotonic() - self.started

    @property
    def covers_ui(self):
        return self.active and self.position() < self.duration - self.fade_seconds - 0.05

    def draw(self):
        if not self.active:
            return
        if self.started is None:
            self.started = time.monotonic()
        elapsed = self.position()
        if elapsed >= self.duration:
            self.close()
            return
        self.frame_index = min(len(self.textures) - 1, int(elapsed * len(self.textures) / self.duration))
        self.texture = self.textures[self.frame_index]
        alpha = min(1.0, (self.duration - elapsed) / self.fade_seconds) if self.fade_seconds else 1.0
        SDL.SDL_SetTextureBlendMode(self.texture, 0 if alpha == 1.0 else 1)
        SDL.SDL_SetTextureAlphaMod(self.texture, round(255 * alpha))
        SDL.SDL_RenderCopy(self.renderer, self.texture, None, None)

    def close(self):
        self.active = False
        for texture in self.textures:
            SDL.SDL_DestroyTexture(texture)
        self.textures.clear()
        self.texture = None


def read_config():
    defaults = {"model": "fast", "context": 768, "max_tokens": 160,
                "system_prompt": SYSTEM_PROMPT, "temperature": 0.25,
                "highlight": True, "speech_rate": TTS_RATE, "background": 20, "intro": True}
    try:
        loaded = json.loads(CONFIG.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            defaults.update(loaded)
    except (OSError, ValueError):
        pass
    try:
        defaults["context"] = max(512, min(1024, int(defaults["context"])))
    except (TypeError, ValueError, OverflowError):
        defaults["context"] = 768
    try:
        defaults["max_tokens"] = max(48, min(256, int(defaults["max_tokens"])))
    except (TypeError, ValueError, OverflowError):
        defaults["max_tokens"] = 160
    if not isinstance(defaults["model"], str) or defaults["model"] not in MODEL_PROFILES:
        defaults["model"] = "fast"
    prompt = defaults["system_prompt"]
    defaults["system_prompt"] = prompt.strip()[:2000] if isinstance(prompt, str) and prompt.strip() else SYSTEM_PROMPT
    for key, fallback, low, high in (("temperature", 0.25, 0.0, 1.0),
                                      ("speech_rate", TTS_RATE, 0.75, 1.5)):
        try:
            value = float(defaults[key])
            defaults[key] = max(low, min(high, value)) if math.isfinite(value) else fallback
        except (TypeError, ValueError, OverflowError):
            defaults[key] = fallback
    for key in ("highlight", "intro"):
        if not isinstance(defaults[key], bool):
            defaults[key] = True
    try:
        defaults["background"] = max(0, min(40, int(defaults["background"])))
    except (TypeError, ValueError, OverflowError):
        defaults["background"] = 20
    return defaults


def save_config(config):
    try:
        temporary = CONFIG.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        temporary.replace(CONFIG)
        return True
    except (OSError, TypeError, ValueError):
        return False


def available_model_profiles():
    result = []
    for name, profile in MODEL_PROFILES.items():
        if (MODELS / profile["file"]).exists():
            result.append((name, profile))
    return result


def load_history():
    try:
        payload = json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    result = []
    for item in payload[-24:]:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = sanitize(item.get("content", "")).strip()
        if content:
            result.append({"role": item["role"], "content": content, "pending": False})
    return result


def save_history(messages):
    data = [{"role": item["role"], "content": item["content"]}
            for item in messages[-24:]
            if item.get("content") and not item.get("pending") and not item.get("transient")]
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        temporary = HISTORY.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
        temporary.replace(HISTORY)
    except OSError:
        pass


def chat_title(messages):
    for item in messages:
        if item.get("role") == "user":
            text = " ".join(str(item.get("content", "")).split())
            if text:
                return text[:48]
    return "Untitled chat"


def relative_time(stamp):
    try:
        stamp = float(stamp)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    delta = time.time() - stamp
    if delta < 90:
        return "just now"
    if delta < 3600:
        return "%dm ago" % int(delta // 60)
    if delta < 86400:
        return "%dh ago" % int(delta // 3600)
    if delta < 604800:
        return "%dd ago" % int(delta // 86400)
    try:
        return time.strftime("%b %d", time.localtime(stamp))
    except (OSError, OverflowError, ValueError):
        return ""


def load_chats():
    try:
        payload = json.loads(CHATS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    result = []
    for entry in payload:
        if len(result) >= MAX_CHATS:
            break
        if not isinstance(entry, dict):
            continue
        raw = entry.get("messages")
        if not isinstance(raw, list):
            continue
        cleaned = []
        for item in raw:
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                continue
            content = sanitize(item.get("content", "")).strip()
            if content:
                cleaned.append({"role": item["role"], "content": content})
        if not any(item["role"] == "user" for item in cleaned):
            continue
        try:
            updated = float(entry.get("updated") or 0.0)
        except (TypeError, ValueError):
            updated = 0.0
        title = sanitize(str(entry.get("title", ""))).strip()[:48]
        result.append({
            "title": title or chat_title(cleaned),
            "updated": updated,
            "messages": cleaned,
        })
    return result


def save_chats(chats):
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        temporary = CHATS.with_suffix(".tmp")
        temporary.write_text(json.dumps(chats[:MAX_CHATS], ensure_ascii=True), encoding="utf-8")
        temporary.replace(CHATS)
    except (OSError, ValueError):
        pass


def archive_entry(messages):
    saved = [{"role": item["role"], "content": item["content"]}
             for item in messages
             if item.get("role") in ("user", "assistant")
             and item.get("content") and not item.get("pending") and not item.get("transient")]
    if not any(item["role"] == "user" for item in saved):
        return None
    return {"title": chat_title(saved), "updated": time.time(), "messages": saved}
    operators = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a ** b,
        ast.USub: lambda a: -a,
        ast.UAdd: lambda a: a,
    }

    def checked(value):
        if isinstance(value, int):
            if value.bit_length() > 333:
                raise ValueError("result out of range")
        elif not math.isfinite(value) or abs(value) > 1e100:
            raise ValueError("result out of range")
        return value

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)):
            return checked(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ValueError("exponent too large")
            return checked(operators[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in operators:
            return checked(operators[type(node.op)](evaluate(node.operand)))
        raise ValueError("unsupported expression")

    if len(expression) > 100:
        raise ValueError("expression too long")
    result = evaluate(ast.parse(expression, mode="eval"))
    return f"{result:g}" if isinstance(result, float) else str(result)


def instant_answer(content):
    stripped = content.strip()
    if stripped.startswith("="):
        try:
            return "Result: " + safe_calculate(stripped[1:].strip())
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as error:
            return "Calculator error: " + str(error)

    lower = stripped.lower()
    if any(phrase in lower for phrase in ("heavier", "weighs more", "weigh more")):
        factors = {
            "g": 1, "gram": 1, "grams": 1,
            "kg": 1000, "kilogram": 1000, "kilograms": 1000,
            "lb": 453.59237, "lbs": 453.59237,
            "pound": 453.59237, "pounds": 453.59237,
            "oz": 28.349523125, "ounce": 28.349523125, "ounces": 28.349523125,
        }
        quantities = re.findall(
            r"(?<![\w.])(\d+(?:\.\d+)?)\s*"
            r"(kilograms?|kg|grams?|g|pounds?|lbs?|ounces?|oz)\b",
            lower,
        )
        if len(quantities) == 2:
            masses = [float(value) * factors[unit] for value, unit in quantities]
            if math.isclose(masses[0], masses[1], rel_tol=1e-9, abs_tol=1e-9):
                return ("They weigh the same because the stated masses are equal. "
                        "The material changes density and volume, not mass.")

    wants_departure = ("leave" in lower or "depart" in lower)
    if wants_departure and ("takes" in lower or "early" in lower):
        clocks = list(re.finditer(
            r"\b(\d{1,2}):([0-5]\d)(?:\s*([ap])\.?m\.?)?", lower))
        if len(clocks) == 1:
            clock = clocks[0]
            hour, minute = int(clock.group(1)), int(clock.group(2))
            meridiem = clock.group(3)
            valid_hour = 1 <= hour <= 12 if meridiem else 0 <= hour <= 23
            if valid_hour:
                if meridiem:
                    hour = hour % 12 + (12 if meridiem == "p" else 0)
                duration = 0.0
                for value in re.findall(
                        r"(\d+(?:\.\d+)?)\s*-?\s*(?:hours?|hrs?|hr)\b", lower):
                    duration += float(value) * 60
                for value in re.findall(
                        r"(\d+(?:\.\d+)?)\s*-?\s*(?:minutes?|mins?|min)\b", lower):
                    duration += float(value)
                total = int(round(duration))
                if 0 < total < 24 * 60:
                    departure = (hour * 60 + minute - total) % (24 * 60)
                    leave_hour, leave_minute = divmod(departure, 60)
                    if meridiem:
                        suffix = "AM" if leave_hour < 12 else "PM"
                        display_hour = leave_hour % 12 or 12
                        display = f"{display_hour}:{leave_minute:02d} {suffix}"
                    else:
                        display = f"{leave_hour:02d}:{leave_minute:02d}"
                    return (f"Leave at {display}. That is {total} minutes before the event, "
                            "covering the stated travel and early-arrival time.")
    return None


class LocalEngine:
    def __init__(self, events):
        self.events = events
        self.config = read_config()
        self.process = None
        self.log = None
        self.ready = False
        self.progress = 0.0
        self.profile = None
        self.worker = None
        self.starter = None
        self.cancel_event = threading.Event()
        self.stop_event = threading.Event()
        self.request_id = 0
        self.lock = threading.Lock()

    def start(self):
        self.stop_event.clear()
        self.starter = threading.Thread(target=self._start_worker, daemon=True)
        self.starter.start()

    def _candidate_names(self):
        selected = self.config.get("model", "fast")
        names = [selected] if selected in MODEL_PROFILES else ["fast"]
        names.extend(name for name in ("fast", "smart") if name not in names)
        return names

    @staticmethod
    def _read_rss_kb(pid):
        try:
            with open(f"/proc/{pid}/status", "r") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return None

    def _start_worker(self):
        server = RUNTIME / "llama-server"
        if not server.exists():
            self.events.put(("engine_error", "Inference runtime is missing"))
            return
        for profile_name in self._candidate_names():
            if self.stop_event.is_set():
                return
            profile = MODEL_PROFILES[profile_name]
            model = MODELS / profile["file"]
            if not model.exists():
                continue
            self.progress = 0.0
            try:
                target_kb = max(1, model.stat().st_size // 1024)
            except OSError:
                target_kb = 1
            self.events.put(("engine_status", f"LOADING {profile['label']}"))
            env = os.environ.copy()
            env["LD_LIBRARY_PATH"] = str(RUNTIME)
            command = [
                str(server), "--offline", "--no-webui", "--no-mmproj",
                "-dev", "none", "-m", str(model),
                "-c", str(self.config["context"]), "-t", "3", "-tb", "4",
                "-b", "128", "-ub", "64", "-np", "1",
                "-cram", "0", "-ctxcp", "0", "-ctk", "q8_0", "-ctv", "q8_0",
                "-rea", "off", "--no-warmup", "--host", "127.0.0.1",
                "--port", "39227", "--sse-ping-interval", "-1", "--log-disable",
            ]
            try:
                self.log = open("/tmp/project_ai_server.log", "ab", buffering=0)
                self.process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
                    env=env, start_new_session=True,
                )
                process = self.process
            except OSError:
                self._stop_process()
                continue
            deadline = time.monotonic() + 55
            while time.monotonic() < deadline:
                if self.stop_event.is_set() or process.poll() is not None:
                    break
                try:
                    with urlopen(SERVER_URL + "/health", timeout=0.6) as response:
                        if response.status == 200 and not self.stop_event.is_set():
                            self.profile = profile_name
                            self.ready = True
                            self.progress = 1.0
                            self.events.put(("engine_ready", profile["label"], profile_name))
                            return
                except (OSError, URLError):
                    pass
                rss_kb = self._read_rss_kb(process.pid)
                if rss_kb:
                    fraction = rss_kb / target_kb
                    if fraction > self.progress:
                        self.progress = min(0.985, fraction)
                time.sleep(0.25)
            self._stop_process()
            if self.stop_event.is_set():
                return
            self.events.put(("engine_status", "MODEL FAILED // TRYING FALLBACK"))
        self.events.put(("engine_error", "No model could start within available memory"))

    def _stop_process(self):
        process, self.process = self.process, None
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process = process
                    raise RuntimeError("The previous model process has not stopped")
        log, self.log = self.log, None
        if log:
            log.close()

    def generate(self, messages):
        with self.lock:
            if not self.ready or (self.worker and self.worker.is_alive()):
                return None
            self.request_id += 1
            request_id = self.request_id
            self.cancel_event.clear()
            self.worker = threading.Thread(
                target=self._generate_worker, args=(request_id, messages), daemon=True)
            self.worker.start()
            return request_id

    @staticmethod
    def _trim_messages(messages, system_prompt=SYSTEM_PROMPT):
        selected = []
        characters = 0
        for item in reversed(messages):
            if item.get("transient"):
                continue
            content = item.get("content", "")
            cost = len(content) + 24
            if selected and characters + cost > 2100:
                break
            selected.append({"role": item["role"], "content": content})
            characters += cost
        selected.reverse()
        return [{"role": "system", "content": system_prompt}] + selected

    def _generate_worker(self, request_id, messages):
        started = time.monotonic()
        body = {
            "model": "local",
            "messages": self._trim_messages(messages, self.config.get("system_prompt", SYSTEM_PROMPT)),
            "stream": True,
            "max_tokens": self.config["max_tokens"],
            "temperature": self.config.get("temperature", 0.25),
            "top_k": 40,
            "top_p": 0.9,
            "repeat_penalty": 1.08,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        request = Request(
            SERVER_URL + "/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timings = {}
        try:
            with urlopen(request, timeout=300) as response:
                for raw_line in response:
                    if self.cancel_event.is_set():
                        self.events.put(("cancelled", request_id))
                        return
                    raw_line = raw_line.strip()
                    if not raw_line.startswith(b"data: "):
                        continue
                    payload = raw_line[6:]
                    if payload == b"[DONE]":
                        break
                    chunk = json.loads(payload.decode("utf-8"))
                    timings = chunk.get("timings") or timings
                    choices = chunk.get("choices") or []
                    if choices:
                        content = (choices[0].get("delta") or {}).get("content")
                        if content:
                            self.events.put(("delta", request_id, content))
            elapsed = max(0.001, time.monotonic() - started)
            self.events.put(("done", request_id, timings, elapsed))
        except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
            if self.cancel_event.is_set():
                self.events.put(("cancelled", request_id))
            else:
                self.events.put(("generation_error", request_id, str(error)))

    def cancel(self):
        self.cancel_event.set()

    def stop(self):
        self.ready = False
        self.stop_event.set()
        self.cancel_event.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=1.5)
        self._stop_process()
        if self.starter and self.starter.is_alive():
            self.starter.join(timeout=2)


def key_geometry(row_index, row=None):
    row = KEYBOARD[row_index] if row is None else row
    count = len(row)
    if count == 1:
        return [(CONTENT_X, CONTENT_W)]
    width = min(KEY_MAX_W, (CONTENT_W - (count - 1) * KEY_GAP) // count)
    slack = CONTENT_W - (count * width + (count - 1) * KEY_GAP)
    gap = KEY_GAP + slack / (count - 1)
    result = []
    x = float(CONTENT_X)
    for _ in row:
        result.append((int(round(x)), width))
        x += width + gap
    return result


MESSAGE_LAYOUT_CACHE = OrderedDict()
WIDTH_CACHE = {}
KEY_LABEL_CACHE = {}


def text_width(font, value):
    key = (font, value)
    cached = WIDTH_CACHE.get(key)
    if cached is None:
        cached = measure(font, value)[0]
        WIDTH_CACHE[key] = cached
    return cached


def key_label_size(font, key):
    cached = KEY_LABEL_CACHE.get((font, key))
    if cached is None:
        cached = measure(font, key)
        KEY_LABEL_CACHE[(font, key)] = cached
    return cached


def message_prefix(item):
    return "[YOU] " if item["role"] == "user" else "[227] "


SCRAMBLE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!<>[]{}/\\|#$%&*+=?-_"
SCRAMBLE_TICK = 0.05
SCRAMBLE_MIN = 8
SCRAMBLE_SPEED = 40.0
SCRAMBLE_DRAIN = 1.2


def scramble_text(text, salt):
    length = len(SCRAMBLE_CHARS)
    pieces = []
    for index, char in enumerate(text):
        if char.isspace():
            pieces.append(char)
            continue
        mixed = (index * 2654435761 + salt * 40503 + 12345) & 0xFFFFFFFF
        pieces.append(SCRAMBLE_CHARS[mixed % length])
    return "".join(pieces)


def advance_scramble(item, elapsed):
    reveal = item.get("reveal")
    if reveal is None:
        return False
    received = len(item.get("content", ""))
    if reveal >= received and not item.get("pending"):
        return False
    if item.get("pending") and received >= SCRAMBLE_MIN:
        ceiling = received - SCRAMBLE_MIN
    else:
        ceiling = received
    speed = max(SCRAMBLE_SPEED, (received - reveal) / SCRAMBLE_DRAIN)
    step = max(1, int(speed * elapsed))
    item["reveal"] = min(reveal + step, ceiling)
    return True



def viewport_metrics(composing):
    top = FRAME_TOP
    bottom = COMPOSE_TOP if composing else INPUT_TOP
    content_top = top + CHAT_PAD
    visible = bottom - top - CHAT_PAD * 2
    return top, bottom, content_top, visible


def message_layout(font, item):
    content = item.get("content", "")
    if item.get("pending") and not content:
        content = "..."
    cache_key = (font, item["role"], content, bool(item.get("pending")))
    cached = MESSAGE_LAYOUT_CACHE.get(cache_key)
    if cached is not None:
        MESSAGE_LAYOUT_CACHE.move_to_end(cache_key)
        return cached
    indent = text_width(font, message_prefix(item))
    avail = MSG_RIGHT - MSG_LEFT - indent
    if item["role"] != "user" and content:
        avail -= SPEAK_GUTTER
    lines = wrap_pixels(font, content, avail)
    result = (lines, MSG_RIGHT - MSG_LEFT, len(lines) * LINE_H)
    MESSAGE_LAYOUT_CACHE[cache_key] = result
    while len(MESSAGE_LAYOUT_CACHE) > 192:
        MESSAGE_LAYOUT_CACHE.popitem(last=False)
    return result


INDEX_LAYOUT_CACHE = OrderedDict()


def message_lines_indexed(font, content, gutter=0):
    cache_key = (font, content, gutter)
    cached = INDEX_LAYOUT_CACHE.get(cache_key)
    if cached is not None:
        INDEX_LAYOUT_CACHE.move_to_end(cache_key)
        return cached
    indent = text_width(font, "[227] ")
    result = wrap_pixels_indexed(font, content, MSG_RIGHT - MSG_LEFT - indent - gutter)
    INDEX_LAYOUT_CACHE[cache_key] = result
    while len(INDEX_LAYOUT_CACHE) > 192:
        INDEX_LAYOUT_CACHE.popitem(last=False)
    return result


def draw_spoken_line(renderer, cache, font, text, x, y, start, word):
    left = max(0, word[0] - start)
    right = min(len(text), word[1] - start)
    if left >= right:
        cache.draw(font, text, x, y, TEXT)
        return
    before, spoken, after = text[:left], text[left:right], text[right:]
    width = text_width(font, before)
    spoken_width = text_width(font, spoken)
    fill(renderer, x + width, y, spoken_width, LINE_H, PANEL_3)
    line(renderer, x + width, y + LINE_H - 1,
         x + width + spoken_width - 1, y + LINE_H - 1, GREEN)
    cache.draw(font, before, x, y, TEXT)
    cache.draw(font, spoken, x + width, y, GREEN)
    cache.draw(font, after, x + width + spoken_width, y, TEXT)


def draw_cursor(renderer, x, y):
    points = [(0, 0), (0, 20), (5, 15), (10, 23),
              (14, 21), (9, 13), (18, 13)]

    def paint(offset_x, offset_y, color):
        for row in range(23):
            scan_y = row + 0.5
            intersections = []
            for start, end in zip(points, points[1:] + points[:1]):
                x1, y1 = start
                x2, y2 = end
                if y1 == y2 or not min(y1, y2) <= scan_y < max(y1, y2):
                    continue
                intersections.append(x1 + (scan_y - y1) * (x2 - x1) / (y2 - y1))
            intersections.sort()
            for index in range(0, len(intersections) - 1, 2):
                left = math.ceil(intersections[index])
                right = math.floor(intersections[index + 1])
                if right >= left:
                    line(renderer, x + offset_x + left, y + offset_y + row,
                         x + offset_x + right, y + offset_y + row, color)

    paint(2, 2, SHADOW)
    paint(0, 0, GREEN)
    for start, end in zip(points, points[1:] + points[:1]):
        line(renderer, x + start[0], y + start[1], x + end[0], y + end[1], SHADOW)


def draw_history(renderer, fonts, cache, chats, index, scroll, background=None):
    title_font, body_font, small_font, key_font = fonts
    fill_panel(renderer, 0, HEADER_H, W, H - HEADER_H, (2, 5, 3, 255), background)

    panel_x, panel_y = CONTENT_X, FRAME_TOP
    panel_w = CONTENT_W
    panel_h = (H - 10) - FRAME_TOP
    fill_panel(renderer, panel_x, panel_y, panel_w, panel_h, PANEL, background)
    box(renderer, panel_x, panel_y, panel_w, panel_h, EDGE)
    for bx, by, dx, dy in ((panel_x, panel_y, 1, 1),
                           (panel_x + panel_w - 1, panel_y, -1, 1),
                           (panel_x, panel_y + panel_h - 1, 1, -1),
                           (panel_x + panel_w - 1, panel_y + panel_h - 1, -1, -1)):
        bracket(renderer, bx, by, dx, dy, 14, GREEN)

    left = HISTORY_LIST_LEFT
    right = HISTORY_LIST_LEFT + HISTORY_LIST_W
    cache.draw(title_font, "CHAT HISTORY", left, panel_y + 14, GREEN)
    count_label = "%d SAVED" % len(chats)
    cache.draw(small_font, count_label, right - text_width(small_font, count_label),
               panel_y + 18, MUTED)
    line(renderer, left, panel_y + 40, right, panel_y + 40, BORDER)

    if not chats:
        cache.draw(body_font, "no saved chats yet", left + 4, HISTORY_LIST_TOP + 10, TEXT)
        cache.draw(small_font, "start a chat, then press Y to file it here",
                   left + 4, HISTORY_LIST_TOP + 32, MUTED)
    for row in range(HISTORY_VISIBLE):
        position = scroll + row
        if position >= len(chats):
            break
        entry = chats[position]
        row_y = HISTORY_LIST_TOP + row * HISTORY_ROW_H
        height = HISTORY_ROW_H - 4
        selected = position == index
        if selected:
            fill_panel(renderer, left, row_y, HISTORY_LIST_W, height, (11, 42, 26, 255), background)
            box(renderer, left, row_y, HISTORY_LIST_W, height, GREEN)
            cache.draw(small_font, ">", left + 4, row_y + 10, GREEN)
        cache.draw(body_font, entry["title"], left + 20, row_y + 3,
                   GREEN if selected else TEXT)
        meta = "%s   %d MSG" % (relative_time(entry["updated"]), len(entry["messages"]))
        cache.draw(small_font, meta, left + 20, row_y + 20, MUTED)

    if len(chats) > HISTORY_VISIBLE:
        track_x = right - 4
        track_y = HISTORY_LIST_TOP
        track_height = HISTORY_VISIBLE * HISTORY_ROW_H - 4
        maximum = len(chats) - HISTORY_VISIBLE
        thumb_height = max(20, int(track_height * HISTORY_VISIBLE / len(chats)))
        offset = min(max(scroll, 0), maximum)
        thumb_y = track_y + int((track_height - thumb_height) * offset / maximum)
        fill(renderer, track_x, track_y, 3, track_height, PANEL_3)
        fill(renderer, track_x, thumb_y, 3, thumb_height, GREEN_DIM)

    controls = "A OPEN   Y DELETE   B CLOSE"
    cache.draw(small_font, controls, (W - text_width(small_font, controls)) // 2,
               H - 30, MUTED)


def draw_models(renderer, fonts, cache, choices, index, background=None):
    title_font, body_font, small_font, key_font = fonts
    fill_panel(renderer, 0, HEADER_H, W, H - HEADER_H, (2, 5, 3, 255), background)

    panel_x, panel_y = CONTENT_X, FRAME_TOP
    panel_w = CONTENT_W
    panel_h = (H - 10) - FRAME_TOP
    fill_panel(renderer, panel_x, panel_y, panel_w, panel_h, PANEL, background)
    box(renderer, panel_x, panel_y, panel_w, panel_h, EDGE)
    for bx, by, dx, dy in ((panel_x, panel_y, 1, 1),
                           (panel_x + panel_w - 1, panel_y, -1, 1),
                           (panel_x, panel_y + panel_h - 1, 1, -1),
                           (panel_x + panel_w - 1, panel_y + panel_h - 1, -1, -1)):
        bracket(renderer, bx, by, dx, dy, 14, GREEN)

    left = HISTORY_LIST_LEFT
    right = HISTORY_LIST_LEFT + HISTORY_LIST_W
    cache.draw(title_font, "CHOOSE A MODEL", left, panel_y + 14, GREEN)
    count_label = "%d AVAILABLE" % len(choices)
    cache.draw(small_font, count_label, right - text_width(small_font, count_label),
               panel_y + 18, MUTED)
    line(renderer, left, panel_y + 40, right, panel_y + 40, BORDER)

    if not choices:
        cache.draw(body_font, "no local models found", left + 4, HISTORY_LIST_TOP + 10, ERROR)
        cache.draw(small_font, "add a .gguf under models/ then relaunch",
                   left + 4, HISTORY_LIST_TOP + 32, MUTED)

    row_h = 52
    for position, (name, profile) in enumerate(choices):
        row_y = HISTORY_LIST_TOP + position * row_h
        height = row_h - 6
        selected = position == index
        if selected:
            fill_panel(renderer, left, row_y, HISTORY_LIST_W, height, (11, 42, 26, 255), background)
            box(renderer, left, row_y, HISTORY_LIST_W, height, GREEN)
            cache.draw(small_font, ">", left + 6, row_y + height // 2 - 6, GREEN)
        cache.draw(body_font, profile["label"], left + 22, row_y + 6,
                   GREEN if selected else TEXT)
        cache.draw(small_font, profile["note"], left + 22, row_y + 26, MUTED)

    controls = "A SELECT   UP/DOWN MOVE   B KEEP LAST"
    cache.draw(small_font, controls, (W - text_width(small_font, controls)) // 2,
               H - 30, MUTED)


def draw_boot(renderer, fonts, cache, vp_top, vp_bottom, model_label, fraction, background=None):
    title_font, body_font, small_font, key_font = fonts
    height = vp_bottom - vp_top
    left = CONTENT_X + 1
    width = CONTENT_W - 2

    fraction = max(0.0, min(1.0, fraction))
    fill_height = int(height * fraction)
    top = vp_bottom - fill_height

    fill_panel(renderer, left, top, width, fill_height, (23, 150, 82, 255), background)
    if fill_height > 0:
        fill(renderer, left, top, width, min(3, fill_height), GREEN)

    label = f"LOADING  {int(round(fraction * 100))}%"
    label_y = vp_top + height // 2 - 12
    covered = top <= label_y + 16
    cache.draw(title_font, label,
               CONTENT_X + (CONTENT_W - text_width(title_font, label)) // 2,
               label_y, BG if covered else GREEN)

    sub = model_label
    sub_y = label_y + 26
    sub_covered = top <= sub_y + 12
    cache.draw(small_font, sub,
               CONTENT_X + (CONTENT_W - text_width(small_font, sub)) // 2,
               sub_y, BG if sub_covered else MUTED)


def draw_keyboard(renderer, fonts, cache, key_row, key_col, background=None,
                  mic_state="idle", action_row=None):
    key_font = fonts[3]
    for row_index, base_row in enumerate(KEYBOARD):
        row = action_row if action_row is not None and row_index == len(KEYBOARD) - 1 else base_row
        geometry = key_geometry(row_index, row)
        key_y = KEY_TOP + row_index * KEY_STEP
        for column, key in enumerate(row):
            x, width = geometry[column]
            selected = row_index == key_row and column == key_col
            active_mic = key == "MIC" and mic_state == "recording"
            label = key
            if key == "MIC":
                if mic_state == "recording":
                    label = "STOP"
                elif mic_state == "transcribing":
                    label = "..."
            if active_mic and not selected:
                fill_panel(renderer, x, key_y, width, KEY_HEIGHT, PANEL_2, background)
                box(renderer, x, key_y, width, KEY_HEIGHT, ERROR)
            else:
                fill_panel(renderer, x, key_y, width, KEY_HEIGHT,
                           GREEN if selected else PANEL_3, None if selected else background)
                box(renderer, x, key_y, width, KEY_HEIGHT, GREEN if selected else BORDER)
            label_width, label_height = key_label_size(key_font, label)
            cache.draw(key_font, label, x + (width - label_width) // 2,
                       key_y + (KEY_HEIGHT - label_height) // 2,
                       BG if selected else (ERROR if active_mic else TEXT))


def draw_ui(renderer, fonts, cache, messages, scroll, composing, draft,
            key_row, key_col, engine_state, model_label, perf_label,
            ram_label, temperature_label, cursor_x, cursor_y, logo, blink_on,
            overlay=None, chats=None, history_index=0, history_scroll=0, boot_fraction=0.0,
            scramble_salt=0, suggestions=None, suggest_focus=False, suggest_index=0,
            mic_state="idle", mic_seconds=0.0, mic_note="", speak_hover=-1, tts_index=-1,
            models=None, model_index=0, tts_word=None, intro=None, background=None,
            settings=None):
    intro_starting = intro is not None and intro.active and intro.started is None
    if intro is not None and intro.covers_ui and not intro_starting:
        intro.draw()
        SDL.SDL_RenderPresent(renderer)
        if intro_starting and intro.active:
            intro.started = time.monotonic()
        return
    title_font, body_font, small_font, key_font = fonts
    suggestions = suggestions or []
    fill_panel(renderer, 0, 0, W, H, BG, background)
    vp_top, vp_bottom, content_top, visible = viewport_metrics(composing)

    fill_panel(renderer, 0, vp_bottom, W, H - vp_bottom, PANEL, background)
    box(renderer, CONTENT_X, vp_top, CONTENT_W, vp_bottom - vp_top, EDGE)
    for bx, by, dx, dy in ((CONTENT_X, vp_top, 1, 1),
                           (CONTENT_X + CONTENT_W - 1, vp_top, -1, 1),
                           (CONTENT_X, vp_bottom - 1, 1, -1),
                           (CONTENT_X + CONTENT_W - 1, vp_bottom - 1, -1, -1)):
        bracket(renderer, bx, by, dx, dy, 12, GREEN)

    if engine_state == "BOOTING":
        clip = Rect(CONTENT_X, vp_top, CONTENT_W, vp_bottom - vp_top)
        SDL.SDL_RenderSetClipRect(renderer, byref(clip))
        draw_boot(renderer, fonts, cache, vp_top, vp_bottom, model_label, boot_fraction, background)
        SDL.SDL_RenderSetClipRect(renderer, None)
    else:
        clip = Rect(CONTENT_X, vp_top, CONTENT_W, vp_bottom - vp_top)
        SDL.SDL_RenderSetClipRect(renderer, byref(clip))
        y = content_top - scroll
        for index, item in enumerate(messages):
            content = item.get("content", "")
            reveal = item.get("reveal")
            animating = reveal is not None and reveal < len(content)
            if animating:
                indexed = message_lines_indexed(body_font, content)
                height = len(indexed) * LINE_H
            else:
                lines, _, height = message_layout(body_font, item)
            if y + height >= vp_top and y <= vp_bottom:
                prefix = message_prefix(item)
                text_x = MSG_LEFT + text_width(body_font, prefix)
                if item["role"] == "user":
                    prefix_color = MUTED
                else:
                    prefix_color = GREEN
                cache.draw(body_font, prefix, MSG_LEFT, y, prefix_color)
                if item.get("pending") and not content:
                    dots = "." * (1 + (scramble_salt // 8) % 3)
                    cache.draw(body_font, dots, text_x, y, GREEN)
                elif animating:
                    text_y = y
                    for line_text, start in indexed:
                        cut = reveal - start
                        if cut >= len(line_text):
                            cache.draw(body_font, line_text, text_x, text_y, TEXT)
                        elif cut <= 0:
                            cache.draw(body_font, scramble_text(line_text, scramble_salt),
                                       text_x, text_y, GREEN)
                        else:
                            settled = line_text[:cut]
                            tail = line_text[cut:]
                            width, _ = cache.draw(body_font, settled, text_x, text_y, TEXT)
                            cache.draw(body_font, scramble_text(tail, scramble_salt),
                                       text_x + width, text_y, GREEN)
                        text_y += LINE_H
                elif index == tts_index and tts_word is not None:
                    for row, (text_line, start) in enumerate(
                            message_lines_indexed(body_font, content, SPEAK_GUTTER)):
                        draw_spoken_line(renderer, cache, body_font, text_line,
                                         text_x, y + row * LINE_H, start, tts_word)
                else:
                    body_color = MUTED if item.get("pending") and not content else TEXT
                    text_y = y
                    for text_line in lines:
                        cache.draw(body_font, text_line, text_x, text_y, body_color)
                        text_y += LINE_H
                if item["role"] != "user" and content:
                    if index == tts_index:
                        glyph_color = GREEN if scramble_salt % 2 == 0 else GREEN_DIM
                    elif index == speak_hover:
                        glyph_color = GREEN
                    else:
                        glyph_color = GREEN_DIM
                    draw_speak_glyph(renderer, MSG_RIGHT - 11, y + 4, glyph_color)
            y += height + MSG_GAP
        SDL.SDL_RenderSetClipRect(renderer, None)

        total_height = sum(message_layout(body_font, item)[2] + MSG_GAP for item in messages)
        if total_height > visible:
            track_x = CONTENT_X + CONTENT_W - 8
            track_y = vp_top + CHAT_PAD
            track_height = vp_bottom - vp_top - CHAT_PAD * 2
            maximum_scroll = max(1.0, total_height - visible)
            thumb_height = max(20, int(track_height * visible / total_height))
            thumb_y = track_y + int((track_height - thumb_height) *
                                    min(max(scroll, 0.0), maximum_scroll) / maximum_scroll)
            fill(renderer, track_x, track_y, 3, track_height, PANEL_3)
            fill(renderer, track_x, thumb_y, 3, thumb_height, GREEN_DIM)

    if not composing:
        ready = engine_state == "READY"
        hovered = CONTENT_X <= cursor_x < CONTENT_X + CONTENT_W and 424 <= cursor_y < 458
        fill_panel(renderer, CONTENT_X, 424, CONTENT_W, 34, PANEL_2, background)
        box(renderer, CONTENT_X, 424, CONTENT_W, 34, GREEN if (hovered or ready) else BORDER)
        prompt_width = text_width(body_font, "> ")
        cache.draw(body_font, ">", MSG_LEFT, 433, GREEN if blink_on else GREEN_DIM)
        hint = "ask 227 anything" if ready else model_label.lower()
        cache.draw(body_font, hint, MSG_LEFT + prompt_width, 433, TEXT if ready else MUTED)
        controls = "A TYPE/SPEAK   X HISTORY   Y NEW CHAT   B EXIT"
        if tts_index >= 0:
            controls = "A STOP   X HISTORY   Y NEW CHAT   B EXIT"
        cache.draw(small_font, controls, (W - text_width(small_font, controls)) // 2, 462, MUTED)
    else:
        fill_panel(renderer, CONTENT_X, DRAFT_TOP, CONTENT_W, DRAFT_H, PANEL_2, background)
        box(renderer, CONTENT_X, DRAFT_TOP, CONTENT_W, DRAFT_H, GREEN_DIM)
        cache.draw(small_font, "COMPOSE", MSG_LEFT, DRAFT_TOP + 6, GREEN_DIM)
        count_label = f"{len(draft)}/500"
        cache.draw(small_font, count_label, MSG_RIGHT - text_width(small_font, count_label), DRAFT_TOP + 6, MUTED)
        line(renderer, MSG_LEFT, DRAFT_TOP + 23, MSG_RIGHT, DRAFT_TOP + 23, BORDER)
        cache.draw(body_font, ">", MSG_LEFT, DRAFT_TOP + 28, GREEN)
        draft_x = MSG_LEFT + text_width(body_font, "> ")
        draft_lines = wrap_pixels(body_font, draft[-220:], MSG_RIGHT - draft_x)[-2:] if draft else [""]
        text_y = DRAFT_TOP + 28
        caret_x = draft_x
        for draft_line in draft_lines:
            caret_x = draft_x + cache.draw(body_font, draft_line, draft_x, text_y, TEXT)[0]
            text_y += LINE_H
        caret_y = DRAFT_TOP + 28 + (len(draft_lines) - 1) * LINE_H
        if blink_on and caret_x + text_width(body_font, "M") <= MSG_RIGHT:
            cache.draw(body_font, BLOCK_CHAR, caret_x, caret_y, GREEN)

        if mic_state == "recording":
            fill_panel(renderer, CONTENT_X, SUGGEST_TOP, CONTENT_W, SUGGEST_H, PANEL_2, background)
            box(renderer, CONTENT_X, SUGGEST_TOP, CONTENT_W, SUGGEST_H, ERROR)
            seconds = int(mic_seconds)
            dot = BLOCK_CHAR if blink_on else " "
            status = f"{dot} REC  {seconds // 60}:{seconds % 60:02d}   TAP MIC TO STOP"
            cache.draw(small_font, status, MSG_LEFT, SUGGEST_TOP + 6, ERROR)
        elif mic_state == "transcribing":
            fill_panel(renderer, CONTENT_X, SUGGEST_TOP, CONTENT_W, SUGGEST_H, PANEL_3, background)
            box(renderer, CONTENT_X, SUGGEST_TOP, CONTENT_W, SUGGEST_H, GREEN_DIM)
            status = "TRANSCRIBING" + "." * (scramble_salt % 4)
            cache.draw(small_font, status, MSG_LEFT, SUGGEST_TOP + 6, GREEN_DIM)
        elif mic_note:
            fill_panel(renderer, CONTENT_X, SUGGEST_TOP, CONTENT_W, SUGGEST_H, PANEL_2, background)
            box(renderer, CONTENT_X, SUGGEST_TOP, CONTENT_W, SUGGEST_H, ERROR)
            cache.draw(small_font, mic_note, MSG_LEFT, SUGGEST_TOP + 6, ERROR)
        else:
            chip_geometry = suggestion_geometry(len(suggestions))
            for index, word in enumerate(suggestions):
                chip_x, chip_width = chip_geometry[index]
                selected = suggest_focus and index == suggest_index
                fill_panel(renderer, chip_x, SUGGEST_TOP, chip_width, SUGGEST_H,
                           GREEN if selected else PANEL_3, None if selected else background)
                box(renderer, chip_x, SUGGEST_TOP, chip_width, SUGGEST_H,
                    GREEN if selected else BORDER)
                label_width, label_height = key_label_size(key_font, word)
                cache.draw(key_font, word, chip_x + (chip_width - label_width) // 2,
                           SUGGEST_TOP + (SUGGEST_H - label_height) // 2,
                           BG if selected else TEXT)

        draw_keyboard(renderer, fonts, cache, key_row, key_col, background, mic_state)
        compose_controls = "A TYPE   X SEND   B DELETE / CLOSE   MIC DICTATE"
        if suggestions and mic_state == "idle":
            compose_controls += "   UP SUGGEST"
        cache.draw(small_font, compose_controls,
                   (W - text_width(small_font, compose_controls)) // 2, 450, MUTED)

    fill_panel(renderer, 0, 0, W, HEADER_H, PANEL, background)
    line(renderer, 0, HEADER_H - 1, W, HEADER_H - 1, BORDER)
    if logo:
        logo_width, logo_height = LOGO_W * 5 // 8, LOGO_H * 5 // 8
        target = Rect((W - logo_width) // 2, (HEADER_H - logo_height) // 2,
                      logo_width, logo_height)
        SDL.SDL_RenderCopy(renderer, logo, None, byref(target))
    else:
        cache.draw(title_font, "227", (W - text_width(title_font, "227")) // 2, 18, GREEN)
    cache.draw(small_font, model_label, 16, 22, MUTED)

    status_label = {"READY": "[READY]", "BOOTING": "[BOOT]", "STOPPING": "[STOP]",
                    "SELECT": "[MENU]", "ERROR": "[ERROR]"}.get(engine_state, "[--]")
    if engine_state == "ERROR":
        status_color = ERROR
    elif engine_state == "READY":
        status_color = GREEN
    else:
        status_color = GREEN_DIM
    metrics = " | ".join(value for value in (perf_label, ram_label, temperature_label) if value)
    metrics_x = W - 22 - text_width(small_font, metrics) if metrics else W - 22
    if metrics:
        cache.draw(small_font, metrics, metrics_x, 22, MUTED)
    cache.draw(small_font, status_label,
               16 + text_width(small_font, model_label) + 12, 22, status_color)

    if overlay == "history":
        draw_history(renderer, fonts, cache, chats or [], history_index, history_scroll, background)
    elif overlay == "models":
        draw_models(renderer, fonts, cache, models or [], model_index, background)
    elif overlay == "settings" and settings is not None:
        settings.draw(renderer, fonts, cache, background)
    if not overlay or overlay == "settings":
        draw_cursor(renderer, cursor_x, cursor_y)
    if intro is not None:
        intro.draw()
    SDL.SDL_RenderPresent(renderer)
    if intro_starting and intro.active:
        intro.started = time.monotonic()


def load_rgba(renderer, path, width, height, opacity=255):
    """Load a raw RGBA asset without image-library dependencies."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) != width * height * 4:
        return None
    buffer = (c_uint8 * len(raw)).from_buffer_copy(raw)
    surface = SDL.SDL_CreateRGBSurfaceFrom(buffer, width, height, 32, width * 4,
                                           0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
    if not surface:
        return None
    texture = SDL.SDL_CreateTextureFromSurface(renderer, surface)
    SDL.SDL_FreeSurface(surface)
    if texture:
        SDL.SDL_SetTextureBlendMode(texture, 1)
        SDL.SDL_SetTextureAlphaMod(texture, opacity)
    return texture


def load_logo(renderer):
    return load_rgba(renderer, ROOT / LOGO_NAME, LOGO_W, LOGO_H)


def load_background(renderer, path, opacity=51):
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) != W * H * 4:
        return None
    background = BackgroundCache(renderer, raw, opacity)
    if not background.surface:
        background.close()
        return None
    return background


def read_device_metrics():
    ram = ""
    temperature = ""
    try:
        values = {}
        for line_value in Path("/proc/meminfo").read_text().splitlines():
            key, value = line_value.split(":", 1)
            values[key] = int(value.strip().split()[0])
        ram = f"RAM {values.get('MemAvailable', 0) // 1024}M"
    except (OSError, ValueError):
        pass
    for path in (Path("/sys/class/thermal/thermal_zone0/temp"),
                 Path("/sys/class/thermal/thermal_zone1/temp")):
        try:
            value = int(path.read_text().strip())
            temperature = f"CPU {value / 1000:.0f}C"
            break
        except (OSError, ValueError):
            continue
    return ram, temperature


def stick_motion(value, deadzone=5000):
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    motion = (magnitude - deadzone) / (32767 - deadzone)
    return -motion if value < 0 else motion


def main():
    from project_ai_settings import SettingsPanel

    if SDL.SDL_Init(0x20 | 0x200 | 0x2000) != 0 or TTF.TTF_Init() != 0:
        return 1
    window = SDL.SDL_CreateWindow(b"Project 227", 0, 0, W, H, 1)
    renderer = SDL.SDL_CreateRenderer(window, -1, 6) if window else None
    regular_path = b"/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    bold_path = b"/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
    title_font = TTF.TTF_OpenFont(bold_path, 16)
    body_font = TTF.TTF_OpenFont(regular_path, 14)
    small_font = TTF.TTF_OpenFont(regular_path, 11)
    key_font = TTF.TTF_OpenFont(bold_path, 15)
    fonts = (title_font, body_font, small_font, key_font)
    if not renderer or not all(fonts):
        if renderer:
            SDL.SDL_DestroyRenderer(renderer)
        if window:
            SDL.SDL_DestroyWindow(window)
        TTF.TTF_Quit()
        SDL.SDL_Quit()
        return 1

    controller = SDL.SDL_GameControllerOpen(0) if SDL.SDL_NumJoysticks() > 0 else None
    joystick = (SDL.SDL_JoystickOpen(0)
                if controller is None and SDL.SDL_NumJoysticks() > 0 else None)
    SDL.SDL_ShowCursor(0)
    cache = TextCache(renderer)
    logo_texture = load_logo(renderer)
    events = queue.Queue()
    engine = LocalEngine(events)
    opacity = engine.config.get("background", 20)
    background = load_background(renderer, ROOT / "intro-background.rgba",
                                 opacity=round(opacity * 255 / 100)) if opacity else None
    intro = IntroPlayer(renderer, ROOT, enabled=engine.config.get("intro", True))

    messages = load_history()
    engine_state = "BOOTING"
    model_label = "INITIALIZING LOCAL MODEL"
    perf_label = ""
    ram_label, temperature_label = read_device_metrics()
    last_metrics = time.monotonic()
    composing = False
    draft = ""
    key_row = key_col = 0
    suggestions = []
    suggest_focus = False
    suggest_index = 0
    suggest_source = None
    mic_state = "idle"
    mic_proc = None
    mic_started = 0.0
    mic_transcribe_started = 0.0
    mic_note = ""
    mic_note_time = 0.0
    whisper_model = resolve_whisper_model()
    mic_ready = WHISPER_BIN.exists() and whisper_model.exists()
    capture_device = choose_capture_device(detect_capture_devices())
    resolved_start = resolve_capture_device(detect_capture_devices(), engine.config.get("audio_device", "auto"))
    if resolved_start:
        capture_device = resolved_start
    scroll = 100000.0
    scroll_target = 100000.0
    follow_bottom = True
    active_request = None
    boot_progress = 0.0
    scramble_salt = 0
    last_scramble_time = time.monotonic()
    last_button = None
    last_button_time = 0
    cursor_x, cursor_y = 320, 438
    cursor_fx, cursor_fy = float(cursor_x), float(cursor_y)
    last_frame_time = time.monotonic()
    last_render_key = None
    running = True
    event_buffer = (ctypes.c_uint8 * 64)()
    overlay = None
    chats = load_chats()
    history_index = 0
    history_scroll = 0
    history_move_at = 0.0
    tts_state = "idle"
    tts_index = -1
    tts_players = []
    tts_token = 0
    tts_path = None
    tts_timings = []
    tts_started = None
    tts_position = -1
    tts_word = None
    speak_hover = -1
    settings_return = None
    settings_cursor = (cursor_x, cursor_y, cursor_fx, cursor_fy)
    settings_pointer = False
    restart_pending = False
    restart_thread = None

    model_choices = available_model_profiles()
    saved_model = engine.config.get("model", "fast")
    model_index = next((i for i, (name, _) in enumerate(model_choices) if name == saved_model), 0)
    model_move_at = 0.0
    if len(model_choices) > 1:
        overlay = "models"
        engine_state = "SELECT"
        model_label = "SELECT A MODEL"
    else:
        engine.start()

    def content_height():
        return sum(message_layout(body_font, item)[2] + MSG_GAP for item in messages)

    def clear_chat():
        nonlocal messages, scroll, scroll_target, follow_bottom, perf_label
        if active_request is not None:
            return
        stop_speaking()
        archive_current()
        messages = [{
            "role": "assistant",
            "content": "227 is ready for a new local session.",
            "pending": False,
            "transient": True,
        }]
        scroll = scroll_target = 100000.0
        follow_bottom = True
        perf_label = ""
        save_history(messages)

    def send_message():
        nonlocal composing, draft, active_request, scroll, scroll_target, follow_bottom, perf_label
        nonlocal suggest_focus, suggest_index
        content = draft.strip()
        if not content or engine_state != "READY" or active_request is not None:
            return
        if mic_state != "idle":
            return
        messages.append({"role": "user", "content": content, "pending": False})
        draft = ""
        composing = False
        suggest_focus = False
        suggest_index = 0
        follow_bottom = True
        scroll = scroll_target = 100000.0
        perf_label = ""
        answer = instant_answer(content) if engine.config.get("system_prompt", SYSTEM_PROMPT) == SYSTEM_PROMPT else None
        if answer is not None:
            messages.append({"role": "assistant", "content": answer, "pending": False, "reveal": 0})
            save_history(messages)
            return
        messages.append({"role": "assistant", "content": "", "pending": True, "reveal": 0})
        active_request = engine.generate(messages[:-1])
        if active_request is None:
            messages[-1]["content"] = "The local model is busy. Try again in a moment."
            messages[-1]["pending"] = False

    def park_key_cursor():
        nonlocal cursor_x, cursor_y, cursor_fx, cursor_fy
        key_x, key_width = key_geometry(key_row)[key_col]
        cursor_x = key_x + key_width // 2
        cursor_y = KEY_TOP + key_row * KEY_STEP + KEY_HEIGHT // 2
        cursor_fx, cursor_fy = float(cursor_x), float(cursor_y)
        SDL.SDL_WarpMouseInWindow(window, cursor_x, cursor_y)

    def park_suggest_cursor():
        nonlocal cursor_x, cursor_y, cursor_fx, cursor_fy
        chip_geometry = suggestion_geometry(len(suggestions))
        if not chip_geometry:
            return
        chip_x, chip_width = chip_geometry[suggest_index]
        cursor_x = chip_x + chip_width // 2
        cursor_y = SUGGEST_TOP + SUGGEST_H // 2
        cursor_fx, cursor_fy = float(cursor_x), float(cursor_y)
        SDL.SDL_WarpMouseInWindow(window, cursor_x, cursor_y)

    def accept_suggestion(word):
        nonlocal draft, suggest_focus, suggest_index
        cut = draft.rfind(" ") + 1
        candidate = draft[:cut] + word + " "
        if len(candidate) > 500:
            return
        draft = candidate
        suggest_focus = False
        suggest_index = 0
        park_key_cursor()

    def insert_transcript(text):
        nonlocal draft
        text = (text or "").strip()
        if not text:
            return
        addition = text if (not draft or draft.endswith(" ")) else " " + text
        if len(draft) >= 500:
            return
        draft = (draft + addition)[:500]

    def transcribe_worker():
        text = ""
        error = ""
        if not MIC_WAV.exists() or MIC_WAV.stat().st_size < 8000:
            mic_log("transcribe: no usable audio (missing or too short)")
            events.put(("mic_text", ""))
            return
        try:
            peak = wav_peak(MIC_WAV)
            mic_log(f"transcribe: peak={peak} size={MIC_WAV.stat().st_size}")
        except Exception as exc:  # noqa: BLE001 - never leave the mic stuck
            mic_log(f"transcribe: peak check failed {exc}")
            peak = None
        if peak is not None and peak < 50:
            mic_log("transcribe: microphone captured silence")
            events.put(("mic_text", ""))
            return
        try:
            result = subprocess.run(
                [str(WHISPER_BIN), "-m", str(whisper_model), "-f", str(MIC_WAV),
                 "-l", "en", "-nt", "-t", str(WHISPER_THREADS)],
                capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                tail = (result.stderr or "transcription failed").strip().splitlines()
                error = tail[-1][:120] if tail else "transcription failed"
                mic_log(f"transcribe: whisper rc={result.returncode} err={error}")
            else:
                text = clean_transcript(result.stdout)
                mic_log(f"transcribe: whisper ok text={text[:80]!r}")
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)[:120]
            mic_log(f"transcribe: whisper exception {error}")
        except Exception as exc:  # noqa: BLE001 - never leave the mic stuck
            error = str(exc)[:120]
            mic_log(f"transcribe: unexpected {error}")
        if error:
            events.put(("mic_error", error))
        else:
            events.put(("mic_text", text))

    def start_recording():
        nonlocal mic_state, mic_proc, mic_started, suggest_focus, mic_note, mic_note_time, capture_device, mic_ready
        if mic_state != "idle":
            return
        devices = detect_capture_devices()
        wanted_mic = engine.config.get("audio_device", "auto")
        capture_device = resolve_capture_device(devices, wanted_mic) or choose_capture_device(devices)
        mic_ready = WHISPER_BIN.exists() and whisper_model.exists() and capture_device is not None
        if not mic_ready:
            mic_log(f"start: not ready device={capture_device} bin={WHISPER_BIN.exists()} model={whisper_model.exists()}")
            mic_note = "VOICE UNAVAILABLE"
            mic_note_time = time.monotonic()
            return
        enable_capture_input(capture_device)
        device_arg = capture_device["arg"]
        MIC_WAV.unlink(missing_ok=True)
        try:
            mic_proc = subprocess.Popen(
                ["arecord", "-D", device_arg, "-f", "S16_LE", "-r", "16000",
                 "-c", "1", "-t", "wav", "-q", str(MIC_WAV)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            mic_proc = None
            mic_log(f"start: arecord spawn failed {exc}")
            mic_note = "MIC ERROR"
            mic_note_time = time.monotonic()
            return
        mic_started = time.monotonic()
        mic_note = ""
        mic_state = "recording"
        mic_log(f"start: device={device_arg} card={capture_device['card']} usb={capture_device['usb']}")
        if suggest_focus:
            suggest_focus = False
            park_key_cursor()

    def stop_recording():
        nonlocal mic_state, mic_proc, mic_transcribe_started
        if mic_state != "recording":
            return
        proc = mic_proc
        mic_proc = None
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
        size = MIC_WAV.stat().st_size if MIC_WAV.exists() else 0
        code = proc.returncode if proc is not None else None
        repair_wav_header(MIC_WAV)
        mic_log(f"stop: arecord rc={code} wav={size}B dur={time.monotonic() - mic_started:.1f}s")
        mic_state = "transcribing"
        mic_transcribe_started = time.monotonic()
        threading.Thread(target=transcribe_worker, daemon=True).start()

    def toggle_mic():
        if mic_state == "idle":
            start_recording()
        elif mic_state == "recording":
            stop_recording()

    def stop_speaking():
        nonlocal tts_state, tts_index, tts_players, tts_token, tts_path
        nonlocal tts_timings, tts_started, tts_position, tts_word
        tts_token += 1
        tts_state = "idle"
        tts_index = -1
        tts_word = None
        tts_timings = []
        tts_started = None
        tts_position = -1
        for player in tts_players:
            if player.poll() is None:
                player.terminate()
        tts_players = []
        if tts_path is not None:
            tts_path.unlink(missing_ok=True)
            tts_path = None

    def tts_worker(text, index, token):
        output = TTS_WAV.with_name(f"{TTS_WAV.stem}-{os.getpid()}-{token}.wav")
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--tts-synthesize", str(output), text],
                capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError("Speech synthesis failed")
            timings = json.loads(result.stdout)
            if engine.config.get("audio_device", "auto") != "auto" and capture_device is not None:
                devices = [capture_device["arg"]]
            else:
                devices = tts_output_devices()
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
            output.unlink(missing_ok=True)
            events.put(("tts_done", token))
        else:
            if token != tts_token:
                output.unlink(missing_ok=True)
            else:
                events.put(("tts_ready", token, index, output, timings, devices))

    def speak_message(index):
        nonlocal tts_state, tts_index, follow_bottom, scroll, scroll_target
        if index < 0 or index >= len(messages):
            return
        item = messages[index]
        if item["role"] == "user" or item.get("pending"):
            return
        text = sanitize(item.get("content") or "")
        if not text.strip():
            return
        if tts_state == "playing" and tts_index == index:
            stop_speaking()
            return
        stop_speaking()
        tts_index = index
        tts_state = "playing"
        follow_bottom = False
        scroll = scroll_target = sum(message_layout(body_font, previous)[2] + MSG_GAP
                                     for previous in messages[:index])
        item["reveal"] = len(item.get("content") or "")
        threading.Thread(target=tts_worker, args=(text[:600], index, tts_token), daemon=True).start()

    def hovered_speak_index():
        for index, top, height, item in message_blocks(body_font, messages, scroll):
            if item["role"] == "user" or item.get("pending") or not item.get("content"):
                continue
            if top <= cursor_y < top + max(height, LINE_H):
                return index
        return -1


    def activate_key():
        nonlocal draft, composing
        if suggest_focus and suggestions:
            accept_suggestion(suggestions[suggest_index])
            return
        key = KEYBOARD[key_row][key_col]
        if key == "SPACE":
            if len(draft) < 500:
                draft += " "
        elif key == "DEL":
            draft = draft[:-1]
        elif key == "SEND":
            send_message()
        elif key == "CLOSE":
            composing = False
        elif key == "MIC":
            toggle_mic()
        elif len(draft) < 500:
            draft += key

    def move_key(dy=0, dx=0):
        nonlocal key_row, key_col, suggest_focus, suggest_index
        if suggest_focus:
            if dy > 0:
                suggest_focus = False
                park_key_cursor()
            elif dx:
                suggest_index = max(0, min(len(suggestions) - 1, suggest_index + dx))
                park_suggest_cursor()
            return
        if dy < 0 and key_row == 0 and suggestions and mic_state == "idle":
            suggest_focus = True
            suggest_index = min(suggest_index, len(suggestions) - 1)
            park_suggest_cursor()
            return
        key_row = max(0, min(len(KEYBOARD) - 1, key_row + dy))
        key_col = max(0, min(len(KEYBOARD[key_row]) - 1, key_col + dx))
        park_key_cursor()

    def sync_key_to_cursor():
        nonlocal key_row, key_col, suggest_focus, suggest_index
        if suggestions and mic_state == "idle" and cursor_y < KEY_TOP:
            suggest_focus = True
            chip_geometry = suggestion_geometry(len(suggestions))
            suggest_index = min(range(len(chip_geometry)),
                                key=lambda index: abs(cursor_x - (chip_geometry[index][0] + chip_geometry[index][1] / 2)))
            return
        suggest_focus = False
        key_row = max(0, min(len(KEYBOARD) - 1,
                             round((cursor_y - (KEY_TOP + KEY_HEIGHT / 2)) / KEY_STEP)))
        geometry = key_geometry(key_row)
        key_col = min(range(len(geometry)),
                      key=lambda index: abs(cursor_x - (geometry[index][0] + geometry[index][1] / 2)))

    def history_row_y(position):
        return HISTORY_LIST_TOP + (position - history_scroll) * HISTORY_ROW_H

    def park_history_cursor():
        nonlocal cursor_x, cursor_y, cursor_fx, cursor_fy
        cursor_x = HISTORY_LIST_LEFT + 12
        cursor_y = history_row_y(history_index) + HISTORY_ROW_H // 2
        cursor_fx, cursor_fy = float(cursor_x), float(cursor_y)
        SDL.SDL_WarpMouseInWindow(window, cursor_x, cursor_y)

    def move_history(step):
        nonlocal history_index, history_scroll
        if not chats:
            return
        history_index = max(0, min(len(chats) - 1, history_index + step))
        if history_index < history_scroll:
            history_scroll = history_index
        elif history_index >= history_scroll + HISTORY_VISIBLE:
            history_scroll = history_index - HISTORY_VISIBLE + 1
        park_history_cursor()

    def open_history():
        nonlocal overlay, history_index, history_scroll
        if active_request is not None:
            return
        stop_speaking()
        overlay = "history"
        history_index = 0
        history_scroll = 0
        park_history_cursor()

    def close_history():
        nonlocal overlay
        overlay = None

    def move_model(step):
        nonlocal model_index
        if not model_choices:
            return
        model_index = max(0, min(len(model_choices) - 1, model_index + step))

    def confirm_model():
        nonlocal overlay, engine_state, model_label
        if not model_choices:
            return
        name, _ = model_choices[model_index]
        engine.config["model"] = name
        save_config(engine.config)
        overlay = None
        engine_state = "BOOTING"
        model_label = "INITIALIZING LOCAL MODEL"
        engine.start()

    def dismiss_model_picker():
        nonlocal overlay, engine_state, model_label
        overlay = None
        engine_state = "BOOTING"
        model_label = "INITIALIZING LOCAL MODEL"
        engine.start()

    def archive_current():
        nonlocal chats
        entry = archive_entry(messages)
        if entry is None:
            return
        key = (entry["title"], len(entry["messages"]))
        chats = [item for item in chats
                 if (item["title"], len(item["messages"])) != key]
        chats.insert(0, entry)
        del chats[MAX_CHATS:]
        save_chats(chats)

    def open_selected_chat():
        nonlocal messages, overlay, scroll, scroll_target, follow_bottom, perf_label
        if not chats:
            return
        selected = chats.pop(min(history_index, len(chats) - 1))
        archive_current()
        messages = [{"role": item["role"], "content": item["content"], "pending": False}
                    for item in selected["messages"]]
        save_history(messages)
        save_chats(chats)
        overlay = None
        scroll = scroll_target = 100000.0
        follow_bottom = True
        perf_label = ""

    def delete_selected_chat():
        nonlocal history_index, history_scroll
        if not chats:
            return
        chats.pop(min(history_index, len(chats) - 1))
        save_chats(chats)
        history_index = max(0, min(len(chats) - 1, history_index))
        history_scroll = max(0, min(history_scroll, max(0, len(chats) - HISTORY_VISIBLE)))
        park_history_cursor()

    def park_settings_cursor():
        nonlocal cursor_x, cursor_y, cursor_fx, cursor_fy
        x, y, width, height = settings.focus_rect()
        cursor_x, cursor_y = x + width // 2, y + height // 2
        cursor_fx, cursor_fy = float(cursor_x), float(cursor_y)
        SDL.SDL_WarpMouseInWindow(window, cursor_x, cursor_y)

    def toggle_settings():
        nonlocal overlay, settings_return, settings_cursor, settings_pointer
        nonlocal cursor_x, cursor_y, cursor_fx, cursor_fy
        if overlay == "settings":
            if settings.editing:
                settings.cancel_prompt()
            overlay = settings_return
            cursor_x, cursor_y, cursor_fx, cursor_fy = settings_cursor
            SDL.SDL_WarpMouseInWindow(window, cursor_x, cursor_y)
        else:
            if mic_state == "recording":
                stop_recording()
            stop_speaking()
            settings_return = overlay
            settings_cursor = (cursor_x, cursor_y, cursor_fx, cursor_fy)
            settings.open(getattr(engine, "profile", None) or engine.config.get("model", "fast"))
            overlay = "settings"
            park_settings_cursor()
        settings_pointer = False

    def reload_engine():
        nonlocal restart_pending, engine_state, model_label, perf_label, settings_return
        nonlocal restart_thread
        stop_speaking()
        restart_pending = True
        engine_state = "BOOTING"
        model_label = "RELOADING LOCAL MODEL"
        perf_label = ""
        if settings_return == "models":
            settings_return = None
        old_engine = engine
        old_engine.ready = False
        old_engine.progress = 0.0

        def stop_worker():
            try:
                old_engine.stop()
            except Exception as exc:
                events.put(("settings_restart_error", str(exc)))
            else:
                events.put(("settings_engine_stopped",))

        restart_thread = threading.Thread(target=stop_worker, daemon=True)
        restart_thread.start()

    def apply_setting(key, value):
        nonlocal background, model_index, capture_device
        reload_needed = (
            key == "model" and (engine_state != "READY" or
                               (getattr(engine, "profile", None) or engine.config.get("model")) != value)
            or key == "context" and value != engine.config.get("context", 768) and engine_state != "SELECT"
        )
        if reload_needed and (active_request is not None or mic_state != "idle" or restart_pending
                              or engine_state in ("BOOTING", "STOPPING")):
            settings.note = "Wait for loading, answering or dictation to finish."
            return False
        if key == "model" and not (MODELS / MODEL_PROFILES[value]["file"]).is_file():
            settings.note = "The selected model file is not available."
            return False
        replacement = None
        if key == "background" and value:
            replacement = load_background(renderer, ROOT / "intro-background.rgba",
                                          opacity=round(value * 255 / 100))
            if replacement is None:
                settings.note = "Background unavailable; previous setting retained."
                return False
        config = dict(engine.config)
        config[key] = value
        if not save_config(config):
            if replacement is not None:
                replacement.close()
            settings.note = "Could not save settings. Check the SD card."
            return False
        engine.config = config
        if key == "background":
            if background is not None:
                background.close()
            background = replacement
        elif key == "speech_rate":
            stop_speaking()
        elif key == "model":
            model_index = next((i for i, (name, _) in enumerate(model_choices) if name == value), 0)
        elif key == "audio_device":
            audio_devices = detect_capture_devices()
            capture_device = resolve_capture_device(audio_devices, value) or choose_capture_device(audio_devices)
            if capture_device:
                enable_capture_input(capture_device)
        if reload_needed:
            reload_engine()
        return True

    settings = SettingsPanel(model_choices, config=engine.config, on_change=apply_setting, preview=False,
                             audio_devices=lambda: detect_capture_devices(), on_mic=toggle_mic)

    try:
        while running:
            now = time.monotonic()
            frame_time = min(0.05, now - last_frame_time)
            last_frame_time = now
            while SDL.SDL_PollEvent(event_buffer):
                event_type = int.from_bytes(bytes(event_buffer[:4]), "little")
                if event_type == 0x100:
                    running = False
                    continue
                if event_type == 0x603 and event_buffer[12] == 7:
                    if intro.active:
                        intro.close()
                    toggle_settings()
                    continue
                if intro.active:
                    if event_type == 0x603 and event_buffer[12] in (0, 1, 13):
                        intro.close()
                    continue
                if event_type == 0x602:
                    hat = event_buffer[13]
                    if overlay == "settings":
                        button = next((button for bit, button in ((1, 8), (4, 9), (8, 10), (2, 11))
                                       if hat & bit), None)
                        if button is not None:
                            settings.controller_button(button)
                            settings_pointer = False
                            park_settings_cursor()
                    elif overlay == "models":
                        if hat & 1:
                            move_model(-1)
                        elif hat & 4:
                            move_model(1)
                    elif overlay == "history":
                        if hat & 1:
                            move_history(-1)
                        elif hat & 4:
                            move_history(1)
                    elif composing:
                        if hat & 8:
                            move_key(dx=-1)
                        elif hat & 2:
                            move_key(dx=1)
                        elif hat & 1:
                            move_key(dy=-1)
                        elif hat & 4:
                            move_key(dy=1)
                    elif hat & 1:
                        scroll_target = max(0, scroll_target - 72)
                        follow_bottom = False
                    elif hat & 4:
                        scroll_target += 72
                        follow_bottom = False
                    continue
                if event_type != 0x603:
                    continue
                button = event_buffer[12]
                # Debounce press times, not frame times: input can queue during a slow draw.
                button_time = int.from_bytes(bytes(event_buffer[4:8]), "little") or (int(now * 1000) & 0xFFFFFFFF)
                if button == last_button and ((button_time - last_button_time) & 0xFFFFFFFF) < 140:
                    continue
                last_button, last_button_time = button, button_time
                if overlay == "settings":
                    was_editing = settings.editing
                    if button == 1 and settings_pointer:
                        result = settings.click(cursor_x, cursor_y)
                    else:
                        if button in (8, 9, 10, 11):
                            settings_pointer = False
                        result = settings.controller_button(button)
                    if result == "close":
                        toggle_settings()
                    else:
                        if was_editing != settings.editing:
                            settings_pointer = False
                        if not settings_pointer:
                            park_settings_cursor()
                    continue
                if active_request is not None and button == 0:
                    engine.cancel()
                    engine_state = "STOPPING"
                    continue
                if overlay == "models":
                    if button in (1, 13):
                        confirm_model()
                    elif button in (0, 12):
                        dismiss_model_picker()
                    elif button == 8:
                        move_model(-1)
                    elif button == 9:
                        move_model(1)
                elif overlay == "history":
                    if button == 1:
                        open_selected_chat()
                    elif button in (0, 2):
                        close_history()
                    elif button == 3:
                        delete_selected_chat()
                    elif button == 8:
                        move_history(-1)
                    elif button == 9:
                        move_history(1)
                elif composing:
                    if button == 1:
                        activate_key()
                    elif button == 0:
                        if draft:
                            draft = draft[:-1]
                        else:
                            composing = False
                    elif button == 2:
                        send_message()
                    elif button == 3 and len(draft) < 500:
                        draft += " "
                    elif button == 8:
                        move_key(dy=-1)
                    elif button == 9:
                        move_key(dy=1)
                    elif button == 10:
                        move_key(dx=-1)
                    elif button == 11:
                        move_key(dx=1)
                else:
                    if button == 1:
                        if tts_state == "playing":
                            stop_speaking()
                        elif speak_hover >= 0:
                            speak_message(speak_hover)
                        elif engine_state == "READY":
                            composing = True
                    elif button == 0:
                        running = False
                    elif button == 3:
                        clear_chat()
                    elif button == 2:
                        open_history()
                    elif button == 8:
                        scroll_target = max(0, scroll_target - 72)
                        follow_bottom = False
                    elif button == 9:
                        scroll_target += 72
                        follow_bottom = False

            axis_x = axis_y = right_x = right_y = 0
            if controller:
                axis_x = SDL.SDL_GameControllerGetAxis(controller, 0)
                axis_y = SDL.SDL_GameControllerGetAxis(controller, 1)
                right_x = SDL.SDL_GameControllerGetAxis(controller, 2)
                right_y = SDL.SDL_GameControllerGetAxis(controller, 3)
            elif joystick:
                axis_x = SDL.SDL_JoystickGetAxis(joystick, 0)
                axis_y = SDL.SDL_JoystickGetAxis(joystick, 1)
                right_x = SDL.SDL_JoystickGetAxis(joystick, 2)
                right_y = SDL.SDL_JoystickGetAxis(joystick, 3)
            move_x = stick_motion(axis_x)
            move_y = stick_motion(axis_y)
            if overlay == "models":
                nav = stick_motion(axis_y, deadzone=11000)
                if nav and now - model_move_at > 0.16:
                    move_model(1 if nav > 0 else -1)
                    model_move_at = now
            elif overlay == "history":
                nav = stick_motion(axis_y, deadzone=11000)
                if nav and now - history_move_at > 0.16:
                    move_history(1 if nav > 0 else -1)
                    history_move_at = now
            elif move_x or move_y:
                if composing and overlay != "settings":
                    speed = 210 + 800 * max(abs(move_x), abs(move_y))
                    cursor_fx = max(14.0, min(float(W - 22),
                                              cursor_fx + move_x * speed * frame_time))
                    show_suggest = suggestions and mic_state == "idle"
                    floor_y = float(SUGGEST_TOP - 4) if show_suggest else float(KEY_TOP - 6)
                    cursor_fy = max(floor_y,
                                    min(float(KEY_TOP + (len(KEYBOARD) - 1) * KEY_STEP + KEY_HEIGHT + 6),
                                        cursor_fy + move_y * speed * frame_time))
                else:
                    speed = 260 + 1000 * max(abs(move_x), abs(move_y))
                    cursor_fx = max(4.0, min(float(W - 22),
                                              cursor_fx + move_x * speed * frame_time))
                    cursor_fy = max(float(HEADER_H + 4), min(float(H - 6),
                                                             cursor_fy + move_y * speed * frame_time))
                cursor_x, cursor_y = round(cursor_fx), round(cursor_fy)
                SDL.SDL_WarpMouseInWindow(window, cursor_x, cursor_y)
                if overlay == "settings":
                    settings_pointer = True
                    settings.hover(cursor_x, cursor_y)
                elif composing:
                    sync_key_to_cursor()
            if not composing and not overlay:
                scroll_motion = stick_motion(right_y, deadzone=6000)
                if scroll_motion:
                    scroll_target += scroll_motion * (170 + 520 * abs(scroll_motion)) * frame_time
                    follow_bottom = False

            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                kind = event[0]
                if kind == "engine_status":
                    model_label = event[1]
                elif kind == "engine_ready":
                    model_label = event[1]
                    engine_state = "READY"
                    if len(event) > 2:
                        settings.values["model"] = event[2]
                        model_index = next((i for i, (name, _) in enumerate(model_choices) if name == event[2]), 0)
                elif kind == "engine_error":
                    engine_state = "ERROR"
                    model_label = event[1]
                    settings.note = "Model could not start. Select another installed model."
                elif kind == "settings_engine_stopped":
                    try:
                        engine = LocalEngine(events)
                        engine.start()
                    except Exception as exc:
                        engine_state = "ERROR"
                        model_label = "MODEL RESTART FAILED"
                        settings.note = str(exc)[:80]
                    restart_pending = False
                elif kind == "settings_restart_error":
                    restart_pending = False
                    engine_state = "ERROR"
                    model_label = "MODEL STOP FAILED"
                    settings.note = event[1][:80]
                elif kind == "delta" and event[1] == active_request:
                    messages[-1]["content"] += event[2]
                    messages[-1]["pending"] = True
                elif kind == "done" and event[1] == active_request:
                    messages[-1]["pending"] = False
                    if not messages[-1]["content"].strip():
                        messages[-1]["content"] = "No answer was generated. Please try another wording."
                    timings = event[2]
                    speed = timings.get("predicted_per_second")
                    perf_label = f"{float(speed):.1f} tok/s" if speed else "LOCAL"
                    active_request = None
                    engine_state = "READY"
                    save_history(messages)
                elif kind == "cancelled" and event[1] == active_request:
                    if messages[-1].get("pending"):
                        messages[-1]["pending"] = False
                        messages[-1]["content"] = messages[-1]["content"].rstrip() or "Generation stopped."
                    active_request = None
                    engine_state = "READY"
                    save_history(messages)
                elif kind == "generation_error" and event[1] == active_request:
                    messages[-1]["pending"] = False
                    messages[-1]["content"] = "Local inference failed: " + event[2][:100]
                    active_request = None
                    engine_state = "READY" if engine.ready else "ERROR"
                    save_history(messages)
                elif kind == "mic_text":
                    mic_state = "idle"
                    if event[1]:
                        if overlay == "settings" and settings.editing:
                            settings.text_input(event[1])
                        else:
                            insert_transcript(event[1])
                    else:
                        mic_note = "HEARD NOTHING"
                        mic_note_time = now
                elif kind == "mic_error":
                    mic_state = "idle"
                    mic_note = "VOICE ERROR"
                    mic_note_time = now
                elif kind == "tts_ready":
                    if event[1] != tts_token:
                        event[3].unlink(missing_ok=True)
                        continue
                    tts_path = event[3]
                    tts_timings = event[4]
                    for device in event[5]:
                        try:
                            player = subprocess.Popen(
                                ["aplay", "-q", "-D", device, str(tts_path)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
                            if tts_started is None:
                                tts_started = time.monotonic()
                            tts_players.append(player)
                        except OSError:
                            continue
                    if not tts_players:
                        stop_speaking()
                elif kind == "tts_done" and event[1] == tts_token:
                    stop_speaking()

            if tts_started is not None:
                if all(player.poll() is not None for player in tts_players):
                    stop_speaking()
                else:
                    elapsed = time.monotonic() - tts_started
                    while (tts_position + 1 < len(tts_timings)
                           and tts_timings[tts_position + 1][0] <= elapsed):
                        tts_position += 1
                    word = tuple(tts_timings[tts_position][1:]) if tts_position >= 0 else None
                    if word is not None and word != tts_word:
                        tts_word = word
                        content = messages[tts_index]["content"]
                        indexed = message_lines_indexed(body_font, content, SPEAK_GUTTER)
                        row = next((i for i, (text, start) in enumerate(indexed)
                                    if start <= word[0] < start + len(text)), 0)
                        top = sum(message_layout(body_font, item)[2] + MSG_GAP
                                  for item in messages[:tts_index]) + row * LINE_H
                        visible = viewport_metrics(composing)[3]
                        if top < scroll_target:
                            scroll_target = top
                        elif top + LINE_H > scroll_target + visible:
                            scroll_target = top + LINE_H - visible

            if mic_state == "recording" and now - mic_started > MIC_MAX_SECONDS:
                stop_recording()
            if mic_state == "transcribing" and now - mic_transcribe_started > 200:
                mic_log("watchdog: transcription timed out; resetting mic")
                mic_state = "idle"
                mic_note = "VOICE ERROR"
                mic_note_time = now
            if mic_note and now - mic_note_time > 3.0:
                mic_note = ""
            if now - last_metrics > 2:
                ram_label, temperature_label = read_device_metrics()
                last_metrics = now
            viewport_height = viewport_metrics(composing)[3]
            maximum_scroll = max(0.0, content_height() - viewport_height)
            if follow_bottom:
                scroll_target = maximum_scroll
            scroll_target = max(0.0, min(scroll_target, maximum_scroll))
            scroll_delta = scroll_target - scroll
            if abs(scroll_delta) < 0.5:
                scroll = scroll_target
            else:
                scroll += scroll_delta * min(1.0, frame_time * 18)
                scroll = max(0.0, min(scroll, maximum_scroll))
            if engine_state == "BOOTING":
                boot_progress += (engine.progress - boot_progress) * min(1.0, frame_time * 9.0)
            else:
                boot_progress = 0.0
            if now - last_scramble_time >= SCRAMBLE_TICK:
                elapsed = now - last_scramble_time
                last_scramble_time = now
                scramble_salt += 1
                for item in messages:
                    advance_scramble(item, elapsed)
            if draft != suggest_source:
                suggest_source = draft
                suggestions = word_suggestions(draft)
                if suggest_index >= len(suggestions):
                    suggest_index = 0
                if (not suggestions or mic_state != "idle") and suggest_focus:
                    suggest_focus = False
                    park_key_cursor()
            if overlay == "settings":
                settings.mic_state = mic_state
                settings.update()
            elif settings.mic_test.state != "idle":
                settings.mic_test.cancel()
            speak_hover = hovered_speak_index()
            message_state = tuple((item["role"], item.get("content", ""),
                                   bool(item.get("pending")), item.get("reveal"))
                                  for item in messages)
            scrambling = any(reveal is not None and reveal < len(content)
                             for _, content, _, reveal in message_state)
            waiting = any(pending and not content for _, content, pending, _ in message_state)
            blink_on = int(now * 2) % 2 == 0
            render_key = (
                message_state, int(scroll), composing, draft, key_row, key_col,
                engine_state, model_label, perf_label, ram_label, temperature_label,
                cursor_x, cursor_y, blink_on, overlay, history_index, history_scroll,
                tuple((entry["title"], entry["updated"], len(entry["messages"])) for entry in chats),
                int(boot_progress * 1000) if overlay != "settings" else 0,
                scramble_salt if scrambling or mic_state == "transcribing" else None,
                (scramble_salt // 8) % 3 if waiting else None,
                scramble_salt % 2 if tts_index >= 0 else None,
                tuple(suggestions), suggest_focus, suggest_index, mic_state,
                int(now - mic_started) if mic_state == "recording" else 0,
                mic_note, speak_hover, tts_index, tts_word, model_index,
                settings.render_key() if overlay == "settings" else None,
            )
            if intro.active or render_key != last_render_key:
                draw_ui(renderer, fonts, cache, messages, scroll, composing, draft,
                        key_row, key_col, engine_state, model_label, perf_label,
                        ram_label, temperature_label, cursor_x, cursor_y,
                        logo_texture, blink_on,
                        overlay, chats, history_index, history_scroll, boot_progress,
                        scramble_salt, suggestions, suggest_focus, suggest_index,
                        mic_state, (now - mic_started) if mic_state == "recording" else 0.0,
                        mic_note, speak_hover, tts_index, model_choices, model_index,
                        tts_word if engine.config.get("highlight", True) else None,
                        intro=intro, background=background, settings=settings)
                last_render_key = None if intro.active else render_key
            period = 1.0 / (24 if intro.active else 30)
            remaining = period - (time.monotonic() - now)
            SDL.SDL_Delay(max(1, int(remaining * 1000)))
    finally:
        if restart_thread is not None and restart_thread.is_alive():
            restart_thread.join(timeout=7)
        intro.close()
        if mic_proc and mic_proc.poll() is None:
            mic_proc.terminate()
        stop_speaking()
        engine.stop()
        save_history(messages)
        cache.close()
        if background:
            background.close()
        if logo_texture:
            SDL.SDL_DestroyTexture(logo_texture)
        if controller:
            SDL.SDL_GameControllerClose(controller)
        if joystick:
            SDL.SDL_JoystickClose(joystick)
        for font in fonts:
            TTF.TTF_CloseFont(font)
        SDL.SDL_DestroyRenderer(renderer)
        SDL.SDL_DestroyWindow(window)
        TTF.TTF_Quit()
        SDL.SDL_Quit()
    return 0


if __name__ == "__main__":
    sys.modules["project_ai_r36s"] = sys.modules[__name__]
    if len(sys.argv) == 4 and sys.argv[1] == "--tts-synthesize":
        print(json.dumps(synthesize_speech(sys.argv[3], Path(sys.argv[2]))))
    else:
        raise SystemExit(main())
