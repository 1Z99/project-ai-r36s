# 227 AI — offline AI for the R36S

**227 AI** turns the Anbernic **R36S** handheld into a private, fully offline
AI assistant. It runs a small language model **entirely on the device** — no
internet, no cloud, no accounts. Ask questions with the on-screen keyboard,
dictate with a USB microphone, and hear the replies read aloud.

This repository contains the app, artwork, licenses and a **graphical deployment
manager** for installing, updating and removing 227 AI. The app gets a **227**
system page in EmulationStation so you launch it like a game.

```
   ┌─────────────── 227 ───────────────┐
   │  LFM 2.5 // 350M            [READY]│
   │                                    │
   │  > ask 227 anything                │
   │                                    │
   │  A TYPE/SPEAK  X HISTORY  Y NEW …  │
   └────────────────────────────────────┘
```

---

## Highlights

- **100% offline** — the model runs locally via `llama.cpp`. Nothing leaves the
  device.
- **Graphical manager** — controller-driven install, update / repair and uninstall
  with explicit SD card selection and resumable downloads.
- **Chat with a real keyboard** — a full on-screen keyboard with word
  suggestions.
- **Read aloud** — offline text-to-speech with word-by-word highlighting.
- **Dictation** — offline speech-to-text with `whisper.cpp` (USB microphone).
- **Settings on the device** — change model, system prompt, generation options,
  speech rate and background from the app (press **R2**).
- **Your microphone, your choice** — pick the audio device in **R2 → VOICE**
  and check it with the built-in **TEST MICROPHONE**. The choice is remembered
  across card renumbering. There is no separate MIC TEST application.
- **Dictate into the system prompt** — the system-prompt keyboard has its own
  **MIC** key, so the prompt can be written by voice too.
- **Chat history** — keep and reopen recent conversations.
- **No network access at runtime** — the network is only used by the installer.

---

## Requirements

- An **Anbernic R36S** running **dArkOSRE** (aarch64), or a compatible
  aarch64 ArkOS-style image with EmulationStation.
- A writable, actually mounted **main SD card** (`/roms`) or **second SD card**
  (`/roms2`). Both are detected independently of EmulationStation's ROM layout;
  the graphical manager always requires you to select a destination explicitly.
- Allow about **650 MB free** on the app's destination card for downloads and
  staging. A preflight check budgets free space and credits reusable models,
  runtime and retained partial downloads. Cached downloads also consume space
  until uninstall.
- Python **3.9+**, `curl`, `libSDL2`, `libSDL2_ttf`, and the DejaVu Sans Mono font
  (`/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf`) for the manager.
- `espeak-ng` / `libespeak-ng.so.1` for speech, and ALSA `arecord` / `aplay` for
  microphone recording and playback. These are usually present in dArkOSRE.
- Internet access for downloads unless all required files are supplied locally.
  The installed app needs no network connection. Optional `dialog` can keep a
  bootstrap failure visible when a usable console is available.

---

## Install

1. Connect the handheld to Wi-Fi and copy `install.sh` into a mounted card's
   `tools/` folder. Mark it executable if launching it directly from Tools.
2. Run it from Tools, or run `sh install.sh` over SSH. The UI appears on the
   handheld, not in the SSH terminal. It needs no terminal input.
3. Select **Main SD / SD1** or **Second SD / SD2** with D-Pad and **A**. Select
   **INSTALL**, then explicitly confirm. Unmounted/read-only cards are unavailable.
4. After success, restart EmulationStation (or reboot), then open **227 > 227 AI**.

To download the bootstrap over SSH, use the Tools directory on your mounted card:

```sh
cd /roms2/tools                 # or /roms/tools
curl -fL --connect-timeout 15 --max-time 120 -o install.sh \
  https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/install.sh
chmod +x install.sh
sh install.sh
```

The small bootstrap fetches the manager's Python UI, core and CLI modules into
`<card>/tools/227-ai-manager/`, validates them in temporary files, then publishes
them with atomic per-file renames. Downloads have finite connection, low-speed
and overall timeouts. It prefers the Tools card it was launched from; otherwise
it uses a writable mounted `/roms2`, then `/roms`. **This only chooses manager
storage, never the app destination.** The manager stays on that Tools card even
when you install or remove the app on the other card.

Reopen **Tools > Install 227 AI** for maintenance. The self-locating launcher
runs the stored manager directly, without an SSH session or terminal chooser.
The bootstrap sets `SDL_VIDEODRIVER=kmsdrm` and `SDL_RENDER_DRIVER=software` on
the handheld; `--desktop` leaves the desktop's SDL environment unchanged.

### Progress and failures

Progress is **per file / task**, with actual transferred bytes, transfer rate,
elapsed time and file ETA where known. Unknown lengths are indeterminate; there
is no invented overall download percentage. Failures stay on screen: **A**
retries/resumes, **X** opens error details, and **B** returns to the menu.
**B** during work offers a cancellation confirmation; downloaded partials remain.
Once final publication or removal starts, cancellation is deferred until it is
safe. Failed updates restore replaced files; a failed rollback reports retained
backup locations. Do not power off while finalizing.

Manager events/errors are saved in `227-ai-manager/installer.log`; bootstrap
downloads and UI startup errors go to `227-ai-manager/bootstrap.log`. A bootstrap
failure tries `dialog --msgbox` when installed. If SDL cannot start and no usable
console exists, no persistent graphical error display is possible: inspect the
log over SSH, fix the dependency/network problem, and relaunch. No TTY read is
used to hold the launcher open. Before manager storage is available, errors can
only go to stderr / an available dialog.

### Headless CLI

CLI mode is explicit; graphical mode does not accept `--roms` or action flags.
Every CLI change requires both an explicit card and `--yes`:

```sh
sh install.sh --help            # no SDL, network or writes
sh install.sh --cli --roms /roms2 --action install --yes
sh install.sh --cli --roms /roms2 --action update --yes
sh install.sh --cli --roms /roms2 --action uninstall --yes
sh install.sh --cli --roms /roms2 --action install --dry-run
```

Dry-run discovers both cards and validates the selected target without creating
logs, locks, caches, bytecode or manager files. It requires an existing
`installer_core.py` beside the bootstrap or in `installer/`; a standalone fresh
bootstrap deliberately refuses to download anything for dry-run. It does not
test downloads or estimate required staging space. CLI-only `--es-cfg PATH` and
`--themes PATH` allow isolated tests; changes are logged in `installer-cli.log`.

### Local builds

From a checkout, `sh install.sh` uses `installer/` without downloading manager
modules and automatically passes the checkout root as `--source` when `app/`
exists. This tests **unpushed app/assets/system changes**, not the published
copies. Use `sh install.sh --desktop` for a PC window, or `--source PATH` to
select another release tree. Local logs live in `installer/`.

A self-contained manager build can place `install.sh` and the three Python
modules together, with a release tree at `bundle/` beside them. The UI and CLI
automatically use `bundle/` when present. Include app, assets, licenses and system
folders; put the runtime archive plus its pinned `.sha256` sidecar and both model
files at the bundle root for fully local installation. Missing binaries still
need downloads; an incomplete local app bundle fails instead of mixing sources.

Development overrides remain supported:

- `PROJECT_AI_RAW_BASE`: raw repository base for manager modules and remote
  app/assets/licenses/system files. Supports `file://` fixtures.
- `PROJECT_AI_REL_BASE`: release asset base for runtime, checksum and model mirrors.
- `PROJECT_AI_RUNTIME_LOCAL`: local runtime archive; its adjacent `.sha256` is
  required and must match the backend's pinned checksum.

For an isolated headless source test, create the target and an ES config first:

```sh
mkdir -p /tmp/227-test/card /tmp/227-test/themes
printf '<systemList></systemList>\n' > /tmp/227-test/es_systems.cfg
sh install.sh --cli --roms /tmp/227-test/card --action install --dry-run \
  --es-cfg /tmp/227-test/es_systems.cfg --themes /tmp/227-test/themes
# Replace --dry-run with --yes to actually install the local source build.
```

---

## What the installer does

`$R` is the explicitly selected app destination (`/roms` or `/roms2`). `$T` is
the independent Tools card where the bootstrap keeps the manager.

| Path | Contents |
| --- | --- |
| `$R/tools/project_ai_r36s.py` | The application |
| `$R/tools/project_ai_settings.py` | Settings panel module |
| `$T/tools/Install 227 AI.sh` | Self-locating manager launcher |
| `$T/tools/227-ai-manager/` | Bootstrap, Python manager modules, optional bundle and logs |
| `$R/227ai/227 AI.sh` | EmulationStation launcher (the "ROM") |
| `$R/227ai/gamelist.xml` + `227-art.jpg` | System listing and cover art |
| `$R/tools/project_ai/runtime/` | `llama.cpp` server + libraries |
| `$R/tools/project_ai/whisper/whisper-cli` | Speech-to-text engine |
| `$R/tools/project_ai/models/` | LFM 2.5 model + whisper model |
| `$R/tools/project_ai/logo.rgba`, `intro-*.{zip,rgba}`, `words.txt` | Interface artwork and word list |
| `$R/tools/project_ai/config.json` | Your settings (created only if missing) |
| `$R/tools/project_ai/licenses/` | Third-party license texts |
| `$R/.227-ai-cache/` | Download cache and temporary staging |
| `/etc/emulationstation/es_systems.cfg` | Only the **227** system registration is added/updated/removed |

The application finds its own folder at run time, so it works on either card.
Your chats (`chats.json`) and history (`history.json`) live in
`$R/tools/project_ai/`. Install and update / repair preserve these and existing
settings, verify reusable models/runtime, and refresh app/assets/system files.
Uninstall deletes app data on the selected card, including all conversations.
The manager and its Tools entry remain. ES has one shared **227** registration;
installing on another card repoints it there without deleting the other copy.

---

## Using 227 AI

### Main screen

| Button | Action |
| --- | --- |
| **D-Pad Up/Down** | Scroll the conversation |
| **A** | Open the keyboard (or read a highlighted reply aloud) |
| **X** | Chat history |
| **Y** | New chat |
| **B** | Exit |
| **R2** | Open / close Settings |

### On-screen keyboard

| Button | Action |
| --- | --- |
| **D-Pad** | Move between keys |
| **A** | Type the selected key |
| **X** | Send the message |
| **Y** | Insert a space |
| **B** | Delete a character (closes the keyboard when the draft is empty) |
| **Up** (from the top row) | Pick a word suggestion |
| Select the **MIC** key | Start dictation; tap it again to stop |

### History

| Button | Action |
| --- | --- |
| **D-Pad Up/Down** | Move |
| **A** | Open the selected chat |
| **X** / **B** | Close |
| **Y** | Delete the selected chat |

### Settings (press **R2**)

| Button | Action |
| --- | --- |
| **Up/Down** | Switch page (or move between rows when focused on values) |
| **Right** | Focus the value panel |
| **Left/Right** | Change a value |
| **A** | Select / adjust |
| **B** | Close Settings |

Settings pages: **MODELS**, **SYSTEM PROMPT**, **GENERATION**, **VOICE**,
**APPEARANCE**, **SYSTEM INFO**. Changes are saved to `config.json`
immediately. Changing the model or context reloads the engine in place.

---

## Models and storage

227 AI uses the following models (SHA-256 verified by the installer):

| Model | File | Size | Source |
| --- | --- | --- | --- |
| LFM 2.5 350M | `models/LFM2.5-350M-QAD-Q4_0.gguf` | ~209 MB | [LiquidAI/LFM2.5-350M-GGUF](https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF) |
| Whisper tiny.en (q5_1) | `models/whisper/ggml-tiny.en-q5_1.bin` | ~31 MB | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) |

Downloads are attempted from this repository's
[releases](https://github.com/1Z99/project-ai-r36s/releases) first, then Hugging Face, and
are verified against a checksum before use. The model is licensed under the
**LFM Open License v1.0** — see `licenses/LFM2.5-Open-License.txt`.

---

## Update / uninstall

Open **Tools > Install 227 AI**, explicitly select the app's card, then choose
**UPDATE / REPAIR** or **UNINSTALL** and confirm. Update preserves settings and
chats. **Uninstall permanently removes the selected card's app, models, settings,
chat history and cache. Back up `chats.json` / `history.json` first if needed.**

For headless removal:

```sh
sh install.sh --cli --roms /roms2 --action uninstall --yes
```

Only the selected card's ES registration is removed; unrelated systems and a
registration pointing at another card are preserved. A legacy whole-config
backup is never restored. The independent manager remains for reinstalling.
Restart EmulationStation after installation or removal to refresh its systems.

---

## Repository layout

```
install.sh                              graphical / CLI bootstrap
installer/installer_ui.py                SDL deployment manager
installer/installer_core.py              shared install / update / uninstall backend
installer/installer_cli.py               explicit headless frontend
app/project_ai_r36s.py                  application (default model: LFM 2.5)
app/project_ai_settings.py              settings panel
assets/                                 logo, intro frames, background, art, fonts
system/227 AI.sh, system/gamelist.xml   EmulationStation system files
licenses/                               third-party license texts
dev/                                    optional PC preview + build tools
```

`dev/project_ai_preview.py` renders the interface on a desktop computer for
development (`dev/prepare_intro_frames.py` rebuilds the intro asset).

---

## Release assets

The installer pulls binary files from the repository's **Releases** page:

| Asset | SHA-256 |
| --- | --- |
| `project-ai-r36s-runtime-arm64.tar.gz` | `4ad9f9a9aa29d05370982e3b7bdf53b4b9879893a41a4ffa9e7fd72b39a8c1b2` |
| `LFM2.5-350M-QAD-Q4_0.gguf` (mirror) | `3d10b6ab8fc91a919534b9558e266255aca0bbc7f6d015963599aa9e74e05b1d` |
| `ggml-tiny.en-q5_1.bin` (mirror) | `c77c5766f1cef09b6b7d47f21b546cbddd4157886b3b5d6d4f709e91e66c7c2b` |

The runtime is `llama.cpp` (`0.7.0-dev`, build `11669`, commit `620d2225c`,
aarch64) plus `whisper.cpp`'s `whisper-cli`, both MIT-licensed.

---

## Troubleshooting

- **"missing libespeak-ng.so.1" / no voice** — install espeak-ng:
  `sudo apt-get install espeak-ng libespeak-ng1`.
- **No microphone** — dictation needs a **USB** microphone; the internal mic on
  the R36S is not usable. Check `arecord` / `aplay` and R2 > VOICE > TEST MICROPHONE.
- **The "227" system does not appear** — restart EmulationStation or reboot.
  Confirm `es_systems.cfg` contains `<name>227</name>`; select the correct card
  and run **UPDATE / REPAIR** to register it if missing.
- **Download fails** — check Wi-Fi, then press **A** to retry or relaunch the
  manager. App downloads resume; model downloads try the release mirror, then
  Hugging Face. See `installer.log` / `bootstrap.log` for errors.
- **First reply is slow** — the model loads into RAM on the first request; later
  replies are quicker. Settings shows engine state on the main screen.

---

## Credits and license

Created by **1Z99**. Application and artwork are **MIT** licensed (see
[`LICENSE`](LICENSE)). Bundled/used third-party projects — `llama.cpp`,
`whisper.cpp`, **LFM 2.5**, Whisper, SDL2, espeak-ng and DejaVu fonts — keep
their own licenses; see [`CREDITS.md`](CREDITS.md) and the
[`licenses/`](licenses) directory.
