"""Adversarial identity and section-boundary tests for the collection gate."""
import unittest
from unittest.mock import patch
from contextlib import nullcontext
import hashlib
import io
import json
import collect_wikipedia_plots as collector
from collect_wikipedia_plots import resolve_identity, sections, plain_plot, page_record


def binding(qid='Q1', lookup='10', tmdb='10', imdb='tt0000001'):
    return {k: {'value': v} for k, v in {'item': 'http://www.wikidata.org/entity/' + qid,
            'lookup': lookup, 'tmdb': tmdb, 'imdb': imdb, 'en': 'https://en.wikipedia.org/wiki/Test'}.items() if v}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.row = {'movie_id': 1, 'tmdb_id': 10, 'imdb_id': 'tt0000001'}

    def test_exact_dual_id_and_duplicate_query_rows(self):
        r = resolve_identity(self.row, [binding(), binding(lookup='tt0000001')])
        self.assertEqual((r['mapping_status'], r['qid']), ('MATCH_BOTH_IDS', 'Q1'))

    def test_disagreement_and_multiple_items_quarantined(self):
        self.assertEqual(resolve_identity(self.row, [binding(), binding(qid='Q2', lookup='tt0000001')])['mapping_status'], 'REVIEW_MULTIPLE_QIDS')
        self.assertEqual(resolve_identity(self.row, [binding(imdb='tt9999999')])['mapping_status'], 'REVIEW_CONFLICTING_IDS')

    def test_missing_counterpart_is_not_conflict(self):
        self.assertEqual(resolve_identity(self.row, [binding(imdb='')])['mapping_status'], 'MATCH_SINGLE_ID')
        self.assertEqual(resolve_identity(self.row, [])['mapping_status'], 'NO_ID_MATCH')

    def test_plot_stops_at_next_peer_but_retains_subsections(self):
        w = 'lead\n==Plot==\nfirst\n===Ending===\nlast\n==Reception==\nPOSITIVE REVIEWS\n==Cast==\nactor'
        p = sections(w)
        self.assertEqual(len(p), 1)
        self.assertIn('last', p[0]['wikitext'])
        self.assertNotIn('REVIEWS', p[0]['wikitext'])

    def test_korean_and_comments_and_no_fallback(self):
        w = '<!--\n==Plot==\nfake\n-->\n== 줄거리 ==\n실제 내용\n== 평가 ==\n별점'
        self.assertEqual(sections(w)[0]['wikitext'], '실제 내용')
        self.assertEqual(sections('lead only\n==Reception==\nreview'), [])

    def test_nested_recognized_heading_not_duplicated(self):
        self.assertEqual(len(sections('==Plot==\na\n===Synopsis===\nb\n==Cast==\nc')), 1)

    def test_nested_markup_citations_and_file_caption(self):
        w = "[[Hero|Alice]] meets ''Bob''.<ref name='a'>5/5 [[review]]</ref>{{note|{{nested}}}} [[File:A.jpg|thumb|[[Actor]]]]\n{| table |}\nThey survive."
        p, templates, flags = plain_plot(w)
        self.assertIn('Alice meets Bob.', p)
        self.assertIn('They survive.', p)
        self.assertNotIn('review', p)
        self.assertNotIn('Actor', p)
        self.assertEqual(templates, 2)
        self.assertEqual(flags, [])

    def test_malformed_markup_flagged(self):
        self.assertIn('UNBALANCED_MARKUP', plain_plot('story {{bad')[2])

    def test_literal_tags_do_not_create_or_end_sections(self):
        self.assertEqual(sections('<nowiki>\n==Plot==\nFAKE\n</nowiki>\n==Reception==\nreview'), [])
        p = sections('==Plot==\nopening\n<nowiki>\n==Reception==\n</nowiki>\nending\n==Cast==\nactors')
        self.assertIn('ending', p[0]['wikitext'])
        self.assertNotIn('actors', p[0]['wikitext'])

    def test_self_closing_reference_does_not_hide_next_heading(self):
        p = sections('==Plot==\nstory<ref name="a"/>\n==Cast==\nACTORS\n==Reception==\nREVENUE<ref>source</ref>')
        self.assertEqual(p[0]['wikitext'], 'story<ref name="a"/>')
        p = sections('lead<ref name="a" />\n==Plot==\nSTORY\n==Cast==\nactor<ref>source</ref>')
        self.assertEqual(p[0]['wikitext'], 'STORY')

    def test_redirect_to_franchise_rejected(self):
        p = {'title': 'Franchise', 'pageid': 3, 'ns': 0, 'pageprops': {'wikibase_item': 'Q2'}}
        r = page_record(p, {'qid': 'Q1', 'requested_title': 'Film'}, {'fetched_at': 'now', 'body_sha256': 'hash'}, 'cache', 'en', {'url': 'license', 'text': 'CC'})
        self.assertEqual(r['page_status'], 'REVIEW_PAGE_QID_MISMATCH')
        self.assertFalse(r['plot_available'])

    def test_read_query_reuses_prior_post_and_keeps_actual_request(self):
        url = 'https://en.wikipedia.org/w/api.php'
        params = {'action': 'query', 'titles': 'Test', 'format': 'json'}
        spec = {'url': url, 'params': params, 'method': 'POST'}
        key = hashlib.sha256(collector.canonical(spec)).hexdigest()
        cached = collector.RAW_CACHE_FALLBACKS[0] / key[:2] / (key + '.json.gz')
        body = {'query': {'pages': []}}
        record = {'request': spec, 'body': body, 'body_sha256': hashlib.sha256(collector.canonical(body)).hexdigest()}
        client = collector.Client(collector.OUT)
        with patch.object(collector.Path, 'exists', new=lambda self: self == cached), \
             patch.object(collector.gzip, 'open', new=lambda *a, **kw: nullcontext(io.StringIO(json.dumps(record)))), \
             patch.object(client.session, 'get', side_effect=AssertionError('Unexpected network')):
            actual, path = client.get(url, params, post=False)
        self.assertEqual(actual['request']['method'], 'POST')
        self.assertEqual(path, str(cached.relative_to(collector.ROOT)))
        self.assertEqual(client.network, 0)


if __name__ == '__main__':
    unittest.main()
