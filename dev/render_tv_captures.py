#!/usr/bin/env python3
"""Capture the production TV UI and real libmpv playback on a headless PC.

Requires a trusted TV checkout, SDL2/SDL2_ttf and native libmpv (no FFmpeg
executable). Network and decoding require --capture-streams. Validation only
prints a plan, without importing the checkout or loading native libraries.
"""

import argparse
from contextlib import ExitStack
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs/media/227-tv"
SCENES = ("world", "country", "search", "watch", "matrix", "settings", "quality",
          "installer", "confirm")
CATALOGS = ("https://iptv-org.github.io/iptv/countries/fr.m3u",
            "https://iptv-org.github.io/api/streams.json")
LANGUAGES = ("French", "English", "Arabic", "Spanish")


def capture_plan(capture_streams=False):
    return dict(origin="pc-demo", method="native-render", productionRenderer=True,
                width=640, height=480, lossless=True, fictionalChannels=False,
                stillPhotographs=False, realPlayback=bool(capture_streams),
                liveStreams=bool(capture_streams), videoDecoding=bool(capture_streams),
                networkAccess=bool(capture_streams), audioPlayback=False,
                deviceAccess=False, controllerAccess=False, userPersistence=False,
                persistence=False, userStateRead=False, installation=False,
                renderer="app.App.finish_player + app.App.update + app.App.draw + tv_manager.draw",
                playbackEngine="native libmpv software RGBA renderer",
                limitations="Scripted PC browser/menu states and live playback, not handheld captures or performance benchmarks.",
                rightsNotice="Broadcast images and station marks belong to their respective rights holders; public stream listings do not grant redistribution rights. Broadcast content is not covered by the site's MIT license. No broadcaster endorsement is implied.",
                captures=[dict(id=s, file=s+".png") for s in SCENES])


def forbidden(*args, **kwargs):
    raise RuntimeError("TV capture attempted worker, installation, controller or persistence access")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def render(source, output):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    plan = capture_plan(True)
    plan["sourceFiles"] = {name: hashlib.sha256((source/name).read_bytes()).hexdigest()
                           for name in ("app.py", "tv_manager.py", "tv_backend.py", "tv_media.py",
                                        "tv_quality.py", "tv_globe.py", "tv_style.py", "tv_installer.py")}
    plan["sourceAssets"] = {name: hashlib.sha256((source/"assets"/name).read_bytes()).hexdigest()
                            for name in ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
                                         "logo.rgba", "intro-background.rgba", "globe.json")}
    plan["scriptedStates"] = "Temporary demo=True constructor; authentic station catalog replaces sample entries. finish_player bypasses only the demo tuning guard. Browser/menu selection and installer cards are scripted; playback/status/quality warnings are production code."
    plan["frameCountSemantics"] = "decodedFrameCount counts non-None production Player.frame() RGBA renders with observed source dimensions, not the decoder's internal frame total. No performance claims."
    original_cdll = ctypes.CDLL

    def video_only_library(name, *args, **kwargs):
        library = original_cdll(name, *args, **kwargs)
        if name == "libSDL2-2.0.so.0":
            initialize = library.SDL_Init
            initialize.argtypes, initialize.restype = [ctypes.c_uint32], ctypes.c_int
            library.SDL_Init = lambda flags: initialize(0x20)
            library.SDL_NumJoysticks = lambda: 0
            library.SDL_JoystickOpen = forbidden
        return library

    with tempfile.TemporaryDirectory(prefix="227-tv-captures-") as temporary, ExitStack() as guards:
        guards.enter_context(patch.dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_RENDER_DRIVER="software",
                                        SDL_AUDIODRIVER="dummy", TV_MEDIA_AO="null",
                                        TV227_GLOBE_FILTER=""))
        app = importlib.import_module("app")
        manager = importlib.import_module("tv_manager")
        backend = importlib.import_module("tv_backend")
        media = importlib.import_module("tv_media")
        quality = importlib.import_module("tv_quality")
        plan["appVersion"] = manager.VERSION
        guards.enter_context(patch.object(app.App, "work", forbidden))
        guards.enter_context(patch.object(app.State, "save", forbidden))
        guards.enter_context(patch.object(backend.Health, "scan", forbidden))
        guards.enter_context(patch.object(manager, "discover_cards", forbidden))
        guards.enter_context(patch.object(manager.Manager, "start", forbidden))
        guards.enter_context(patch.object(ctypes, "CDLL", video_only_library))
        available, diagnostic = media.availability()
        if not available:
            raise RuntimeError(diagnostic)
        plan["playbackAvailability"] = diagnostic

        catalogs = []
        for url in CATALOGS:
            body = backend._download(url, timeout=8, maximum=8*1024*1024)
            catalogs.append(body)
            plan.setdefault("catalogSources", []).append(dict(url=url, fetchedUTC=utc_now(),
                                                               sha256=hashlib.sha256(body).hexdigest()))
        french = next(row for row in backend.Catalog._m3u(catalogs[0].decode("utf-8-sig"))
                      if row["channel"] == "France24.fr" and row["feed"] == "French"
                      and row["url"].startswith("https://live.france24.com/"))
        streams = json.loads(catalogs[1])
        stations = []
        for language in LANGUAGES:
            row = french if language == "French" else next(
                row for row in streams if row.get("channel") == "France24.fr"
                and row.get("feed") == language
                and row["url"].startswith("https://live.france24.com/")
                and row["url"].endswith("/master_5000.m3u8"))
            station = dict(uuid="France24.fr::"+language, name="France 24 "+language,
                           url=row["url"], countryCode="FR", country="France", language=language,
                           tags="news", user_agent="", referrer="", height=1080)
            stations.append(quality.resolve_station(station))
        plan["channels"] = [dict(name=s["name"], countryCode="FR", language=s["language"],
                                 sourceURL=s["source_url"], playbackURL=s["url"])
                            for s in stations]

        observations = {}
        original_frame = media.Player.frame

        def observe_frame(player):
            # Observe, never replace or inject pixels; App.update owns texture uploads.
            frame = original_frame(player)
            if frame is not None and player.source_width and player.source_height:
                record = observations.setdefault(player, dict(frames=0, first=time.monotonic(), hashes=set()))
                pixels = bytes(frame)
                digest = hashlib.sha256(pixels).hexdigest()
                record["frames"] += 1
                record["hashes"].add(digest)
                record["lastHash"] = digest
                samples = [pixels[(y*player.width+x)*4+c]
                           for y in range(player.height//4, player.height*3//4, max(1, player.height//16))
                           for x in range(player.width//8, player.width*7//8, max(1, player.width//16))
                           for c in range(3)]
                record["picture"] = max(samples)-min(samples) > 32 and sum(samples)/len(samples) > 12
            return frame

        guards.enter_context(patch.object(media.Player, "frame", observe_frame))
        display = app.Display(desktop=True)
        try:
            # Reuse the live single-channel session for both menus; do not retune or fabricate formats.
            for group in (("world", "country", "search"), ("watch", "settings", "quality"),
                          ("matrix",), ("installer", "confirm")):
                ui = app.App(display, Path(temporary), demo=True)
                try:
                    ui.stations = [dict(station) for station in stations]
                    ui.country = "FR"
                    ui.refilter()
                    ui.globe.lat, ui.globe.lon = 24., -15.
                    ui.pointer_x, ui.pointer_y = 620., 434.
                    ui.notice, ui.notice_until = "", 0.
                    if group[0] in ("watch", "matrix"):
                        matrix = group[0] == "matrix"
                        ui.view = "matrix" if matrix else "video"
                        started = time.monotonic()
                        for cell, station in enumerate(stations if matrix else stations[:1]):
                            ui.finish_player(cell, dict(station), matrix=matrix)
                        while True:
                            ui.update()
                            ui.draw()
                            now = time.monotonic()
                            if all(observations.get(player, {}).get("frames", 0) >= 8
                                   and len(observations[player]["hashes"]) >= 3
                                   and observations[player]["picture"]
                                   and now-observations[player]["first"] >= 6
                                   and ui.statuses[cell].get("playing")
                                   and not ui.statuses[cell].get("buffering")
                                   and not ui.statuses[cell].get("error")
                                   and now >= ui.playback_notices.get(cell, (None, None, 0))[2]
                                   for cell, player in ui.players.items()):
                                break
                            errors = {cell: status for cell, status in ui.statuses.items()
                                      if status.get("error") or status.get("ended")}
                            if errors or now-started >= 30:
                                raise RuntimeError("Live playback failed or startup exceeded 30s: "+json.dumps(errors or ui.statuses))
                            time.sleep(.025)
                        print("Decoded "+", ".join(ui.playing[cell]["name"]+": "+str(observations[player]["frames"])+" frames"
                                                     for cell, player in ui.players.items()), flush=True)

                    for scene in group:
                        if scene == "country":
                            ui.globe.fly_to(ui.by_country["FR"], now=time.monotonic()-5)
                            ui.globe.advance_flight(now=time.monotonic())
                        elif scene == "search":
                            ui.modal, ui.draft, ui.query = "search", "france", "france"
                            ui.refilter()
                            ui.keyboard_row, ui.keyboard_col = 4, 0
                        elif scene == "watch":
                            ui.last_input = time.monotonic()
                        elif scene == "matrix":
                            # Normal timeout, not a clock patch: no header/footer clipping the cells.
                            ui.last_input = time.monotonic()-3
                        elif scene == "settings":
                            ui.modal = "menu"
                        elif scene == "quality":
                            ui.open_quality()
                        if scene in ("installer", "confirm"):
                            cards = [dict(name="SD1", path="/roms", available=True),
                                     dict(name="SD2", path="/roms2", available=True)]
                            install = manager.Manager({}, cards=cards, worker=forbidden)
                            install.card, install.focus = 0, 1
                            if scene == "confirm":
                                install.state = "confirm"
                            manager.draw(display, install)
                        else:
                            ui.update()
                            ui.draw()
                        display.screenshot(output/(scene+".png"))
                        capture = next(row for row in plan["captures"] if row["id"] == scene)
                        capture.update(capturedUTC=utc_now(), realPlayback=bool(ui.players),
                                       playbackVisible=scene in ("watch", "matrix"),
                                       scriptedUI=True, channels=[s["name"] for s in stations],
                                       playback=[])
                        for cell, player in ui.players.items():
                            station, status, observed = ui.playing[cell], ui.statuses[cell], observations[player]
                            capture["playback"].append(dict(
                                cell=cell, live=True, channel=station["name"], language=station["language"],
                                sourceURL=station["source_url"], playbackURL=station["url"],
                                decodedFrameCount=observed["frames"], distinctFrameCount=len(observed["hashes"]),
                                lastFrameSHA256=observed["lastHash"], sourceWidth=status["source_width"],
                                sourceHeight=status["source_height"], sourceFPS=status["source_fps"],
                                outputWidth=player.width, outputHeight=player.height,
                                elapsedPlaybackSeconds=round(time.monotonic()-observed["first"], 3),
                                status=dict(status), audioOutput="null" if status["audible"] else "disabled"))
                        if scene == "matrix":
                            capture.update(cellCount=len(ui.players), distinctChannelCount=len({s["uuid"] for s in ui.playing.values()}),
                                           caption="Four cells showing four live France 24 language feeds (French, English, Arabic, Spanish); not a handheld performance test.",
                                           chromeHidden=time.monotonic()-ui.last_input >= 2.5)
                        elif scene in ("settings", "quality"):
                            capture["caption"] = "Production menu with real playback continuing behind the opaque menu."
                        elif scene in ("installer", "confirm"):
                            capture["caption"] = "Production installer UI with scripted cards; no discovery, worker or installation."
                finally:
                    ui.close()
                    observations.clear()
        finally:
            display.close()
    for capture in plan["captures"]:
        image = (output/capture["file"]).read_bytes()
        capture.update(bytes=len(image), sha256=hashlib.sha256(image).hexdigest())
    plan["generatedUTC"] = utc_now()
    (output/"captures.json").write_text(json.dumps(plan, indent=2)+"\n")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=MEDIA)
    parser.add_argument("--capture-streams", action="store_true", help="Explicitly allow public stream network access and native decoding")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        print(json.dumps(capture_plan(args.capture_streams), indent=2))
        return
    if not args.capture_streams:
        parser.error("--capture-streams is required to allow network access and real playback")
    if not args.source_dir or not (args.source_dir/"app.py").is_file():
        parser.error("--source-dir must be a trusted TV source checkout")
    if not args.output_dir.is_dir():
        parser.error("--output-dir must already exist")

    def deadline(signum, frame):
        raise TimeoutError("TV capture exceeded its 140-second total budget")

    previous = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(140)
    try:
        # Publish only after every native capture and its provenance have succeeded.
        with tempfile.TemporaryDirectory(prefix=".tv-captures-", dir=args.output_dir) as stage:
            render(args.source_dir.resolve(), Path(stage))
            for name in [scene+".png" for scene in SCENES]+["captures.json"]:
                (Path(stage)/name).replace(args.output_dir/name)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    print("Rendered nine native TV captures with real live playback to", args.output_dir)


if __name__ == "__main__":
    main()
