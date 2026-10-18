"""Check static cache policy on isolated HTTP servers without a database."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server as service
from desktop.backend import make_handler


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if tag == "script" and values.get("src"):
            self.references.append(values["src"])
        elif tag == "link" and "stylesheet" in values.get("rel", "").split():
            self.references.append(values["href"])


@contextmanager
def serving(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def request(port, method, path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


class StaticCacheTests(unittest.TestCase):
    def test_versioned_assets_revalidate_without_changing_api_or_desktop_auth(self):
        with tempfile.TemporaryDirectory(prefix="moyu-static-cache-") as temporary:
            site = Path(temporary)
            for asset in service.Handler.STATIC_FILES - {"/"}:
                shutil.copy2(ROOT / asset.lstrip("/"), site / asset.lstrip("/"))

            class StaticHandler(service.Handler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(site), **kwargs)

                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    if self.path == "/api/cache-fixture-json":
                        return self.send_json(200, {"ok": True})
                    if self.path == "/api/cache-fixture-stream":
                        return self.send_sse([("complete", {"ok": True})])
                    return super().do_GET()

            with patch.object(service, "connect", side_effect=AssertionError(
                "Static cache checks must never access a database."
            )), serving(StaticHandler) as port:
                index = (site / "index.html").read_bytes()
                for path in ("/", "/index.html?cache-check=1"):
                    for method in ("GET", "HEAD"):
                        with self.subTest(path=path, method=method):
                            status, headers, body = request(port, method, path)
                            self.assertEqual(status, 200)
                            self.assertEqual(headers.get_all("Cache-Control"), ["no-store"])
                            self.assertEqual(body, index if method == "GET" else b"")
                            self.assertEqual(int(headers["Content-Length"]), len(index))

                parser = AssetParser()
                parser.feed(index.decode("utf-8"))
                paths = set()
                self.assertTrue(parser.references)
                for reference in parser.references:
                    url = urlsplit(reference)
                    path = "/" + url.path.lstrip("/")
                    paths.add(path)
                    with self.subTest(asset=reference):
                        self.assertFalse(url.scheme or url.netloc)
                        self.assertTrue(parse_qs(url.query).get("v", [""])[0])
                        expected = (site / path.lstrip("/")).read_bytes()
                        for target in (path, path + "?" + url.query):
                            status, headers, body = request(port, "GET", target)
                            self.assertEqual(status, 200)
                            self.assertEqual(headers.get_all("Cache-Control"), ["no-store"])
                            self.assertEqual(body, expected)
                        status, headers, body = request(port, "HEAD", path + "?" + url.query)
                        self.assertEqual(status, 200)
                        self.assertEqual(headers.get_all("Cache-Control"), ["no-store"])
                        self.assertEqual(int(headers["Content-Length"]), len(expected))
                        self.assertEqual(body, b"")
                self.assertEqual(paths, service.Handler.STATIC_FILES - {"/", "/index.html"})

                asset = site / "app.js"
                os.utime(asset, (1700000000, 1700000000))
                status, initial_headers, initial_body = request(port, "GET", "/app.js")
                self.assertEqual(status, 200)
                conditional = {"If-Modified-Since": initial_headers["Last-Modified"]}
                for method in ("GET", "HEAD"):
                    status, headers, body = request(port, method, "/app.js", conditional)
                    self.assertEqual(status, 304)
                    self.assertEqual(headers.get_all("Cache-Control"), ["no-store"])
                    self.assertEqual(body, b"")
                updated = initial_body + b"\n/* updated cache fixture */\n"
                asset.write_bytes(updated)
                os.utime(asset, (1700000005, 1700000005))
                for method in ("GET", "HEAD"):
                    status, headers, body = request(port, method, "/app.js", conditional)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers.get_all("Cache-Control"), ["no-store"])
                    self.assertEqual(int(headers["Content-Length"]), len(updated))
                    self.assertEqual(body, updated if method == "GET" else b"")

                status, headers, body = request(port, "GET", "/api/cache-fixture-json")
                self.assertEqual(status, 200)
                self.assertEqual(headers.get_all("Cache-Control"), ["no-store"])
                self.assertEqual(json.loads(body), {"ok": True})
                status, headers, body = request(port, "GET", "/api/cache-fixture-stream")
                self.assertEqual(status, 200)
                self.assertEqual(headers.get_all("Cache-Control"), ["no-cache"])
                self.assertIn(b"event: complete", body)

            class DesktopHandler(make_handler(service, b"synthetic-desktop-cache-token")):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(site), **kwargs)

            with patch.object(service, "connect", side_effect=AssertionError(
                "Static cache checks must never access a database."
            )), serving(DesktopHandler) as port:
                for path in ("/index.html", "/index.html?v=cache-fixture", "http://["):
                    for method in ("GET", "HEAD"):
                        with self.subTest(unauthorized_path=path, method=method):
                            status, headers, body = request(port, method, path)
                            self.assertEqual(status, 401)
                            self.assertEqual(headers.get_all("Cache-Control"), ["no-store"])
                            self.assertEqual(body, b'{"error":"desktop request rejected"}'
                                             if method == "GET" else b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
