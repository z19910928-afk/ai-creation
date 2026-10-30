#!/usr/bin/env python3
"""Pure-function checks for the Q1 capable-provider arm."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.budgeted.capable import build_report  # noqa: E402


CHECKS = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global CHECKS
    if not condition:
        raise AssertionError(f"{label} failed{': ' + detail if detail else ''}")
    CHECKS += 1
    print(f"ok - {label}")


def main() -> int:
    first, markdown = build_report()
    second, _ = build_report()

    check(first["q1_engine"]["passed"] == 8, "Q1 engine 8/8", str(first["q1_engine"]))
    check(first["q1_engine"]["total"] == 8, "Q1 engine denominator excludes external", str(first["q1_engine"]))
    check(first["q1_engine"]["rate"] == 1.0, "Q1 engine rate 100%", str(first["q1_engine"]))
    check(first["q1_engine"]["met"] is True, "Q1 engine meets 90% floor", str(first["q1_engine"]))

    check(first["q1_capable_all"]["passed"] == 8, "Q1 capable all 8/9", str(first["q1_capable_all"]))
    check(first["q1_capable_all"]["total"] == 9, "Q1 capable all denominator 9", str(first["q1_capable_all"]))
    check(first["q1_capable_all"]["met"] is False, "Q1 capable all stays below 90%", str(first["q1_capable_all"]))

    check(first["all_targets"]["passed"] == 18, "all targets 18/20", str(first["all_targets"]))
    check(first["all_targets"]["total"] == 20, "all targets denominator 20", str(first["all_targets"]))

    check(first["sc15"]["first_pass"] is True, "sc-15 capable pass", str(first["sc15"]))
    check(first["sc15"]["characters"] == 4400, "sc-15 lands at upper bound", str(first["sc15"]))
    check(first["sc18"]["first_pass"] is True, "sc-18 capable pass", str(first["sc18"]))
    check(first["sc18"]["characters"] == 2700, "sc-18 reaches lower bound", str(first["sc18"]))
    check(first["sc18"]["rounds"] == 12, "sc-18 uses the hard cap", str(first["sc18"]))

    check(first["sc19"]["first_pass"] is False, "sc-19 external budget still fails", str(first["sc19"]))
    check(first["sc19"]["stop_reason"] == "budget_exhausted", "sc-19 stopped by budget", str(first["sc19"]))
    check(first["sc20"]["first_pass"] is False, "sc-20 cancellation still fails", str(first["sc20"]))
    check(first["sc20"]["stop_reason"] == "canceled", "sc-20 stopped by cancel", str(first["sc20"]))

    legacy = first["legacy_adversarial"]["target_3000"]
    check(legacy["first_pass"] == 7, "legacy adversarial remains 7/9", str(legacy))
    check(first["legacy_adversarial"]["q1_target_met"] is False, "legacy Q1 remains unmet", str(legacy))

    check(first["digest"] == second["digest"], "capable report deterministic", first["digest"][:24])
    check(first["digest"].startswith("sha256:"), "digest has algorithm prefix", first["digest"][:18])
    check("Q1 engine" in markdown and "100.0%" in markdown, "markdown reports Q1 engine", markdown[:80])
    check("legacy adversarial" in markdown and "77.8%" in markdown, "markdown preserves legacy", markdown[-200:])

    print(f"q1 capable fixtures: {CHECKS} checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
