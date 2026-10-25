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
import urllib.parse
import zipfile
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "MOYU_DB": tempfile.mktemp(suffix=".sqlite3"), "MOYU_HOST": "127.0.0.1", "MOYU_PORT": str(port), "MOYU_AI_BASE_URL": "", "MOYU_AI_MODEL": ""}
    log = tempfile.TemporaryFile()
    process = subprocess.Popen([os.sys.executable, "server.py"], cwd=ROOT, env=env,
                               stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        ready = False
        for _ in range(200):
            if process.poll() is not None:
                log.seek(0)
                details = log.read().decode("utf-8", "replace")[-4000:]
                raise RuntimeError(f"server exited before health check: {details}")
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=1) as response:
                    if response.status != 200:
                        raise RuntimeError(f"health returned HTTP {response.status}")
                    ready = True
                    break
            except Exception:
                time.sleep(0.05)
        if not ready:
            log.seek(0)
            details = log.read().decode("utf-8", "replace")[-4000:]
            raise RuntimeError(f"server did not become healthy within 10 seconds: {details}")
        with urllib.request.urlopen(base + "/api/state", timeout=3) as response:
            chapter = json.load(response)["chapters"][0]
        profile = urllib.parse.quote(json.dumps({"max_tokens": 4_000, "chapter_top_k": 0, "recent_window": 0, "memory_top_k": 0, "record_top_k": 0}))
        with urllib.request.urlopen(base + f"/api/context?chapter_id={chapter['id']}&retrieval_profile={profile}", timeout=5) as response:
            context = json.load(response)
        assert context["budget"]["profile"]["max_tokens"] == 4_000
        assert context["budget"]["retrieval_tiers"]["lexical"] == 0
        assert context["budget"]["retrieval_tiers"]["recency"] == 0
        coverage_profile = urllib.parse.quote(json.dumps({"min_term_coverage": 0.5}))
        with urllib.request.urlopen(base + f"/api/context?chapter_id={chapter['id']}&retrieval_profile={coverage_profile}", timeout=5) as response:
            covered_context = json.load(response)
        assert covered_context["budget"]["profile"]["min_term_coverage"] == 0.5
        invalid_coverage = urllib.parse.quote(json.dumps({"min_term_coverage": 1.5}))
        try:
            urllib.request.urlopen(base + f"/api/context?chapter_id={chapter['id']}&retrieval_profile={invalid_coverage}", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("out-of-range coverage floor unexpectedly passed")
        bad_profile = urllib.parse.quote(json.dumps({"unknown": 1}))
        try:
            urllib.request.urlopen(base + f"/api/context?chapter_id={chapter['id']}&retrieval_profile={bad_profile}", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("invalid retrieval profile unexpectedly passed")
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
            assert manifest["manifest_version"] == 2
            assert len(manifest["source_sha256"]) == 64
            assert manifest["chapter_count"] == len(manifest["chapters"])
            assert all(len(chapter["body_sha256"]) == 64 for chapter in manifest["chapters"])
            assert manifest["author_override"]["reason"].startswith("fixture")
        with urllib.request.urlopen(base + "/api/export/manifest", timeout=5) as response:
            api_manifest = json.load(response)
        assert api_manifest["source_sha256"] == manifest["source_sha256"]
        assert api_manifest["finalization"]["allowed"] is False
        assert api_manifest["finalization"]["issues"]
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
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        log.close()
    print("AI contract fixtures: ok")


if __name__ == "__main__":
    main()
