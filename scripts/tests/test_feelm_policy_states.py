"""State/role counterexamples with synthetic movies and supplied scores only."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('states', Path(__file__).resolve().parents[1] / 'feelm_policy_states.py')
m = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)
PRIOR = [(i + .5) / 10 for i in range(10)]


def movie(mid, taste, **features):
    return {'id': mid, 'taste': taste, **features}


def fixture():
    items = [movie(1, 'F1', directors=[900]), movie(2, 'F1', keywords=[800]),
             movie(3, 'F2', directors=[900]), movie(4, 'F3', genres=[28]),
             *[movie(i, 'F1') for i in (11, 12, 13, 14, 15, 16)],
             movie(21, 'F2', directors=[900]), movie(22, 'F2', directors=[900]),
             movie(23, 'F3', directors=[900]), movie(24, 'F2', keywords=[800]),
             movie(99, None, directors=[900])]
    scores = {item['id']: 100 - item['id'] for item in items}
    return items, scores


def suggest(items, states, scores, **kwargs):
    # Explicit forced mode is a synthetic probe, not a K=1 routing decision.
    mode = 'FORCED_RANK_DIAGNOSTIC' if m.profile(items, states, PRIOR)['input_movies'] else 'NO_INPUT_POPULAR_EXAMPLE'
    return m.recommend(items, states, PRIOR, scores, scores, ranking_mode=mode, **kwargs)


class PolicyStatesTests(unittest.TestCase):
    def test_skip_and_dislike_differ(self):
        items, scores = fixture()
        empty = suggest(items, [], scores)
        self.assertEqual(empty['status'], 'NO_INPUT_POPULAR'); self.assertEqual(empty['input_movies'], 0)
        self.assertIsNone(empty['final_K'])
        negative = suggest(items, [{'id': 1, 'onboarding': 'DISLIKE'}], scores)
        self.assertEqual(negative['status'], 'NO_POSITIVE_TASTE'); self.assertEqual(negative['input_movies'], 1)
        with self.assertRaises(ValueError): m.profile(items, [{'id': 1, 'onboarding': 'SKIP'}], PRIOR)

    def test_onboarding_only_has_taste_without_popcorn(self):
        items, scores = fixture(); states = [{'id': 1, 'onboarding': 'LIKE'}]
        p = m.profile(items, states, PRIOR)
        self.assertEqual(p['popcorn'], {}); self.assertEqual(p['experienced_tastes'], set())
        self.assertEqual(p['positive_tastes'], {'F1'})
        result = suggest(items, states, scores)
        self.assertEqual(result['status'], 'T2_D1')
        self.assertEqual([(x['id'], x['type']) for x in result['items']], [(2, 'TASTE'), (11, 'TASTE'), (3, 'DISCOVERY')])
        self.assertEqual(result['items'][2]['connection_facts'], [{'anchor_movie_id': 1, 'field': 'directors', 'id': 900}])
        self.assertNotIn(1, [x['id'] for x in result['items']])

    def test_rating_priority_edit_delete_and_experience(self):
        items, _ = fixture(); state = {'id': 1, 'onboarding': 'DISLIKE'}
        rated = m.change_rating(state, 5.)
        p = m.profile(items, [rated], PRIOR)
        self.assertAlmostEqual(p['effective'][1], .75); self.assertEqual(p['input_movies'], 1)
        self.assertEqual(p['popcorn'], {'F1': 1})
        edited = m.change_rating(rated, 4.5)
        self.assertEqual(m.profile(items, [edited], PRIOR)['popcorn'], {'F1': 1})
        deleted = m.change_rating(edited, None)
        p = m.profile(items, [deleted], PRIOR)
        self.assertEqual(deleted['watch'], 'WATCHED_UNRATED'); self.assertEqual(p['effective'][1], -1)
        self.assertEqual(p['popcorn'], {}); self.assertEqual(p['experienced_tastes'], {'F1'}); self.assertIn(1, p['excluded'])
        deleted['onboarding'] = None
        p = m.profile(items, [deleted], PRIOR)
        self.assertEqual(p['input_movies'], 0); self.assertEqual(p['experienced_tastes'], {'F1'})
        with self.assertRaises(ValueError): m.change_rating(deleted, None)

    def test_rating_grid_is_exact(self):
        for value in (.5, 1., 1.5, 2., 2.5, 3., 3.5, 4., 4.5, 5.): self.assertEqual(m.rating_value(value), value)
        for value in (0, .6, 5.5, float('nan'), float('inf'), True, '5'):
            with self.assertRaises(ValueError): m.rating_value(value)

    def test_count_is_not_preference_and_all_experienced_has_no_d(self):
        items, scores = fixture()
        states = [{'id': mid, 'watch': 'RATED', 'rating': .5} for mid in (1, 2, 11)] + [{'id': 3, 'watch': 'RATED', 'rating': 5.}]
        p = m.profile(items, states, PRIOR)
        self.assertEqual(p['popcorn'], {'F1': 3, 'F2': 1}); self.assertEqual(p['positive_tastes'], {'F2'})
        _, taste, discovery, _ = m.pools(items, states, PRIOR, scores)
        self.assertNotIn(12, taste); self.assertNotIn(12, discovery); self.assertIn(21, taste)
        states = [{'id': 1, 'onboarding': 'LIKE'}, {'id': 3, 'watch': 'WATCHED_UNRATED'}, {'id': 4, 'watch': 'WATCHED_UNRATED'}]
        self.assertEqual(suggest(items, states, scores)['status'], 'NO_DISCOVERY_T3')

    def test_clicked_unrated_expired_are_distinct(self):
        items, scores = fixture()
        p = m.profile(items, [{'id': 1, 'watch': 'LINK_CLICKED'}, {'id': 3, 'watch': 'WATCHED_UNRATED'}], PRIOR)
        self.assertEqual(p['excluded'], {1, 3}); self.assertEqual(p['experienced_tastes'], {'F2'}); self.assertEqual(p['popcorn'], {})
        with self.assertRaises(m.UnspecifiedState): m.profile(items, [{'id': 4, 'watch': 'EXPIRED'}], PRIOR)
        with self.assertRaises(ValueError): m.profile(items, [{'id': 1, 'watch': 'WATCHED_RATED', 'rating': 5}], PRIOR)

    def test_zero_mean_and_recompute_after_delete(self):
        items, _ = fixture()
        p = m.profile(items, [{'id': 1, 'onboarding': 'LIKE'}, {'id': 2, 'onboarding': 'DISLIKE'}], PRIOR)
        self.assertEqual(p['taste_means']['F1'], 0); self.assertEqual(p['positive_tastes'], set())
        self.assertEqual(m.relative_weights([.5, 5.], PRIOR), [-11 / 14, 11 / 14])
        self.assertAlmostEqual(m.relative_weights([5.], PRIOR)[0], .75)

    def test_connection_requires_positive_anchor_and_same_id_namespace(self):
        items = [movie(1, 'F1', genres=[28]), movie(2, 'F1', directors=[900]),
                 movie(3, 'F2', keywords=[28]), movie(4, 'F2', directors=[900]), movie(5, 'F2', genres=[28])]
        states = [{'id': 1, 'onboarding': 'LIKE'}, {'id': 2, 'watch': 'RATED', 'rating': .5}]
        p, _, d, facts = m.pools(items, states, PRIOR, {x['id']: 0 for x in items})
        self.assertEqual(p['positive_tastes'], {'F1'}); self.assertEqual(p['anchors'], {1})
        self.assertEqual(d, [5]); self.assertEqual(facts[5][0]['field'], 'genres')
        states += [{'id': 5, 'onboarding': 'DISLIKE'}]
        # An independent negative taste never supplies an anchor merely because it has an input movie.
        self.assertNotIn(5, m.profile(items, states, PRIOR)['anchors'])

    def test_shortage_and_cutoff_are_visible(self):
        items, scores = fixture(); states = [{'id': 1, 'onboarding': 'LIKE'}]
        self.assertEqual(suggest(items, states, scores, available=[11, 21, 22])['status'], 'INSUFFICIENT_TASTE')
        self.assertEqual(suggest(items, states, scores, available=[11, 12])['status'], 'INSUFFICIENT_TASTE_FALLBACK')
        self.assertEqual(suggest(items, states, scores, available=[11, 12, 13])['status'], 'NO_DISCOVERY_T3')
        full = m.pools(items, states, PRIOR, scores)[2]
        cut = m.pools(items, states, PRIOR, scores, available=[11, 12, 13])[2]
        self.assertTrue(full); self.assertFalse(cut)
        self.assertNotIn(99, m.pools(items, states, PRIOR, scores)[1] + full)
        unknown = m.profile(items, [{'id': 99, 'onboarding': 'LIKE'}], PRIOR)
        self.assertEqual(unknown['input_movies'], 1); self.assertEqual(unknown['unassigned_input_movies'], 1)

    def test_same_type_buffer_replacement_without_recompute(self):
        buffer = [{'id': 13, 'type': 'TASTE'}, {'id': 22, 'type': 'DISCOVERY'}]
        self.assertEqual(m.replace_from_buffer(buffer, 'DISCOVERY', {21})['id'], 22)
        self.assertEqual(m.replace_from_buffer(buffer, 'TASTE', {11})['id'], 13)
        self.assertIsNone(m.replace_from_buffer(buffer, 'DISCOVERY', {21, 22}))
        self.assertEqual(buffer, [{'id': 13, 'type': 'TASTE'}, {'id': 22, 'type': 'DISCOVERY'}])

    def test_same_meaning_under_code_renaming_ties_and_more(self):
        items, _ = fixture(); scores = {x['id']: 1 for x in items}; states = [{'id': 1, 'onboarding': 'LIKE'}]
        first = suggest(items, states, scores)
        renamed = [{**x, 'taste': 'OTHER_' + x['taste'] if x['taste'] else None} for x in reversed(items)]
        self.assertEqual(first, suggest(renamed, list(reversed(states)), dict(reversed(list(scores.items())))))
        shown = {x['id'] for x in first['items']}
        more = suggest(items, states, scores, blocked=shown)
        self.assertEqual(more['status'], 'T2_D1'); self.assertFalse(shown & {x['id'] for x in more['items']})
        with self.assertRaises(ValueError): m.profile(items, states + states, PRIOR)

    def test_no_automatic_K_or_binary_to_ALS_bridge(self):
        items, scores = fixture(); states = [{'id': 1, 'onboarding': 'LIKE'}]
        with self.assertRaises(m.UnspecifiedState):
            m.recommend(items, states, PRIOR, scores, scores, ranking_mode='AUTOMATIC_K1')
        result = suggest(items, states, scores)
        self.assertIsNone(result['final_K']); self.assertEqual(result['ranking_mode'], 'FORCED_RANK_DIAGNOSTIC')
        # The simulator API accepts supplied scores; it has no fitting/fold-in/model dependency.
        self.assertNotIn('KMeans', vars(m)); self.assertNotIn('fold_in', vars(m))

    def test_popular_excludes_unassigned_and_iterable_exclusions_are_stable(self):
        items, scores = fixture(); scores[99] = 10000
        result = suggest(items, [], scores, blocked=iter([1, 2]), available=iter([1, 2, 3, 4, 11, 99]))
        self.assertEqual([x['id'] for x in result['items']], [3, 4, 11])
        self.assertEqual(result['unassigned_in_available_pool'], 1)
        only_unknown = suggest(items, [], scores, available=[99])
        self.assertEqual(only_unknown['status'], 'INSUFFICIENT_POPULAR'); self.assertEqual(only_unknown['items'], [])
        buffer = [{'id': 1, 'type': 'TASTE'}, {'id': 2, 'type': 'TASTE'}]
        self.assertIsNone(m.replace_from_buffer(buffer, 'TASTE', iter([1, 2])))


if __name__ == '__main__':
    unittest.main()
