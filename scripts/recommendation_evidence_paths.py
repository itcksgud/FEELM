from __future__ import annotations

import hashlib
from os import fspath
from pathlib import Path
from typing import Any


def repository_path(value: str | Path) -> Path:
    """Resolve a repository-relative manifest path on Windows or POSIX."""
    return Path(fspath(value).replace("\\", "/"))


def artifact_matches(path: Path, record: dict[str, Any]) -> bool:
    """Verify exact artifact bytes, allowing only Git's CRLF/LF checkout transform."""
    if not path.is_file():
        return False
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    candidates = (raw, lf, lf.replace(b"\n", b"\r\n"))
    expected_bytes = record.get("bytes")
    expected_sha256 = record.get("sha256")
    return any(
        (expected_bytes is None or len(candidate) == expected_bytes)
        and hashlib.sha256(candidate).hexdigest() == expected_sha256
        for candidate in dict.fromkeys(candidates)
    )
