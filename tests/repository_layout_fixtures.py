"""Verify source entry points after grouping the application into folders."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryLayoutTests(unittest.TestCase):
    def isolated_environment(self, directory):
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("MOYU_", "LANGCHAIN_", "LANGSMITH_"))
        }
        environment.update(
            MOYU_DB=str(Path(directory) / "temporary.sqlite3"),
            MOYU_CREDENTIALS_DIR=str(Path(directory) / "credentials"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        return environment

    def test_legacy_import_uses_the_same_mutable_service(self):
        with tempfile.TemporaryDirectory(prefix="moyu-layout-import-") as temporary:
            result = subprocess.run(
                [sys.executable, "-B", "-c", """
import server
from backend import server as service
assert server is service
server.PORT = 12345
assert service.PORT == 12345
assert service.ROOT == __import__("pathlib").Path.cwd()
assert service.FRONTEND_ROOT == service.ROOT / "frontend"
"""],
                cwd=ROOT, env=self.isolated_environment(temporary),
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(temporary) / "temporary.sqlite3").exists())

    def test_root_launcher_serves_assets_from_another_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="moyu-layout-cli-") as temporary:
            result = subprocess.run(
                [sys.executable, "-B", "-c", """
import http.client
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from backend import server as service
with socket.socket() as reservation:
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
environment = {**os.environ, "MOYU_PORT": str(port), "MOYU_HOST": "127.0.0.1"}
process = subprocess.Popen(
    [sys.executable, "-B", str(root / "server.py")],
    env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
)
def get(path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()
try:
    for attempt in range(200):
        if process.poll() is not None:
            raise AssertionError(process.stderr.read())
        try:
            if get("/api/health")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.025)
    else:
        raise AssertionError("source launcher did not become healthy")
    for path in service.Handler.STATIC_FILES:
        status, body = get(path)
        assert status == 200, path
        filename = "index.html" if path == "/" else path.lstrip("/")
        assert body == (root / "frontend" / filename).read_bytes(), path
    for path in ("/backend/server.py", "/frontend/", "/frontend/index.html",
                 "/.github/SECURITY.md", "/docs/schema-v1.sql"):
        assert get(path)[0] == 404, path
    assert Path(os.environ["MOYU_DB"]).is_file()
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    process.stderr.close()
""", str(ROOT)],
                cwd=temporary, env=self.isolated_environment(temporary),
                capture_output=True, text=True, timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
