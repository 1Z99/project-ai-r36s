#!/usr/bin/env python3
"""Local static preview with single-byte-range support for video seeking."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re


DOCS = Path(__file__).resolve().parents[1] / "docs"


class PreviewHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        self.byte_range = None
        header = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not header or not os.path.isfile(path):
            return super().send_head()
        try:
            stream = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None
        stat = os.fstat(stream.fileno())
        size = stat.st_size
        modified = self.date_time_string(stat.st_mtime)
        etag = '"{:x}-{:x}"'.format(stat.st_mtime_ns, size)
        if_range = self.headers.get("If-Range")
        if if_range and if_range not in (modified, etag):
            stream.close()
            return super().send_head()
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
        start = end = None
        if match and (match[1] or match[2]) and size:
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]), size - 1) if match[2] else size - 1
            elif int(match[2]) > 0:
                start, end = max(0, size - int(match[2])), size - 1
        if start is None or start >= size or start > end:
            stream.close()
            self.send_response(416)
            self.send_header("Content-Range", "bytes */{}".format(size))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        self.byte_range = (start, end)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", "bytes {}-{}/{}".format(start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Last-Modified", modified)
        self.send_header("ETag", etag)
        self.end_headers()
        stream.seek(start)
        return stream

    def copyfile(self, source, output):
        if self.byte_range is None:
            return super().copyfile(source, output)
        left = self.byte_range[1] - self.byte_range[0] + 1
        while left:
            chunk = source.read(min(64 * 1024, left))
            if not chunk:
                break
            output.write(chunk)
            left -= len(chunk)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=2270)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--directory", type=Path, default=DOCS)
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error("Preview directory does not exist")
    handler = partial(PreviewHandler, directory=str(args.directory.resolve()))
    with ThreadingHTTPServer((args.bind, args.port), handler) as server:
        print("227 site preview: http://{}:{}/ (video seeking enabled)".format(args.bind, args.port), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
