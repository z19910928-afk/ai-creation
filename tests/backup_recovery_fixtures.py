#!/usr/bin/env python3
"""Executable SQLite backup/recovery fixtures.

Run with ``python tests/backup_recovery_fixtures.py``.  The fixture intentionally
covers the release S0 paths: Online Backup consistency, SHA-256 manifest checks,
corrupt backup rejection, atomic restore and finite RPO/RTO measurements.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from recovery_drill import run_recovery_drill  # noqa: E402
from sqlite_backup import CorruptDatabaseError, backup_database, restore_database, verify_backup, verify_database  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="backup-fixtures-") as temp:
        root = Path(temp)
        source = root / "source.sqlite"
        backup = root / "backup.sqlite"
        restored = root / "restored.sqlite"
        conn = sqlite3.connect(source)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        conn.execute("INSERT INTO notes(body) VALUES ('before backup')")
        conn.commit()
        conn.close()

        backup_result = backup_database(source, backup)
        assert backup_result["verified"] is True
        assert backup_result["sha256"] == verify_backup(backup)["sha256"]
        assert backup_result["rpo_seconds"] is not None and backup_result["rpo_seconds"] >= 0
        assert backup_result["rpo_status"] in {"estimated_from_file_mtime", "measured"}

        # Changes after the snapshot must not appear in the restored copy.
        conn = sqlite3.connect(source)
        conn.execute("INSERT INTO notes(body) VALUES ('after backup')")
        conn.commit()
        conn.close()
        restore_result = restore_database(backup, restored)
        assert restore_result["verified"] is True
        assert restore_result["rto_seconds"] is not None and restore_result["rto_seconds"] >= 0
        with sqlite3.connect(restored) as conn:
            rows = [r[0] for r in conn.execute("SELECT body FROM notes ORDER BY id")]
        assert rows == ["before backup"], rows

        # A sidecar digest makes even a corruption that SQLite does not read (a
        # trailing payload byte) fail closed during restore.
        corrupt = root / "corrupt.sqlite"
        shutil.copyfile(backup, corrupt)
        with corrupt.open("ab") as fh:
            fh.write(b"tampered")
        try:
            restore_database(corrupt, root / "should-not-exist.sqlite", manifest=backup.with_name("corrupt.sqlite.manifest.json"))
        except CorruptDatabaseError:
            pass
        else:
            raise AssertionError("corrupt backup accepted")

        # A normal restore cannot silently overwrite user data.
        try:
            restore_database(backup, restored)
        except Exception as exc:
            assert "destination exists" in str(exc)
        else:
            raise AssertionError("restore unexpectedly overwrote destination")

        # The deterministic end-to-end drill must reject a malformed snapshot
        # and restore the valid one within the release RPO/RTO budgets.
        report = run_recovery_drill(work_dir=root / "drill")
        assert report["success"] is True, report
        assert report["corrupt_backup_rejected"] is True
        assert report["restored"] is True
        assert report["rpo_seconds"] is not None and report["rpo_seconds"] <= 300
        assert report["rto_seconds"] is not None and report["rto_seconds"] <= 900
        json.dumps(report)

        # A real application schema need not contain the drill marker.  The
        # generic schema/count snapshot must still make the exercise pass.
        app_db = root / "app.sqlite"
        with sqlite3.connect(app_db) as app:
            app.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            app.executemany("INSERT INTO users(name) VALUES (?)", [("A",), ("B",)])
            app.commit()
        generic = run_recovery_drill(app_db, work_dir=root / "generic-drill")
        assert generic["success"] is True, generic
        assert generic["restored_rows"] == 0
        assert generic["expected_snapshot"] == generic["restored_snapshot"]

    print("backup/recovery fixtures passed: online backup, digest, corruption rejection, restore, generic drill, RPO/RTO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
