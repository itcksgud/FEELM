from __future__ import annotations

from os import fspath
from pathlib import Path


def repository_path(value: str | Path) -> Path:
    """Resolve a repository-relative manifest path on Windows or POSIX."""
    return Path(fspath(value).replace("\\", "/"))
