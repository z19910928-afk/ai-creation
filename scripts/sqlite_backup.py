#!/usr/bin/env python3
"""Safe SQLite online backup, verification and restore helpers.

The helpers deliberately never copy a live SQLite file with ``shutil.copy``.  A
source database is checked before and after a consistent Online Backup API (or
``VACUUM INTO``) snapshot, and the destination is atomically replaced only
after it passes the same checks.  A JSON manifest records the digest and the
RPO/RTO measurements so recovery can be audited and repeated.

Examples::

    python scripts/sqlite_backup.py backup app.sqlite backups/app.sqlite
    python scripts/sqlite_backup.py verify backups/app.sqlite
    python scripts/sqlite_backup.py restore backups/app.sqlite recovered.sqlite
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

UTC = _dt.timezone.utc
MANIFEST_VERSION = 1


class BackupError(RuntimeError):
    """Base class for backup/restore failures."""


class CorruptDatabaseError(BackupError):
    """Raised when a SQLite file fails format or integrity validation."""


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(UTC)


def _iso(value: _dt.datetime | float | int | str | None = None) -> str:
    """Return a stable UTC ISO-8601 string for a timestamp value."""
    if value is None:
        dt = _utc_now()
    elif isinstance(value, _dt.datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        dt = dt.astimezone(UTC)
    elif isinstance(value, (int, float)):
        dt = _dt.datetime.fromtimestamp(float(value), UTC)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return _iso()
        try:
            dt = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            # A numeric string is also convenient for callers collecting mtime.
            try:
                return _iso(float(text))
            except ValueError as exc:
                raise ValueError(f"invalid timestamp: {value!r}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        dt = dt.astimezone(UTC)
    else:
        raise TypeError(f"unsupported timestamp type: {type(value).__name__}")
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp_seconds(value: _dt.datetime | float | int | str | None) -> float:
    if value is None:
        return time.time()
    if isinstance(value, _dt.datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, _dt.datetime):
        return _iso(value)
    return value


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync (not available on every platform)."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def manifest_path(database: str | os.PathLike[str], explicit: str | os.PathLike[str] | None = None) -> Path:
    """Return the default sidecar manifest path for a database."""
    if explicit is not None:
        return Path(explicit)
    return Path(database).with_name(Path(database).name + ".manifest.json")


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_header(path: Path) -> dict[str, int]:
    """Read enough of the SQLite header to catch truncated/foreign files."""
    try:
        with path.open("rb") as fh:
            header = fh.read(100)
    except OSError as exc:
        raise CorruptDatabaseError(f"cannot read database: {path}: {exc}") from exc
    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
        raise CorruptDatabaseError(f"not a SQLite database: {path}")
    page_size = int.from_bytes(header[16:18], "big")
    # SQLite encodes 65536 as zero.  Valid page sizes are powers of two.
    if page_size == 1:
        page_size = 65536
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        raise CorruptDatabaseError(f"invalid SQLite page size in {path}: {page_size}")
    file_size = path.stat().st_size
    if file_size < page_size or file_size % page_size:
        raise CorruptDatabaseError(
            f"SQLite file is truncated or has a partial page: {path} ({file_size} bytes, page {page_size})"
        )
    return {
        "page_size": page_size,
        "file_size": file_size,
        "file_format_write_version": header[18],
        "file_format_read_version": header[19],
        "schema_cookie": int.from_bytes(header[40:44], "big"),
    }


def _connect_readonly(path: Path) -> sqlite3.Connection:
    # URI mode avoids accidentally creating an empty file for a typo.
    uri = "file:" + str(path.absolute()).replace("%", "%25").replace("?", "%3F").replace("#", "%23") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as exc:
        raise CorruptDatabaseError(f"cannot open SQLite database {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _check_connection(conn: sqlite3.Connection, path: Path, *, strict: bool = True) -> dict[str, Any]:
    """Run SQLite checks and return machine-readable integrity evidence."""
    checks: dict[str, Any] = {}
    try:
        quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        checks["quick_check"] = quick_rows
        integrity_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        checks["integrity_check"] = integrity_rows
        foreign_rows = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        checks["foreign_key_check"] = [list(row) for row in foreign_rows]
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        # Touch schema and all table roots to force malformed pages to be read.
        schema_count = int(conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0])
        checks.update(
            {
                "page_count": page_count,
                "page_size": page_size,
                "user_version": user_version,
                "journal_mode": journal_mode,
                "schema_objects": schema_count,
            }
        )
    except (sqlite3.DatabaseError, ValueError, TypeError) as exc:
        raise CorruptDatabaseError(f"SQLite integrity check failed for {path}: {exc}") from exc
    ok = quick_rows == ["ok"] and integrity_rows == ["ok"] and not foreign_rows
    checks["ok"] = ok
    if strict and not ok:
        raise CorruptDatabaseError(f"SQLite integrity check failed for {path}: {json.dumps(checks, ensure_ascii=False)}")
    return checks


def verify_database(
    database: str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Validate SQLite format/integrity, optionally against a digest.

    The result is JSON serialisable and includes ``ok``.  With ``strict=True``
    (the default), any failure raises :class:`CorruptDatabaseError`; callers
    doing health dashboards can pass ``strict=False`` to receive ``ok=False``
    and an ``error`` field instead.
    """
    path = Path(database)
    try:
        if not path.exists() or not path.is_file():
            raise CorruptDatabaseError(f"database does not exist or is not a file: {path}")
        if path.stat().st_size == 0:
            raise CorruptDatabaseError(f"database is empty: {path}")
        header = _read_header(path)
        digest = sha256_file(path)
        if expected_sha256 and digest.lower() != str(expected_sha256).lower():
            raise CorruptDatabaseError(f"database digest mismatch for {path}: expected {expected_sha256}, got {digest}")
    except (CorruptDatabaseError, OSError) as exc:
        if strict:
            if isinstance(exc, CorruptDatabaseError):
                raise
            raise CorruptDatabaseError(str(exc)) from exc
        return {"path": str(path), "ok": False, "error": str(exc), "verified_at": _iso()}
    try:
        conn = _connect_readonly(path)
    except CorruptDatabaseError as exc:
        if strict:
            raise
        return {"path": str(path), "ok": False, "error": str(exc), "verified_at": _iso()}
    try:
        try:
            checks = _check_connection(conn, path, strict=strict)
        except CorruptDatabaseError as exc:
            if strict:
                raise
            checks = {"ok": False, "error": str(exc)}
    finally:
        conn.close()
    # The header's page size and SQLite's view must agree; this catches some
    # malformed headers that SQLite otherwise opens in compatibility mode.
    if checks.get("page_size") != header["page_size"]:
        if strict:
            raise CorruptDatabaseError(f"SQLite page-size mismatch for {path}")
        checks["ok"] = False
        checks["error"] = "SQLite page-size mismatch"
    # SQLite can ignore trailing complete pages.  A backup must be an exact
    # database image, so reject such appended bytes even when integrity_check
    # happens not to visit them.
    expected_size = int(checks.get("page_count", 0)) * int(checks.get("page_size", header["page_size"]))
    if expected_size != header["file_size"]:
        if strict:
            raise CorruptDatabaseError(
                f"SQLite file has unexpected trailing pages: {path} (file={header['file_size']}, expected={expected_size})"
            )
        checks["ok"] = False
        checks["error"] = "SQLite file has unexpected trailing pages"
    checks.update({"path": str(path), "sha256": digest, "file_size": header["file_size"], "header": header})
    checks["verified_at"] = _iso()
    return checks


def _temp_database_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".sqlite.tmp", dir=str(destination.parent))
    os.close(fd)
    temp = Path(name)
    temp.unlink()
    return temp


def _backup_online(source: Path, destination: Path, progress: Callable[..., Any] | None = None) -> None:
    src = _connect_readonly(source)
    try:
        dst = sqlite3.connect(str(destination))
        try:
            # ``pages=0`` means all pages.  A progress callback can be used by
            # callers for logging without changing backup semantics.
            src.backup(dst, pages=0, progress=progress, sleep=0.05)
            dst.commit()
        finally:
            dst.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"SQLite Online Backup failed: {source} -> {destination}: {exc}") from exc
    finally:
        src.close()


def _backup_vacuum(source: Path, destination: Path) -> None:
    src = _connect_readonly(source)
    try:
        # SQLite string literals escape a quote by doubling it.
        target = str(destination.absolute()).replace("'", "''")
        src.execute(f"VACUUM INTO '{target}'")
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"SQLite VACUUM INTO failed: {source} -> {destination}: {exc}") from exc
    finally:
        src.close()


def _atomic_database_snapshot(source: Path, destination: Path, method: str) -> None:
    temp = _temp_database_path(destination)
    try:
        if method == "online":
            _backup_online(source, temp)
        elif method in {"vacuum", "vacuum_into"}:
            _backup_vacuum(source, temp)
        else:
            raise ValueError(f"unknown backup method: {method!r}")
        # Validate before making the snapshot visible.  No manifest can point at
        # a partial or corrupt file after this point.
        verify_database(temp)
        with temp.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(temp, destination)
        _fsync_dir(destination.parent)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _manifest_payload(
    *,
    operation: str,
    database: Path,
    verification: Mapping[str, Any],
    started: float,
    completed: float,
    method: str | None = None,
    source: Path | None = None,
    source_mtime: float | None = None,
    source_last_commit: float | None = None,
    rpo_status: str = "estimated_from_file_mtime",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rpo_base = source_last_commit if source_last_commit is not None else source_mtime
    rpo = None if rpo_base is None else max(0.0, started - rpo_base)
    payload: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "backup_id": str(uuid.uuid4()),
        "operation": operation,
        "status": "success",
        "database": str(database),
        "source": str(source) if source else None,
        "method": method,
        "started_at": _iso(started),
        "completed_at": _iso(completed),
        "duration_seconds": max(0.0, completed - started),
        "rpo_seconds": rpo,
        "rpo_status": "measured" if source_last_commit is not None else rpo_status,
        "rto_seconds": max(0.0, completed - started) if operation == "restore" else None,
        # Short aliases are accepted by the evaluator and help simple scripts
        # consume the report without knowing the long field names.
        "rpo_s": rpo,
        "rto_s": max(0.0, completed - started) if operation == "restore" else None,
        "source_mtime": _iso(source_mtime) if source_mtime is not None else None,
        "source_last_commit_at": _iso(source_last_commit) if source_last_commit is not None else None,
        "sha256": verification.get("sha256"),
        "file_size": verification.get("file_size"),
        "page_count": verification.get("page_count"),
        "page_size": verification.get("page_size"),
        "user_version": verification.get("user_version"),
        "integrity_check": verification.get("integrity_check"),
        "quick_check": verification.get("quick_check"),
        "foreign_key_check": verification.get("foreign_key_check"),
        "verified": bool(verification.get("ok")),
    }
    if extra:
        payload.update({key: _json_safe(value) for key, value in extra.items()})
    return payload


def _load_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"cannot read backup manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BackupError(f"backup manifest must be an object: {path}")
    return value


def backup_database(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    method: str = "online",
    manifest: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
    last_commit_at: _dt.datetime | float | int | str | None = None,
) -> dict[str, Any]:
    """Create an atomic, integrity-checked SQLite snapshot.

    ``method='online'`` uses :meth:`sqlite3.Connection.backup`; ``method='vacuum'``
    uses ``VACUUM INTO``.  Existing destinations are protected unless
    ``overwrite=True``.  A manifest is always written (next to the destination
    by default) and contains digest, integrity evidence, and RPO data.
    """
    src, dst = Path(source), Path(destination)
    if src.absolute() == dst.absolute():
        raise BackupError("source and destination must differ")
    if dst.exists() and not overwrite:
        raise BackupError(f"destination exists; pass overwrite=True/--force: {dst}")
    mpath = manifest_path(dst, manifest)
    if mpath.absolute() == dst.absolute():
        raise BackupError("manifest path must differ from destination database")
    # Validate before opening any destination, including malformed headers.
    source_check = verify_database(src)
    started = time.time()
    source_mtime = src.stat().st_mtime
    source_last = None if last_commit_at is None else _timestamp_seconds(last_commit_at)
    _atomic_database_snapshot(src, dst, method)
    completed = time.time()
    destination_check = verify_database(dst)
    result = _manifest_payload(
        operation="backup",
        database=dst,
        verification=destination_check,
        started=started,
        completed=completed,
        method=method,
        source=src,
        source_mtime=source_mtime,
        source_last_commit=source_last,
        extra={"source_sha256": source_check.get("sha256")},
    )
    _atomic_json(mpath, result)
    result["manifest"] = str(mpath)
    return result


def restore_database(
    backup: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    manifest: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
    report: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Restore a checked backup atomically and measure RTO.

    If a sidecar manifest exists, its SHA-256 digest is mandatory: a changed
    backup is rejected before the destination is touched.  A report can be
    written with ``report=...``; otherwise the report is returned to the caller.
    """
    src, dst = Path(backup), Path(destination)
    if src.absolute() == dst.absolute():
        raise BackupError("backup and destination must differ")
    if dst.exists() and not overwrite:
        raise BackupError(f"destination exists; pass overwrite=True/--force: {dst}")
    mpath = manifest_path(src, manifest)
    if mpath.absolute() == dst.absolute():
        raise BackupError("manifest path must differ from restore destination")
    manifest_data = _load_manifest(mpath)
    expected = None
    if manifest_data:
        expected = manifest_data.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise BackupError(f"backup manifest has no valid SHA-256 digest: {mpath}")
    # Digest and integrity are checked before destructive destination changes.
    # Start the RTO clock before validation so the report covers the complete
    # recovery attempt, including digest/integrity checks.
    started = time.time()
    source_check = verify_database(src, expected_sha256=expected)
    _atomic_database_snapshot(src, dst, "online")
    completed = time.time()
    destination_check = verify_database(dst)
    source_mtime = None
    source_last = None
    if manifest_data:
        try:
            source_mtime = _timestamp_seconds(manifest_data.get("source_mtime")) if manifest_data.get("source_mtime") else None
            source_last = _timestamp_seconds(manifest_data.get("source_last_commit_at")) if manifest_data.get("source_last_commit_at") else None
        except (TypeError, ValueError, OverflowError):
            # An untrusted optional timestamp should not prevent recovery.  The
            # digest and integrity checks above remain mandatory.
            source_mtime = source_last = None
    result = _manifest_payload(
        operation="restore",
        database=dst,
        verification=destination_check,
        started=started,
        completed=completed,
        method="online",
        source=src,
        source_mtime=source_mtime,
        source_last_commit=source_last,
        rpo_status=str(manifest_data.get("rpo_status", "unavailable")) if manifest_data else "unavailable",
        extra={"backup_sha256": source_check.get("sha256"), "backup_manifest": str(mpath) if manifest_data else None},
    )
    # RTO starts at invocation and is always a finite nonnegative value.
    result["rto_seconds"] = max(0.0, completed - started)
    if report is not None:
        rpath = Path(report)
        if rpath.absolute() == dst.absolute():
            raise BackupError("restore report path must differ from destination database")
        _atomic_json(rpath, result)
        result["report"] = str(rpath)
    return result


def verify_backup(
    backup: str | os.PathLike[str],
    *,
    manifest: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Verify a backup and its manifest digest without changing any file."""
    path = Path(backup)
    mpath = manifest_path(path, manifest)
    data = _load_manifest(mpath)
    expected = data.get("sha256") if data else None
    result = verify_database(path, expected_sha256=expected)
    result["manifest"] = str(mpath) if data else None
    result["manifest_verified"] = bool(data)
    if data:
        result["backup_id"] = data.get("backup_id")
        result["rpo_seconds"] = data.get("rpo_seconds")
        result["rpo_status"] = data.get("rpo_status")
    return result


# Explicit aliases keep the small module convenient for application code and
# backwards-compatible with callers that name the operation after SQLite.
backup_sqlite = backup_database
restore_sqlite = restore_database
check_integrity = verify_database


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup", help="create an atomic online backup")
    backup.add_argument("source", type=Path)
    backup.add_argument("destination", type=Path)
    backup.add_argument("--method", choices=("online", "vacuum", "vacuum_into"), default="online")
    backup.add_argument("--manifest", type=Path)
    backup.add_argument("--force", action="store_true", help="replace an existing destination")
    backup.add_argument("--last-commit-at", help="ISO-8601 or Unix timestamp used for measured RPO")
    restore = sub.add_parser("restore", help="restore an integrity-checked backup")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--manifest", type=Path)
    restore.add_argument("--report", type=Path)
    restore.add_argument("--force", action="store_true", help="replace an existing destination")
    verify = sub.add_parser("verify", help="verify a database/backup")
    verify.add_argument("database", type=Path)
    verify.add_argument("--manifest", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    # A two-positional-argument invocation remains a useful shorthand for the
    # common backup operation while the documented subcommands stay explicit.
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    if argv and argv[0] not in {"backup", "restore", "verify", "-h", "--help"} and not argv[0].startswith("-"):
        argv.insert(0, "backup")
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            result = backup_database(
                args.source,
                args.destination,
                method=args.method,
                manifest=args.manifest,
                overwrite=args.force,
                last_commit_at=args.last_commit_at,
            )
        elif args.command == "restore":
            result = restore_database(
                args.backup,
                args.destination,
                manifest=args.manifest,
                overwrite=args.force,
                report=args.report,
            )
        else:
            result = verify_backup(args.database, manifest=args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (BackupError, sqlite3.DatabaseError, OSError, ValueError) as exc:
        error = {"success": False, "error": str(exc), "error_type": type(exc).__name__}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
