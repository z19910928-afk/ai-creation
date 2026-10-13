#!/usr/bin/env python3
"""One authorized short request; record response shapes without raw content."""
from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import socket
import ssl
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moyu_provider import prepare_chat_request
from scripts.literary_evaluation import now, write_json


def describe(value, secret: str):
    if isinstance(value, str):
        return {"type": "string", "chars": len(value)}
    if value is None:
        return {"type": "null"}
    if isinstance(value, dict):
        return {"type": "object", "keys": [str(key).replace(secret, "[hidden]")[:80]
                                          for key in value] if secret else list(value)}
    if isinstance(value, list):
        return {"type": "array", "length": len(value)}
    return {"type": type(value).__name__}


def event_shape(value, secret: str) -> dict:
    result = describe(value, secret)
    if not isinstance(value, dict):
        return result
    result["model"] = str(value.get("model", "")).replace(secret, "[hidden]")[:200] if secret else str(value.get("model", ""))[:200]
    result["choices"] = []
    for choice in value.get("choices", []) if isinstance(value.get("choices"), list) else []:
        row = describe(choice, secret)
        if isinstance(choice, dict):
            finish = choice.get("finish_reason")
            row["finish_reason"] = (
                finish.replace(secret, "[hidden]")[:120] if secret else finish[:120]
                ) if isinstance(finish, str) else describe(finish, secret)
            row["finish_reason_type"] = type(finish).__name__
            for field in ("delta", "message"):
                if field in choice:
                    item = choice[field]
                    row[field] = describe(item, secret)
                    if isinstance(item, dict):
                        row[field]["fields"] = {
                            str(key).replace(secret, "[hidden]")[:80] if secret else str(key)[:80]:
                            describe(content, secret) for key, content in item.items()}
        result["choices"].append(row)
    usage = value.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {key: usage[key] for key in
                            ("prompt_tokens", "completion_tokens", "total_tokens")
                            if isinstance(usage.get(key), int) and not isinstance(usage[key], bool)}
        result["usage_shape"] = describe(usage, secret)
    if "error" in value:
        result["error_shape"] = describe(value["error"], secret)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("MOYU_AI_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("MOYU_AI_MODEL", ""))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute-paid", action="store_true")
    args = parser.parse_args()
    if not args.execute_paid:
        raise ValueError("一次真实诊断需要已获授权的收费请求标记。")
    if args.output.exists():
        raise ValueError("诊断记录已存在，禁止覆盖或自动重试。")
    key = os.environ.get("MOYU_AI_API_KEY") or getpass.getpass("请输入模型密钥（不会显示或保存）：")
    config = {"base_url": args.base_url, "model": args.model, "api_key": key}
    prepared = prepare_chat_request(
        config, [{"role": "system", "content": "只返回连接测试文字，不需要解释。"},
                 {"role": "user", "content": "只回复：连接正常"}],
        task="probe", max_tokens=128, stream=True, parameters={"temperature": 0})
    record = {"created_at": now(), "purpose": "诊断协议字段，不能作为文学质量证据",
              "status": "reserved", "billing_state": "unknown",
              "request_audit": prepared["request_audit"], "events": [], "response_bytes": 0,
              "done_received": False}
    write_json(args.output, record)
    connection = (http.client.HTTPSConnection(
        prepared["host"], prepared["port"], timeout=180, context=ssl.create_default_context())
        if prepared["endpoint"].startswith("https:") else
        http.client.HTTPConnection(prepared["host"], prepared["port"], timeout=180))
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
               "X-Client-Request-Id": prepared["request_audit"]["request_id"]}
    if key:
        headers["Authorization"] = "Bearer " + key
    finished = threading.Event()
    started = time.monotonic()
    response = None
    def stop() -> None:
        if finished.wait(180):
            return
        sock = connection.sock
        if sock is None and response is not None:
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    monitor = threading.Thread(target=stop, daemon=True)
    monitor.start()
    def shape(data: bytes) -> None:
        try:
            value = json.loads(data)
            item = event_shape(value, key)
        except (UnicodeError, ValueError):
            item = {"invalid_json": True, "bytes": len(data)}
        record["events"].append(item)
        print(json.dumps({"event": len(record["events"]), "shape": item}, ensure_ascii=False), flush=True)
    try:
        record["request_audit"]["dispatched"] = True
        write_json(args.output, record)
        connection.request("POST", prepared["path"], body=prepared["body"], headers=headers)
        response = connection.getresponse()
        record["http_status"] = response.status
        record["content_type"] = response.getheader("Content-Type", "").split(";", 1)[0]
        if response.status != 200:
            record["status"] = "http_error"
        elif record["content_type"] == "text/event-stream":
            lines = []
            while True:
                raw = response.readline(256_001)
                if not raw:
                    break
                record["response_bytes"] += len(raw)
                if len(raw) > 256_000 or record["response_bytes"] > 1_000_000:
                    record["status"] = "size_limit"
                    break
                line = raw.rstrip(b"\r\n")
                if line.startswith(b"data:"):
                    lines.append(line[5:].lstrip(b" "))
                elif not line and lines:
                    data = b"\n".join(lines)
                    lines = []
                    if data.strip() == b"[DONE]":
                        record["done_received"] = True
                        break
                    shape(data)
            if record["status"] == "reserved":
                record["status"] = "observed"
        else:
            data = response.read(1_000_001)
            record["response_bytes"] = len(data)
            if len(data) > 1_000_000:
                record["status"] = "size_limit"
            else:
                shape(data)
                record["status"] = "observed"
    except (OSError, http.client.HTTPException):
        record["status"] = "connection_unknown"
    except KeyboardInterrupt:
        record["status"] = "interrupted_unknown"
    finally:
        record["latency_ms"] = round((time.monotonic() - started) * 1000)
        record["ended_at"] = now()
        record["billing_state"] = "usage_reported" if any(item.get("usage") for item in record["events"]) else "unknown"
        finished.set()
        connection.close()
        if response is not None:
            response.close()
        monitor.join(timeout=1)
        write_json(args.output, record)
    print(json.dumps({key: record[key] for key in
        ("status", "http_status", "latency_ms", "response_bytes", "done_received", "billing_state")
        if key in record}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
