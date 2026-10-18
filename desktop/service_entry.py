"""Desktop service entry point, distinct from the backend package."""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
import threading
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class DesktopConfigurationError(ValueError):
    pass


def configure_environment():
    data_value = os.environ.get("MOYU_DESKTOP_DATA_DIR", "")
    token = os.environ.get("MOYU_DESKTOP_TOKEN", "")
    port_value = os.environ.get("MOYU_DESKTOP_PORT", "0")
    if not data_value or not re.fullmatch(r"[!-~]{32,4096}", token):
        raise DesktopConfigurationError("DESKTOP_CONFIGURATION_INVALID")
    if not re.fullmatch(r"[0-9]{1,5}", port_value):
        raise DesktopConfigurationError("DESKTOP_CONFIGURATION_INVALID")
    port = int(port_value)
    if port > 65535:
        raise DesktopConfigurationError("DESKTOP_CONFIGURATION_INVALID")
    directory = Path(data_value).expanduser()
    if not directory.is_absolute() or directory.is_symlink():
        raise DesktopConfigurationError("DESKTOP_DATA_DIRECTORY_INVALID")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = directory.resolve(strict=True)
    credentials = directory / "credentials"
    database = directory / "moyu.sqlite3"
    if credentials.is_symlink() or database.is_symlink():
        raise DesktopConfigurationError("DESKTOP_DATA_DIRECTORY_INVALID")
    credentials.mkdir(exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    os.chmod(credentials, 0o700)
    if os.name == "posix":
        os.umask(0o077)

    # Desktop settings come from its own data directory, never shell defaults.
    prefixes = (
        "MOYU_", "OPENAI_", "AZURE_OPENAI_", "ANTHROPIC_", "GEMINI_",
        "GOOGLE_API_", "LANGCHAIN_", "LANGSMITH_",
    )
    for name in list(os.environ):
        if name.startswith(prefixes):
            del os.environ[name]
    os.environ.update(
        MOYU_DB=str(database),
        MOYU_CREDENTIALS_DIR=str(credentials),
        MOYU_HOST="127.0.0.1",
        MOYU_PORT=str(port),
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
    )
    return token.encode("ascii"), port


def make_handler(service, token):
    class DesktopHandler(service.Handler):
        def log_message(self, fmt, *args):
            pass

        def send_error(self, code, message=None, explain=None):
            # The standard handler otherwise reflects parts of invalid requests.
            super().send_error(code)

        def reject_request(self, status):
            raw = b'{"error":"desktop request rejected"}'
            self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
            return False

        def parse_request(self):
            if not super().parse_request():
                return False
            supplied = self.headers.get_all("X-Moyu-Desktop-Token", [])
            if len(supplied) != 1 or not hmac.compare_digest(
                supplied[0].encode("utf-8"), token
            ):
                return self.reject_request(HTTPStatus.UNAUTHORIZED)
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get_all("Host", []) != [host]:
                return self.reject_request(HTTPStatus.FORBIDDEN)
            origin = "http://" + host
            origins = self.headers.get_all("Origin", [])
            if origins and origins != [origin]:
                return self.reject_request(HTTPStatus.FORBIDDEN)
            referers = self.headers.get_all("Referer", [])
            if referers:
                try:
                    parsed = urlsplit(referers[0])
                    valid = (
                        len(referers) == 1 and parsed.scheme == "http"
                        and parsed.netloc == host and not parsed.fragment
                    )
                except ValueError:
                    valid = False
                if not valid:
                    return self.reject_request(HTTPStatus.FORBIDDEN)
            if not self.path.startswith("/") or self.path.startswith("//"):
                return self.reject_request(HTTPStatus.FORBIDDEN)
            return True

        def do_GET(self):
            if urlsplit(self.path).path == "/api/desktop/health":
                return self.send_json(HTTPStatus.OK, {"ok": True})
            return super().do_GET()

        def do_HEAD(self):
            if urlsplit(self.path).path not in self.STATIC_FILES:
                return self.send_error(HTTPStatus.NOT_FOUND)
            return super().do_HEAD()

    return DesktopHandler


class DesktopHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        sys.stderr.write("Desktop request could not be completed.\n")


def resume_saved_work(service):
    with service.connect() as conn:
        scopes = [
            service.workspace.resolve_scope(conn, item["id"], item["branch_id"])
            for item in service.workspace.project_rows(conn) if item["branch_id"]
        ]
    for scope in scopes:
        with service.workspace.use_scope(*scope):
            for job_id in service.recover_ai_jobs():
                service.start_ai_worker(job_id)
            service.writing_agent.advance_ready(service)


def watch_parent_pipe(httpd):
    def wait_for_parent():
        try:
            if sys.stdin is not None:
                while sys.stdin.buffer.read(65536):
                    pass
        except (OSError, ValueError):
            pass
        httpd.shutdown()

    threading.Thread(target=wait_for_parent, daemon=True).start()


def main():
    httpd = None
    try:
        parent_pipe = os.environ.get("MOYU_DESKTOP_PARENT_PIPE") == "1"
        token, port = configure_environment()
        if not getattr(sys, "frozen", False):
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backend import server as service

        os.chdir(service.ROOT)
        service.init_db()
        httpd = DesktopHTTPServer(
            ("127.0.0.1", port), make_handler(service, token)
        )
        resume_saved_work(service)
        print(json.dumps({
            "type": "moyu-ready", "port": httpd.server_port, "pid": os.getpid(),
        }, separators=(",", ":")), flush=True)
        if parent_pipe:
            watch_parent_pipe(httpd)
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    except DesktopConfigurationError:
        sys.stderr.write('{"type":"moyu-error","code":"configuration_invalid"}\n')
        return 1
    except Exception:
        sys.stderr.write('{"type":"moyu-error","code":"startup_failed"}\n')
        return 1
    finally:
        if httpd is not None:
            httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
