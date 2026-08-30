#!/usr/bin/env python3
"""Fail on high-confidence secret material without printing matched values."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
MAX_BYTES = 5 * 1024 * 1024
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b"),
    "openai-like-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
}


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=PROJECT,
        check=True,
        capture_output=True,
    )
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = (PROJECT / raw.decode("utf-8", errors="surrogateescape")).resolve()
        if path != SELF and path.is_file() and path.stat().st_size <= MAX_BYTES:
            paths.append(path)
    return paths


def main() -> None:
    scanned_files = 0
    scanned_bytes = 0
    findings: list[dict[str, str]] = []
    for path in candidate_files():
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        scanned_files += 1
        scanned_bytes += len(payload)
        for rule, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append({"path": path.relative_to(PROJECT).as_posix(), "rule": rule})
    if findings:
        # Paths and rule IDs are safe diagnostics; matched material is never emitted.
        raise SystemExit(json.dumps({"status": "FAIL", "findings": findings}, sort_keys=True))
    print(
        json.dumps(
            {
                "status": "PASS",
                "files_scanned": scanned_files,
                "bytes_scanned": scanned_bytes,
                "high_confidence_findings": 0,
                "ignored_files_scanned": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
