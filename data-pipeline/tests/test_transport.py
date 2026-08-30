from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feelm_catalog_pipeline.transport import (
    FileCachedJsonTransport,
    HttpStatusError,
    RetryingJsonTransport,
)


class SequenceRawTransport:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.calls = 0

    def get(self, path: str, params: dict[str, str]) -> dict[str, object]:
        value = self.values[self.calls]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return value  # type: ignore[return-value]


class CountingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        self.calls += 1
        return {"id": 1, "title": "Cached"}


class TransportTest(unittest.TestCase):
    def test_retries_429_using_retry_after_without_leaking_request_data(self) -> None:
        raw = SequenceRawTransport([HttpStatusError(429, 0.25), {"id": 1}])
        delays: list[float] = []
        transport = RetryingJsonTransport(raw, max_attempts=2, sleeper=delays.append)

        self.assertEqual({"id": 1}, transport.get("movie/1"))
        self.assertEqual(2, raw.calls)
        self.assertEqual([0.25], delays)

    def test_file_cache_resumes_without_second_delegate_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            delegate = CountingTransport()
            cache = FileCachedJsonTransport(delegate, Path(temporary))
            first = cache.get("movie/1", {"language": "en-US"})
            second = cache.get("movie/1", {"language": "en-US"})

        self.assertEqual(first, second)
        self.assertEqual(1, delegate.calls)


if __name__ == "__main__":
    unittest.main()
