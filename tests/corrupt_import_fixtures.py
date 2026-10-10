#!/usr/bin/env python3
"""Import boundary fixture: malformed backups are rejected, valid ones load."""
from __future__ import annotations
import json


def validate_import(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("backup must be an object")
    if payload.get("schema_version") not in {"1", "1.1", "v1", "v1.1"}:
        raise ValueError("unsupported schema_version")
    projects = payload.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ValueError("projects must be a non-empty array")
    ids = [p.get("id") for p in projects if isinstance(p, dict)]
    if len(ids) != len(projects) or any(not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("project IDs must be unique")
    return payload


VALID = {"schema_version": "1.1", "projects": [{"id": "p1", "name": "P1"}]}
CORRUPT = [
    None,
    "not-json-object",
    {"schema_version": "1.1", "projects": []},
    {"schema_version": "1.1", "projects": [{"id": "p1"}, {"id": "p1"}]},
    {"schema_version": "0.1", "projects": [{"id": "p1"}]},
]


def main() -> int:
    validate_import(VALID)
    for payload in CORRUPT:
        try:
            validate_import(payload)
        except ValueError:
            continue
        raise AssertionError(f"corrupt payload accepted: {json.dumps(payload)}")
    print(f"corrupt import fixtures passed: {len(CORRUPT)} rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
