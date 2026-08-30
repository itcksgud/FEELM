from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


YEAR_SUFFIX = re.compile(r"\s*\((\d{4})\)\s*$")


@dataclass(frozen=True)
class MovieLensMovie:
    movie_lens_id: int
    title: str
    release_year: int | None
    genres: tuple[str, ...]
    imdb_id: str | None
    tmdb_id: int | None


def normalize_imdb_id(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.lower().startswith("tt"):
        digits = text[2:]
    else:
        digits = text
    if not digits.isdigit():
        return None
    return "tt" + digits.zfill(7)


def archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _member(archive: zipfile.ZipFile, basename: str) -> str:
    matches = [name for name in archive.namelist() if name == basename or name.endswith("/" + basename)]
    if len(matches) != 1:
        raise ValueError(f"MovieLens archive must contain exactly one {basename}")
    return matches[0]


def _rows(archive: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    with archive.open(member) as binary:
        text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
        return list(csv.DictReader(text))


def read_movielens_archive(path: Path) -> list[MovieLensMovie]:
    with zipfile.ZipFile(path) as archive:
        movie_rows = _rows(archive, _member(archive, "movies.csv"))
        link_rows = _rows(archive, _member(archive, "links.csv"))

    movies: dict[int, tuple[str, int | None, tuple[str, ...]]] = {}
    for row in movie_rows:
        movie_id = int(row["movieId"])
        raw_title = row["title"].strip()
        match = YEAR_SUFFIX.search(raw_title)
        year = int(match.group(1)) if match else None
        title = YEAR_SUFFIX.sub("", raw_title).strip()
        genres = tuple(
            genre.strip()
            for genre in row.get("genres", "").split("|")
            if genre.strip() and genre.strip() != "(no genres listed)"
        )
        movies[movie_id] = (title, year, genres)

    links: dict[int, tuple[str | None, int | None]] = {}
    for row in link_rows:
        tmdb_text = row.get("tmdbId", "").strip()
        links[int(row["movieId"])] = (
            normalize_imdb_id(row.get("imdbId")),
            int(tmdb_text) if tmdb_text else None,
        )

    result: list[MovieLensMovie] = []
    for movie_id in sorted(movies):
        title, year, genres = movies[movie_id]
        imdb_id, tmdb_id = links.get(movie_id, (None, None))
        result.append(
            MovieLensMovie(
                movie_lens_id=movie_id,
                title=title,
                release_year=year,
                genres=genres,
                imdb_id=imdb_id,
                tmdb_id=tmdb_id,
            )
        )
    return result

