import unittest

from fm_zero_n_features import Movie, fit_normalizer, fit_vocabulary, schema_document
from fm_zero_n_verify import KeyDigest, canonical_schema_digest


class VerificationTest(unittest.TestCase):
    def test_schema_digest_detects_mutation(self):
        movies = {1: Movie(1, 2000, ("Drama",))}
        rows = [{"target_movie_id": 1, "total_history_count": 0, "supported_history_count": 0, "history": []}]
        schema = schema_document(fit_vocabulary(rows, movies, 4.0), fit_normalizer(rows, movies, 4.0))
        self.assertEqual(schema["profile_digest"], canonical_schema_digest(schema))
        schema["ordered_names"].append("bad:mutation")
        self.assertNotEqual(schema["profile_digest"], canonical_schema_digest(schema))

    def test_key_multiset_detects_duplicate_substitution(self):
        expected = KeyDigest()
        changed = KeyDigest()
        for key in (("a", 1), ("b", 2)):
            expected.update(key)
        for key in (("a", 1), ("a", 1)):
            changed.update(key)
        self.assertNotEqual(expected.document(), changed.document())


if __name__ == "__main__":
    unittest.main()
