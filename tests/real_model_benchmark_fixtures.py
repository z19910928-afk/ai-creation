#!/usr/bin/env python3
"""真实模型对照基准的纯函数回归：不连服务端、不花额度。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "real-model" / "compare_round_target.py"
spec = importlib.util.spec_from_file_location("compare_round_target", SCRIPT)
assert spec and spec.loader
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_targets_and_dup12() -> None:
    assert benchmark.parse_targets("3000, 1200,1200") == [1200, 3000]
    assert benchmark.dup12("短") == 0.0
    assert benchmark.dup12("字" * 60) > 0.9
    assert benchmark.dup12(benchmark.SEED_BODY) < 0.5


def test_summary_counts_all_attempts() -> None:
    rows = [
        {
            "arm": "ratio", "target_characters": 3000, "first_pass": True,
            "rounds": 2, "characters": 2856, "within_tolerance": True,
        },
        {
            "arm": "ratio", "target_characters": 3000, "first_pass": True,
            "rounds": 2, "characters": 3134, "within_tolerance": True,
        },
        {
            "arm": "ratio", "target_characters": 3000, "first_pass": False,
            "rounds": 2, "characters": 3714, "within_tolerance": False,
            "stop_reason": "overshoot",
        },
        {
            "arm": "baseline", "target_characters": 3000, "first_pass": False,
            "rounds": 3, "characters": 3635, "within_tolerance": False,
            "stop_reason": "overshoot",
        },
    ]
    summary = benchmark.summarize(rows)
    assert summary["ratio"]["runs"] == 3
    assert summary["ratio"]["first_pass"] == 2
    assert summary["ratio"]["rounds_mean"] == 2.0
    assert summary["ratio"]["overshoot"] == [3000]
    assert summary["ratio"]["rows"][2]["characters"] == 3714
    assert summary["baseline"]["first_pass"] == 0


def test_provider_environment_never_returns_key() -> None:
    with patch.dict(os.environ, {
        "MOYU_AI_BASE_URL": "https://example.invalid/v1",
        "MOYU_AI_MODEL": "fake-model",
        "MOYU_AI_API_KEY": "sk-should-not-leak",
    }, clear=False):
        provider = benchmark.provider_environment()
    assert provider["api_key_present"] is True
    assert provider["missing"] == []
    assert "sk-should-not-leak" not in json.dumps(provider, ensure_ascii=False)


def test_preflight_accepts_server_configured_provider_without_env() -> None:
    class FakeServer:
        def call(self, method: str, path: str):
            assert method == "GET"
            assert path == "/api/ai/status"
            return {"mode": "provider", "model": "from-app-settings"}

    provider = {
        "base_url": "", "model": "", "api_key_present": False,
        "missing": ["MOYU_AI_BASE_URL", "MOYU_AI_API_KEY", "MOYU_AI_MODEL"],
    }
    meta = benchmark.preflight(FakeServer(), provider)
    assert meta["server_mode"] == "provider"
    assert meta["server_model"] == "from-app-settings"
    assert meta["provider_environment"]["env_complete"] is False


def main() -> int:
    test_targets_and_dup12()
    test_summary_counts_all_attempts()
    test_provider_environment_never_returns_key()
    test_preflight_accepts_server_configured_provider_without_env()
    print("real model benchmark fixtures: ok (5 checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
