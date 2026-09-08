import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feelm_slot_preservation import RankedCandidate as C, build_reserved_top100


def candidates(first, second):
    return tuple(C(i + 1, first if i < 400 else second) for i in range(500))


class SlotPreservationTests(unittest.TestCase):
    def test_taste_reservation_and_order(self):
        raw = candidates('DISCOVERY', 'TASTE')
        result = build_reserved_top100(raw)
        self.assertEqual(len(result), 100)
        self.assertEqual([r.movie_id for r in result], list(range(1, 98)) + [401, 402, 403])
        self.assertEqual(sum(r.role == 'TASTE' for r in result), 3)
        self.assertTrue(all(r in raw for r in result))

    def test_latest_exclusion_can_remove_every_reserved_taste(self):
        raw = candidates('DISCOVERY', 'TASTE')
        excluded = {401, 402, 403}
        delivered = tuple(r for r in build_reserved_top100(raw) if r.movie_id not in excluded)
        self.assertFalse(any(r.role == 'TASTE' for r in delivered))
        self.assertEqual(sum(r.role == 'TASTE' and r.movie_id not in excluded for r in raw), 97)

    def test_latest_exclusion_can_remove_only_reserved_discovery(self):
        raw = candidates('TASTE', 'DISCOVERY')
        result = build_reserved_top100(raw)
        self.assertEqual([r.movie_id for r in result], list(range(1, 100)) + [401])
        delivered = tuple(r for r in result if r.movie_id != 401)
        self.assertFalse(any(r.role == 'DISCOVERY' for r in delivered))
        self.assertEqual(sum(r.role == 'DISCOVERY' and r.movie_id != 401 for r in raw), 99)
        self.assertGreaterEqual(sum(r.role == 'TASTE' for r in delivered), 3)

    def test_short_and_empty_supply_are_preserved(self):
        raw = (C(1, None), C(2, 'TASTE'), C(3, 'TASTE'), C(4, 'TASTE'))
        self.assertEqual(build_reserved_top100(raw), raw[1:])
        self.assertEqual(build_reserved_top100((C(1, None),)), ())
        self.assertEqual(build_reserved_top100(()), ())

    def test_no_change_when_reservations_already_in_top100(self):
        raw = tuple(C(i + 1, 'DISCOVERY' if i % 3 == 2 else 'TASTE') for i in range(500))
        self.assertEqual(build_reserved_top100(raw), raw[:100])

    def test_invalid_inputs(self):
        for raw in ((C(1, 'TASTE'), C(1, 'DISCOVERY')), (C(True, 'TASTE'),),
                    (C(1, 'UNKNOWN'),), (C(0, None),), ({'movie_id': 1, 'role': 'TASTE'},),
                    tuple(C(i + 1, 'TASTE') for i in range(501))):
            with self.subTest(raw_length=len(raw)):
                with self.assertRaises(ValueError):
                    build_reserved_top100(raw)


if __name__ == '__main__':
    unittest.main()
