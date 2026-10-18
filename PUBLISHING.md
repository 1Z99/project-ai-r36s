# Publishing 227 AI

Maintainer notes for publishing this repository and its first release.

## 1. Create the repository

```sh
cd 227-ai-release
git init -b main
git add .
git commit -m "227 AI for the R36S"
gh repo create 1Z99/project-ai-r36s --public --source=. --push
```

> `gh auth login` must be done once first. The source tree contains only text
> and small assets; the runtime tarball and model mirrors are uploaded to the
> release in step 3 (see `.gitignore`).

## 2. Verify the raw URLs

The installer fetches the app, assets, licenses and system files from
`https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/...`. After the
push, sanity-check one:

```sh
curl -fsSL https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/install.sh | head
```

## 3. Create the release and upload the binary assets

The installer downloads the runtime from
`.../releases/latest/download/project-ai-r36s-runtime-arm64.tar.gz` (plus its
`.sha256` sidecar), and mirrors the two model files there as a fallback.

```sh
gh release create v1.0.0 \
  --title "227 AI v1.0.0" \
  --notes "First release: offline AI for the R36S. Run install.sh to set up." \
  project-ai-r36s-runtime-arm64.tar.gz \
  project-ai-r36s-runtime-arm64.tar.gz.sha256 \
  LFM2.5-350M-QAD-Q4_0.gguf \
  ggml-tiny.en-q5_1.bin
```

The two `.gguf` / `.bin` model files are not in this directory by default —
copy them from a working device (`/roms2/tools/project_ai/models/`) or download
them from Hugging Face:

```sh
curl -fL -o LFM2.5-350M-QAD-Q4_0.gguf \
  https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF/resolve/main/LFM2.5-350M-QAD-Q4_0.gguf
curl -fL -o ggml-tiny.en-q5_1.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en-q5_1.bin
sha256sum LFM2.5-350M-QAD-Q4_0.gguf ggml-tiny.en-q5_1.bin
```

## 4. Test the published installer on a device

```sh
ssh ark@<device-ip>
cd /roms2/tools
curl -fsSLO https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/install.sh
sh install.sh
```

Then restart EmulationStation and open the new **227** system.

## Local / offline testing

The installer honours development overrides so it can be tested without a
published release:

```sh
PROJECT_AI_RUNTIME_LOCAL=/path/to/runtime.tar.gz \
PROJECT_AI_RAW_BASE=file:///path/to/payload \
PROJECT_AI_REL_BASE=file:///path/to/release \
sh install.sh --prefix /tmp/test --es-cfg /tmp/test/es_systems.cfg
```
