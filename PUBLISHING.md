# Publishing 227 AI

Maintainer notes for updating the existing repository and release assets.

**Unreleased local changes:** automatic dependency setup and content-based update
results are under device test. The canonical published bootstrap asset still
lacks this code. The local bootstrap/manager version remains **1.2.0**; do not
describe these changes as a shipped 1.3 release. Remove the short development
notices in the README and website only when publication and verification justify it.

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

The local bootstrap fetches all four modules: `installer_core.py`,
`installer_ui.py`, `installer_cli.py` and `installer_deps.py`, from
`https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/installer/...`.
If any stored module is absent, it refreshes the entire matching set; a complete
set remains cached, without automatic manager updates. Keep bootstrap and all
four modules aligned when manually updating an existing manager.

For default online app install/update, the core resolves `main` via the GitHub
commits API and fetches app/assets/licenses/system files from the resulting
immutable commit, not a changing branch during staging. An API/rate-limit or
source-fetch failure must fail the check, not silently use cache to claim current
source. `--source`/`bundle/` compares local contents and must be identified as local
in the UI. After the source update is published, sanity-check the canonical bootstrap:

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
Publish the matching helper and source changes before refreshing the asset, so
the new bootstrap can fetch all four modules and bundled fonts/license. App
Update / Repair is not a manager updater, even after this publication.

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

Run the focused installer regression suites, including the new dependency
helper, without treating the historical 85 tests at `c83568c` as a current total:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_bootstrap.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_installer_*.py' -v
```

The helper tests mock host/package boundaries; do not test package installation
on the development PC. Verify capability-complete devices perform no package
queries or system changes, and test missing-tool setup only on an approved
handheld. It must require Debian/Ubuntu-family aarch64 with dpkg `arm64` and root
or `sudo -n`, install only an audited, version-pinned missing dependency closure
with `--no-upgrade --no-remove --no-install-recommends`, and reject protected
graphics/firmware changes. Index refresh is only a fallback after failed initial
simulation. Existing broken/old Python must fail without upgrade; missing Python
or `curl` is handled by the shell before Python starts. Manager SDL/`curl` checks
precede display startup; confirmed app install/update checks all dependencies,
including eSpeak English data, ALSA tools and procps. Help, desktop, dry-run and
uninstall must skip automatic setup. Check SHA-256-validated regular/bold fonts
and their license in manager `fonts/`, and bundled app-font fallback.

For content-based results, verify a second identical install/update returns
`up_to_date` without rewriting app files, while changed bytes report
"Updated/repaired files" and missing or invalid files trigger repair. Test actual
model/runtime hashes, executable permissions, config existence and owned ES/theme
metadata; preserve ES statistics and ignore harmless XML rewrites. Do not invent
a version baseline to distinguish a new version from corruption. Keep source/API
failures distinct from a successful no-op, and label local-bundle comparisons.
Allow about 25 MiB staging when installed models/runtime verify versus a 650 MiB
fresh budget, not an exact install size. System packages use separate system
partition space and remain after uninstall; they are outside app rollback.
Cancellation must wait for running APT to finish before taking effect. Do not
power off during setup or finalization. None of this certifies all firmware.

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
