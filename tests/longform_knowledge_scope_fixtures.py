#!/usr/bin/env python3
"""Deterministic long-form knowledge-scope regression fixture.

The fixture models a chapter-three context: the key and storm signal are known,
while the killer identity and hidden motive are future reveals.  The test runs
the release evaluator directly and then injects bad claims to prove that both a
future reveal and an entity-scope mismatch fail the gate without an external
model or network.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluation_runner import evaluate_fixture  # noqa: E402


def main() -> None:
    fixture_path = ROOT / "tests" / "fixtures" / "longform_knowledge_scope.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    # The JSON fixture invokes this script when run by the release runner.  Do
    # not recursively invoke that executable while testing evaluator semantics.
    fixture.pop("executable", None)

    passed = evaluate_fixture(fixture)
    assert passed["passed"], passed
    scope = passed["metrics"]["knowledge_scope"]
    assert scope["leakage_rate"] == 0
    assert scope["precision"] == 1
    assert scope["recall"] == 1
    assert scope["violations"] == []

    future = copy.deepcopy(fixture)
    future["predictions"]["knowledge_claims"].append({"id": "killer_identity", "entity_id": "lan"})
    rejected_future = evaluate_fixture(future)
    assert not rejected_future["passed"]
    assert rejected_future["metrics"]["knowledge_scope"]["leakage_rate"] > 0
    assert any("future_reveal" in item["reasons"] for item in rejected_future["metrics"]["knowledge_scope"]["violations"])

    wrong_entity = copy.deepcopy(fixture)
    wrong_entity["predictions"]["knowledge_claims"][0]["entity_id"] = "unknown_character"
    rejected_entity = evaluate_fixture(wrong_entity)
    assert not rejected_entity["passed"]
    assert any("entity_scope" in item["reasons"] for item in rejected_entity["metrics"]["knowledge_scope"]["violations"])

    print("longform knowledge-scope fixtures: ok")


if __name__ == "__main__":
    main()
