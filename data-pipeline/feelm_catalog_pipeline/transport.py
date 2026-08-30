from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .errors import (
    TmdbAuthenticationError,
    TmdbError,
    TmdbNotFound,
    TmdbTransientError,
)


@dataclass(frozen=True)
class HttpStatusError(Exception):
    status: int
    retry_after: float | None = None


class RawJsonTransport(Protocol):
    def get(self, path: str, params: Mapping[str, str]) -> dict[str, Any]: ...


class JsonTransport(Protocol):
    def get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]: ...


class UrlLibRawJsonTransport:
    def __init__(self, token: str, base_url: str = "https://api.themoviedb.org/3") -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")

    def get(self, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        query = urllib.parse.urlencode(sorted(params.items()))
        url = f"{self._base_url}/{path.lstrip('/')}"
        if query:
            url += "?" + query
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": "FEELM-Catalog-Pipeline/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise TmdbError("TMDB_INVALID_RESPONSE", "TMDB response must be a JSON object")
                return payload
        except urllib.error.HTTPError as error:
            retry_after: float | None = None
            raw_retry_after = error.headers.get("Retry-After") if error.headers else None
            if raw_retry_after:
                try:
                    retry_after = max(0.0, min(float(raw_retry_after), 60.0))
                except ValueError:
                    retry_after = None
            raise HttpStatusError(error.code, retry_after) from None
        except (TimeoutError, urllib.error.URLError) as error:
            raise TmdbTransientError("TMDB_NETWORK_ERROR", "TMDB network request failed") from error


class RetryingJsonTransport:
    def __init__(
        self,
        raw: RawJsonTransport,
        *,
        max_attempts: int = 5,
        sleeper: Callable[[float], None] = time.sleep,
        base_delay: float = 1.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self._raw = raw
        self._max_attempts = max_attempts
        self._sleeper = sleeper
        self._base_delay = base_delay

    def get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        safe_params = dict(params or {})
        for attempt in range(self._max_attempts):
            try:
                return self._raw.get(path, safe_params)
            except HttpStatusError as error:
                if error.status == 401:
                    raise TmdbAuthenticationError() from None
                if error.status == 404:
                    raise TmdbNotFound() from None
                retryable = error.status == 429 or 500 <= error.status < 600
                if not retryable:
                    raise TmdbError(
                        f"TMDB_HTTP_{error.status}", f"TMDB request failed with HTTP {error.status}"
                    ) from None
                if attempt + 1 == self._max_attempts:
                    raise TmdbTransientError(
                        f"TMDB_HTTP_{error.status}", "TMDB retry budget was exhausted"
                    ) from None
                delay = error.retry_after
                if delay is None:
                    delay = min(self._base_delay * (2**attempt), 30.0)
                self._sleeper(delay)
            except TmdbTransientError:
                if attempt + 1 == self._max_attempts:
                    raise
                self._sleeper(min(self._base_delay * (2**attempt), 30.0))
        raise AssertionError("unreachable")


class FileCachedJsonTransport:
    """Caches JSON payloads only; request headers and credentials are never persisted."""

    def __init__(self, delegate: JsonTransport, cache_dir: Path) -> None:
        self._delegate = delegate
        self._cache_dir = cache_dir

    def get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        normalized = {"path": path, "params": sorted(dict(params or {}).items())}
        key = hashlib.sha256(
            json.dumps(normalized, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        cache_path = self._cache_dir / key[:2] / f"{key}.json"
        if cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        payload = self._delegate.get(path, params)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(f".{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache_path)
        return payload
