#!/usr/bin/env python3
"""Render the production UI to a local, scripted website walkthrough.

No model, microphone, audio playback, device discovery, network or persistence
is used. --validate-only prints the plan without importing SDL or writing files.
"""

import argparse
import ctypes
import ctypes.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import wave
import zipfile


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DURATION = 78
WIDTH, HEIGHT = 640, 480
QUESTION = "what can you do?"
REPLY = ("I can help with short questions, draft text, and explain ideas. "
         "I run locally on this handheld. This is a scripted demo, not a live AI answer.")
DICTATION = "explain local AI"
SECOND_REPLY = ("Local AI runs on your own device, without sending your questions "
                "to a cloud service. This answer is scripted sample text.")
MIC_PLAYBACK = "This is a microphone test. Your voice stays on the device."
CHAPTERS = (
    ("intro", "Original Intro", 0, 2,
     "The production 19-frame intro, including its original fade into frame 19."),
    ("loading", "Load LFM", 2, 4,
     "Two-second illustrative loading progress for the release-default LFM model; not a benchmark."),
    ("keyboard", "Type and Send", 4, 12,
     "Type 'what can you do?' on the production on-screen keyboard and explicitly select SEND."),
    ("answer", "Sample Reply", 12, 20,
     "Predefined sample text streams through the production reply renderer; no model runs."),
    ("read-aloud", "Read Aloud", 20, 36,
     "Select the speaker glyph and follow word highlighting; offline speech when available, otherwise silent."),
    ("settings", "USB Microphone Test", 36, 50,
     "R2 opens settings; navigate to VOICE, select USB, then simulate four seconds each of recording and playback, followed by a verdict. No microphone access."),
    ("dictation", "Dictate and Review", 50, 64,
     "Select MIC, simulate recording and transcription, review the draft, then explicitly SEND a second sample question."),
    ("history", "Reopen History", 64, 73,
     "Open in-memory sample history, navigate between entries, and reopen the walkthrough conversation."),
    ("outro", "Close the App", 73, 78,
     "Pause on the reopened chat, then show STOP and fade out as the scripted app closes."),
)


def timeline(fps=20):
    """Pure, read-only plan shared by the renderer and seek metadata."""
    return {
        "duration": DURATION, "fps": fps, "width": WIDTH, "height": HEIGHT,
        "hasAudio": False,
        "chapters": [dict(id=id_, title=title, start=start, end=end,
                          description=description)
                     for id_, title, start, end, description in CHAPTERS],
        "simulatedMic": True, "sampleReplies": True, "localPC": True,
        "productionRenderer": True, "model": "fast", "liveModel": False,
        "microphoneAccess": False, "deviceAccess": False,
        "speechBackend": "none", "highlightTiming": "approximate-silent",
        "video": "227-ai-walkthrough.mp4", "captions": "227-ai-walkthrough.vtt",
    }


def actions():
    result = [(4.4, "compose", None)]
    result.extend((5.0 + i * 0.30, "key", "SPACE" if c == " " else c)
                  for i, c in enumerate(QUESTION))
    result.extend([
        (11.4, "key", "SEND"), (20.0, "speak", None),
        (36.0, "settings", None),
        (37.0, "button", 9), (37.5, "button", 9), (38.0, "button", 9),
        (39.0, "usb", None), (40.0, "test", None),
        (50.0, "close-settings", None), (50.5, "compose", None),
        (51.0, "key", "MIC"), (55.0, "key", "MIC"),
        (57.0, "transcript", DICTATION), (60.0, "key", "SEND"),
        (64.0, "history", None), (66.0, "history-down", None),
        (68.0, "history-up", None), (70.0, "reopen", None),
        (76.0, "exit", None),
    ])
    return result


class SimulatedMicTester:
    """SettingsPanel's MicTester interface, with no capture or playback I/O."""

    def __init__(self, clock, sample_stats=None):
        self.clock = clock
        self.sample_stats = sample_stats or {"peak": 6400, "rms": 1250.0, "cross": 780.0, "dur": 4.0}
        self.cancel()

    def busy(self):
        return self.state in ("recording", "playing")

    def start(self, device):
        if self.busy() or device is None:
            return False
        self.cancel()
        self.device = device
        self.started = self.clock.now
        self.state = "recording"
        return True

    def update(self):
        if self.state == "idle":
            return
        elapsed = self.clock.now - self.started
        self.progress = min(1.0, elapsed / 4.0)
        if elapsed >= 4:
            self.stats = dict(self.sample_stats)
            self.state = "playing" if elapsed < 8 else "done"

    def status(self):
        if self.state == "recording":
            return "RECORDING {}s ...".format(math.ceil(4 * (1 - self.progress)))
        if self.state == "playing":
            return "PLAYING BACK ..."
        if self.state == "done":
            return "SPEECH OK  {}  {}/s".format(self.stats["peak"], round(self.stats["cross"]))
        return "PRESS A TO RECORD"

    def cancel(self):
        self.state, self.progress, self.stats = "idle", 0.0, None
        self.device, self.error, self.started = None, "", 0.0


def speech(app, directory, speech_root=None, text=REPLY, stem="speech"):
    """Synthesize PCM only, never open an audio device. Silence is a valid fallback."""
    wav = directory / (stem + ".wav")
    if speech_root:
        environment = os.environ.copy()
        environment["LD_LIBRARY_PATH"] = str(speech_root / "usr/lib") + (
            os.pathsep + environment["LD_LIBRARY_PATH"] if environment.get("LD_LIBRARY_PATH") else "")
        environment["ESPEAK_DATA_PATH"] = str(speech_root / "usr/share/espeak-ng-data")
        # The dynamic loader needs LD_LIBRARY_PATH at process startup. Production
        # synthesis uses synchronous PCM retrieval, not eSpeak's playback mode.
        worker = (
            "import json,sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); import project_ai_r36s as app; "
            "app.read_config=lambda: {'speech_rate': 1.0}; "
            "print(json.dumps(app.synthesize_speech(sys.argv[3], Path(sys.argv[2]))))"
        )
        result = subprocess.run([sys.executable, "-c", worker, str(ROOT / "app"), str(wav), text],
                                env=environment, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError("Explicit speech root failed: " + result.stderr)
        timings = json.loads(result.stdout)
        if not timings:
            raise RuntimeError("Explicit speech root returned no word events")
        return wav, timings, "espeak-ng", "speech-word-events"
    library = ctypes.util.find_library("espeak-ng")
    if library:
        # Reuse production synthesis and its real word events, without user config.
        with patch.object(app, "read_config", return_value={"speech_rate": 1.0}):
            try:
                timings = app.synthesize_speech(text, wav)
                if not timings:
                    raise RuntimeError("Speech engine returned no word events")
                return wav, timings, "espeak-ng", "speech-word-events"
            except (OSError, RuntimeError, wave.Error) as exc:
                print("Offline eSpeak unavailable: {}".format(exc), file=sys.stderr)
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                             capture_output=True, text=True, check=True).stdout
    if re.search(r"\bflite\s", filters):
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "flite=text='{}'".format(text),
                            "-c:a", "pcm_s16le", str(wav)], check=True, timeout=60)
            with wave.open(str(wav), "rb") as handle:
                length = handle.getnframes() / handle.getframerate()
            return wav, approximate_timings(length, text), "ffmpeg-flite", "approximate-audio"
        except (subprocess.SubprocessError, OSError, wave.Error) as exc:
            print("Offline flite unavailable: {}".format(exc), file=sys.stderr)
    return None, approximate_timings(14.4, text), "none", "approximate-silent"


def approximate_timings(duration, text=REPLY):
    words = list(re.finditer(r"\S+", text))
    weights = [max(2, len(word.group())) for word in words]
    elapsed, total = 0.0, sum(weights)
    timings = []
    for word, weight in zip(words, weights):
        timings.append((elapsed, word.start(), word.end()))
        elapsed += duration * weight / total
    return timings


def fit_speech(wav, timings, limit):
    with wave.open(str(wav), "rb") as handle:
        length = handle.getnframes() / handle.getframerate()
    if length > limit:
        factor = length / limit
        fitted = wav.with_name(wav.stem + "-fit.wav")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(wav), "-af", "atempo={}".format(factor),
                        str(fitted)], check=True, timeout=60)
        wav = fitted
        timings = [(moment / factor, start, end) for moment, start, end in timings]
        with wave.open(str(wav), "rb") as handle:
            length = handle.getnframes() / handle.getframerate()
    if length > limit + 0.1:
        raise RuntimeError("Speech does not fit its fixed timeline window")
    return wav, timings, length


def captions(metadata, timings, speech_end):
    def stamp(seconds):
        millis = round(seconds * 1000)
        return "{:02d}:{:02d}:{:02d}.{:03d}".format(
            millis // 3600000, millis // 60000 % 60, millis // 1000 % 60, millis % 1000)

    cues = [(c["start"], c["end"], c["title"] + ": " + c["description"])
            for c in metadata["chapters"] if c["id"] not in ("read-aloud", "settings")]
    if metadata.get("prerecordedMicPlayback"):
        cues.extend([
            (36, 40, "R2: VOICE settings. Select USB and TEST MICROPHONE. No real microphone access."),
            (40, 44, "VISUAL DEMO: simulated recording. Nothing is captured."),
            (44, 48, "Prerecorded synthetic sample, not live microphone audio: " + MIC_PLAYBACK),
            (48, 50, "VISUAL DEMO: verdict uses the prerecorded sample's waveform, not a microphone."),
        ])
    else:
        chapter = next(c for c in metadata["chapters"] if c["id"] == "settings")
        cues.append((36, 50, chapter["title"] + ": " + chapter["description"]))
    cues.append((20, 20.5, "Read aloud: " + ("offline synthesized speech."
                 if metadata["hasAudio"] else "silent visual highlighting; no speech engine available.")))
    # Short phrases share the same source word timestamps as the green highlight.
    for i in range(0, len(timings), 7):
        group = timings[i:i + 7]
        end = 20.5 + timings[i + 7][0] if i + 7 < len(timings) else speech_end
        cues.append((20.5 + group[0][0], end, REPLY[group[0][1]:group[-1][2]]))
    return "WEBVTT\n\n" + "\n\n".join(
        "{} --> {}\n{}".format(stamp(start), stamp(end), text)
        for start, end, text in sorted(cues)) + "\n"


def render(directory, fps, metadata, app, settings_module, speech_root=None):
    clock = SimpleNamespace(now=0.0)
    # Replace only these modules' time references, not Python's global clock.
    fake_time = SimpleNamespace(monotonic=lambda: clock.now, time=lambda: 1700000000 + clock.now)
    window = renderer = cache = logo = background = intro = None
    fonts = []
    initialized = ttf_initialized = False
    encoder = None
    wav, timings, backend, timing_kind = speech(app, directory, speech_root)
    speech_length = 14.4
    mic_wav = mic_stats = None
    if wav:
        wav, timings, speech_length = fit_speech(wav, timings, 14.4)
        mic_wav, mic_timings, _, _ = speech(app, directory, speech_root, MIC_PLAYBACK, "mic-sample")
        if mic_wav is None:
            raise RuntimeError("Speech backend could not synthesize the mic playback sample")
        mic_wav, _, _ = fit_speech(mic_wav, mic_timings, 3.8)
        padded = directory / "mic-playback.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(mic_wav), "-af", "apad,atrim=duration=4", str(padded)],
                       check=True, timeout=60)
        mic_wav = padded
        mic_stats = settings_module.analyze_wav(mic_wav)
        if settings_module.verdict(mic_stats) != "SPEECH OK":
            raise RuntimeError("Synthesized mic sample did not pass waveform analysis")
    metadata.update(hasAudio=wav is not None, speechBackend=backend, highlightTiming=timing_kind)
    metadata["speechDependencySource"] = "temporary-user-local" if speech_root else ("system" if wav else "none")
    metadata["prerecordedMicPlayback"] = mic_wav is not None
    if mic_wav:
        metadata["micPlayback"] = {"start": 44, "end": 48, "text": MIC_PLAYBACK,
                                   "source": "prerecorded-offline-synthesis", "liveCapture": False,
                                   "stats": mic_stats}
    speech_end = 20.5 + speech_length
    metadata["readAloud"] = {"start": 20.5, "end": round(speech_end, 4),
                              "wordTimings": [[round(20.5 + t, 4), a, b] for t, a, b in timings]}
    if not wav:
        print("Audio: no eSpeak-ng library or FFmpeg flite; rendering a silent visual demo.", flush=True)
    else:
        print("Audio: offline {} PCM, {:.2f}s.".format(backend, speech_length), flush=True)

    def forbidden(*args, **kwargs):
        raise RuntimeError("Walkthrough attempted forbidden device/model/persistence access")

    # Fail closed if a future renderer unexpectedly grows side effects.
    from contextlib import ExitStack
    with ExitStack() as guards:
        guards.enter_context(patch.object(app, "time", fake_time))
        guards.enter_context(patch.object(settings_module, "time", fake_time))
        for name in ("LocalEngine", "detect_capture_devices", "enable_capture_input",
                     "save_config", "save_chats", "save_history"):
            if hasattr(app, name):
                guards.enter_context(patch.object(app, name, forbidden))
        guards.enter_context(patch.object(settings_module, "MicTester",
                                         lambda: SimulatedMicTester(clock, mic_stats)))
        try:
            app.ROOT = ASSETS
            app.WORDS_FILE = ASSETS / "words.txt"
            app.WORD_LIST = app.load_words()
            app.SUGGEST_CACHE.clear()
            if app.SDL.SDL_Init(0x20) != 0:
                raise RuntimeError(app.SDL.SDL_GetError().decode())
            initialized = True
            if app.TTF.TTF_Init() != 0:
                raise RuntimeError("Cannot initialize SDL_ttf")
            ttf_initialized = True
            app.SDL.SDL_RenderReadPixels.argtypes = [app.c_void_p, app.c_void_p, app.c_uint32,
                                                    app.c_void_p, app.c_int]
            app.SDL.SDL_RenderSetLogicalSize.argtypes = [app.c_void_p, app.c_int, app.c_int]
            window = app.SDL.SDL_CreateWindow(b"227 offline walkthrough", 0, 0, WIDTH, HEIGHT, 8)
            renderer = app.SDL.SDL_CreateRenderer(window, -1, 1) if window else None
            if not renderer:
                raise RuntimeError("Cannot create dummy SDL software renderer")
            app.SDL.SDL_RenderSetLogicalSize(renderer, WIDTH, HEIGHT)
            for name, size in (("DejaVuSansMono-Bold.ttf", 16), ("DejaVuSansMono.ttf", 14),
                               ("DejaVuSansMono.ttf", 11), ("DejaVuSansMono-Bold.ttf", 15)):
                font = app.TTF.TTF_OpenFont(str(ASSETS / name).encode(), size)
                if not font:
                    raise RuntimeError("Cannot load preview font: " + name)
                fonts.append(font)
            cache = app.TextCache(renderer)
            logo = app.load_logo(renderer)
            background = app.load_background(renderer, ASSETS / "intro-background.rgba")
            intro = app.IntroPlayer(renderer, ASSETS)
            if not logo or not background or not intro.active or len(intro.textures) != 19:
                raise RuntimeError("Missing production logo, frame-19 background or 19-frame intro")
            if intro.duration > 2:
                raise RuntimeError("Production intro no longer fits the fixed timeline")
            choices = [("fast", app.MODEL_PROFILES["fast"])]
            usb = {"id": "demo-usb", "label": "USB AUDIO", "card": 1,
                   "arg": "SIMULATED-NOT-AN-ALSA-DEVICE", "node": True, "usb": True}
            settings = settings_module.SettingsPanel(choices, audio_devices=lambda: [usb])
            messages = []
            chats = []
            draft, composing, overlay, mic_state = "", False, None, "idle"
            key_row = key_col = history_index = 0
            reply_started = None
            mic_started = 0.0
            stopping = False
            events = actions()
            event_index = 0
            last_point, last_click = (560, 440), -10.0

            def key_location(key):
                for row, keys in enumerate(app.KEYBOARD):
                    if key in keys:
                        col = keys.index(key)
                        x, width = app.key_geometry(row)[col]
                        return row, col, (x + width // 2, app.KEY_TOP + row * app.KEY_STEP + 14)
                raise ValueError("No production keyboard key: " + key)

            def target(event):
                _, kind, value = event
                if kind == "key":
                    return key_location(value)[2]
                if kind == "compose":
                    return (310, 439)
                if kind == "speak":
                    return (app.MSG_RIGHT - 7, app.FRAME_TOP + app.CHAT_PAD + app.LINE_H + app.MSG_GAP + 8)
                if kind == "button":
                    return (100, settings_module.NAV_Y + (settings.page + 1) * settings_module.NAV_STEP + 20)
                if kind in ("usb", "test"):
                    rect = settings.row_rect(2 if kind == "usb" else 3)
                    return (rect[0] + rect[2] - 20, rect[1] + 23)
                if kind in ("history-down", "history-up", "reopen"):
                    index = 1 if kind == "history-down" else 0
                    return (210, app.HISTORY_LIST_TOP + index * app.HISTORY_ROW_H + 15)
                if kind == "history":
                    return (275, 467)
                if kind == "exit":
                    return (480, 467)
                if kind in ("settings", "close-settings"):
                    return (570, 30)
                return last_point

            video = directory / metadata["video"]
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                       "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", "640x480",
                       "-framerate", str(fps), "-i", "pipe:0"]
            if wav:
                command += ["-i", str(wav), "-i", str(mic_wav), "-filter_complex",
                            "[1:a]adelay=20500:all=1[s];[2:a]adelay=44000:all=1[m];"
                            "[s][m]amix=inputs=2:normalize=0:dropout_transition=0,apad[a]",
                            "-map", "0:v", "-map", "[a]",
                            "-c:a", "aac", "-b:a", "48k"]
            else:
                command += ["-an"]
            command += ["-c:v", "libx264", "-preset", "medium", "-crf", "25",
                        "-pix_fmt", "yuv420p", "-threads", "2", "-t", str(DURATION),
                        "-movflags", "+faststart", "-f", "mp4", str(video)]
            pixels = (ctypes.c_uint8 * (WIDTH * HEIGHT * 4))()
            next_progress = 0
            intro_frames = set()
            # Production draw_ui presents internally. Defer that one call so cursor
            # rings and the closing fade are captured in the same SDL backbuffer.
            present = app.SDL.SDL_RenderPresent
            with (directory / "ffmpeg.log").open("wb") as log:
                encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log,
                                           stdout=subprocess.DEVNULL)
                with patch.object(app.SDL, "SDL_RenderPresent", lambda renderer: None):
                    for frame in range(DURATION * fps):
                        now = clock.now = frame / fps
                        while event_index < len(events) and events[event_index][0] <= now + 1e-8:
                            event = events[event_index]
                            moment, kind, value = event
                            last_point, last_click = target(event), moment
                            event_index += 1
                            if kind == "compose":
                                composing = True
                            elif kind == "key":
                                key_row, key_col, _ = key_location(value)
                                if value == "SEND":
                                    if not draft.strip() or mic_state != "idle":
                                        raise RuntimeError("Invalid scripted SEND")
                                    messages += [{"role": "user", "content": draft},
                                                 {"role": "assistant", "content": "", "pending": True}]
                                    draft, composing, reply_started = "", False, moment
                                elif value == "MIC":
                                    mic_state = "transcribing" if mic_state == "recording" else "recording"
                                    mic_started = moment
                                else:
                                    draft += " " if value == "SPACE" else value
                            elif kind == "settings":
                                settings.open("fast")  # Host's R2 handler, as in the PC preview.
                                overlay = "settings"
                            elif kind == "button":
                                settings.controller_button(value)
                            elif kind in ("usb", "test"):
                                settings.click(*last_point)
                            elif kind == "close-settings":
                                if settings.controller_button(0) != "close":
                                    raise RuntimeError("Settings did not close on B")
                                overlay = None
                            elif kind == "transcript":
                                if mic_state != "transcribing":
                                    raise RuntimeError("Transcript arrived before transcription")
                                draft, mic_state = value, "idle"
                            elif kind == "history":
                                chats = [
                                    {"title": QUESTION, "updated": 1700000063, "messages": list(messages)},
                                    {"title": "sample: a short note", "updated": 1699996400,
                                     "messages": [{"role": "user", "content": "draft a short note"},
                                                  {"role": "assistant", "content": "Sample saved note: see you tomorrow."}]},
                                ]
                                overlay, history_index = "history", 0
                            elif kind == "history-down":
                                history_index = min(len(chats) - 1, history_index + 1)
                            elif kind == "history-up":
                                history_index = max(0, history_index - 1)
                            elif kind == "reopen":
                                messages, overlay = list(chats[history_index]["messages"]), None
                            elif kind == "exit":
                                stopping = True

                        if reply_started is not None:
                            second = reply_started > 50
                            text = SECOND_REPLY if second else REPLY
                            delay, span = (0.6, 2.8) if second else (0.8, 6.5)
                            fraction = min(1.0, max(0.0, (now - reply_started - delay) / span))
                            if fraction > 0:
                                messages[-1] = {"role": "assistant", "content": text[:int(len(text) * fraction)],
                                                "pending": fraction < 1}
                        settings.update()
                        if overlay == "settings":
                            if mic_wav and settings.mic_test.state in ("playing", "done"):
                                settings.note = "VISUAL DEMO / prerecorded sample / no mic access"
                            else:
                                settings.note = "VISUAL DEMO / no mic access: " + settings.mic_test.status()

                        cursor = last_point
                        if event_index < len(events):
                            upcoming = events[event_index]
                            travel = max(0.0, min(1.0, (now - upcoming[0] + 0.45) / 0.45))
                            travel = travel * travel * (3 - 2 * travel)
                            destination = target(upcoming)
                            cursor = tuple(round(a + (b - a) * travel) for a, b in zip(last_point, destination))
                            if composing and upcoming[1] == "key" and travel > 0.65:
                                key_row, key_col, _ = key_location(upcoming[2])
                        tts_word = None
                        speaking = 20.5 <= now < speech_end
                        if speaking:
                            for moment, start, end in timings:
                                if 20.5 + moment > now:
                                    break
                                tts_word = (start, end)
                        visible = app.viewport_metrics(composing)[3]
                        height = sum(app.message_layout(fonts[1], item)[2] + app.MSG_GAP for item in messages)
                        scroll = max(0, height - visible) if composing else 0
                        state = "STOPPING" if stopping else ("BOOTING" if 2 <= now < 4 else "READY")
                        app.draw_ui(renderer, fonts, cache, messages, scroll, composing, draft,
                                    key_row, key_col, state, choices[0][1]["label"],
                                    "R2 SETTINGS" if 35 <= now < 36 else "", "", "", *cursor,
                                    logo, int(now * 2) % 2 == 0, overlay=overlay,
                                    chats=chats, history_index=history_index,
                                    boot_fraction=min(1.0, max(0.0, (now - 2) / 1.95)),
                                    scramble_salt=int(now / app.SCRAMBLE_TICK),
                                    suggestions=app.word_suggestions(draft) if composing else [],
                                    mic_state=mic_state, mic_seconds=now - mic_started,
                                    tts_index=1 if speaking else -1, tts_word=tts_word,
                                    models=choices, model_index=0, intro=intro,
                                    background=background, settings=settings)
                        if intro.active:
                            intro_frames.add(intro.frame_index)
                        if now >= 4:
                            if overlay == "history":
                                app.draw_cursor(renderer, *cursor)
                            age = now - last_click
                            if 0 <= age < 0.4:
                                radius = 5 + age * 32
                                color = app.GREEN if age < 0.2 else app.GREEN_DIM
                                for segment in range(32):
                                    a, b = segment * math.tau / 32, (segment + 1) * math.tau / 32
                                    app.line(renderer, last_point[0] + radius * math.cos(a),
                                             last_point[1] + radius * math.sin(a),
                                             last_point[0] + radius * math.cos(b),
                                             last_point[1] + radius * math.sin(b), color)
                        if now >= 77:
                            app.SDL.SDL_SetRenderDrawBlendMode.argtypes = [app.c_void_p, app.c_int]
                            app.SDL.SDL_SetRenderDrawBlendMode(renderer, 1)
                            app.fill(renderer, 0, 0, WIDTH, HEIGHT,
                                     (0, 0, 0, round(255 * min(1.0, (now - 77) / 0.8))))
                            app.SDL.SDL_SetRenderDrawBlendMode(renderer, 0)
                        if app.SDL.SDL_RenderReadPixels(renderer, None, 0x16762004, pixels, WIDTH * 4) != 0:
                            raise RuntimeError(app.SDL.SDL_GetError().decode())
                        present(renderer)
                        encoder.stdin.write(bytes(pixels))
                        progress = frame * 100 // (DURATION * fps)
                        if progress >= next_progress:
                            print("Render {}% ({:.1f}s / {}s)".format(next_progress, now, DURATION), flush=True)
                            next_progress += 20
                encoder.stdin.close()
                if encoder.wait(timeout=120) != 0:
                    raise RuntimeError("FFmpeg failed: " + (directory / "ffmpeg.log").read_text())
            metadata["introFramesRendered"] = len(intro_frames)
            if fps >= 15 and len(intro_frames) != 19:
                raise RuntimeError("Not all original intro frames were rendered")
            (directory / metadata["captions"]).write_text(captions(metadata, timings, speech_end), encoding="utf-8")
            print("Render 100% ({}s / {}s)".format(DURATION, DURATION), flush=True)
        except BrokenPipeError as exc:
            raise RuntimeError("FFmpeg closed its input: " + (directory / "ffmpeg.log").read_text()) from exc
        finally:
            if encoder is not None:
                if encoder.stdin and not encoder.stdin.closed:
                    try:
                        encoder.stdin.close()
                    except BrokenPipeError:
                        pass
                if encoder.poll() is None:
                    encoder.terminate()
                    try:
                        encoder.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        encoder.kill()
                        encoder.wait()
            if intro:
                intro.close()
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


def validate_video(path, fps, has_audio):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    numerator, denominator = map(int, video["avg_frame_rate"].split("/"))
    if (video["codec_name"] != "h264" or video["pix_fmt"] != "yuv420p"
            or (video["width"], video["height"]) != (WIDTH, HEIGHT)
            or numerator / denominator != fps or bool(audio) != has_audio
            or (has_audio and (len(audio) != 1 or audio[0]["codec_name"] != "aac"
                               or abs(float(audio[0]["duration"]) - DURATION) > 0.1))
            or abs(float(info["format"]["duration"]) - DURATION) > 1 / fps
            or int(video["nb_frames"]) != DURATION * fps):
        raise RuntimeError("ffprobe validation failed: " + result.stdout)
    return int(info["format"]["size"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/media/previews")
    parser.add_argument("--fps", type=int, choices=(15, 20), default=20)
    parser.add_argument("--speech-root", type=Path, default=os.environ.get("227_DEMO_SPEECH_ROOT"),
                        help="Extracted eSpeak-ng package root (or 227_DEMO_SPEECH_ROOT); explicit roots require working speech")
    parser.add_argument("--quick", action="store_true", help="Render all 78 seconds at 5 fps for smoke tests")
    parser.add_argument("--validate-only", action="store_true", help="Print the read-only timeline plan; no SDL, speech or output writes")
    args = parser.parse_args(argv)
    fps = 5 if args.quick else args.fps
    metadata = timeline(fps)
    if args.validate_only:
        print(json.dumps(metadata, indent=2))
        return 0
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            parser.error(tool + " is required; nothing has been rendered")
    for name in ("logo.rgba", "intro-background.rgba", "intro-frames.zip",
                 "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", "words.txt"):
        if not (ASSETS / name).is_file():
            parser.error("Missing production asset: " + name)
    with zipfile.ZipFile(ASSETS / "intro-frames.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if len(manifest["frames"]) != 19 or manifest["duration"] > 2:
            parser.error("Production intro does not match this timeline")
    if not args.output_dir.is_dir():
        parser.error("Output directory must already exist")
    if args.speech_root:
        args.speech_root = args.speech_root.resolve()
        for relative in ("usr/lib/libespeak-ng.so.1", "usr/share/espeak-ng-data/phontab"):
            if not (args.speech_root / relative).is_file():
                parser.error("Missing speech-root file: " + relative)
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_RENDER_DRIVER"] = "software"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    sys.path.insert(0, str(ROOT / "app"))
    import project_ai_r36s as app
    import project_ai_settings as settings_module

    # Stage on the destination filesystem. Failures leave published files intact;
    # publish the JSON last so readers never see metadata for an unfinished video.
    with tempfile.TemporaryDirectory(prefix=".walkthrough-", dir=str(args.output_dir)) as temporary:
        stage = Path(temporary)
        render(stage, fps, metadata, app, settings_module, args.speech_root)
        size = validate_video(stage / metadata["video"], fps, metadata["hasAudio"])
        (stage / "walkthrough.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        for name in (metadata["video"], metadata["captions"], "walkthrough.json"):
            (stage / name).replace(args.output_dir / name)
    print("Verified H.264/yuv420p 640x480, {} fps, {} seconds, {:,} bytes, audio={}".format(
        fps, DURATION, size, metadata["hasAudio"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
