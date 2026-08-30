from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feelm_catalog_pipeline.identity import IdentityMap
from feelm_catalog_pipeline.movielens import MovieLensMovie


class IdentityMapTest(unittest.TestCase):
    def test_generated_uuid_is_persisted_and_reused(self) -> None:
        movie = MovieLensMovie(1, "Example", 2020, ("Drama",), "tt0000001", 10)
        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        expected = uuid.UUID("11111111-1111-4111-8111-111111111111")
        identity_map = IdentityMap()
        entry = identity_map.resolve(movie, now, lambda: expected)
        self.assertEqual(str(expected), entry.movie_id)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "identity-map.json"
            identity_map.write(path, now)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, payload["schemaVersion"])
            reloaded = IdentityMap.load(path)
            reused = reloaded.resolve(movie, now, lambda: uuid.uuid4())

        self.assertEqual(str(expected), reused.movie_id)
        self.assertNotEqual(str(uuid.uuid5(uuid.NAMESPACE_URL, "MOVIELENS:1")), reused.movie_id)


if __name__ == "__main__":
    unittest.main()
