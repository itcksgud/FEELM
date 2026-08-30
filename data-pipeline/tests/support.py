from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path


def _csv_text(fieldnames: list[str], rows: list[dict[str, object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def write_movielens_zip(path: Path, movies: list[dict[str, object]], links: list[dict[str, object]]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ml-test/movies.csv", _csv_text(["movieId", "title", "genres"], movies)
        )
        archive.writestr(
            "ml-test/links.csv", _csv_text(["movieId", "imdbId", "tmdbId"], links)
        )


def movie_details(
    tmdb_id: int,
    imdb_id: str,
    title: str,
    year: int,
    *,
    poster: str | None = "/poster.jpg",
) -> dict[str, object]:
    return {
        "id": tmdb_id,
        "imdb_id": imdb_id,
        "title": title,
        "original_title": title,
        "original_language": "en",
        "overview": f"Overview for {title}",
        "release_date": f"{year}-01-01",
        "runtime": 120,
        "poster_path": poster,
        "backdrop_path": "/backdrop.jpg",
        "vote_average": 7.5,
        "vote_count": 100,
        "genres": [{"id": 28, "name": "Action"}],
        "production_countries": [{"iso_3166_1": "US", "name": "United States"}],
    }


def credits() -> dict[str, object]:
    return {
        "crew": [
            {
                "id": 11,
                "job": "Director",
                "name": "Director One",
                "profile_path": "/director.jpg",
            }
        ],
        "cast": [
            {"id": 21, "name": "Actor One", "character": "Hero", "order": 0},
            {"id": 22, "name": "Actor Two", "character": "Friend", "order": 1},
        ],
    }


def translations(title: str) -> dict[str, object]:
    return {
        "translations": [
            {
                "iso_639_1": "ko",
                "iso_3166_1": "KR",
                "data": {"title": f"{title} 한국어", "overview": f"{title} 한국어 줄거리"},
            },
            {
                "iso_639_1": "en",
                "iso_3166_1": "US",
                "data": {"title": title, "overview": f"Overview for {title}"},
            },
        ]
    }


def providers() -> dict[str, object]:
    provider = {
        "provider_id": 8,
        "provider_name": "Netflix",
        "logo_path": "/netflix.jpg",
        "display_priority": 10,
    }
    return {
        "results": {
            "KR": {
                "link": "https://www.themoviedb.org/movie/1/watch?locale=KR",
                "flatrate": [provider],
                "rent": [{**provider, "provider_id": 3, "provider_name": "Google Play"}],
                "buy": [{**provider, "provider_id": 3, "provider_name": "Google Play"}],
                "free": [{**provider, "provider_id": 99, "provider_name": "Free TV"}],
                "ads": [{**provider, "provider_id": 100, "provider_name": "Ad TV"}],
            }
        }
    }

