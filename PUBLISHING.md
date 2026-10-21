# Publishing 227 AI

Maintainer notes for updating the existing repository and release assets.
Website-only publication is a [separate process](#website-only-publication);
it does not require installer release uploads, asset replacement or new tags.

The bootstrap and manager report version **1.2.0**; do not describe dependency
setup or content-based update results as a shipped 1.3 release unless the version
constant is bumped.

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

The deployed manager retains `tools/227-ai-manager/install.bootstrap`, not a
`.sh` file: EmulationStation scans subfolders and would otherwise expose the
manager folder as a duplicate tool. The bootstrap publishes the new internal
file and **Install 227 AI** shortcut before removing legacy manager `install.sh`.
Existing users must run the updated public download once for this migration;
cached managers and app Update / Repair do not fetch a newer bootstrap.

## 2. Verify the source URLs

The bootstrap fetches all four modules: `installer_core.py`,
`installer_ui.py`, `installer_cli.py` and `installer_deps.py`, from
`https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/installer/...`.
If any stored module is absent, it fetches all four sequentially from mutable
`main`. These fetches are not pinned to one immutable revision and do not
guarantee a matching or atomic module set if the branch changes between requests.
A complete stored set remains cached, without automatic manager updates. Keep
bootstrap and all four modules aligned when manually updating an existing manager.
The bootstrap and manager sources are trusted executable code fetched over HTTPS,
not independently authenticated by an immutable release tag or content signature.

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
Publish the required helper and source changes before refreshing the asset, so
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

## Website-Only Publication

The site is plain static content in `docs/`; no website deployment workflow is
needed. Keep this process separate from the installer-release steps above:
do not run `gh release upload`, clobber release assets, create tags or replace
runtime/model files for a website-only change.

### Review Scope

Review the exact file list and diff before staging, committing or pushing. The
website scope is `docs/` public HTML/CSS/JS, manifests, search index, `.nojekyll`,
the exact `LICENSE.txt` copy of `LICENSE`, and approved media. Retain the twelve
AI captures, nine TV captures and their metadata, both MP4s, VTT and walkthrough
metadata.
Remove retired video-frame PNGs, the separate old homepage poster and empty media
placeholders rather than deploying them. Maintainer notes belong in
[SITE_MAINTENANCE.md](SITE_MAINTENANCE.md) and [dev/WEB_MEDIA.md](dev/WEB_MEDIA.md),
not under `docs/`.

Review supporting changes individually: `PUBLISHING.md`, `tools/preview_site.py`,
`tools/build_site_index.py`, `tools/test_site.mjs`, the four retained generators
`dev/render_installer_demo.py`, `dev/render_app_captures.py`,
`dev/render_web_walkthrough.py` and `dev/render_tv_captures.py`, deletion of the
retired frame-extraction helper, and `tests/test_website.py`,
`tests/test_site_preview.py`, `tests/test_installer_demo.py`,
`tests/test_app_captures.py`, `tests/test_web_walkthrough.py` and
`tests/test_tv_captures.py`. Do not stage unrelated application, installer,
binary, model, device-log, personal-data or workstation changes. Avoid blanket
`git add .`; approve an explicit file list.

Preserve the personal 1Z99 root and nested `projects/227-ai/` hierarchy. Shared
assets, search and capture resources resolve from `site.js` so repository subpaths
work; do not introduce leading-slash asset URLs. No compatibility redirects are
needed for old flat project URLs from the unpublished layout. Preserve the
public license, provenance links, third-party terms and southoz credit.

### Validate Locally

Run from the repository root after coordinating the review/test pass:

```sh
python3 tools/build_site_index.py
python3 tools/build_site_index.py --check
python3 -m unittest discover -s tests -p 'test_website.py' -v
python3 -m unittest discover -s tests -p 'test_site_preview.py' -v
python3 -m unittest discover -s tests -q
python3 tools/preview_site.py
```

The last command starts the loopback preview at `http://127.0.0.1:2270/`; stop
it with Ctrl+C after review. With it running, use a second terminal for optional
browser checks with separately installed Playwright and Chromium:

```sh
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright/index.mjs \
CHROMIUM_EXECUTABLE=/path/to/chromium \
node tools/test_site.mjs
```

Review desktop/mobile layout, keyboard navigation, local search, all twenty-one
gallery images, license/credit links, muted walkthrough playback, optional sound,
captions, seeking, reduced motion and no-JavaScript fallback. Verify a
repository-subpath preview as well as the root preview. Local tests do not prove
the eventual host's MIME types or byte-range behavior.

### Approve And Deploy

Only after explicit maintainer approval of the preview and reviewed file scope,
commit and push the approved changes to `main`. Then configure GitHub Pages to
**Deploy from a branch**, selecting **main** and **/docs**, if that source is not
already selected. Keep `.nojekyll`; do not add a website workflow. Do not change
Pages settings or push as part of preparation alone.

### Check The Actual Host

After deployment, validate the actual Pages URL, including its repository path,
not just the local server. For example, set `SITE_URL` to the approved deployed
root and inspect response headers:

```sh
SITE_URL=https://1z99.github.io/project-ai-r36s
curl -fsSI "$SITE_URL/"
curl -fsSI "$SITE_URL/media/previews/app/chat.png"
curl -fsSI "$SITE_URL/media/previews/227-ai-walkthrough.vtt"
curl -fsSI "$SITE_URL/media/previews/227-ai-walkthrough.mp4"
curl -fsS -D - -o /dev/null -H 'Range: bytes=0-1023' \
  "$SITE_URL/media/previews/227-ai-walkthrough.mp4"
curl -fsS -D - -o /dev/null -H 'Range: bytes=0-1023' \
  "$SITE_URL/media/previews/installer/installer-demo.mp4"
```

Expect appropriate `Content-Type` values (`text/html`, `image/png`, `text/vtt`,
`video/mp4`); check CSS, JavaScript and JSON types too. Both range GETs must
return `206 Partial Content`, a correct `Content-Range` and the requested bytes;
an `Accept-Ranges` header alone is not proof. Open the deployed nested pages and
verify search destinations, capture loading, license/footer links, both videos,
walkthrough captions and seeking in a real browser. Resolve hosting failures
before treating publication as verified.
