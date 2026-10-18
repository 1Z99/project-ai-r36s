# 227 AI — offline AI for the R36S

**227 AI** turns the Anbernic **R36S** handheld into a private, fully offline
AI assistant. It runs a small language model **entirely on the device** — no
internet, no cloud, no accounts. Ask questions with the on-screen keyboard,
dictate with a USB microphone, and hear the replies read aloud.

This repository contains the app, its artwork and licenses, and a **one-shot
installer** that sets everything up for you and adds a new **227** system page
to EmulationStation so you launch it like a game.

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
- **One-shot install** — download one script, run it, and 227 AI appears under a
  new EmulationStation system called **227**.
- **Chat with a real keyboard** — a full on-screen keyboard with word
  suggestions.
- **Read aloud** — offline text-to-speech with word-by-word highlighting.
- **Dictation** — offline speech-to-text with `whisper.cpp` (USB microphone).
- **Settings on the device** — change model, system prompt, generation options,
  speech rate and background from the app (press **R2**).
- **Chat history** — keep and reopen recent conversations.
- **No network access at runtime** — the network is only used by the installer.

---

## Requirements

- An **Anbernic R36S** running **dArkOSRE** (aarch64), or a compatible
  aarch64 ArkOS-style image with EmulationStation.
- A second SD card mounted at **`/roms2`** with about **400 MB free**.
- The app loads these from the operating system (usually already present):
  - Python 3
  - `libSDL2`, `libSDL2_ttf` (display + fonts)
  - `espeak-ng` / `libespeak-ng.so.1` (text-to-speech)
  - ALSA tools `arecord` / `aplay` (microphone + audio)
  - `curl` or `wget`, `tar`, `sha256sum` (installer)
- Internet access **only for the installer** (to download the runtime, model and
  speech-to-text model).

---

## Install

The installer downloads everything it needs (~350 MB) and registers the **227**
system page. It is **safe to re-run**: verified files are kept, interrupted
downloads resume, and the EmulationStation config is changed at most once
(a backup is written next to it).

### Option A — from the Tools menu

1. Connect the R36S to Wi-Fi (only so the installer can download).
2. Copy `install.sh` to **`/roms2/tools/`** on the second SD card — either by
   putting the card in a computer, or over SSH (see Option B's copy command).
3. On the handheld, open the **Tools** menu and run **install.sh**.
4. When it finishes, **restart EmulationStation** (or reboot).
5. Open the new **227** system and select **227 AI**.

### Option B — over SSH (recommended)

1. Find the device IP (Network Info in the device menu) and connect:

   ```sh
   ssh ark@<device-ip>          # default password: arkOS
   ```

2. Download and run the installer:

   ```sh
   cd /roms2/tools
   curl -fsSLO https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/install.sh
   sh install.sh
   ```

3. Restart EmulationStation (or reboot), open **227**, and select **227 AI**.

### Installer options

```
sh install.sh                 # normal install to /roms2
sh install.sh --dry-run       # show the plan without downloading anything
sh install.sh --prefix DIR    # install under DIR (testing)
sh install.sh --no-system     # do not touch the EmulationStation config
sh install.sh --es-cfg PATH   # use a specific es_systems.cfg
sh install.sh --force         # re-download and overwrite existing files
```

---

## What the installer does

| Path | Contents |
| --- | --- |
| `/roms2/tools/project_ai_r36s.py` | The application |
| `/roms2/tools/project_ai_settings.py` | Settings panel module |
| `/roms2/227ai/227 AI.sh` | EmulationStation launcher (the "ROM") |
| `/roms2/227ai/gamelist.xml` + `227-art.jpg` | System listing and cover art |
| `/roms2/tools/project_ai/runtime/` | `llama.cpp` server + libraries |
| `/roms2/tools/project_ai/whisper/whisper-cli` | Speech-to-text engine |
| `/roms2/tools/project_ai/models/` | LFM 2.5 model + whisper model |
| `/roms2/tools/project_ai/logo.rgba`, `intro-*.{zip,rgba}`, `words.txt` | Interface artwork and word list |
| `/roms2/tools/project_ai/config.json` | Your settings (written once) |
| `/roms2/tools/project_ai/licenses/` | Third-party license texts |
| `/etc/emulationstation/es_systems.cfg` | Patched to add the **227** system (backup: `es_systems.cfg.pre-227ai`) |

Your chats (`chats.json`) and history (`history.json`) live in
`/roms2/tools/project_ai/` and are never touched by the installer.

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

Downloads are attempted from Hugging Face first, then from this repository's
[releases](https://github.com/1Z99/project-ai-r36s/releases) as a mirror, and
are verified against a checksum before use. The model is licensed under the
**LFM Open License v1.0** — see `licenses/LFM2.5-Open-License.txt`.

---

## Uninstall / rollback

```sh
rm -rf /roms2/tools/project_ai /roms2/tools/project_ai_r36s.py \
       /roms2/tools/project_ai_settings.py /roms2/227ai

# remove the EmulationStation system (restores the pre-install config)
sudo cp /etc/emulationstation/es_systems.cfg.pre-227ai \
        /etc/emulationstation/es_systems.cfg
```

Your `chats.json` / `history.json` are deleted with `project_ai/`; back them up
first if you want to keep them.

---

## Repository layout

```
install.sh                              the one-shot installer
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
  the R36S is not usable. The installer warns if `arecord` is missing.
- **The "227" system does not appear** — restart EmulationStation or reboot.
  Confirm `es_systems.cfg` contains `<name>227</name>`; re-running the installer
  will register it if it is missing.
- **Download fails** — connect to Wi-Fi and re-run `install.sh`; partial
  downloads resume. As a fallback the installer mirrors models from the GitHub
  release.
- **First reply is slow** — the model loads into RAM on the first request; later
  replies are quicker. Settings shows engine state on the main screen.

---

## Credits and license

Created by **1Z99**. Application and artwork are **MIT** licensed (see
[`LICENSE`](LICENSE)). Bundled/used third-party projects — `llama.cpp`,
`whisper.cpp`, **LFM 2.5**, Whisper, SDL2, espeak-ng and DejaVu fonts — keep
their own licenses; see [`CREDITS.md`](CREDITS.md) and the
[`licenses/`](licenses) directory.
