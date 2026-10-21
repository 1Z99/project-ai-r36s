# PC Captures And Walkthrough Assets

The [227 AI gallery](../docs/projects/227-ai/gallery.html) shows the installer first:
a 14-second silent PC demo and four native screenshots. Eight lossless app
screenshots follow. All twelve are **640 x 480 native renders**, not
decoded video frames or physical-device captures. Badges identify them as
**PC demo / native render**. There are no pending-image tiles, handheld photo
placeholder or capture checklist.

The [227 TV gallery](../docs/projects/227-tv/gallery.html) adds nine lossless
640 x 480 native PC captures: real France 24 playback, scripted browser states,
menus and two manager screens. Its
separate manifest brings the site total to **21 images: 12 AI and 9 TV**.
The website has eleven HTML pages: the homepage, five AI pages and five TV pages.

The original 78-second app walkthrough, audio, VTT captions and metadata are
retained separately. The homepage SVG fallback and video poster reuse the
lossless LFM capture `docs/media/previews/app/chat.png`. Video-frame extraction
and a separate homepage poster are not part of the workflow.

## Native Installer Demo

`dev/render_installer_demo.py` uses the production installer UI with synthetic
cards and scripted state/progress fixtures. It never executes the real installer
worker, bootstrap, card discovery, package changes, network access, device input
or installer logging. Confirmation is shown but never executed; success is a
simulated result. Download rates and ETA are illustrative, not benchmarks, and
no application assets or models are installed or downloaded.

Default output directory: `docs/media/previews/installer/`.

| File | Content |
| --- | --- |
| `installer/home.png` | Main SD selected; Install, Update / Repair and Uninstall |
| `installer/confirm.png` | Synthetic main-card confirmation; YES highlighted, not executed |
| `installer/download.png` | Simulated 50% model download with sample speed and ETA |
| `installer/success.png` | Simulated completion, explicitly sourced from the PC demo |
| `installer/demo.json` | Dimensions, timeline, capture states and simulation/safety provenance |
| `installer/installer-demo.mp4` | 640 x 480, 12 fps, 14 seconds, silent H.264/yuv420p fast-start MP4 |

Paths in this and the app table are relative to `docs/media/previews/`.
The PNGs are lossless pixels read directly from the native renderer, not frames
decoded from the encoded MP4.

From the repository root, using existing SDL2/SDL2_ttf and FFmpeg with libx264:

```sh
python3 dev/render_installer_demo.py --validate-only
python3 dev/render_installer_demo.py
python3 dev/render_installer_demo.py --skip-video
python3 dev/render_installer_demo.py --output-dir /existing/output/installer
```

`--validate-only` prints the pure plan without installer/SDL imports or writes.
`--skip-video` writes the four PNGs and `demo.json` without running FFmpeg;
it does not regenerate or remove an existing MP4. Use a separate output directory
for experiments. The renderer forces dummy video/software rendering and does
not install dependencies.

## Native App Captures

`dev/render_app_captures.py` calls production `app/project_ai_r36s.py:draw_ui`
and the actual `SettingsPanel`, with bundled fonts, logo and background.
Scripted replies, sample history, prompt/settings values and simulated microphone
states exist only in memory. No model runs, audio plays, speech is synthesized,
microphone or device is opened, or network is accessed. Personal history and
configuration are neither read nor written. Bundled UI assets are read and only
capture outputs are written; these are not live answers, recordings or
asset/performance benchmarks.

Default output directory: `docs/media/previews/app/`.

| File | Content |
| --- | --- |
| `app/chat.png` | Two short questions and complete scripted sample replies |
| `app/keyboard.png` | Draft, word suggestions, MIC and selected SEND |
| `app/speech.png` | Silent read-aloud illustration with a synthetic word highlight |
| `app/voice.png` | VOICE controls with `SIMULATED / NO CAPTURE`; no recording or playback |
| `app/dictation.png` | Sample dictated draft ready to review and explicitly SEND |
| `app/history.png` | Three in-memory sample conversations, not saved user history |
| `app/prompt.png` | Complete system-prompt editor with MIC, APPLY and CANCEL |
| `app/settings.png` | Generation controls: 768-token context, 160-token reply limit, 0.25 temperature |
| `app/captures.json` | Fixtures, renderer/assets, dimensions, safety provenance, PNG byte sizes and SHA-256 hashes |

All eight PNGs are lossless native 640 x 480 RGBA captures read before present,
without scaling or video compression. No FFmpeg or speech dependency is needed;
use the existing system SDL2/SDL2_ttf libraries:

```sh
python3 dev/render_app_captures.py --validate-only
python3 dev/render_app_captures.py
python3 dev/render_app_captures.py --output-dir /existing/output/app
```

`--validate-only` prints the pure plan without SDL imports or file I/O. Normal
rendering forces dummy/software SDL video, without initializing audio or input
devices. The output parent must already exist. All PNGs are staged before
per-file replacement, with `captures.json` published last.

## Native TV Captures

`dev/render_tv_captures.py` captures the production SDL interfaces from a trusted
TV source checkout without modifying that app. It starts real streams through
`app.App.finish_player`, then drives production `App.update` and `App.draw`,
including libmpv decoding and SDL texture uploads. Installer states use
`tv_manager.draw`. The temporary demo constructor supplies isolated state; the
generator bypasses only the demo tuning guard, not the playback/status/quality code.

Watch shows France 24 French; matrix shows four distinct France 24 language feeds
(French, English, Arabic, Spanish), not four unrelated stations. World, country
and search are scripted browser fixtures using four authentic entries under
France, not a downloaded world catalog. Settings and quality use the same active
single-feed session behind opaque menus, with actual broadcaster source choices.
Installer destinations and confirmation are scripted; no installation runs.
The manifest marks watch, matrix, settings and quality with `realPlayback: true`;
their visible badges read **PC playback / native capture**. Other states retain
the **PC demo / native render** badge.

Default output directory: `docs/media/227-tv/`.

| File | Content |
| --- | --- |
| `world.png` | World atlas with four authentic France 24 entries under France |
| `country.png` | France close-up and four-feed scripted list |
| `search.png` | Production keyboard with a scripted `france` query |
| `watch.png` | Fullscreen France 24 French, decoded live in the app |
| `matrix.png` | Four live France 24 language feeds: French, English, Arabic, Spanish |
| `settings.png` | Production Start/settings menu over continuing real playback |
| `quality.png` | Actual broadcaster source choices over continuing real playback |
| `installer.png` | Install 227 TV manager home with a sample destination |
| `confirm.png` | Scripted installation confirmation; no action executed |
| `captures.json` | Source URLs/timestamps, observed frames/formats, provenance flags, catalog/source/asset and PNG hashes |
| `NOTICE.md` | France 24 / France Medias Monde broadcast credit, sources and rights information |

Paths in this table are relative to `docs/media/227-tv/`. Preserve
[NOTICE.md](../docs/media/227-tv/NOTICE.md) with the captures. Broadcast frames and
station marks belong to France Medias Monde and the respective rights holders,
not the site's MIT license. Public stream listings do not grant redistribution
rights; no broadcaster endorsement is implied.

From this repository root, using source-compatible host SDL2/SDL2_ttf and native
libmpv (no FFmpeg executable dependency):

```sh
python3 dev/render_tv_captures.py --validate-only
python3 dev/render_tv_captures.py --capture-streams --source-dir /path/to/melkor227-tv
python3 dev/render_tv_captures.py --capture-streams --source-dir /path/to/melkor227-tv --output-dir /existing/output/tv
```

`--validate-only` prints a pure plan without production/native-library imports,
file I/O or network access; it requires no source checkout or dependency checks.
Even with `--capture-streams`, validation only describes the intended run. Normal
capture requires explicit `--capture-streams` opt-in, `--source-dir` and an
already-existing output directory; there is no network access by default.
Override the checkout's private playback runtime to a known compatible host
library rather than attempting to load its shipped ARM64 runtime. Loading a
checkout or native library executes trusted code; this is not a security sandbox.

Rendering fetches public listings, resolves the four France 24 feeds and performs
real native decoding using dummy/software SDL video with null/disabled audio
output. App workers, health scans, state saving, card discovery and installer
actions are blocked. No handheld, controller, audio device or personal saved
state is accessed; no installation runs. The global `networkAccess`, `realPlayback`
and `videoDecoding` flags describe the capture run, while each scene records
whether playback ran and was visible. The website itself loads only local media,
without stream connections or external-media requests.

All nine PNGs are staged before per-file replacement, with `captures.json` last.
The record includes source-file/asset and catalog hashes, fetch/capture timestamps,
source and selected playback URLs, per-player status/formats, observed frame
counts, and output PNG sizes/hashes. The current watch capture records 151 frames;
matrix records 152 French, 151 English, 152 Arabic and 153 Spanish frames. All
sources were observed at 640 x 360 and 25 fps. `decodedFrameCount` counts non-empty
production `Player.frame()` RGBA renders with observed dimensions, not the
decoder's internal frame total. Single output is 640 x 480; each matrix output
is 298 x 218. These observations verify PC playback during capture, not audio,
sustained performance, handheld throughput or new hardware acceptance.

The stock app's `--scene matrix` preview still renders unavailable-signal cells.
This separate generator starts actual players instead; do not confuse the two
paths or relabel these PC captures as handheld evidence.

## Gallery Manifests

[`docs/screenshots.json`](../docs/screenshots.json) points twelve entries at
`media/previews/installer/` and `media/previews/app/`, with `origin: "pc-demo"`
and `method: "native-render"`. None has a video `time`; timing in installer
`demo.json` describes its separate demo timeline, not video-frame provenance
for these native PNGs. It contains no unused device entry or suggested filenames.
Future physical-device media belongs in an intentionally created project media
folder, not an empty directory that must exist at checkout.

Pages opt into the manifest with
`data-project="227-ai" data-captures="screenshots.json"` on the body, including
`docs/projects/227-ai/gallery.html`. The manifest URL and image paths resolve
relative to root `docs/site.js`, not the nested page or manifest folder: keep
entries such as `media/previews/app/chat.png`, without `../../` or a leading `/`.
This also supports subdirectory hosting. Ordinary HTML asset links on a 227 AI
page instead use `../../media/previews/...`; Markdown links in these notes use
`../docs/projects/227-ai/...` for project pages. Other projects should use their
own information folders and capture manifests, not reuse 227 AI's slots.

[`docs/projects/227-tv/screenshots.json`](../docs/projects/227-tv/screenshots.json)
contains the nine TV entries, all `origin: "pc-demo"` / `method: "native-render"`,
without video `time` values. TV pages opt in with
`data-project="227-tv" data-captures="projects/227-tv/screenshots.json"`.
Its image paths likewise resolve from root `docs/site.js`, using
`media/227-tv/<id>.png`, not paths relative to the nested manifest. The homepage
cards select their own project manifests rather than loading one on the body.

The full-suite command discovers the capture and walkthrough tests:

```sh
python3 -m unittest discover -s tests -q
```

See [site maintenance](../SITE_MAINTENANCE.md) for navigation, the homepage
illustration and future project folders. AI media stays under
`docs/media/previews/` and TV media under `docs/media/227-tv/`; application assets
and binary paths are unchanged.

## Animated Walkthrough

`227-ai-walkthrough.mp4` is an automated recording of the **production SDL app
renderer**, not an HTML recreation or an on-device capture. It uses the real
`SettingsPanel`, production fonts/logo, the original 19-frame intro and the
frame-19 background. Only the standard release's `fast` LFM profile is shown.
Model loading, replies, microphone testing and dictation are scripted, not
measurements or live inference. No model engine or real microphone is started;
no ALSA device is opened, no R36S is contacted, and no chat/config is saved.

Generated assets:

- `227-ai-walkthrough.mp4`: 640 x 480, 20 fps, 78 seconds, H.264/yuv420p,
  fast-start MP4, with mono AAC at 22,050 Hz.
- `walkthrough.json`: seek chapters, duration/dimensions/fps, audio availability,
  provenance flags and absolute word-highlight timestamps. Each word timing is
  `[videoSeconds, replyCharacterStart, replyCharacterEnd]`, with an exclusive end.
- `227-ai-walkthrough.vtt`: optional English track with chapter descriptions and
  read-aloud phrases. Captions are not burned into the small app UI.

Regenerate using Python 3.9+, system SDL2/SDL2_ttf and FFmpeg/ffprobe with libx264:

```sh
python3 dev/render_web_walkthrough.py --fps 20 --speech-root /path/to/speech-root
env 227_DEMO_SPEECH_ROOT=/path/to/speech-root python3 dev/render_web_walkthrough.py
python3 dev/render_web_walkthrough.py --output-dir /existing/output/directory --fps 15
python3 dev/render_web_walkthrough.py --validate-only
```

`--validate-only` prints the timeline plan without loading SDL, checking speech
engines, starting subprocesses or writing files. Its `hasAudio: false` is a
conservative plan default, not an audio capability probe. `--quick` renders the
same 78-second sequence at 5 fps for smoke tests; use a separate output directory
to avoid replacing the production asset. Normal 15/20 fps renders verify that
all 19 original intro frames were presented. Progress logs report 0/20/40/60/80/100%.
Files are staged locally and verified with ffprobe before atomic per-file
replacement; the JSON is published last. Failures clean up temporary files,
the encoder and SDL resources.

### Seek Timeline

| ID | Seconds | Action |
| --- | --- | --- |
| `intro` | 0-2 | Original intro and fade into the app |
| `loading` | 2-4 | Illustrative LFM loading progress |
| `keyboard` | 4-12 | Click compose, type `what can you do?`, select SEND at 11.4 |
| `answer` | 12-20 | Stream the predefined sample answer |
| `read-aloud` | 20-36 | Select speaker; actual speech and word-event highlighting from 20.5 to 29.7702 |
| `settings` | 36-50 | R2 opens settings; navigate to VOICE; select USB at 39; TEST at 40 |
| `dictation` | 50-64 | Compose; MIC records 51-55; transcription 55-57; draft review 57-60; explicit SEND at 60 |
| `history` | 64-73 | Open history; down at 66, up at 68, reopen at 70 |
| `outro` | 73-78 | Pause, close at 76, fade to black from 77 |

The mic test records **nothing**: its injected simulated tester displays recording
at 40-44, prerecorded playback at 44-48 and a sample-waveform verdict at 48-50.
The settings note says `VISUAL DEMO / no mic access` during recording and
`VISUAL DEMO / prerecorded sample / no mic access` during playback and verdict.
The audible sample says: "This is a microphone test. Your voice stays on the
device." It is offline synthetic speech, not a recording of a real person or
the visitor's microphone. Production waveform analysis of this prerecorded
four-second WAV supplies the simulated test's peak/RMS/zero-crossing statistics.
The cursor moves between native app coordinates with brief click rings; no
large labels obscure the chat. Public simulation labels belong in the website
wrapper, using `simulatedMic`, `sampleReplies` and `localPC` from the JSON.

### Offline Speech

The retained video has **audible offline eSpeak-ng 1.52.0 speech**. Production
`synthesize_speech` runs in an isolated Python subprocess with a fixed 1.0x
reading speed, `LD_LIBRARY_PATH` pointing only at the temporary dependency tree
(plus any inherited library path), and `ESPEAK_DATA_PATH` pointing at its data.
Real speech word events drive the green highlighting and VTT phrase timings.
No app source changes, browser speech APIs or hosted synthesis are involved.

`--speech-root PATH`, or `227_DEMO_SPEECH_ROOT`, points at a package extraction
root containing `usr/lib/libespeak-ng.so.1` and `usr/share/espeak-ng-data/`.
An explicit root must work: synthesis errors abort generation rather than
silently publishing a video without speech. Without a root, installed eSpeak-ng
is tried first, followed by FFmpeg `flite` with approximate audio timings, then
a silent fallback. Use the explicit root to reproduce the speaking asset.

To reproduce the speaking asset without a system install, obtain trusted
host-architecture packages for eSpeak-ng 1.52.0 and its required shared libraries
(including pcaudiolib and libsonic where required by the package). Verify package
authenticity using distributor signatures or authenticated checksums before extraction;
HTTPS alone is not package verification. Create `/path/to/speech-root`, then
extract each reviewed package there, for example with
`bsdtar -xf ARCHIVE -C /path/to/speech-root`. Preserve its `usr/lib/` and
`usr/share/espeak-ng-data/` layout. Loading these libraries executes trusted
native code; an isolated subprocess is not a security sandbox.

This extraction requires no sudo, package database writes or global configuration
changes. The generator does not install or download dependencies. Keep the
dependency tree outside the repository and remove it after rendering if desired;
recreate it for later regeneration. Only rendered audio is bundled, not the
speech engine or data. Preserve the third-party terms in [CREDITS.md](../CREDITS.md)
and [the eSpeak notice](../licenses/espeak-ng-NOTICE.txt).

Speech is synthesized to temporary PCM WAVs and muxed into the MP4 at 20.5
seconds for read-aloud and 44 seconds for mic-test playback. Slower speech is
fitted to its fixed window, with highlight timings adjusted together. The mic
sample is fitted within 3.8 seconds and padded to four seconds. Nothing is
played through a local speaker during generation or verification.

Current JSON flags are `hasAudio: true`, `speechBackend: "espeak-ng"`,
`highlightTiming: "speech-word-events"`,
`speechDependencySource: "temporary-user-local"`, and
`prerecordedMicPlayback: true`. `micPlayback` identifies the exact phrase,
44-48 second window, `liveCapture: false`, and sample waveform statistics.
`simulatedMic`, `sampleReplies` and `localPC` stay true; `liveModel`,
`microphoneAccess` and `deviceAccess` stay false. Chapter IDs and boundaries
remain unchanged.

ffprobe verifies H.264/yuv420p video and a 78-second AAC audio track. When
reviewing a regeneration, also check speech audibility, word highlighting and
silence before 20.5 seconds and between/after the two speech windows.
The wrapper should still begin muted and let the visitor enable sound;
autoplay behavior is outside this asset generator.

## License And Publication

Original site/app media is MIT-licensed under [the project license](../LICENSE);
third-party components retain their own terms and credits, including southoz.
Keep the [public license copy](../docs/LICENSE.txt), footer and capture-provenance
links available. Media regeneration is not publication: follow the separate
[website-only approval process](../PUBLISHING.md#website-only-publication).
