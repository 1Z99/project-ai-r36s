# Credits

227 AI is built by **1Z99** and stands on the shoulders of several excellent
open-source projects. The components below ship with, or are used by, 227 AI.

## 227 AI application

- **Author / maintainer:** 1Z99
- **License:** MIT (see [`LICENSE`](LICENSE))

## Firmware and platform

Special thanks to **southoz and the dArkOSRE-R36 contributors** for the
firmware used on the R36S where 227 AI is developed and tested.

- Project: [dArkOSRE-R36](https://github.com/southoz/dArkOSRE-R36)
- The firmware is installed separately; it is not bundled with 227 AI.

## Inference runtime

- **llama.cpp** — the local LLM inference server bundled in
  `project_ai_r36s-runtime-arm64.tar.gz` (`runtime/`).
  - Upstream: https://github.com/ggml-org/llama.cpp
  - Version: `0.7.0-dev`, build `11669`, commit `620d2225c` (aarch64)
  - License: MIT — Copyright (c) 2023-2026 The ggml authors
  - Full text: [`licenses/llama.cpp-MIT.txt`](licenses/llama.cpp-MIT.txt)

- **whisper.cpp** — the speech-to-text engine (`whisper/whisper-cli`).
  - Upstream: https://github.com/ggml-org/whisper.cpp
  - License: MIT — Copyright (c) 2023-2026 The ggml authors
  - Full text: [`licenses/whisper.cpp-MIT.txt`](licenses/whisper.cpp-MIT.txt)

## Models

- **LFM2.5-350M** (`LFM2.5-350M-QAD-Q4_0.gguf`)
  - Upstream: https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF
  - Author: Liquid AI, Inc.
  - License: **LFM Open License v1.0** (redistribution permitted; see terms)
  - Full text: [`licenses/LFM2.5-Open-License.txt`](licenses/LFM2.5-Open-License.txt)

- **Whisper tiny.en** (`ggml-tiny.en-q5_1.bin`)
  - Upstream: https://huggingface.co/ggerganov/whisper.cpp
  - Model: OpenAI Whisper `tiny.en`, converted to GGML by ggml.org
  - License: MIT (whisper.cpp distribution)

## Supporting libraries

- **SDL2 / SDL2_ttf** — window, input and text rendering. Loaded from the
  device operating system (`libSDL2`, `libSDL2_ttf`); not bundled with 227 AI.
  Licensed under the zlib license. https://www.libsdl.org/

- **espeak-ng** — offline text-to-speech. Loaded from the device operating
  system (`libespeak-ng.so.1`); not bundled with 227 AI.
  - Upstream: https://github.com/espeak-ng/espeak-ng
  - License: GNU GPL v3.0
  - See [`licenses/espeak-ng-NOTICE.txt`](licenses/espeak-ng-NOTICE.txt)

- **DejaVu Sans Mono** regular and bold fonts — bundled SD-card assets for the
  manager and app, also used by the optional PC preview. The bootstrap
  SHA-256-validates the manager's `fonts/` copies and license; the app keeps
  copies in its data root. Both can fall back to bundled fonts when system fonts
  are unavailable.
  - Upstream: https://dejavu-fonts.github.io/
  - License: Bitstream Vera / public-domain additions (permissive)
  - See [`licenses/DejaVu-fonts-copyright`](licenses/DejaVu-fonts-copyright)

## Artwork

- The 227 AI background, logo and cover art were created by **1Z99** and are
  released under the same MIT license as the application.
- The original 1Z99 website, handheld illustration, original demo content and
  captions are also covered by the project MIT license. The 227 AI demos use
  scripted sample data, not private conversations or recordings. Third-party
  projects and components retain their own licenses and credits above.
- The 227 TV website playback screenshots contain real **France 24** broadcast
  frames, decoded in the native app on a PC. Broadcast images and station marks
  belong to **France Medias Monde** and the respective rights holders, not the
  project's MIT license. No broadcaster endorsement is implied, and public stream
  listings do not grant redistribution rights. Sources and capture provenance are
  linked in [`docs/media/227-tv/NOTICE.md`](docs/media/227-tv/NOTICE.md).

---

If you believe your work is used here without proper attribution, please open
an issue on the repository.
