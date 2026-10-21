"""Byte-range preview tests. Uses a temporary local server, never the R36S."""

from functools import partial
from http.server import ThreadingHTTPServer
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SPEC = importlib.util.spec_from_file_location("site_preview", Path(__file__).resolve().parents[1] / "tools/preview_site.py")
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class QuietHandler(preview.PreviewHandler):
    def log_message(self, *args):
        pass


class SitePreviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = bytes(range(256)) * 1024
        Path(self.temp.name, "demo.mp4").write_bytes(self.data)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=self.temp.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:{}/demo.mp4".format(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, headers=None, method="GET"):
        return urlopen(Request(self.url, headers=headers or {}, method=method), timeout=3)

    def test_full_body_and_head_advertise_ranges(self):
        with self.request() as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.read(), self.data)
        with self.request(method="HEAD") as response:
            self.assertEqual(response.headers["Content-Length"], str(len(self.data)))
            self.assertEqual(response.read(), b"")

    def test_partial_bounded_open_and_suffix_ranges(self):
        for header, start, end in (("bytes=2-11", 2, 11), ("bytes=200000-", 200000, len(self.data) - 1),
                                   ("bytes=-7", len(self.data) - 7, len(self.data) - 1),
                                   ("bytes=0-9999999", 0, len(self.data) - 1)):
            with self.subTest(range=header), self.request({"Range": header}) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers["Content-Range"], "bytes {}-{}/{}".format(start, end, len(self.data)))
                self.assertEqual(response.headers["Content-Length"], str(end - start + 1))
                self.assertEqual(response.read(), self.data[start:end + 1])

    def test_invalid_and_multiple_ranges_return416(self):
        for header in ("bytes=9999999-", "bytes=9-1", "bytes=-0", "bytes=", "bytes=1-2,8-9"):
            with self.subTest(range=header), self.assertRaises(HTTPError) as raised:
                self.request({"Range": header})
            self.assertEqual(raised.exception.code, 416)
            self.assertEqual(raised.exception.headers["Content-Range"], "bytes */{}".format(len(self.data)))
            raised.exception.close()

    def test_head_range_and_if_range_validation(self):
        with self.request({"Range": "bytes=10-19"}, method="HEAD") as response:
            self.assertEqual(response.status, 206)
            etag = response.headers["ETag"]
            self.assertEqual(response.read(), b"")
        with self.request({"Range": "bytes=10-19", "If-Range": etag}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), self.data[10:20])
        with self.request({"Range": "bytes=10-19", "If-Range": '"old"'}) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), self.data)


if __name__ == "__main__":
    unittest.main()
