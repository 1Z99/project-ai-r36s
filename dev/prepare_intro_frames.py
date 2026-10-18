#!/usr/bin/env python3
"""Prepare an image-only intro on the PC; the app needs no FFmpeg or audio."""

import argparse
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--fade", type=float, default=0.8)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parent.parent / "assets")
    args = parser.parse_args()
    if (not math.isfinite(args.duration) or not 0 < args.duration <= 30
            or not math.isfinite(args.fade) or not 0 <= args.fade <= args.duration):
        parser.error("Duration must be 0-30 seconds and fade must fit inside it")
    if not args.output_dir.is_dir():
        parser.error("Output directory does not exist")
    with zipfile.ZipFile(args.archive) as source, tempfile.TemporaryDirectory(prefix="project227-frames-") as folder:
        folder = Path(folder)
        names = sorted(info.filename for info in source.infolist()
                       if not info.is_dir() and info.filename.lower().endswith((".jpg", ".jpeg")))
        if not 1 <= len(names) <= 128:
            parser.error("Archive must contain 1-128 JPEG frames")
        for index, name in enumerate(names, 1):
            if source.getinfo(name).file_size > 8 * 1024 * 1024:
                parser.error("An input frame exceeds 8 MiB")
            (folder / f"input-{index:03d}.jpg").write_bytes(source.read(name))
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-start_number", "1", "-i", str(folder / "input-%03d.jpg"),
             "-frames:v", str(len(names)), "-pix_fmt", "bgr24", str(folder / "frame-%03d.bmp")],
            check=True)
        frame_names = [f"frame-{index:03d}.bmp" for index in range(1, len(names) + 1)]
        manifest = {"version": 1, "width": 640, "height": 480, "duration": args.duration,
                    "fade_seconds": args.fade, "frames": frame_names}
        temporary = args.output_dir / "intro-frames.zip.tmp"
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as output:
            output.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            for name in frame_names:
                data = (folder / name).read_bytes()
                if data[:2] != b"BM" or struct.unpack_from("<ii", data, 18) != (640, 480):
                    parser.error("Every frame must be 640x480")
                output.writestr(name, data)
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(folder / frame_names[-1]),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", str(folder / "last.rgba")],
            check=True)
        background = args.output_dir / "intro-background.rgba.tmp"
        background.write_bytes((folder / "last.rgba").read_bytes())
        background.replace(args.output_dir / "intro-background.rgba")
        temporary.replace(args.output_dir / "intro-frames.zip")
    print(f"Prepared {len(names)} silent frames over {args.duration:.2f} seconds; fade {args.fade:.2f}s")
    print(args.output_dir / "intro-frames.zip")


if __name__ == "__main__":
    main()
