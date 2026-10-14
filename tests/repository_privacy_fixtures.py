#!/usr/bin/env python3
"""Repository privacy fixture for tracked and staged Git content only.

This check reads blobs from the Git index, never the working tree.  Ignored
local credentials, databases, and blind mappings therefore cannot be scanned or
printed accidentally.  Findings contain only a path and a rule category.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

_SK_KEY = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{30,}(?![A-Za-z0-9])")
_BEARER = re.compile(
    r"(?i)(?:[\"']?authorization[\"']?\s*[:=]\s*[\"']?Bearer)\s+"
    r"([A-Za-z0-9._~+/=-]{20,})"
)
_ENV_ASSIGNMENT = re.compile(
    r"(?im)^[ \t]*(?:MOYU_AI_API_KEY|OPENAI_API_KEY|API_KEY)"
    r"[ \t]*=[ \t]*([^\s#]+)"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"
)

_SAFE_PLACEHOLDER_PREFIXES = (
    "fixture-",
    "test-",
    "example-",
    "placeholder-",
    "dummy-",
    "redacted-",
    "fake-",
    "your-key",
    "changeme",
)
_SAFE_PLACEHOLDER_VALUES = {
    "",
    "none",
    "null",
    "your-key-here",
    "<your-key>",
    "<redacted>",
    "[redacted]",
    "replace-me",
}


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str


def _safe_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'")
    lowered = normalized.casefold()
    return (
        lowered in _SAFE_PLACEHOLDER_VALUES
        or lowered.startswith(_SAFE_PLACEHOLDER_PREFIXES)
        or normalized.startswith(("<", "[", "${"))
    )


def _index_blobs() -> list[tuple[str, bytes]]:
    """Return path/blob pairs from the Git index, never from the worktree."""
    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    entries: list[tuple[str, str]] = []
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, object_id, _stage = metadata.split()
        # A gitlink is a submodule commit, not a blob to inspect.
        if mode == b"160000":
            continue
        entries.append((os_fsdecode(raw_path), object_id.decode("ascii")))

    if not entries:
        return []
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        input="".join(object_id + "\n" for _, object_id in entries).encode("ascii"),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    blobs: list[tuple[str, bytes]] = []
    offset = 0
    for path, expected_id in entries:
        header_end = batch.find(b"\n", offset)
        if header_end < 0:
            raise AssertionError("Git index blob batch response was incomplete.")
        header = batch[offset:header_end].split()
        if len(header) != 3 or header[0].decode("ascii") != expected_id or header[1] != b"blob":
            raise AssertionError("Git index did not resolve to the expected blob.")
        size = int(header[2])
        body_start = header_end + 1
        body_end = body_start + size
        if body_end >= len(batch) or batch[body_end:body_end + 1] != b"\n":
            raise AssertionError("Git index blob batch body was incomplete.")
        blobs.append((path, batch[body_start:body_end]))
        offset = body_end + 1
    return blobs


def os_fsdecode(raw_path: bytes) -> str:
    """Decode Git's NUL-delimited path without changing or displaying content."""
    return raw_path.decode("utf-8", errors="surrogateescape")


def _sensitive_path_rule(path: str) -> str | None:
    name = Path(path).name.casefold()
    if name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.template"}):
        return "tracked_env_file"
    if name == "blind-key-private.json":
        return "tracked_blind_mapping"
    if name in {
        "credentials.json",
        "credentials.yml",
        "credentials.yaml",
        "secrets.json",
        "secrets.yml",
        "secrets.yaml",
        "api-key.json",
        "api_keys.json",
    }:
        return "tracked_secret_file"
    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return "tracked_private_key_file"
    return None


def _text_findings(path: str, content: bytes) -> list[Finding]:
    text = content.decode("utf-8", errors="replace")
    findings: list[Finding] = []
    if _SK_KEY.search(text):
        findings.append(Finding(path, "sk_api_key"))
    for match in _BEARER.finditer(text):
        if not _safe_placeholder(match.group(1)):
            findings.append(Finding(path, "authorization_bearer"))
            break
    for match in _ENV_ASSIGNMENT.finditer(text):
        if not _safe_placeholder(match.group(1)):
            findings.append(Finding(path, "api_key_environment_value"))
            break
    if _PRIVATE_KEY.search(text):
        findings.append(Finding(path, "private_key_material"))
    return findings


def scan_index() -> list[Finding]:
    findings: list[Finding] = []
    for path, content in _index_blobs():
        path_rule = _sensitive_path_rule(path)
        if path_rule:
            findings.append(Finding(path, path_rule))
        findings.extend(_text_findings(path, content))
    return findings


def _assert_synthetic_contract() -> None:
    """Exercise the detector without placing a real credential in this repo."""
    synthetic_sk = "sk-" + ("0" * 64)
    assert _text_findings("fixture.txt", synthetic_sk.encode()) == [
        Finding("fixture.txt", "sk_api_key")
    ]
    synthetic_bearer = "Bearer " + "abcdefghijklmnopqrstuvwxyz012345"
    assert _text_findings(
        "fixture.txt", ("Authorization: " + synthetic_bearer).encode()
    ) == [Finding("fixture.txt", "authorization_bearer")]
    assert _text_findings(
        "fixture.json", ('{"Authorization": "' + synthetic_bearer + '"}').encode()
    ) == [Finding("fixture.json", "authorization_bearer")]
    assert _text_findings(
        "fixture.txt", b"Authorization: Bearer fixture-original-credential"
    ) == []
    assert _text_findings(
        ".env.example", b"MOYU_AI_API_KEY=your-key-here\n"
    ) == []
    assert _text_findings(
        ".env.example", ("MOYU_AI_API_KEY=" + synthetic_sk).encode()
    ) == [Finding(".env.example", "sk_api_key"), Finding(".env.example", "api_key_environment_value")]
    assert _sensitive_path_rule(".env.example") is None
    assert _sensitive_path_rule(".env.production") == "tracked_env_file"
    assert _sensitive_path_rule("docs/blind-key-private.json") == "tracked_blind_mapping"
    assert _sensitive_path_rule("keys/provider.pem") == "tracked_private_key_file"
    private_header = ("-----BEGIN " + "PRIVATE KEY-----").encode()
    assert _text_findings("fixture.txt", private_header) == [
        Finding("fixture.txt", "private_key_material")
    ]


def main() -> int:
    _assert_synthetic_contract()
    findings = scan_index()
    if findings:
        # Do not print matched content or local secret material.
        details = ", ".join(f"{item.path} ({item.rule})" for item in findings)
        raise AssertionError("tracked/staged privacy violation(s): " + details)
    print("repository privacy fixtures: ok (Git index only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
