#!/usr/bin/env python3
"""Run a repeatable SQLite backup/recovery and corruption-rejection drill.

The drill creates a tiny database when ``--database`` is omitted, takes an
Online Backup snapshot, deliberately corrupts a copy, confirms that restore
rejects it, then restores the good snapshot and compares its schema/version and
row-count snapshot (plus a sentinel row for generated fixtures).  A
JSON report is emitted with measured RPO/RTO values suitable for
``scripts/evaluation_runner.py``'s ``recovery`` fixture field.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

# Running as ``python scripts/recovery_drill.py`` puts scripts/ on sys.path.
try:
    from sqlite_backup import BackupError, CorruptDatabaseError, backup_database, restore_database, verify_backup, verify_database
except ImportError:  # pragma: no cover - import path for package-style callers
    from scripts.sqlite_backup import BackupError, CorruptDatabaseError, backup_database, restore_database, verify_backup, verify_database

UTC_SUFFIX = "Z"


def _utc_iso(seconds: float | None = None) -> str:
    import datetime as dt

    stamp = dt.datetime.fromtimestamp(seconds if seconds is not None else time.time(), dt.timezone.utc)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", UTC_SUFFIX)


def _create_fixture_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE IF NOT EXISTS drill_marker (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL,
                committed_at TEXT NOT NULL
            );
            DELETE FROM drill_marker;
            """
        )
        conn.execute("INSERT INTO drill_marker(value, committed_at) VALUES (?, datetime('now'))", ("recovery-drill",))
        conn.commit()
    finally:
        conn.close()


def _corrupt_copy(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)
    # Flip bytes in the first page while keeping file size intact.  This is a
    # deterministic malformed snapshot and should trip both digest and SQLite
    # integrity checks.  Do not mutate the known-good backup.
    with destination.open("r+b") as fh:
        fh.seek(100)
        current = fh.read(1)
        if not current:
            raise RuntimeError(f"cannot corrupt empty backup: {destination}")
        fh.seek(100)
        fh.write(bytes([current[0] ^ 0xFF]))
        fh.flush()
        os.fsync(fh.fileno())


def _database_snapshot(path: Path) -> dict[str, Any]:
    """Capture schema/version/count evidence for any SQLite schema."""
    with sqlite3.connect(path) as conn:
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        objects = conn.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        schema_material = [[str(x) for x in row] for row in objects]
        schema_digest = hashlib.sha256(
            json.dumps(schema_material, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        tables: dict[str, int | None] = {}
        for obj_type, name, _tbl_name, _sql in objects:
            if obj_type != "table":
                continue
            identifier = '"' + str(name).replace('"', '""') + '"'
            try:
                tables[str(name)] = int(conn.execute(f"SELECT count(*) FROM {identifier}").fetchone()[0])
            except sqlite3.DatabaseError:
                # Some virtual/shadow tables may not be queryable; integrity
                # checks still gate success and schema evidence is retained.
                tables[str(name)] = None
    return {"user_version": user_version, "schema_sha256": schema_digest, "table_counts": tables}


def run_recovery_drill(
    database: str | os.PathLike[str] | None = None,
    *,
    work_dir: str | os.PathLike[str] | None = None,
    output: str | os.PathLike[str] | None = None,
    keep_artifacts: bool = False,
) -> dict[str, Any]:
    """Execute one drill and return a JSON-serialisable report.

    ``database`` is treated as an input-only source.  If omitted, a temporary
    fixture database is generated.  Corruption rejection is a required part of
    success; a drill that restores data but accepts a corrupted copy fails.
    """
    own_work_dir = work_dir is None
    temporary_root: Path | None = None
    if own_work_dir:
        # Use mkdtemp rather than TemporaryDirectory so ``keep_artifacts`` can
        # intentionally leave the generated drill evidence available to a
        # caller after this function returns.
        temporary_root = Path(tempfile.mkdtemp(prefix="sqlite-recovery-drill-"))
        root = temporary_root
    else:
        root = Path(work_dir).absolute()
        root.mkdir(parents=True, exist_ok=True)
    source = Path(database).absolute() if database else root / "source.sqlite"
    if database is None:
        _create_fixture_database(source)
    backup = root / "backup.sqlite"
    backup_manifest = root / "backup.sqlite.manifest.json"
    corrupt = root / "corrupt.sqlite"
    restored = root / "restored.sqlite"
    report_path = Path(output).absolute() if output else None
    started = time.time()
    errors: list[str] = []
    corruption_rejected = False
    restored_ok = False
    source_digest: str | None = None
    backup_digest: str | None = None
    restored_rows = 0
    backup_result: dict[str, Any] | None = None
    restore_result: dict[str, Any] | None = None
    expected_snapshot: dict[str, Any] | None = None
    restored_snapshot: dict[str, Any] | None = None
    try:
        source_check = verify_database(source)
        source_digest = str(source_check["sha256"])
        # Source mtime gives a conservative and reproducible lower-bound RPO
        # when an application does not expose its last committed transaction.
        backup_result = backup_database(source, backup, manifest=backup_manifest, overwrite=True)
        backup_digest = str(backup_result["sha256"])
        # Compare against the actual backup snapshot, not a product-specific
        # table (real application schemas need not have drill_marker).
        expected_snapshot = _database_snapshot(backup)
        _corrupt_copy(backup, corrupt)
        # The corrupted file has no trusted sidecar; restore must still reject it
        # through SQLite format/integrity checks.  If a stale copied manifest is
        # ever present, digest verification catches it first.
        try:
            restore_database(corrupt, root / "must-not-restore.sqlite", overwrite=True)
            errors.append("corrupt backup was accepted")
        except (BackupError, CorruptDatabaseError, sqlite3.DatabaseError, OSError):
            corruption_rejected = True
        restore_result = restore_database(backup, restored, manifest=backup_manifest, overwrite=True)
        restored_check = verify_database(restored)
        restored_snapshot = _database_snapshot(restored)
        if expected_snapshot and "drill_marker" in expected_snapshot.get("table_counts", {}):
            restored_rows = int(restored_snapshot.get("table_counts", {}).get("drill_marker") or 0)
        restored_ok = bool(
            restored_check["ok"]
            and expected_snapshot == restored_snapshot
            and restored_check["sha256"] == backup_digest
        )
        if not restored_ok:
            errors.append("restored database failed integrity or schema/count snapshot verification")
    except (BackupError, CorruptDatabaseError, sqlite3.DatabaseError, OSError, ValueError) as exc:
        errors.append(str(exc))
    finally:
        finished = time.time()
    # RPO is measured by the backup operation (source mtime/last commit) and RTO
    # by restore+verification.  Keep explicit nulls if the backup never ran.
    rpo = backup_result.get("rpo_seconds") if backup_result else None
    rto = restore_result.get("rto_seconds") if restore_result else None
    report: dict[str, Any] = {
        "drill_id": str(uuid.uuid4()),
        "schema_version": "sqlite-recovery-drill-v1",
        "started_at": _utc_iso(started),
        "completed_at": _utc_iso(finished),
        "success": not errors and corruption_rejected and restored_ok,
        "corrupt_backup_rejected": corruption_rejected,
        "restored": restored_ok,
        "rpo_seconds": rpo,
        "rpo_s": rpo,
        "rpo_status": backup_result.get("rpo_status") if backup_result else "unmeasured",
        "rto_seconds": rto,
        "rto_s": rto,
        "rto_status": "measured" if rto is not None else "unmeasured",
        "source": str(source),
        "backup": str(backup),
        "restored_database": str(restored),
        "source_sha256": source_digest,
        "backup_sha256": backup_digest,
        "restored_rows": restored_rows,
        "expected_snapshot": expected_snapshot,
        "restored_snapshot": restored_snapshot,
        "errors": errors,
        "artifacts_kept": bool(keep_artifacts or not own_work_dir),
    }
    if backup_result:
        report["backup_manifest"] = str(backup_manifest)
        report["backup_duration_seconds"] = backup_result.get("duration_seconds")
    if restore_result:
        report["restore_result"] = restore_result
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if own_work_dir and temporary_root is not None and not keep_artifacts:
        # Keep temporary artifacts only when explicitly asked; by default they
        # are removed after report generation.
        shutil.rmtree(temporary_root, ignore_errors=True)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="source SQLite database (default: generated fixture)")
    parser.add_argument("--work-dir", type=Path, help="directory for backup/corruption/recovery artifacts")
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    parser.add_argument("--keep-artifacts", action="store_true", help="keep generated temporary artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = run_recovery_drill(args.database, work_dir=args.work_dir, output=args.output, keep_artifacts=args.keep_artifacts)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
