#!/usr/bin/env python3
"""AI patch fixture: applying and reverting each patch restores exact content."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Patch:
    before: str
    after: str


PATCHES = [
    Patch("海风穿过旧港。", "海风穿过沉睡的旧港。"),
    Patch("她合上书。", "她慢慢合上书，抬头望向窗外。"),
]


def apply(text: str, patch: Patch) -> str:
    if text != patch.before:
        raise ValueError("patch base does not match")
    return patch.after


def revert(text: str, patch: Patch) -> str:
    if text != patch.after:
        raise ValueError("patch result does not match")
    return patch.before


def main() -> int:
    for patch in PATCHES:
        changed = apply(patch.before, patch)
        restored = revert(changed, patch)
        assert restored == patch.before
    print(f"reversible patch fixtures passed: {len(PATCHES)} restored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
