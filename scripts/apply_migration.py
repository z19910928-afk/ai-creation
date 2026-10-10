#!/usr/bin/env python3
"""Transactional, idempotent runner for the v1.1 SQLite migration.
Usage: python scripts/apply_migration.py path/to/db.sqlite
"""
import sqlite3, sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit('usage: apply_migration.py DB_PATH')
db = Path(sys.argv[1])
conn = sqlite3.connect(db)
conn.execute('PRAGMA foreign_keys=ON')
row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='migration_ledger'").fetchone()
if row and conn.execute("SELECT 1 FROM migration_ledger WHERE version=11 AND name='schema-v1.1' AND checksum='schema-v1.1-migration'").fetchone():
    required = {'assertion','export_job','run_manifest','migration_ledger','character_state','plot_thread','external_source','prov_entity','prov_activity','prov_agent','prov_edge','index_checkpoint','actor'}
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required_triggers = {'prevent_revision_replace','prevent_event_replace','check_assertion_canon_insert','check_export_confirmation_insert','check_prov_entity_scope','check_prov_edge_scope','check_run_manifest_scope'}
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    required_columns = {'candidate_memory': {'created_by'}, 'source_span': {'external_uri','snapshot_hash','snapshot_at'}, 'ai_run': {'context_item_ids_json','token_json','latency_ms','cost_json','author_decision','consent_json'}}
    columns_ok = all({r[1] for r in conn.execute(f"PRAGMA table_info({table})")} >= cols for table, cols in required_columns.items())
    user_version = conn.execute('PRAGMA user_version').fetchone()[0]
    if user_version == 11 and required <= present and required_triggers <= triggers and columns_ok:
        print('schema-v1.1 already applied; no-op')
        conn.close(); raise SystemExit(0)
    raise SystemExit('migration ledger is inconsistent with schema; refusing no-op')
conn.executescript(Path(__file__).parents[1].joinpath('docs/schema-v1.1-migration.sql').read_text())
print('schema-v1.1 applied')
conn.close()
