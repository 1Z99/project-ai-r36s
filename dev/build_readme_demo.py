#!/usr/bin/env python3
"""Composite the original walkthrough into the standalone homepage SVG.

Requires ffmpeg/ffprobe, Node, Chromium and an existing Playwright installation.
No website files are needed unless explicitly re-extracting the artwork.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
DURATION = 78

# Capture resolved production paint/text styles, not website layout constraints.
EXTRACT_JS = r"""
import fs from 'node:fs';
import path from 'node:path';
const [homepage, output] = process.argv.slice(1);
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE);
const browser = await chromium.launch({executablePath: process.env.CHROMIUM,
  headless: true, args: ['--no-sandbox']});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  await page.route('**/*', route => route.abort());
  const html = fs.readFileSync(homepage, 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<link\b[^>]*>/gi, '')
    .replace(/<foreignObject\b[^>]*>[\s\S]*?<\/foreignObject>/gi, '')
    .replace(/<image\b[^>]*\/?\s*>/gi, '');
  const css = fs.readFileSync(path.join(path.dirname(homepage), 'site.css'), 'utf8');
  await page.setContent(html);
  await page.addStyleTag({content: css});
  await page.evaluate(() => document.fonts.ready);
  const result = await page.evaluate(() => {
    const svg = document.querySelector('svg.handheld-illustration');
    const props = ['fill', 'stroke', 'stroke-width', 'stroke-linecap',
      'stroke-linejoin', 'fill-opacity', 'stroke-opacity', 'opacity',
      'font-family', 'font-size', 'font-weight', 'text-anchor', 'letter-spacing'];
    const rules = new Map();
    for (const element of svg.querySelectorAll('[class]')) {
      const name = element.getAttribute('class');
      const style = getComputedStyle(element);
      const declarations = props.map(prop => `    ${prop}: ${style.getPropertyValue(prop)};`);
      const rule = `  .${name} {\n${declarations.join('\n')}\n  }`;
      if (rules.has(name) && rules.get(name) !== rule)
        throw new Error(`Class ${name} has inconsistent computed styles`);
      rules.set(name, rule);
    }
    svg.removeAttribute('class');
    svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    svg.setAttribute('width', '360');
    svg.setAttribute('height', '470');
    svg.setAttribute('aria-label', 'Original R36S illustration by 1Z99; screen reserved for the 227 AI PC walkthrough');
    const ns = 'http://www.w3.org/2000/svg';
    const style = document.createElementNS(ns, 'style');
    style.textContent = '\n  svg { display: block; width: 360px; height: 470px; max-height: none; padding: 0; -webkit-font-smoothing: antialiased; }\n'
      + [...rules.values()].join('\n') + '\n';
    const background = document.createElementNS(ns, 'rect');
    for (const [key, value] of Object.entries({width: '360', height: '470', fill: '#080d0b'}))
      background.setAttribute(key, value);
    svg.prepend(style, background);
    return '<?xml version="1.0" encoding="UTF-8"?>\n'
      + '<!-- Extracted from docs/index.html; production computed styles. Copyright (c) 2026 1Z99, MIT. -->\n'
      + svg.outerHTML + '\n';
  });
  fs.writeFileSync(output, result.split('\n').map(line => line.trimEnd()).join('\n'));
} finally { await browser.close(); }
"""

RASTER_JS = r"""
import fs from 'node:fs';
const [source, output] = process.argv.slice(1);
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE);
const browser = await chromium.launch({executablePath: process.env.CHROMIUM,
  headless: true, args: ['--no-sandbox']});
try {
  const page = await browser.newPage({viewport: {width: 360, height: 470}, deviceScaleFactor: 1});
  await page.route('**/*', route => route.abort());
  await page.setContent('<style>html,body {margin:0;padding:0;background:#080d0b}</style>'
    + fs.readFileSync(source, 'utf8'));
  await page.evaluate(() => document.fonts.ready);
  const bounds = await page.locator('svg').boundingBox();
  if (bounds.x !== 0 || bounds.y !== 0 || bounds.width !== 360 || bounds.height !== 470)
    throw new Error(`Unexpected SVG bounds: ${JSON.stringify(bounds)}`);
  await page.screenshot({path: output});
} finally { await browser.close(); }
"""


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', required=True, help='Original 78s walkthrough: local path or explicit HTTP(S) release URL')
    parser.add_argument('--output-dir', '--output', type=Path, default=ROOT / 'assets/readme')
    parser.add_argument('--mp4', type=Path, help='Optional full-audio composite, outside the repository')
    parser.add_argument('--extract-art-from', type=Path, help='Explicitly regenerate r36s.svg from a local homepage and its site.css')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_dir():
        parser.error('--output-dir must be an existing directory')
    mp4 = args.mp4.resolve() if args.mp4 else None
    if mp4 and (mp4.is_relative_to(ROOT) or not mp4.parent.is_dir()):
        parser.error('--mp4 must have an existing parent directory outside the repository')
    env = dict(os.environ, PLAYWRIGHT_MODULE=os.environ.get('PLAYWRIGHT_MODULE', 'playwright'),
               CHROMIUM=os.environ.get('CHROMIUM', shutil.which('chromium') or shutil.which('google-chrome') or 'chromium'))
    art = output / 'r36s.svg'
    if args.extract_art_from:
        run('node', '--input-type=module', '-e', EXTRACT_JS, args.extract_art_from.resolve(), art, env=env)
    elif not art.is_file():
        shutil.copyfile(ROOT / 'assets/readme/r36s.svg', art)
    svg = ET.parse(art).getroot()
    screen = svg.find(".//*[@class='device-screen']")
    if screen is None or svg.get('viewBox') != '0 0 360 470':
        parser.error('Expected the original 360x470 R36S artwork and device-screen')
    x, y, width, height = (int(screen.get(key)) for key in ('x', 'y', 'width', 'height'))
    if (x, y, width, height) != (72, 56, 216, 162):
        parser.error('The original screen geometry must be 72,56,216,162')
    if any(element.tag.rsplit('}', 1)[-1] in ('foreignObject', 'image', 'script') for element in svg.iter()):
        parser.error('Artwork must not contain HTML, scripts or poster images')

    with tempfile.TemporaryDirectory(prefix='227-readme-') as temp:
        temp = Path(temp)
        if args.video.startswith(('https://', 'http://')):
            video = temp / 'walkthrough.mp4'
            with urllib.request.urlopen(args.video, timeout=120) as response, video.open('wb') as target:
                shutil.copyfileobj(response, target)
        else:
            video = Path(args.video).resolve(strict=True)
        metadata = json.loads(run('ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                  '-of', 'json', video, capture_output=True, text=True).stdout)
        if abs(float(metadata['format']['duration']) - DURATION) > 0.001:
            parser.error('The walkthrough must be exactly 78 seconds')
        streams = metadata['streams']
        source_video = next(stream for stream in streams if stream['codec_type'] == 'video')
        if source_video['width'] * 3 != source_video['height'] * 4:
            parser.error('The walkthrough must have a native 4:3 frame')
        if mp4 and not any(stream['codec_type'] == 'audio' and stream['codec_name'] == 'aac' for stream in streams):
            parser.error('The original AAC audio is required for the MP4')
        outline = temp / 'outline.png'
        run('node', '--input-type=module', '-e', RASTER_JS, art, outline, env=env)
        inputs = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-y',
                  '-filter_complex_threads', '1', '-i', video, '-loop', '1',
                  '-framerate', source_video['r_frame_rate'], '-i', outline]
        composite = (f'[0:v]setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos,setsar=1[screen];'
                     f'[1:v][screen]overlay=x={x}:y={y}:eof_action=endall,setsar=1')
        # A global palette and no dithering keep the static artwork stable.
        gif_filters = (composite + ',fps=8,split[frames][colors];'
                       '[colors]palettegen=max_colors=128:reserve_transparent=0:stats_mode=full[palette];'
                       '[frames][palette]paletteuse=dither=none[out]')
        # GIF uses centiseconds: alternate 12/13 ticks; finish at exactly 78s.
        run(*inputs, '-filter_complex', gif_filters, '-map', '[out]', '-an',
            '-t', DURATION, '-loop', '0', '-final_delay', '12', output / '227-ai-demo.gif')
        if mp4:
            run(*inputs, '-filter_complex', composite + '[out]', '-map', '[out]', '-map', '0:a:0',
                '-t', DURATION, '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
                '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', mp4)
        print(f'GIF: {output / "227-ai-demo.gif"}')
        if mp4:
            print(f'MP4: {mp4} (original AAC audio copied without re-encoding)')


if __name__ == '__main__':
    main()
