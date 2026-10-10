#!/usr/bin/env python3
"""Deterministic JSON release-fixture evaluator.

The runner intentionally has no model/runtime dependencies: fixtures contain a fixed
ranking and labels, allowing CI to compare behaviour and enforce release gates.

Usage examples::

    python scripts/evaluation_runner.py --fixture tests/fixtures/evaluation_pass.json
    python scripts/evaluation_runner.py --fixtures-dir tests/fixtures --output artifacts/evaluation.json

A JSON snapshot is written even when a gate fails.  The process exits 1 when any
fixture is invalid or fails a threshold, making this suitable for CI.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_K = (1, 5, 10)

# Release defaults from docs/evaluation-spec-v1.md (v1.1).
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "branch_leakage": {"max": 0},
    "stale_citation_rate": {"max": 0},
    "unsupported_canon_rate": {"max": 0},
    "corrupt_import_acceptance_rate": {"max": 0},
    "conflict_recall_hard": {"min": 0.95},
    "reversible_patch_rate": {"min": 1},
    "freshness_p50_ms": {"max": 5000},
    "freshness_p95_ms": {"max": 30000},
    "rpo_p95_seconds": {"max": 300},
    "rto_p95_seconds": {"max": 900},
    "recovery_success_rate": {"min": 1},
}


def _number(value: Any) -> float | None:
    if isinstance(value, Mapping):
        value = value.get("value", value.get("actual"))
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _id(item: Any) -> str:
    if isinstance(item, Mapping):
        for key in ("id", "evidence_id", "claim_id", "conflict_id", "entity_id", "key", "name"):
            if item.get(key) is not None:
                return str(item[key])
    return str(item)


def _bool(item: Any, *keys: str, default: bool = False) -> bool:
    if isinstance(item, bool):
        return item
    if isinstance(item, Mapping):
        for key in keys:
            if key in item:
                value = item[key]
                if isinstance(value, str):
                    if value.strip().lower() in {"false", "0", "no", "n", "off", ""}:
                        return False
                    if value.strip().lower() in {"true", "1", "yes", "y", "on"}:
                        return True
                return bool(value)
    return default


def _values(items: Any, *keys: str) -> list[float]:
    out: list[float] = []
    for item in _items(items):
        value: Any = item
        if isinstance(item, Mapping):
            value = next((item[k] for k in keys if k in item), None)
        n = _number(value)
        if n is not None:
            out.append(n)
    return out


def _dedupe(items: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = _id(item)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def _pct(values: Sequence[float], percentile: float) -> float | None:
    """Linear percentile; p50 and p95 are deterministic for tiny samples."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def _metric(value: float | None, sample_count: int) -> dict[str, Any]:
    return {"value": value, "sample_count": sample_count, "na": value is None, "na_count": 1 if value is None else 0, "status": "NA" if value is None else "measured"}


def _f1(gold: Iterable[Any], predicted: Iterable[Any]) -> dict[str, Any]:
    g = set(_dedupe(gold))
    p = set(_dedupe(predicted))
    hit = len(g & p)
    precision = _ratio(hit, len(p))
    recall = _ratio(hit, len(g))
    return {"precision": precision, "recall": recall, "f1": None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall), "sample_count": len(g), "predicted_count": len(p), "hits": hit, "na": precision is None or recall is None}


def _branch_ancestors(graph: Any, branch: str) -> set[str]:
    """Return branch plus ancestors from common JSON branch-graph encodings.

    Accepted forms are ``{child: parent}``, ``{"nodes": [{"id", "parent_id"}]}``,
    ``{"edges": [{"parent", "child"}]}``, and a bare node/edge list.
    """
    parents: dict[str, list[str]] = {}

    def add(child: Any, parent: Any) -> None:
        if child is None or parent is None:
            return
        parents.setdefault(str(child), []).append(str(parent))

    if isinstance(graph, Mapping):
        nodes = graph.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, Mapping):
                    child = node.get("id", node.get("branch_id"))
                    parent = node.get("parent_id", node.get("parent"))
                    for p in _items(parent):
                        add(child, p)
        edges = graph.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if isinstance(edge, Mapping):
                    parent = edge.get("parent", edge.get("parent_id", edge.get("from")))
                    child = edge.get("child", edge.get("child_id", edge.get("to")))
                    add(child, parent)
        # Plain child -> parent map (metadata keys are ignored). An explicit
        # direction also supports parent -> children representations.
        parent_to_children = str(graph.get("direction", "")).lower() in {"parent_to_children", "parent->children", "parent-children"}
        for key, value in graph.items():
            if key in {"nodes", "edges", "root", "direction"}:
                continue
            if isinstance(value, Mapping):
                value = value.get("children" if parent_to_children else "parents", value.get("parent", value.get("parent_id")))
            for other in _items(value):
                if other is not None:
                    add(other, key) if parent_to_children else add(key, other)
    elif isinstance(graph, list):
        for item in graph:
            if isinstance(item, Mapping):
                child = item.get("child", item.get("child_id", item.get("id", item.get("branch_id", item.get("to")))))
                parent = item.get("parent", item.get("parent_id", item.get("from")))
                add(child, parent)

    seen: set[str] = set()
    stack = [str(branch)]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(parents.get(current, []))
    return seen


def _extract_predictions(fixture: Mapping[str, Any]) -> Mapping[str, Any]:
    p = fixture.get("predictions", fixture.get("results", fixture.get("outputs", {})))
    return p if isinstance(p, Mapping) else {}


def evaluate_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one fixture and return a JSON-serialisable snapshot."""
    errors: list[str] = []
    metadata = {key: fixture.get(key) for key in ("fixture_id", "schema_version", "model", "prompt_version", "seed")}
    for key, value in metadata.items():
        if value is None or value == "":
            errors.append(f"missing metadata: {key}")
    pred = _extract_predictions(fixture)
    gold = fixture.get("gold", {}) if isinstance(fixture.get("gold", {}), Mapping) else {}
    thresholds = dict(DEFAULT_THRESHOLDS)
    custom = fixture.get("thresholds", {})
    if isinstance(custom, Mapping):
        for key, val in custom.items():
            if isinstance(val, Mapping):
                thresholds[key] = dict(val)
            else:
                # Numeric thresholds are interpreted according to the metric's default direction.
                direction = "min" if key in {"conflict_recall_hard", "reversible_patch_rate", "recovery_success_rate"} else "max"
                thresholds[key] = {direction: val}

    # Evidence Recall/Precision at every required K. Candidate/ranking aliases are accepted.
    gold_evidence = _dedupe(gold.get("evidence", gold.get("evidence_ids", [])))
    ranking = _dedupe(pred.get("evidence", pred.get("evidence_ranking", pred.get("candidates", []))))
    evidence: dict[str, Any] = {}
    for k in DEFAULT_K:
        top = ranking[:k]
        hits = len(set(top) & set(gold_evidence))
        recall = _ratio(hits, len(gold_evidence))
        precision = _ratio(hits, len(top))
        evidence[str(k)] = {"recall": recall, "precision": precision, "hits": hits, "gold_count": len(gold_evidence), "returned_count": len(top), "sample_count": len(gold_evidence), "na_recall": recall is None, "na_precision": precision is None, "na_count": int(recall is None) + int(precision is None)}

    citations = _items(pred.get("citations", pred.get("citation_results", [])))
    cited = [x for x in citations if not isinstance(x, Mapping) or bool(x.get("cited", True))]
    supported = sum(1 for x in cited if _bool(x, "supported", "valid", "entails", default=False))
    citation_precision = _metric(_ratio(supported, len(cited)), len(cited))

    # Branch leakage: facts whose branch is not the requested branch or an ancestor.
    facts = _items(pred.get("facts", pred.get("recalled_facts", [])))
    graph = fixture.get("branch_graph", {})
    graph = graph if isinstance(graph, Mapping) else {}
    requested = pred.get("requested_branch", fixture.get("active_branch", fixture.get("branch_id")))
    allowed_source = pred.get("allowed_branches", fixture.get("allowed_branches", gold.get("allowed_branches", [])))
    allowed = set(str(x) for x in _items(allowed_source))
    if requested is not None and not allowed:
        allowed = _branch_ancestors(graph, str(requested))
    leakage_count = 0
    branch_context_available = requested is not None or bool(allowed)
    for item in facts:
        if not isinstance(item, Mapping):
            leakage_count += 1
            continue
        branch = item.get("branch", item.get("branch_id"))
        # A fact without provenance cannot be proven ancestor-safe.
        if branch is None or (allowed and str(branch) not in allowed):
            leakage_count += 1
    leakage = None if facts and not branch_context_available else _ratio(leakage_count, len(facts))

    default_results = [x for x in citations if not isinstance(x, Mapping) or bool(x.get("default", True))]
    stale_count = sum(1 for x in default_results if _bool(x, "stale", "invalid", "expired", default=False))
    stale_rate = _ratio(stale_count, len(default_results))

    canon = _items(pred.get("canon_writes", pred.get("canonical_writes", [])))
    unsupported_canon = 0
    for item in canon:
        if isinstance(item, Mapping):
            is_canon = str(item.get("state", "canon")).lower() == "canon"
            has_evidence = bool(_items(item.get("evidence", item.get("evidence_ids", []))))
        else:
            # A bare canonical ID has no attached evidence and therefore fails the gate.
            is_canon, has_evidence = True, False
        unsupported_canon += int(is_canon and not has_evidence)
    unsupported_canon_rate = _ratio(unsupported_canon, len(canon))

    conflicts_gold = gold.get("conflicts", gold.get("conflict_ids", []))
    conflicts_pred = pred.get("conflicts", pred.get("conflict_ids", []))
    conflict_all = _f1(conflicts_gold, conflicts_pred)
    hard_gold = gold.get("hard_conflicts", [])
    hard_pred = pred.get("hard_conflicts", conflicts_pred if not hard_gold else [])
    conflict_hard = _f1(hard_gold, hard_pred)
    # A gold fixture may mark conflict objects as hard rather than provide a second list.
    if not hard_gold:
        hard_gold = [_id(x) for x in _items(conflicts_gold) if isinstance(x, Mapping) and bool(x.get("hard", False))]
        hard_pred = [_id(x) for x in _items(conflicts_pred) if isinstance(x, Mapping) and bool(x.get("hard", False))]
        conflict_hard = _f1(hard_gold, hard_pred)

    merges = _f1(gold.get("merges", gold.get("entity_merges", [])), pred.get("merges", pred.get("entity_merges", [])))
    splits = _f1(gold.get("splits", gold.get("entity_splits", [])), pred.get("splits", pred.get("entity_splits", [])))

    revisions = _values(pred.get("revision_distance", pred.get("revision_distances", [])), "distance", "edits", "value")
    revision_distance = {"median": _pct(revisions, 0.5), "p95": _pct(revisions, 0.95), "sample_count": len(revisions), "na": not revisions}

    freshness_input = pred.get("freshness_ms", pred.get("freshness", []))
    freshness = _values(freshness_input, "latency_ms", "freshness_ms", "ms", "value")
    if not freshness and "freshness_seconds" in pred:
        freshness = [value * 1000 for value in _values(pred.get("freshness_seconds"), "latency_seconds", "freshness_seconds", "seconds", "value")]
    freshness_metrics = {"p50_ms": _metric(_pct(freshness, 0.5), len(freshness)), "p95_ms": _metric(_pct(freshness, 0.95), len(freshness))}

    recoveries = _items(pred.get("recovery", pred.get("recoveries", [])))
    rpo = _values(recoveries, "rpo_seconds", "rpo_s", "rpo")
    rto = _values(recoveries, "rto_seconds", "rto_s", "rto")
    recovery_successes = sum(1 for x in recoveries if _bool(x, "success", "recovered", default=False))
    recovery = {
        "rpo_p50_seconds": _metric(_pct(rpo, 0.5), len(rpo)), "rpo_p95_seconds": _metric(_pct(rpo, 0.95), len(rpo)),
        "rto_p50_seconds": _metric(_pct(rto, 0.5), len(rto)), "rto_p95_seconds": _metric(_pct(rto, 0.95), len(rto)),
        "success_rate": _metric(_ratio(recovery_successes, len(recoveries)), len(recoveries)),
    }

    imports = _items(pred.get("imports", pred.get("import_results", [])))
    corrupt = [x for x in imports if isinstance(x, Mapping) and bool(x.get("corrupt", x.get("valid") is False))]
    accepted_corrupt = sum(1 for x in corrupt if _bool(x, "accepted", "imported", default=False))
    corrupt_rate = _ratio(accepted_corrupt, len(corrupt))

    patches = _items(pred.get("patches", pred.get("ai_patches", [])))
    patch_reversible = sum(1 for x in patches if _bool(x, "reversible", "reverted", "undo_verified", default=False))
    reversible_rate = _ratio(patch_reversible, len(patches))

    metrics: dict[str, Any] = {
        "evidence": evidence,
        # Flat aliases make CI extraction convenient while retaining the grouped form.
        **{f"recall_at_{k}": evidence[str(k)]["recall"] for k in DEFAULT_K},
        **{f"precision_at_{k}": evidence[str(k)]["precision"] for k in DEFAULT_K},
        "citation_precision": citation_precision,
        "branch_leakage": _metric(leakage, len(facts)),
        "stale_citation_rate": _metric(stale_rate, len(default_results)),
        "unsupported_canon_rate": _metric(unsupported_canon_rate, len(canon)),
        "conflict_recall": conflict_all,
        "conflict_recall_hard": conflict_hard,
        "merge_f1": merges,
        "split_f1": splits,
        "revision_distance": revision_distance,
        "freshness": freshness_metrics,
        "recovery": recovery,
        "corrupt_import_acceptance_rate": _metric(corrupt_rate, len(corrupt)),
        "reversible_patch_rate": _metric(reversible_rate, len(patches)),
    }

    # Flatten gate values so threshold names remain stable and easy to consume.
    direct_metrics = pred.get("metrics", fixture.get("observed_metrics", {}))
    direct_metrics = direct_metrics if isinstance(direct_metrics, Mapping) else {}

    def observed(name: str, computed: float | None) -> float | None:
        # Declarative fixtures may provide an observed metric directly (useful for
        # importing an external run); computed values always take precedence.
        return computed if computed is not None else _number(direct_metrics.get(name))

    flat: dict[str, float | None] = {
        "branch_leakage": observed("branch_leakage", leakage),
        "stale_citation_rate": observed("stale_citation_rate", stale_rate),
        "unsupported_canon_rate": observed("unsupported_canon_rate", unsupported_canon_rate),
        "corrupt_import_acceptance_rate": observed("corrupt_import_acceptance_rate", corrupt_rate),
        "conflict_recall_hard": observed("conflict_recall_hard", conflict_hard.get("recall")),
        "reversible_patch_rate": observed("reversible_patch_rate", reversible_rate),
        "freshness_p50_ms": observed("freshness_p50_ms", _pct(freshness, 0.5)),
        "freshness_p95_ms": observed("freshness_p95_ms", _pct(freshness, 0.95)),
        "rpo_p95_seconds": observed("rpo_p95_seconds", _pct(rpo, 0.95)),
        "rto_p95_seconds": observed("rto_p95_seconds", _pct(rto, 0.95)),
        "recovery_success_rate": observed("recovery_success_rate", _ratio(recovery_successes, len(recoveries))),
    }
    # Reflect imported/observed values in the grouped snapshot as well.
    for metric_name in (
        "branch_leakage", "stale_citation_rate", "unsupported_canon_rate",
        "corrupt_import_acceptance_rate", "reversible_patch_rate",
    ):
        if metric_name in metrics and flat[metric_name] is not None:
            metrics[metric_name]["value"] = flat[metric_name]
            metrics[metric_name]["na"] = False
            metrics[metric_name]["status"] = "measured"

    threshold_results: dict[str, Any] = {}
    for name, rule in thresholds.items():
        rule = rule if isinstance(rule, Mapping) else {}
        actual = flat.get(name)
        if actual is None:
            threshold_results[name] = {"actual": None, "rule": dict(rule), "passed": False, "status": "unmeasured"}
            continue
        passed = True
        if "min" in rule:
            target = _number(rule["min"])
            passed = target is not None and actual >= target
        if "max" in rule:
            target = _number(rule["max"])
            passed = passed and target is not None and actual <= target
        threshold_results[name] = {"actual": actual, "rule": dict(rule), "passed": passed, "status": "pass" if passed else "fail"}
    passed = not errors and all(x["passed"] for x in threshold_results.values())
    # Optional fixture-local executable (for schema/import/patch smoke tests).
    executable = fixture.get("executable")
    executable_result: dict[str, Any] | None = None
    if executable:
        command = executable if isinstance(executable, list) else [sys.executable, str(executable)]
        try:
            proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=float(fixture.get("timeout_seconds", 30)))
            executable_result = {"command": command, "returncode": proc.returncode, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:], "passed": proc.returncode == 0}
            if proc.returncode != 0:
                errors.append(f"executable failed with exit {proc.returncode}")
                passed = False
        except (OSError, subprocess.SubprocessError) as exc:
            executable_result = {"command": command, "error": str(exc), "passed": False}
            errors.append(f"executable error: {exc}")
            passed = False

    sample_count = {
        "evidence_gold": len(gold_evidence), "evidence_returned": len(ranking), "citations": len(cited), "facts": len(facts), "conflicts": len(_items(conflicts_gold)), "revisions": len(revisions), "freshness": len(freshness), "recoveries": len(recoveries), "corrupt_imports": len(corrupt), "patches": len(patches)
    }
    na_count = sum(int(v.get("na_count", 0)) for v in metrics.values() if isinstance(v, Mapping))
    na_count += sum(int(item.get("na_count", 0)) for item in evidence.values())
    na_count += sum(int(item.get("na", False)) for item in (merges, splits) if isinstance(item, Mapping))

    snapshot: dict[str, Any] = {
        **metadata,
        "tolerance": fixture.get("tolerance", fixture.get("tolerances", {})),
        "k": list(DEFAULT_K),
        "default_k": 5,
        "sample_count": sample_count,
        "samples": sample_count,
        "na_count": na_count,  # backwards-compatible alias used by early CI consumers
        "metrics": metrics,
        "metric_values": {name: result["actual"] for name, result in threshold_results.items()},
        "thresholds": threshold_results,
        "threshold_results": threshold_results,
        "passed": passed,
        "fixture_passed": passed,
        "errors": errors,
    }
    if executable_result is not None:
        snapshot["executable"] = executable_result
    return snapshot


def _load(path: Path) -> list[Mapping[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, Mapping) and isinstance(data.get("fixtures"), list):
        return [x for x in data["fixtures"] if isinstance(x, Mapping)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, Mapping)]
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: expected fixture object or fixtures array")
    return [data]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="append", type=Path, help="JSON fixture (repeatable)")
    parser.add_argument("--fixtures-dir", type=Path, help="directory containing *.json fixtures")
    parser.add_argument("--output", type=Path, help="write JSON snapshot (single object or array)")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    args = parser.parse_args(argv)
    paths: list[Path] = list(args.fixture or [])
    if args.fixtures_dir:
        paths.extend(sorted(args.fixtures_dir.glob("*.json")))
    if not paths:
        # A checked-in fixture set makes ``python scripts/evaluation_runner.py`` a
        # useful CI smoke test while explicit paths remain supported.
        default_dir = ROOT / "tests" / "fixtures"
        paths.extend(sorted(default_dir.glob("*.json")))
    if not paths:
        parser.error("provide --fixture or --fixtures-dir (and no default fixtures were found)")
    snapshots: list[dict[str, Any]] = []
    load_errors: list[str] = []
    for path in paths:
        try:
            fixtures = _load(path)
            if not fixtures:
                raise ValueError("no fixture objects")
            for fixture in fixtures:
                snap = evaluate_fixture(fixture)
                snap["fixture_file"] = str(path)
                snapshots.append(snap)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            load_errors.append(f"{path}: {exc}")
    result: Any = snapshots[0] if len(snapshots) == 1 else {"fixtures": snapshots, "passed": bool(snapshots) and all(bool(s.get("passed")) for s in snapshots)}
    if load_errors:
        if isinstance(result, Mapping):
            result = dict(result)
            result.setdefault("errors", []).extend(load_errors)
            result["passed"] = False
        else:
            result["load_errors"] = load_errors
    encoded = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    failed = bool(load_errors) or any(not bool(s.get("passed")) for s in snapshots)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
