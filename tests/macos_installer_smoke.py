"""Verify a real macOS disk image using an isolated temporary installation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
APPLICATION_NAME = "墨屿.app"
HDIUTIL = "/usr/bin/hdiutil"
CODESIGN = "/usr/bin/codesign"


def redact(value: object, paths: tuple[Path, ...]) -> str:
    message = str(value)
    for directory in (*paths, ROOT, Path.home()):
        message = message.replace(str(directory), "<local-path>")
    message = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "<redacted>", message)
    message = re.sub(
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+",
        r"\1<redacted>",
        message,
    )
    return " ".join(message.split())[-800:]


def stop_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The original child can exit while one of its descendants is still running.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        raise RuntimeError("MACOS_INSTALLER_SMOKE_CHILD_CLEANUP_TIMEOUT") from None


def run(command: list[str], label: str, timeout: int) -> str:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_process_group(process)
        raise RuntimeError(f"MACOS_INSTALLER_SMOKE_TIMEOUT_{label}") from None
    except BaseException:
        stop_process_group(process)
        raise
    if process.returncode != 0:
        detail = stderr.strip() or stdout.strip()
        raise RuntimeError(
            f"MACOS_INSTALLER_SMOKE_{label}_EXIT_{process.returncode}: {detail}"
        )
    return stdout


def verify_desktop_report(output: str) -> int:
    try:
        report = json.loads(output)
    except (TypeError, ValueError):
        raise RuntimeError("MACOS_INSTALLER_SMOKE_INVALID_DESKTOP_REPORT") from None
    cases = report.get("cases") if isinstance(report, dict) else None
    if (
        not isinstance(cases, list)
        or len(cases) != 10
        or any(not isinstance(case, dict) or case.get("passed") is not True for case in cases)
        or report.get("passed") is not True
        or report.get("target") != "packaged"
        or report.get("synthetic_data_only") is not True
        or report.get("provider_jobs") != 0
        or report.get("unexpected_external_requests") != 0
        or report.get("page_errors") != 0
    ):
        raise RuntimeError("MACOS_INSTALLER_SMOKE_DESKTOP_REPORT_FAILED")
    return len(cases)


def detach(mount: Path) -> None:
    if not os.path.ismount(mount):
        return
    try:
        run([HDIUTIL, "detach", str(mount)], "DETACH", 30)
    except RuntimeError:
        run([HDIUTIL, "detach", "-force", str(mount)], "FORCE_DETACH", 30)
    if os.path.ismount(mount):
        raise RuntimeError("MACOS_INSTALLER_SMOKE_VOLUME_STILL_MOUNTED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dmg", required=True, type=Path)
    parser.add_argument("--node", default="node")
    args = parser.parse_args(argv)
    report = {
        "passed": False,
        "installation_verified": False,
        "codesign_verified": False,
        "installed_desktop_cases": 0,
        "unmounted": False,
        "temporary_cleanup": False,
        "synthetic_data_only": True,
    }
    temporary = None
    temporary_path = None
    mount = None
    failure = None
    cleanup_errors = []
    paths = (args.dmg.absolute(),)
    try:
        if sys.platform != "darwin":
            raise RuntimeError("MACOS_INSTALLER_SMOKE_REQUIRES_MACOS")
        if args.dmg.is_symlink() or not args.dmg.is_file() or args.dmg.suffix.lower() != ".dmg":
            raise RuntimeError("MACOS_INSTALLER_SMOKE_DMG_MISSING_OR_UNSAFE")
        dmg = args.dmg.resolve(strict=True)
        node = shutil.which(args.node)
        if node is None:
            raise RuntimeError("MACOS_INSTALLER_SMOKE_NODE_NOT_FOUND")
        temporary = tempfile.TemporaryDirectory(prefix="moyu-macos-installer-smoke-")
        temporary_path = Path(temporary.name).resolve()
        paths = (temporary_path, dmg)
        mount = temporary_path / "mount"
        mount.mkdir(mode=0o700)
        receipt = run(
            [
                HDIUTIL, "attach", "-readonly", "-nobrowse", "-noautoopen", "-plist",
                "-mountpoint", str(mount), str(dmg),
            ],
            "ATTACH",
            120,
        )
        try:
            entities = plistlib.loads(receipt.encode("utf-8"))["system-entities"]
            if not isinstance(entities, list):
                raise ValueError("Invalid mount entities")
        except (KeyError, TypeError, ValueError, plistlib.InvalidFileException):
            raise RuntimeError("MACOS_INSTALLER_SMOKE_INVALID_MOUNT_RECEIPT") from None
        mounted_here = [
            entry for entry in entities
            if isinstance(entry, dict)
            and isinstance(entry.get("mount-point"), str)
            and Path(entry["mount-point"]).resolve() == mount
        ]
        if len(mounted_here) != 1 or not os.path.ismount(mount):
            raise RuntimeError("MACOS_INSTALLER_SMOKE_UNEXPECTED_MOUNT_LOCATION")
        applications = list(mount.glob("*.app"))
        if (
            len(applications) != 1
            or applications[0].name != APPLICATION_NAME
            or applications[0].is_symlink()
            or not applications[0].is_dir()
        ):
            raise RuntimeError("MACOS_INSTALLER_SMOKE_UNEXPECTED_APPLICATION_IN_DMG")
        installation = temporary_path / "安装 验证"
        installation.mkdir(mode=0o700)
        installed_app = installation / APPLICATION_NAME
        shutil.copytree(applications[0], installed_app, symlinks=True)
        executable = installed_app / "Contents" / "MacOS" / "墨屿"
        if (
            not executable.is_file()
            or not executable.resolve().is_relative_to(installed_app)
            or not os.access(executable, os.X_OK)
        ):
            raise RuntimeError("MACOS_INSTALLER_SMOKE_INSTALLED_EXECUTABLE_MISSING")
        report["installation_verified"] = True
        run([CODESIGN, "--verify", "--deep", "--strict", str(installed_app)], "CODESIGN", 120)
        report["codesign_verified"] = True
        desktop_output = run(
            [
                node, str(ROOT / "tests" / "desktop_smoke.cjs"),
                "--executable", str(executable),
            ],
            "INSTALLED_DESKTOP",
            420,
        )
        report["installed_desktop_cases"] = verify_desktop_report(desktop_output)
    except KeyboardInterrupt:
        failure = "MACOS_INSTALLER_SMOKE_INTERRUPTED"
    except (OSError, RuntimeError, ValueError) as error:
        failure = redact(error, paths)
    finally:
        if mount is not None:
            try:
                detach(mount)
                report["unmounted"] = True
            except (OSError, RuntimeError) as error:
                cleanup_errors.append(redact(error, paths))
        if temporary is not None:
            # A failed detach must never let recursive cleanup enter the mounted image.
            temporary._finalizer.detach()
            if mount is not None and os.path.ismount(mount):
                cleanup_errors.append("MACOS_INSTALLER_SMOKE_TEMPORARY_MOUNT_RETAINED")
            else:
                try:
                    run(
                        ["/bin/rm", "-rf", "--", str(temporary_path)],
                        "TEMPORARY_CLEANUP",
                        30,
                    )
                    if temporary_path.exists():
                        raise RuntimeError("MACOS_INSTALLER_SMOKE_TEMPORARY_DIRECTORY_REMAINED")
                    report["temporary_cleanup"] = True
                except (OSError, RuntimeError) as error:
                    cleanup_errors.append(redact(error, paths))
    if failure is not None:
        report["failure"] = failure
        print(failure, file=sys.stderr)
    if cleanup_errors:
        report["cleanup_errors"] = cleanup_errors
        for error in cleanup_errors:
            print(error, file=sys.stderr)
    report["passed"] = (
        failure is None
        and not cleanup_errors
        and report["installed_desktop_cases"] == 10
        and report["unmounted"]
        and report["temporary_cleanup"]
    )
    print(json.dumps(report, ensure_ascii=True, separators=(",", ":")))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
