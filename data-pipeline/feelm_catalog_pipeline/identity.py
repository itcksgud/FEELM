from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .errors import IdentityMapConflict
from .movielens import MovieLensMovie


@dataclass
class IdentityEntry:
    movie_id: str
    created_at: str
    external_ids: dict[str, set[str]] = field(default_factory=dict)

    def add(self, source: str, external_id: str | int | None) -> None:
        if external_id is None:
            return
        text = str(external_id).strip()
        if text:
            self.external_ids.setdefault(source, set()).add(text)


class IdentityMap:
    def __init__(self, entries: list[IdentityEntry] | None = None) -> None:
        self._entries: dict[str, IdentityEntry] = {}
        self._index: dict[tuple[str, str], str] = {}
        for entry in entries or []:
            self._add_entry(entry)

    @classmethod
    def load(cls, path: Path | None) -> "IdentityMap":
        if path is None or not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != 1 or not isinstance(payload.get("mappings"), list):
            raise ValueError("identity map must use schemaVersion 1")
        entries = []
        for raw in payload["mappings"]:
            entry = IdentityEntry(movie_id=str(raw["movieId"]), created_at=str(raw["createdAt"]))
            uuid.UUID(entry.movie_id)
            for external in raw.get("externalIds", []):
                entry.add(str(external["source"]), str(external["externalId"]))
            entries.append(entry)
        return cls(entries)

    def _add_entry(self, entry: IdentityEntry) -> None:
        if entry.movie_id in self._entries:
            raise IdentityMapConflict(f"duplicate movieId in identity map: {entry.movie_id}")
        self._entries[entry.movie_id] = entry
        for source, values in entry.external_ids.items():
            for value in values:
                key = (source, value)
                owner = self._index.get(key)
                if owner and owner != entry.movie_id:
                    raise IdentityMapConflict(f"external ID {source}:{value} has multiple movieIds")
                self._index[key] = entry.movie_id

    def resolve(
        self,
        movie: MovieLensMovie,
        now: datetime,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> IdentityEntry:
        candidate_ids = {
            self._index[key]
            for key in (
                ("MOVIELENS", str(movie.movie_lens_id)),
                ("IMDB", movie.imdb_id or ""),
                ("TMDB", str(movie.tmdb_id) if movie.tmdb_id is not None else ""),
            )
            if key[1] and key in self._index
        }
        if len(candidate_ids) > 1:
            raise IdentityMapConflict(
                f"MovieLens {movie.movie_lens_id} resolves to conflicting movie identities"
            )
        if candidate_ids:
            entry = self._entries[next(iter(candidate_ids))]
        else:
            entry = IdentityEntry(movie_id=str(uuid_factory()), created_at=now.isoformat())
            self._entries[entry.movie_id] = entry
        self.attach(entry, "MOVIELENS", movie.movie_lens_id)
        self.attach(entry, "IMDB", movie.imdb_id)
        self.attach(entry, "TMDB", movie.tmdb_id)
        return entry

    def attach(self, entry: IdentityEntry, source: str, external_id: str | int | None) -> None:
        if external_id is None or not str(external_id).strip():
            return
        value = str(external_id).strip()
        key = (source, value)
        owner = self._index.get(key)
        if owner and owner != entry.movie_id:
            raise IdentityMapConflict(f"external ID {source}:{value} already belongs to another movie")
        entry.add(source, value)
        self._index[key] = entry.movie_id

    def write(self, path: Path, generated_at: datetime) -> None:
        mappings = []
        for entry in sorted(self._entries.values(), key=lambda item: item.movie_id):
            external_ids = [
                {"source": source, "externalId": value}
                for source in sorted(entry.external_ids)
                for value in sorted(entry.external_ids[source])
            ]
            mappings.append(
                {"movieId": entry.movie_id, "createdAt": entry.created_at, "externalIds": external_ids}
            )
        payload = {
            "schemaVersion": 1,
            "generatedAt": generated_at.isoformat(),
            "mappings": mappings,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

