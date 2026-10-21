# The 1Z99 Personal Website

The public site lives in `docs/`: static HTML, CSS, JavaScript, JSON and media.
No build framework, npm installation, external fonts, analytics, account service
or backend is required. Keep maintainer notes and developer tools outside that
deployed tree. See [web media](dev/WEB_MEDIA.md) for regeneration recipes and
[publishing](PUBLISHING.md#website-only-publication) for the separate approval gate.

## Preview Locally

From the repository root:

```sh
python3 tools/preview_site.py
```

Open **http://127.0.0.1:2270/**. Stop that server with Ctrl+C when finished.
The pages themselves are readable when opened as files, but search and image
configuration need the HTTP preview because browsers restrict local JSON fetches.

The preview is bound to this computer only. It does not connect to the R36S,
upload files or publish the site.
Use this supplied server rather than plain `python3 -m http.server`: it supports
HTTP byte ranges, which browsers need for reliable video scrubbing and scene
selection. It is still only Python's standard library, with no backend service
or build dependency required for production hosting.

## Find Your Way Around

| File | Purpose |
| --- | --- |
| `index.html` | Personal 1Z99 homepage: Projects, About and My R36S |
| `projects/227-ai/index.html` | 227 AI overview and links to its documentation |
| `projects/227-ai/guide.html` | Installation, SD selection, updates, backups and recovery |
| `projects/227-ai/reference.html` | Controls, settings, speech, models, paths, privacy and licenses |
| `projects/227-ai/development.html` | Tested environment, architecture, safety and verification |
| `projects/227-ai/gallery.html` | Installer demo and four screenshots, eight app screenshots and capture provenance |
| `projects/227-tv/index.html` | 227 TV overview: the Omarchy plugin port, ownership and hardware status |
| `projects/227-tv/guide.html` | Offline bundle, Install 227 TV manager, shared 227 menu, updates, safety and CLI |
| `projects/227-tv/reference.html` | Controls, tabs, Start menu, caches, storage, privacy and licenses |
| `projects/227-tv/development.html` | Tested environment, globe rendering, private libmpv build, safety and verification |
| `projects/227-tv/gallery.html` | Seven app and two manager screenshots, real France 24 playback provenance and broadcast credits |
| `projects/227-tv/screenshots.json` | Nine native 227 TV PC captures and scripted interface states, shared by TV pages and the homepage card |
| `site.css` | Dark layout, responsive documentation navigation and print styles |
| `site.js` | Local search, clipboard, filters, image viewer and walkthrough controls |
| `screenshots.json` | Twelve current native 227 AI captures, shared by opted-in pages |
| `search-index.json` | Generated searchable text from HTML throughout `docs/` |
| `media/previews/installer/` | Native installer PNGs, `demo.json` and silent 14-second `installer-demo.mp4` |
| `media/previews/app/` | Eight native lossless app PNGs and `captures.json` |
| `media/previews/` | Preserved 78-second app walkthrough, captions and `walkthrough.json` |
| `media/227-tv/` | Nine native lossless TV PNGs, playback provenance in `captures.json` and broadcast credits in `NOTICE.md` |
| `LICENSE.txt` | Exact copy of the project's MIT notice, including its third-party notice |

The site has eleven HTML pages: the homepage, five 227 AI pages and five 227 TV
pages. Paths in this table are relative to `docs/`, the deployed site root. The local
homepage is **http://127.0.0.1:2270/**; the project overview is
**http://127.0.0.1:2270/projects/227-ai/index.html**. For example, open screenshots
at **http://127.0.0.1:2270/projects/227-ai/gallery.html**, not `/gallery.html`.

The global identity is **1Z99**, with Projects, About and My R36S navigation to
`index.html#projects`, `index.html#about` and `index.html#device`. Each project
has its own scoped navigation and breadcrumbs back to 1Z99 / Projects / project.
From a 227 AI page, shared HTML assets use `../../site.css`, `../../site.js` and
`../../media/...`; sibling documentation links use names such as `guide.html`.
JavaScript resolves shared resources and search destinations relative to the
`site.js` URL, not the current page. Keep these resources at the site root;
site-root-relative here does not mean a leading `/`, which would break a
deployment under a repository subdirectory.

Search opens with the header button, `/`, or Ctrl+K / Cmd+K. It searches all
eleven pages locally. Escape closes it; Arrow Down enters results; Enter opens a result.
The search dialog includes a switch to disable the single-character `/` shortcut;
the Search button and Ctrl+K / Cmd+K remain available.
The Field Manual, Get Started and Build Notes pages can be printed from the
browser. The top-right Menu button opens the navigation on phones and desktops;
Escape or an outside click closes it. Desktop navigation also remains inline.
The site stays dark; there is no light-theme toggle.

## Capture Sources

The 227 AI gallery starts with a **14-second silent installer PC demo** and four native
640 x 480 PNGs: card selection, confirmation, simulated download and success.
The app section follows with eight fresh, lossless 640 x 480 PNGs: chat, keyboard,
speech, voice, dictation, history, system prompt and generation settings.
All twelve manifest entries use `origin: "pc-demo"` and `method: "native-render"`,
with no video `time`; their badges distinguish **PC demo / native render**.
There are no pending-image tiles in the current gallery.

### 227 TV Captures

The 227 TV project uses `docs/projects/227-tv/screenshots.json` with media under
`docs/media/227-tv/`. Its [gallery](docs/projects/227-tv/gallery.html) shows nine
lossless native 640 x 480 PC captures: seven app views (`world`, `country`,
`search`, `watch`, `matrix`, `settings`, `quality`) and two manager states
(`installer`, `confirm`), each saved as `<id>.png`. All use
`origin: "pc-demo"` / `method: "native-render"`.
The four playback-session entries also set `realPlayback: true`, displaying
**PC playback / native capture** instead of the scripted-state demo badge.

`dev/render_tv_captures.py` starts real France 24 playback through production
`app.App.finish_player`, `App.update` and `App.draw`; installer screens use
`tv_manager.draw`. Watch shows France 24 French; matrix shows four distinct
France 24 language feeds (French, English, Arabic, Spanish), not four unrelated
stations. Browser/country/search remain scripted fixtures with four authentic
entries under France and a `france` query, not a downloaded world catalog.
Settings and quality menus cover the active single-feed session with opaque UI;
quality choices come from the broadcaster. Installer cards are examples only.

Broadcast images and station marks belong to France Medias Monde and the
respective rights holders, not the site's MIT license. Preserve the sources and
rights information in [NOTICE.md](docs/media/227-tv/NOTICE.md); no endorsement or
broad redistribution permission is implied.

From this repository root, use a trusted TV source checkout and source-compatible
host SDL2/SDL2_ttf and libmpv libraries. Override the source checkout's private
playback runtime to a known compatible host library rather than loading the
shipped ARM64 binary. No FFmpeg executable is required:

```sh
python3 dev/render_tv_captures.py --validate-only
python3 dev/render_tv_captures.py --capture-streams --source-dir /path/to/melkor227-tv
python3 dev/render_tv_captures.py --capture-streams --source-dir /path/to/melkor227-tv --output-dir /existing/output/tv
```

`--validate-only` prints a pure plan without production/native-library imports,
file I/O or network access; it needs no source checkout. Capture requires explicit
`--capture-streams` opt-in: there is no network access by default. Normal output
defaults to `docs/media/227-tv/`, and `--output-dir` must name an existing directory.
Rendering uses temporary isolated state, dummy/software SDL video and null/disabled
audio output. App workers, health scanning, card discovery, installation and state
saving are blocked; no handheld, controller, audio device or personal saved state
is accessed. Network requests and native decoding do occur during capture. Site
visitors load only local media, not streams or external media resources.

The generator stages all nine PNGs before per-file replacement and writes
`captures.json` last. It records source URLs/timestamps, catalog/source/asset
SHA-256 hashes, per-scene playback details and output PNG sizes/hashes. The current
watch capture records 151 observed frames; matrix records 151-153 for each of the
four feeds, all from 640 x 360, 25 fps sources. These counts are non-empty
production `Player.frame()` RGBA renders, not internal decoder totals or measured
performance. The stock app's `--scene matrix` preview still shows unavailable
cells; this generator instead exercises real playback. Keep these PC captures
separate from audio verification, handheld benchmarks and the dated hardware
record. See [web media](dev/WEB_MEDIA.md) for the output map and regeneration details.

### 227 AI Captures

These captures use the production installer UI and the app's actual `draw_ui`
and `SettingsPanel`, with scripted, memory-only fixtures. The installer never
runs the real worker, card discovery, package changes, network access, device
input or installer logging. App captures do not start audio, a microphone or a
model, or read/write personal history or configuration. They are simulations,
not actual installations, handheld captures, live answers or asset/performance
benchmarks.

Regenerate from the repository root with existing system SDL2/SDL2_ttf libraries
and, for the installer video, FFmpeg with libx264:

```sh
python3 dev/render_installer_demo.py --validate-only
python3 dev/render_installer_demo.py
python3 dev/render_installer_demo.py --skip-video --output-dir /existing/output/installer
python3 dev/render_app_captures.py --validate-only
python3 dev/render_app_captures.py
python3 dev/render_app_captures.py --output-dir /existing/output/app
```

Defaults are `docs/media/previews/installer/` and `docs/media/previews/app/`.
Validation prints plans without loading SDL or writing files; `--skip-video`
generates only installer PNGs and metadata. App captures do not require FFmpeg.
See [the web media notes](dev/WEB_MEDIA.md) for output maps and details.
These native states are separate from the original **78-second app walkthrough**.
Its MP4, audio, captions and metadata are retained; video-frame extraction is
not part of the capture workflow.

## Capture Manifests

The [root manifest](docs/screenshots.json) contains only the twelve current
227 AI native captures; the [TV manifest](docs/projects/227-tv/screenshots.json)
contains nine more, for twenty-one manifest images total. The
[227 AI gallery](docs/projects/227-ai/gallery.html) presents
their provenance, not a photo placeholder or browser-local capture checklist.

Capture loading is opt-in on the page's body:

```html
<body data-project="227-ai" data-captures="screenshots.json">
```

`data-project` identifies the project; `data-captures` selects its manifest.
The manifest URL, like each image's `src`, resolves from the shared `site.js`
directory. Keep `screenshots.json` unchanged in this attribute even on nested
227 AI pages; do not use `../../screenshots.json` or `/screenshots.json` here.
Pages without captures should omit `data-captures` rather than load 227 AI's
manifest automatically. Ordinary HTML links to that manifest from 227 AI pages
still use `../../screenshots.json`.

The personal homepage selects `data-captures` on each project's card, not
on its body. TV pages use `data-captures="projects/227-tv/screenshots.json"`.
For multiple projects, set `data-captures` on each card or figure
to select its own manifest or override a body's manifest. A future project can use its own
`projects/<slug>/screenshots.json`; its image paths still resolve from the site
root. The loader fetches each selected manifest once per page.

Use forward slashes, preserve valid JSON, and use only site-local paths under
`media/`. The loader deliberately refuses remote URLs. If a configured file is
missing or cannot decode, the placeholder stays in place. Clear `src` back to
`null` to intentionally remove an image.

Future device captures should use an intentionally created project media folder,
for example `docs/media/227-ai/`; no empty capture directory is required at
checkout. Prefer lossless 640 x 480 PNGs, preserve aspect ratio and remove private
information. Describe the real image in `alt` and its visible caption; when
replacing a PC render, use `origin: "device"` and remove `method: "native-render"`.
Do not label a PC simulation as handheld evidence. Adding a manifest entry alone
does not add a figure: review the corresponding HTML explicitly.

## Update Documentation

### Add Another Project

The homepage lists **1Z99's R36S apps**, not just 227 AI. Each item in
`ol.project-cards` contains an `article.project-feature`. Add the next project
as a sibling list item using the same bordered card style, with a unique ID,
description, screenshot and text links. Do not replace this with a compact
sidebar list or change the established page layout.

Give each project an independent information folder, `docs/projects/<slug>/`,
with its own `index.html` overview and any guide, reference, development or
gallery pages it needs. Link the homepage card to `projects/<slug>/index.html`;
do not mix another project's instructions into the 227 AI section. Keep the
global 1Z99 navigation, add project-scoped navigation and breadcrumbs, and use
`data-project="<slug>"` on that project's pages. Use a title such as
`Overview | Project Name | 1Z99` so search can distinguish projects.

Reuse the root stylesheet and script rather than copying them into each project.
The existing `docs/screenshots.json` and `docs/media/` assets stay where they are.
If a new project needs captures, give it a separate manifest, for example
`docs/projects/<slug>/screenshots.json`, and opt in with
`data-captures="projects/<slug>/screenshots.json"`. Its image paths still resolve
from `site.js` at the site root, not from its manifest folder. Keep new media
distinct, for example under `docs/media/<slug>/`, without moving the current
227 AI assets or changing application/binary paths.

The homepage illustration is the original inline `svg.handheld-illustration`.
Its screen contains the **78-second automated walkthrough**, rendered from the
actual app UI with the original intro, frame-19 background, keyboard typing,
sample replies, read-aloud, simulated mic test/dictation, history and closing.
The existing lossless LFM capture `docs/media/previews/app/chat.png` supplies
both its SVG fallback and video poster; no separate poster render is needed.
The MP4, VTT captions and `walkthrough.json` all live under `media/previews/`.
They are explicitly a desktop demonstration, not live inference or a real R36S
recording. No microphone permission is requested; mic playback is prerecorded
synthetic speech. Generated audio uses offline eSpeak NG, not a cloud API.

The walkthrough autoplays **muted** while visible, loops, and pauses when hidden.
Reduced-motion preference disables autoplay. The controls support Play/Pause,
Replay, optional sound, scrubbing, scene jumps, captions and an expanded player.
Sound is opt-in every page load. Expand moves the same player into a dialog,
preserving its current position and mute state. Without JavaScript, the direct
video link and native player remain available.

The nine scenes and their times are generated in `walkthrough.json`; update the
renderer rather than hand-editing those times. Regeneration and speech dependency
instructions are in [the web media notes](dev/WEB_MEDIA.md).
The handheld's geometry is independent of the homepage copy. Panel 4 is the owner's reported
screen identification, not a promise about every R36S display variant.

### Edit A Guide

Edit the static HTML directly under the relevant project's folder. Keep the
`id` on each section stable so links and bookmarks remain valid. A section with
`data-search-section` is included in site-wide search. The search builder
recursively discovers all HTML under `docs/`, including new project folders;
there is no fixed page list to extend. Search URLs retain their path from the
site root, such as `projects/227-ai/guide.html#easy-install`. Project entries
are labeled `227 AI / Get Started`, `227 AI / Field Manual`, and so on; personal
homepage entries are labeled `1Z99`. After changing searchable content or adding
a project, regenerate the shared root index:

```sh
python3 tools/build_site_index.py
python3 tools/build_site_index.py --check
python3 -m unittest discover -s tests -p test_website.py -v
```

For the entire application and website validation suite, including automatically
discovered installer-demo and native app-capture tests:

```sh
python3 -m unittest discover -s tests -q
```

An optional real-browser suite is included in `tools/test_site.mjs`. It needs a
separately installed Playwright package and Chromium; neither is a site runtime
dependency. With the preview server running, for example:

```sh
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright/index.mjs \
CHROMIUM_EXECUTABLE=/path/to/chromium \
node tools/test_site.mjs
```

If Playwright can already be resolved by Node, omit `PLAYWRIGHT_MODULE`. Omit
`CHROMIUM_EXECUTABLE` to use Playwright's installed Chromium. Set `SITE_URL` for
a different preview address. Browser screenshots and a printable PDF go into
the temporary directory reported by the test, never into the website. The
suite checks four viewport sizes, search, copy, filters, responsive
image replacement, keyboard behavior, printing and no-JavaScript reading.

The **85 passing tests** documented in Build Notes describe the original
`c83568c` app/manager revision, not the current total after website tests are
added. Keep that historical distinction. Likewise, manager **1.2.0** and the
binary release **v1.0.0** are different versioned things.

Keep hardware claims tied to a dated source revision and the actions checked;
do not turn one device run into universal firmware support or invent performance
numbers. Installer bootstrap trust and manager/app update boundaries are described
in [publishing](PUBLISHING.md), independently of website publication.

### Licenses And Credits

Keep [the public license](docs/LICENSE.txt) identical to [LICENSE](LICENSE).
The app, site and original media are MIT-licensed; bundled third-party components
retain their own terms in [CREDITS.md](CREDITS.md) and `licenses/`. Preserve the
public license/provenance and footer links, including the southoz credit.

## Hosting

After explicit maintainer approval, `docs/` can be served as a static directory.
GitHub Pages supports a repository-branch source using `main` and `/docs`;
`.nojekyll` keeps these files as plain static content. No website workflow is
needed. All navigation and asset paths are relative so repository subpaths work.
The personal homepage is at the deployment root and 227 AI lives under
`projects/227-ai/`. There are no redirects for the old root-level guide,
reference, development or gallery URLs: that flat layout was never published.
Keep the current nested project paths stable instead of adding compatibility
pages for the retired local layout.

Do not change publishing settings or push until the maintainer approves the
preview and reviewed file scope. Follow the website-only steps in
[PUBLISHING.md](PUBLISHING.md#website-only-publication), including remote MIME and
byte-range checks after deployment. Do not put models, device logs, private
recordings, personal chat data, SSH keys or environment files into this site.
