# 227 AI README Demo

`227-ai-demo.gif` is a GitHub README-compatible, silent animation of the full
78-second PC walkthrough, from startup through closing the app. Replies are
scripted sample answers and microphone input is simulated, not a real handheld
or microphone capture. The separate release-upload MP4 includes the original
audio; the GIF cannot play sound.

The 360 x 470 R36S artwork is extracted from the original homepage, with its
production dark styles preserved in self-contained `r36s.svg`. The video fills
the native 4:3 screen at `(72, 56, 216, 162)`. No player controls, poster image,
HTML, external fonts or outside caption are embedded. The GIF loops at 8 fps
with a global 128-color palette and no dithering.

## Regenerate

From the repository root, with the original walkthrough downloaded locally:

```sh
python3 dev/build_readme_demo.py \
  --video /path/to/227-ai-walkthrough.mp4 \
  --mp4 /tmp/227-ai-demo.mp4
```

`--video` also accepts an explicit HTTP(S) release asset URL. No download or
publication happens unless requested by that input. `--output-dir` defaults to
`assets/readme` and must exist. The optional MP4 must be outside the repository.
Only the SVG and original walkthrough are needed, not the unpublished website.

Requires Python's standard library, FFmpeg/ffprobe, Node, Chromium and an already
installed Playwright module. Set `PLAYWRIGHT_MODULE` to its absolute module path
if Node cannot resolve `playwright`, and `CHROMIUM` to the browser executable if
it is not on PATH. No packages are installed and rendering blocks network requests.

To explicitly refresh the artwork from the local homepage and its stylesheet,
append `--extract-art-from docs/index.html`. This captures Chromium-computed
class styles, removes the embedded player and poster, and preserves the original
SVG geometry. Normal regeneration uses the saved SVG without reading the site.

Original artwork and demo: **1Z99**, copyright 2026, MIT license. See the
repository's [`LICENSE`](../../LICENSE) for the complete license notice.
