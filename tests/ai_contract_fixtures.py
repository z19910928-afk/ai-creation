#!/usr/bin/env python3
"""Verify structured offline AI contracts and the buffered SSE transport."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "MOYU_DB": tempfile.mktemp(suffix=".sqlite3"), "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(port), "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": ""}
    process = subprocess.Popen([os.sys.executable, "server.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=1):
                    break
            except Exception:
                time.sleep(0.05)
        with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
            chapter = json.load(response)["chapters"][0]
        try:
            urllib.request.urlopen(base + "/api/export/epub", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        else:
            raise AssertionError("unreviewed export unexpectedly passed")
        override_request = urllib.request.Request(base + "/api/export/epub", headers={"X-Moyu-Export-Override": "fixture verifies author override"})
        with urllib.request.urlopen(override_request, timeout=5) as response:
            epub = response.read()
        with zipfile.ZipFile(io.BytesIO(epub)) as archive:
            assert archive.read("mimetype") == b"application/epub+zip"
            manifest = json.loads(archive.read("META-INF/moyu-export-manifest.json"))
            assert manifest["loss_manifest"]
            assert manifest["author_override"]["reason"].startswith("fixture")
        payload = json.dumps({"task": "scene", "chapter_id": chapter["id"], "response_format": "json"}).encode()
        request = urllib.request.Request(base + "/api/ai", method="POST", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
        assert result["response_format"] == "json" and isinstance(result["structured"], dict)
        payload = json.dumps({"task": "continue", "chapter_id": chapter["id"], "stream": True}).encode()
        request = urllib.request.Request(base + "/api/ai", method="POST", data=payload, headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode()
        assert "event: run" in body and "event: delta" in body and "event: done" in body
    finally:
        process.terminate()
        process.wait(timeout=3)
    print("AI contract fixtures: ok")


if __name__ == "__main__":
    main()
