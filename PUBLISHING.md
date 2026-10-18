# Publishing 227 AI

Maintainer notes for updating the existing repository and release assets.

## 1. Use the existing release

The repository is `1Z99/project-ai-r36s`. The existing release is
[v1.0.0](https://github.com/1Z99/project-ai-r36s/releases/tag/v1.0.0).
No new repository, tag or release is needed to add or refresh the canonical
`install227ai.sh` bootstrap asset.

The public download is:
`https://github.com/1Z99/project-ai-r36s/releases/download/v1.0.0/install227ai.sh`.
It bootstraps current manager modules and app code from `main`; the release
tag in this URL does not freeze those payloads. Existing stored manager modules
are reused, not automatically updated.

The deployed manager deliberately retains `tools/227-ai-manager/install.sh`
for compatibility with shipped Tools launchers. Do not rename that internal
path when updating the public installer filename.

## 2. Verify the source URLs

The bootstrap fetches all three `installer/installer_*.py` modules; the manager
fetches the app, assets, licenses and system files from
`https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/...`. After the
source update is published, sanity-check the canonical bootstrap:

```sh
curl -fsSL https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/install227ai.sh
```

## 3. Update assets on v1.0.0

After reviewing and verifying the local bootstrap, upload it to the existing
release. These are manual maintainer commands, not part of installation:

```sh
gh release view v1.0.0 --repo 1Z99/project-ai-r36s
gh release upload v1.0.0 install227ai.sh --clobber --repo 1Z99/project-ai-r36s
```

`gh` must already be authenticated. `--clobber` replaces an asset with the same
name; review the file before uploading. Keep the release description and user
instructions aligned with `install227ai.sh` and its current-code behavior.

The installer downloads the runtime from
`.../releases/latest/download/project-ai-r36s-runtime-arm64.tar.gz` (plus its
`.sha256` sidecar), and tries the two model mirrors there before Hugging Face.

Only if binary assets also need replacement, verify them against the manager
core's pinned hashes before uploading. Replacing a binary without matching
pins in the consuming core will make installs fail verification.

```sh
gh release upload v1.0.0 \
  project-ai-r36s-runtime-arm64.tar.gz \
  project-ai-r36s-runtime-arm64.tar.gz.sha256 \
  LFM2.5-350M-QAD-Q4_0.gguf \
  ggml-tiny.en-q5_1.bin \
  --clobber --repo 1Z99/project-ai-r36s
```

The two `.gguf` / `.bin` model files are not in this directory by default;
copy them from a working device (`/roms2/tools/project_ai/models/`) or download
them from Hugging Face:

```sh
curl -fL -o LFM2.5-350M-QAD-Q4_0.gguf \
  https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF/resolve/main/LFM2.5-350M-QAD-Q4_0.gguf
curl -fL -o ggml-tiny.en-q5_1.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en-q5_1.bin
sha256sum LFM2.5-350M-QAD-Q4_0.gguf ggml-tiny.en-q5_1.bin
```

Keep runtime archives, model mirrors and local build outputs out of source
commits. Review `.gitignore` when assembling release assets; any ignore-rule
changes are a separate maintainer task.

## 4. Test the published installer on a device

```sh
ssh ark@<device-ip>
cd /roms2/tools
curl -fL --connect-timeout 15 --max-time 120 -o install227ai.sh.download \
  https://github.com/1Z99/project-ai-r36s/releases/download/v1.0.0/install227ai.sh && \
mv install227ai.sh.download install227ai.sh && less install227ai.sh
```

Stop if the download or review fails. After a successful review, run
`sh install227ai.sh` on the handheld. The bootstrap opens the manager on the
handheld screen, not an interactive SSH terminal chooser.

Then restart EmulationStation and open the new **227** system.

## Local / offline testing

The installer honours development overrides so it can be tested without a
published release:

```sh
mkdir -p /tmp/test/card /tmp/test/themes
printf '<systemList></systemList>\n' > /tmp/test/es_systems.cfg
PROJECT_AI_RUNTIME_LOCAL=/path/to/runtime.tar.gz \
PROJECT_AI_RAW_BASE=file:///path/to/payload \
PROJECT_AI_REL_BASE=file:///path/to/release \
sh install227ai.sh --cli --roms /tmp/test/card --action install --yes \
  --source /path/to/payload --es-cfg /tmp/test/es_systems.cfg --themes /tmp/test/themes
```

Use `--dry-run` instead of `--yes` for read-only card discovery/validation.
The bootstrap uses the local checkout's installer modules without downloading
them. A local runtime requires its pinned `.sha256` sidecar; include the model
files at the source root to avoid network downloads.
