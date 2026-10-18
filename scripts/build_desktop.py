#!/usr/bin/env python3
"""Build the bundled backend and Electron application on their native platform."""

from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import shutil
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIRECTORY = ROOT / "build" / "backend" / "moyu-service"
BUILDER_CLI = ROOT / "node_modules" / "electron-builder" / "cli.js"


def native_architecture() -> str:
    machine = platform.machine().lower()
    if struct.calcsize("P") != 8:
        raise RuntimeError("Desktop builds require a 64-bit Python interpreter.")
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x64"
    raise RuntimeError(f"Unsupported build architecture: {machine}")


def native_target() -> str:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform == "win32":
        return "win"
    raise RuntimeError("Build macOS packages on macOS and Windows packages on Windows.")


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def node_executable(value: str, architecture: str) -> str:
    executable = shutil.which(value)
    if executable is None:
        raise RuntimeError("Node.js 24 was not found. Install dependencies with npm ci first.")
    result = subprocess.run(
        [
            executable,
            "-e",
            "process.stdout.write(JSON.stringify({platform:process.platform,"
            "arch:process.arch,version:process.versions.node}))",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    identity = json.loads(result.stdout)
    if identity["platform"] != sys.platform or identity["arch"] != architecture:
        raise RuntimeError("Python and Node.js must use the same native platform and architecture.")
    if identity["version"].split(".", 1)[0] != "24":
        raise RuntimeError("Desktop builds require Node.js 24.")
    if not BUILDER_CLI.is_file():
        raise RuntimeError("electron-builder was not found. Run npm ci first.")
    return executable


def build_backend() -> Path:
    specification = ROOT / "desktop" / "backend.spec"
    if not specification.is_file():
        raise RuntimeError("desktop/backend.spec was not found.")
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            "build/backend",
            "--workpath",
            "build/pyinstaller",
            "desktop/backend.spec",
        ]
    )
    filename = "moyu-service.exe" if sys.platform == "win32" else "moyu-service"
    executable = BACKEND_DIRECTORY / filename
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not create the expected backend: {executable}")
    return executable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x64"), help="Must match the current machine.")
    parser.add_argument("--backend-only", action="store_true", help="Only build the native backend.")
    parser.add_argument("--dir", action="store_true", help="Build an unpacked desktop application.")
    parser.add_argument("--node", default=os.environ.get("MOYU_NODE_BINARY", "node"))
    args = parser.parse_args(argv)
    if args.backend_only and args.dir:
        parser.error("--backend-only and --dir cannot be combined.")
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Use Python 3.11 to build the bundled service.")
    target = native_target()
    architecture = native_architecture()
    if args.arch and args.arch != architecture:
        raise RuntimeError("Cross compilation is disabled. Build on the target architecture.")
    if target == "win" and architecture != "x64":
        raise RuntimeError("The Windows desktop package currently supports x64 only.")
    node = None if args.backend_only else node_executable(args.node, architecture)
    backend = build_backend()
    if args.backend_only:
        print(f"Bundled backend: {backend}")
        return 0
    environment = os.environ.copy()
    environment["CSC_IDENTITY_AUTO_DISCOVERY"] = "false"
    command = [
        node,
        str(BUILDER_CLI),
        f"--{target}",
        f"--{architecture}",
        "--projectDir",
        str(ROOT),
        "--config",
        "desktop/electron-builder.yml",
        "--publish",
        "never",
    ]
    if args.dir:
        command.append("--dir")
    run(command, env=environment)
    print(f"Desktop build output: {ROOT / 'dist'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Desktop build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
