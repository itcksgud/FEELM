from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from .errors import TmdbNotFound


class FakeTmdbGateway:
    """External-call-free adapter for unit, importer and local fixture tests."""

    def __init__(
        self,
        *,
        details: dict[int, dict[str, Any]] | None = None,
        credits: dict[int, dict[str, Any]] | None = None,
        translations: dict[int, dict[str, Any]] | None = None,
        providers: dict[int, dict[str, Any]] | None = None,
        finds: dict[str, dict[str, Any]] | None = None,
        errors: dict[tuple[str, int | str], Exception] | None = None,
    ) -> None:
        self._details = details or {}
        self._credits = credits or {}
        self._translations = translations or {}
        self._providers = providers or {}
        self._finds = finds or {}
        self._errors = errors or {}
        self.calls: Counter[tuple[str, int | str]] = Counter()

    def _read(self, endpoint: str, key: int | str, values: dict[Any, dict[str, Any]]) -> dict[str, Any]:
        self.calls[(endpoint, key)] += 1
        error = self._errors.get((endpoint, key))
        if error:
            raise error
        if key not in values:
            raise TmdbNotFound()
        return deepcopy(values[key])

    def details(self, tmdb_id: int) -> dict[str, Any]:
        return self._read("details", tmdb_id, self._details)

    def credits(self, tmdb_id: int) -> dict[str, Any]:
        return self._read("credits", tmdb_id, self._credits)

    def translations(self, tmdb_id: int) -> dict[str, Any]:
        return self._read("translations", tmdb_id, self._translations)

    def watch_providers(self, tmdb_id: int) -> dict[str, Any]:
        return self._read("providers", tmdb_id, self._providers)

    def find_by_imdb(self, imdb_id: str) -> dict[str, Any]:
        return self._read("find", imdb_id, self._finds)

