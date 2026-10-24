#!/usr/bin/env python3
"""真实模型对照：每轮额度口径 ``upper - 已写`` vs ``按目标摊平（round_target_ratio）``。

走**真实生产链路**，不进程内调用引擎：

    POST /api/projects            建项目
    POST /api/chapters            建章（正文作为续写起点）
    POST /api/ai/queue            提交 continue 任务，带 budgeted_continuation
    GET  /api/ai/jobs/<id>        轮询到终态，读 result.budgeted_continuation

凭据只从环境变量读，**不写入任何文件、不打印**：

    MOYU_AI_BASE_URL   provider 的 base URL（服务端也用同一组变量，脚本只用于回显）
    MOYU_AI_API_KEY    provider 的 key（脚本只检查「非空」，绝不回显）
    MOYU_AI_MODEL      provider 的模型名

服务端必须已经用同一组环境变量启动（``GET /api/ai/status`` 的 mode 为 provider）。
脚本自身连哪个服务端由 ``--server-url`` 指定（默认 http://127.0.0.1:8765）。

典型用法::

    MOYU_AI_BASE_URL=... MOYU_AI_API_KEY=... MOYU_AI_MODEL=... \\
      .venv/bin/python benchmarks/real-model/compare_round_target.py \\
        --targets 1200,3000 --runs 1 --ratio 1.35 --baseline \\
        --out benchmarks/real-model/round-target-2026-10-25.json

退出码：
    0  全部配置都跑完（含「按比例臂全部成功」）
    2  服务端未配置 provider（mode=offline）或连不上
    3  按比例臂被服务端拒绝（例如 budget 字段白名单还没有 round_target_ratio）
    4  输出文件已存在（默认不覆盖任何已有证据）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERVER = "http://127.0.0.1:8765"
TERMINAL_STATES = {"success", "failed", "canceled"}
# 引擎侧默认值：显式写进请求，避免服务端默认值变化影响对照。
BUDGET_DEFAULTS = {
    "max_rounds": 6,
    "min_round_gain": 50,
    "max_stall_retries": 1,
    "hard_max_rounds": 12,
}
SEED_BODY = (
    "第一章开篇：雨停之后，镇上的钟楼第一次没有响。"
    "沈砚站在石阶上，手里的信纸被风掀起一角，露出半行褪色的字。"
)


class Blocked(RuntimeError):
    """服务端明确拒绝某个配置（不是脚本 bug，也不是模型失败）。"""


def provider_environment() -> dict[str, str]:
    """读取 provider 环境变量；只回报「是否配置」，绝不回显 key。"""
    base_url = (os.environ.get("MOYU_AI_BASE_URL") or "").strip()
    model = (os.environ.get("MOYU_AI_MODEL") or "").strip()
    key = (os.environ.get("MOYU_AI_API_KEY") or "").strip()
    missing = [
        name
        for name, value in (
            ("MOYU_AI_BASE_URL", base_url),
            ("MOYU_AI_API_KEY", key),
            ("MOYU_AI_MODEL", model),
        )
        if not value
    ]
    return {
        "base_url": base_url,
        "model": model,
        "api_key_present": bool(key),
        "missing": missing,
    }


def parse_targets(raw: str) -> list[int]:
    """把 ``--targets 1200,3000`` 解析成去重升序的目标字数列表。"""
    values: list[int] = []
    for piece in str(raw).split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            value = int(piece)
        except ValueError:
            raise SystemExit(f"--targets 里有非整数：{piece!r}") from None
        if not 500 <= value <= 20_000:
            raise SystemExit(f"--targets 必须在 500–20000 之间：{value}")
        if value not in values:
            values.append(value)
    if not values:
        raise SystemExit("--targets 至少要有一个目标字数。")
    return sorted(values)


def dup12(text: str) -> float:
    """重复 12-gram 占比：衡量「模型在原地打转」的粗略指标。

    先去掉所有空白（与引擎的字符口径一致），再滑窗取 12 字片段；
    片段太少（< 2 个）时返回 0.0，避免把短文本误判成重复。
    """
    dense = "".join(str(text).split())
    if len(dense) < 24:
        return 0.0
    grams = [dense[i:i + 12] for i in range(len(dense) - 11)]
    if len(grams) < 2:
        return 0.0
    return round(1 - len(set(grams)) / len(grams), 6)


class Server:
    """最小 HTTP 客户端：只发 JSON，失败时把服务端原文带进异常。"""

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.project: str | None = None

    def call(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.project:
            headers["X-Moyu-Project"] = self.project
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {"error": raw[:500]}
            return {"__status__": exc.code, **parsed}
        except urllib.error.URLError as exc:
            raise Blocked(f"连不上服务端 {self.base_url}：{exc.reason}") from None


def preflight(server: Server, provider: dict[str, str]) -> dict:
    """确认服务端处于 provider 模式，并记录回显信息（不含 key）。"""
    status = server.call("GET", "/api/ai/status")
    if "__status__" in status:
        raise Blocked(f"GET /api/ai/status 失败：{status}")
    if status.get("mode") != "provider":
        raise Blocked(
            "服务端处于离线模式（mode=%s）。请用同一组 MOYU_AI_BASE_URL / "
            "MOYU_AI_API_KEY / MOYU_AI_MODEL 启动服务端后再跑对照。"
            % status.get("mode")
        )
    return {
        "server_mode": status.get("mode"),
        "server_model": status.get("model"),
        "provider_environment": {
            "base_url": provider["base_url"],
            "model": provider["model"],
            "api_key_present": provider["api_key_present"],
        },
    }


def new_project(server: Server) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    created = server.call("POST", "/api/projects", {"name": f"round-target-{stamp}"})
    if "__status__" in created:
        raise Blocked(f"POST /api/projects 失败：{created}")
    # 服务端 POST /api/projects 返回 {"project": {...}, "state": {...}}，
    # 不是裸的 {"id": ...}：项目 id 在 project 子对象里。
    identifier = (created.get("id") or created.get("project_id")
                  or (created.get("project") or {}).get("id"))
    if not identifier:
        raise Blocked(f"POST /api/projects 没返回项目 id：{created}")
    server.project = str(identifier)
    return server.project


def new_chapter(server: Server, target: int, arm: str) -> str:
    chapter = server.call(
        "POST",
        "/api/chapters",
        {"title": f"rt-{target}-{arm}", "body": SEED_BODY},
    )
    if "__status__" in chapter:
        raise Blocked(f"POST /api/chapters 失败：{chapter}")
    identifier = chapter.get("id")
    if not identifier:
        raise Blocked(f"POST /api/chapters 没返回章节 id：{chapter}")
    return str(identifier)


def queue_continuation(
    server: Server, chapter_id: str, target: int, ratio: float | None
) -> dict:
    """提交一条真实的 budgeted_continuation 任务；返回 job。"""
    budget = {"target_characters": target, **BUDGET_DEFAULTS}
    if ratio is not None:
        budget["round_target_ratio"] = ratio
    queued = server.call(
        "POST",
        "/api/ai/queue",
        {
            "task": "continue",
            "chapter_id": chapter_id,
            "response_format": "json",
            "stream": False,
            "budgeted_continuation": budget,
        },
    )
    if "__status__" in queued:
        message = str(queued.get("error") or "")
        if ratio is not None and ("round_target_ratio" in message or "未知字段" in message):
            raise Blocked(
                "服务端不接受 budgeted_continuation.round_target_ratio："
                f"{message}（需要 backend/server.py 的 BUDGETED_CONTINUATION_FIELDS 与 "
                "_run_budgeted_continuation_job 透传该字段）"
            )
        raise Blocked(f"POST /api/ai/queue 失败：{queued}")
    if not queued.get("id"):
        raise Blocked(f"POST /api/ai/queue 没返回 job id：{queued}")
    return queued


def wait_job(server: Server, job_id: str, poll: float, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = server.call("GET", f"/api/ai/jobs/{job_id}")
        if "__status__" in job:
            raise Blocked(f"GET /api/ai/jobs/{job_id} 失败：{job}")
        if job.get("status") in TERMINAL_STATES:
            return job
        if time.monotonic() > deadline:
            raise Blocked(f"job {job_id} 在 {timeout:.0f}s 内没有到终态：{job.get('status')}")
        time.sleep(poll)


def measure(
    server: Server, target: int, arm: str, ratio: float | None, poll: float, timeout: float
) -> dict:
    """跑一组配置，返回可直接写进 JSON 的一行证据。"""
    chapter_id = new_chapter(server, target, arm)
    started = time.monotonic()
    queued = queue_continuation(server, chapter_id, target, ratio)
    job = wait_job(server, str(queued["id"]), poll, timeout)
    elapsed = round(time.monotonic() - started, 2)
    result = job.get("result") or {}
    report = result.get("budgeted_continuation") or {}
    text = str(result.get("text") or "")
    rounds = [
        {
            "round_index": item.get("round_index"),
            "characters": item.get("characters"),
            "cumulative_characters": item.get("cumulative_characters"),
            "retried": item.get("retried"),
            "finish_reason": item.get("finish_reason"),
        }
        for item in (report.get("round_metrics") or [])
    ]
    if not report:
        return {
            "arm": arm,
            "ratio": ratio,
            "target_characters": target,
            "status": job.get("status"),
            "error": job.get("error"),
            "note": "job 没有 budgeted_continuation 报告：该配置没有走到多轮引擎",
            "elapsed_s": elapsed,
        }
    return {
        "arm": arm,
        "ratio": ratio,
        "target_characters": target,
        "job_status": job.get("status"),
        "characters": report.get("characters"),
        "target_range": report.get("target_range"),
        "target_reached": report.get("target_reached"),
        "within_tolerance": report.get("within_tolerance"),
        "first_pass": bool(report.get("target_reached"))
        and report.get("within_tolerance") is True,
        "rounds": report.get("rounds"),
        "dispatched_calls": report.get("dispatched_calls"),
        "retries": report.get("retries"),
        "extended_rounds": report.get("extended_rounds"),
        "stop_reason": report.get("stop_reason"),
        "round_characters": [item["characters"] for item in rounds],
        "round_metrics": rounds,
        "model": result.get("model") or result.get("returned_model"),
        "elapsed_s": elapsed,
        "dup12": dup12(text),
        "text_characters": len("".join(text.split())),
    }


def summarize(rows: list[dict]) -> dict:
    """按臂汇总：通过数、3000 字子集、平均轮数。全部计入（不是 non-counting）。"""
    summary: dict[str, dict] = {}
    for arm in ("baseline", "ratio"):
        arm_rows = [row for row in rows if row.get("arm") == arm and "first_pass" in row]
        if not arm_rows:
            summary[arm] = {"runs": 0, "first_pass": 0, "note": "没有可用数据"}
            continue
        summary[arm] = {
            "runs": len(arm_rows),
            "first_pass": sum(1 for row in arm_rows if row["first_pass"]),
            "rounds_mean": round(
                sum(row["rounds"] for row in arm_rows) / len(arm_rows), 2
            ),
            "overshoot": [
                row["target_characters"] for row in arm_rows
                if row.get("stop_reason") == "target_reached" and not row["first_pass"]
            ],
            "rows": [
                {
                    "target_characters": row["target_characters"],
                    "characters": row["characters"],
                    "within_tolerance": row["within_tolerance"],
                    "rounds": row["rounds"],
                }
                for row in arm_rows
            ],
        }
    return summary


def self_check() -> int:
    """不连服务端、不花额度的自检：只验本地纯函数。"""
    assert dup12("字" * 60) > 0.9, dup12("字" * 60)
    assert dup12(SEED_BODY) == 0.0 or dup12(SEED_BODY) < 0.5
    assert dup12("短") == 0.0
    assert parse_targets("3000, 1200,1200") == [1200, 3000]
    assert summarize([
        {"arm": "ratio", "target_characters": 1200, "first_pass": True, "rounds": 2,
         "characters": 1150, "within_tolerance": True},
        {"arm": "baseline", "target_characters": 1200, "first_pass": False, "rounds": 1,
         "characters": 1414, "within_tolerance": False, "stop_reason": "target_reached"},
    ])["ratio"]["first_pass"] == 1
    print(json.dumps({"self_check": "ok", "dup12_seed": dup12(SEED_BODY)},
                     ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="真实模型对照：每轮额度按上界 vs 按目标摊平（round_target_ratio）"
    )
    parser.add_argument("--targets", default="1200,3000", help="目标字数，逗号分隔")
    parser.add_argument("--runs", type=int, default=1, help="每组配置重复次数")
    parser.add_argument("--ratio", type=float, default=1.35, help="按目标摊平的系数")
    parser.add_argument("--baseline", action="store_true", help="同时跑基线臂（ratio=None）")
    parser.add_argument("--server-url", default=DEFAULT_SERVER, help="本地服务端地址")
    parser.add_argument("--out", required=False, help="输出 JSON 路径（必填，且不覆盖已有文件）")
    parser.add_argument("--poll", type=float, default=2.0, help="轮询间隔秒")
    parser.add_argument("--timeout", type=float, default=600.0, help="单个 job 的等待上限秒")
    parser.add_argument(
        "--allow-missing-ratio",
        action="store_true",
        help="按比例臂被服务端拒绝时仍继续跑基线臂（退出码仍为 3）",
    )
    parser.add_argument(
        "--self-check", action="store_true", help="只跑本地纯函数自检，不连服务端"
    )
    args = parser.parse_args(argv)

    if args.self_check:
        return self_check()
    if not args.out:
        parser.error("必须显式给 --out（默认不写任何证据文件，避免覆盖）")
    out_path = Path(args.out)
    if out_path.exists():
        print(f"拒绝覆盖已有证据文件：{out_path}", file=sys.stderr)
        return 4
    if args.runs < 1:
        parser.error("--runs 必须 >= 1")
    if args.ratio is None or args.ratio < 1.0:
        parser.error("--ratio 必须 >= 1.0")
    targets = parse_targets(args.targets)

    provider = provider_environment()
    if provider["missing"]:
        print(
            "缺少 provider 环境变量：" + "、".join(provider["missing"]),
            file=sys.stderr,
        )
        return 2

    server = Server(args.server_url, args.timeout)
    try:
        meta = preflight(server, provider)
        project = new_project(server)
    except Blocked as exc:
        print(f"预检失败：{exc}", file=sys.stderr)
        return 2

    arms: list[tuple[str, float | None]] = []
    if args.baseline:
        arms.append(("baseline", None))
    arms.append(("ratio", args.ratio))

    rows: list[dict] = []
    blocked: list[str] = []
    for run_index in range(1, args.runs + 1):
        for target in targets:
            for arm, ratio in arms:
                try:
                    row = measure(server, target, arm, ratio, args.poll, args.timeout)
                except Blocked as exc:
                    row = {
                        "arm": arm,
                        "ratio": ratio,
                        "target_characters": target,
                        "blocked": str(exc),
                    }
                    blocked.append(str(exc))
                row["run"] = run_index
                rows.append(row)
                print(
                    "[{run}] {arm:8s} target={target:5d} "
                    "chars={chars} tol={tol} rounds={rounds} stop={stop} {extra}".format(
                        run=run_index,
                        arm=arm,
                        target=target,
                        chars=row.get("characters", "-"),
                        tol=row.get("within_tolerance", "-"),
                        rounds=row.get("rounds", "-"),
                        stop=row.get("stop_reason", row.get("job_status", "blocked")),
                        extra=("blocked=" + row["blocked"][:80]) if "blocked" in row else "",
                    )
                )

    payload = {
        "script": "benchmarks/real-model/compare_round_target.py",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server_url": args.server_url,
        "project_id": project,
        **meta,
        "config": {
            "targets": targets,
            "runs": args.runs,
            "ratio": args.ratio,
            "baseline": bool(args.baseline),
            "budget_defaults": BUDGET_DEFAULTS,
        },
        "accounting": {
            "counted": True,
            "first_pass_rule": "stop_reason == target_reached and within_tolerance",
            "note": "真实模型数据，全部计入；不做 non-counting 标注。",
        },
        "rows": rows,
        "summary": summarize(rows),
        "blocked": sorted(set(blocked)),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"写入 {out_path}")
    if blocked:
        print("有配置被服务端拒绝，见 blocked 字段。", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
