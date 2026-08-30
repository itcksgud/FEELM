from __future__ import annotations

from typing import Any, Protocol

from .transport import JsonTransport


class TmdbGateway(Protocol):
    def details(self, tmdb_id: int) -> dict[str, Any]: ...

    def credits(self, tmdb_id: int) -> dict[str, Any]: ...

    def translations(self, tmdb_id: int) -> dict[str, Any]: ...

    def watch_providers(self, tmdb_id: int) -> dict[str, Any]: ...

    def find_by_imdb(self, imdb_id: str) -> dict[str, Any]: ...


class NetworkTmdbGateway:
    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport

    def details(self, tmdb_id: int) -> dict[str, Any]:
        return self._transport.get(f"movie/{tmdb_id}", {"language": "en-US"})

    def credits(self, tmdb_id: int) -> dict[str, Any]:
        return self._transport.get(f"movie/{tmdb_id}/credits", {"language": "en-US"})

    def translations(self, tmdb_id: int) -> dict[str, Any]:
        return self._transport.get(f"movie/{tmdb_id}/translations")

    def watch_providers(self, tmdb_id: int) -> dict[str, Any]:
        return self._transport.get(f"movie/{tmdb_id}/watch/providers")

    def find_by_imdb(self, imdb_id: str) -> dict[str, Any]:
        return self._transport.get(
            f"find/{imdb_id}", {"external_source": "imdb_id", "language": "en-US"}
        )

