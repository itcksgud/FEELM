"""Describe frozen clusters with separate metadata channels and synopsis evidence."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from rec_ev_033_tastes import Budget, ctfidf, pin, read_json, require, sha, write_json

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/recommendation/experiments/rec-ev-037'
FILES = {'design': PLAN / 'README.md', 'config': PLAN / 'config.json', 'runner': Path(__file__).resolve(),
         'tests': ROOT / 'scripts/tests/test_rec_ev_037_descriptions.py', 'helper': ROOT / 'scripts/rec_ev_033_tastes.py'}
OUTPUTS = ('channel-counts.npz', 'top-terms.json', 'diagnostics.json', 'synopsis-only-cards.json',
           'sample-keyword-evidence.json', 'budget.json')


def fingerprint():
    return {key: sha(path) for key, path in FILES.items()}


def term_statistics(counts, sizes):
    counts, sizes = np.asarray(counts, dtype=float), np.asarray(sizes, dtype=float)
    require(counts.ndim == 2 and sizes.shape == (len(counts),) and len(sizes) >= 2
            and np.isfinite(counts).all() and np.isfinite(sizes).all()
            and (sizes > 0).all() and (counts >= 0).all() and (counts <= sizes[:, None]).all(), 'invalid document counts')
    within = counts / sizes[:, None]
    outside = (counts.sum(axis=0)[None, :] - counts) / (sizes.sum() - sizes)[:, None]
    return within, outside, within - outside


def describe_channel(counts, vocabulary, texts, sizes, codes, limit):
    scores = ctfidf(counts)
    within, outside, delta = term_statistics(counts, sizes)
    top = {}
    for k, code in enumerate(codes):
        order = sorted(range(len(vocabulary)), key=lambda j: (-scores[k, j], str(vocabulary[j])))
        js = [j for j in order if scores[k, j] > 0][:limit]
        top[str(code)] = [{'term_key': str(vocabulary[j]), 'text': str(texts[j]), 'score': float(scores[k, j]),
            'within_count': int(counts[k, j]), 'within_denominator': int(sizes[k]), 'within_fraction': float(within[k, j]),
            'outside_count': int(counts[:, j].sum() - counts[k, j]), 'outside_denominator': int(sum(sizes) - sizes[k]),
            'outside_fraction': float(outside[k, j]), 'fraction_difference': float(delta[k, j])} for j in js]
    norm = np.linalg.norm(scores, axis=1, keepdims=True)
    unit = np.divide(scores, norm, out=np.zeros_like(scores), where=norm > 0)
    overlap = [[len({r['term_key'] for r in top[str(a)]} & {r['term_key'] for r in top[str(b)]}) for b in codes] for a in codes]
    return scores, top, {'top_term_intersection_counts': overlap, 'ctfidf_cosine': (unit @ unit.T).tolist()}


def validate_excerpts(cards, records, cfg):
    originals = {row['card']: row['overview'] for row in cards}
    require(len(originals) == len(cards), 'duplicate original card')
    require(isinstance(records, list) and len(records) == len(originals), 'excerpt record count mismatch')
    require({row['card'] for row in records} == set(originals), 'excerpt ID mismatch')
    for row in records:
        require(set(row) == {'card', 'excerpts', 'theme', 'note'}, 'excerpt record schema mismatch')
        require(isinstance(row['theme'], str) and bool(row['theme'].strip()) and isinstance(row['note'], str), 'invalid semantic note')
        excerpts = row['excerpts']
        require(isinstance(excerpts, list) and cfg['excerpt_min_count'] <= len(excerpts) <= cfg['excerpt_max_count'], 'excerpt count invalid')
        require(all(isinstance(q, str) and 0 < len(q) <= cfg['excerpt_max_characters'] and q in originals[row['card']] for q in excerpts), 'excerpt not exact bounded substring')
        require(len(excerpts) == len(set(excerpts)), 'duplicate excerpt')
    return sorted(records, key=lambda row: row['card'])


class Run:
    def __init__(self):
        self.identity = fingerprint()
        review = read_json(PLAN / 'review.json')
        require(review['status'] == 'PASS' and review['fingerprint'] == self.identity, 'review missing or stale')
        self.cfg = read_json(FILES['config'])
        require(self.cfg['experiment'] == 'REC037_SEPARATE_TASTE_DESCRIPTIONS', 'experiment mismatch')
        self.root = ROOT / self.cfg['output_root']
        require(self.root.resolve() == ROOT / 'outputs/recommendation-evidence/rec-ev-037', 'wrong output root')
        self.paths = {k: ROOT / v['path'] for k, v in self.cfg['inputs'].items()}

    def sources(self):
        require(fingerprint() == self.identity, 'code changed')
        for key, spec in self.cfg['inputs'].items():
            require(pin(self.paths[key]) == {k: spec[k] for k in ('bytes', 'sha256')}, 'source changed: ' + key)
        for key, field in [('completion033', 'outputs'), ('completion036', 'artifacts')]:
            seal = read_json(self.paths[key])
            for name, spec in seal[field].items():
                require(pin(self.paths[key].parent / name) == spec, 'ancestor artifact changed: ' + name)
        previous = read_json(self.paths['review036'])
        require(previous['status'] == 'PASS_AUDIT_COMPLETE' and previous['completion_seal'] == pin(self.paths['completion036']), 'previous review lineage mismatch')

    def calculate(self):
        cfg = self.cfg
        terms = pd.read_parquet(self.paths['terms'])
        metadata = pd.read_parquet(self.paths['metadata']).sort_values('movie_id').reset_index(drop=True)
        assignments = pd.read_parquet(self.paths['assignments']).sort_values('movie_id').reset_index(drop=True)
        require(len(metadata) == cfg['expected_movies'] == len(assignments)
                and not metadata.movie_id.duplicated().any() and not assignments.movie_id.duplicated().any()
                and np.array_equal(metadata.movie_id, assignments.movie_id), 'movie IDs mismatch')
        z = np.load(self.paths['class_terms'], allow_pickle=False)
        vocabulary, codes, counts = z['vocabulary'], z['B_KMEANS_codes'], z['B_KMEANS_counts']
        require(np.array_equal(vocabulary, terms.term_key) and codes.tolist() == [f'SEMANTIC_{k:02d}' for k in range(1, 9)], 'term/code order mismatch')
        require(counts.shape == (8, len(vocabulary)) and len(set(vocabulary)) == len(vocabulary), 'bad count shape')
        lookup = {str(v): i for i, v in enumerate(vocabulary)}
        labels = np.array([codes.tolist().index(code) for code in assignments.semantic_code])
        sizes = np.bincount(labels, minlength=8)
        rebuilt = np.zeros_like(counts)
        raw_coverage = {channel: np.zeros(8, dtype=int) for channel in ('genre', 'keyword')}
        retained_coverage = {channel: np.zeros(8, dtype=int) for channel in ('genre', 'keyword')}
        docs = []
        for k, row in zip(labels, metadata.itertuples(index=False), strict=True):
            tokens = set()
            for channel, ids in [('genre', row.genre_ids), ('keyword', row.keyword_ids)]:
                raw_coverage[channel][k] += bool(len(ids))
                kept = {lookup[f'{channel}:{int(i)}'] for i in set(ids) if f'{channel}:{int(i)}' in lookup}
                retained_coverage[channel][k] += bool(kept)
                tokens.update(kept)
            for j in tokens:
                rebuilt[k, j] += 1
            docs.append(tokens)
        require(np.array_equal(rebuilt, counts) and np.array_equal(counts.sum(axis=0), terms.movie_document_frequency)
                and (counts.sum(axis=0) >= cfg['min_df']).all(), 'original document counts mismatch')
        require(np.array_equal(ctfidf(counts), z['B_KMEANS_ctfidf']), 'mixed ctfidf mismatch')
        masks = {'mixed': np.ones(len(vocabulary), dtype=bool), 'genre': np.array([s.startswith('genre:') for s in vocabulary]),
                 'keyword': np.array([s.startswith('keyword:') for s in vocabulary])}
        require(np.all(masks['genre'] ^ masks['keyword']), 'unknown term namespace')
        old = {t['code']: t for t in read_json(self.paths['old_summary'])['tastes'] if t['policy'] == 'B_KMEANS'}
        tops, diagnostics, arrays = {}, {}, {'codes': codes, 'sizes': sizes}
        for channel in cfg['channels']:
            mask = masks[channel]
            values, top, diag = describe_channel(counts[:, mask], vocabulary[mask], terms.display_text.to_numpy()[mask], sizes, codes, cfg['top_terms'])
            if channel == 'mixed':
                for code in codes:
                    require([r['term_key'] for r in top[str(code)]] == [r['term_key'] for r in old[str(code)]['ctfidf_terms']], 'mixed top mismatch')
            else:
                diag['metadata_coverage'] = [{'code': str(code), 'movies': int(sizes[k]), 'raw_present': int(raw_coverage[channel][k]),
                    'retained_present': int(retained_coverage[channel][k])} for k, code in enumerate(codes)]
            tops[channel], diagnostics[channel] = top, diag
            arrays[channel + '_vocabulary'] = vocabulary[mask]
            arrays[channel + '_counts'] = counts[:, mask]
            arrays[channel + '_ctfidf'] = values
        np.savez_compressed(self.root / 'channel-counts.npz', **arrays)
        write_json(self.root / 'top-terms.json', tops)
        write_json(self.root / 'diagnostics.json', {'movies': len(metadata), 'codes': codes.tolist(), 'channels': diagnostics,
            'assignments_changed': 0, 'fit_count': 0, 'new_encoding_count': 0, 'rating_data_access': False})
        cards = read_json(self.paths['cards'])
        require(len(cards) == cfg['expected_cards'] and len({r['card'] for r in cards}) == len(cards)
                and len({r['movie_id'] for r in cards}) == len(cards), 'sample card count mismatch')
        rowmap = {int(mid): i for i, mid in enumerate(metadata.movie_id)}
        evidence = []
        for card in cards:
            i = rowmap[card['movie_id']]
            require(card['code'] == assignments.iloc[i].semantic_code and set(card['roles']).issubset({'nearest', 'hash', 'boundary'}), 'sample key mismatch')
            matching = [t['term_key'] for t in tops['keyword'][card['code']] if lookup[t['term_key']] in docs[i]]
            evidence.append({'card': card['card'], 'movie_id': card['movie_id'], 'title': card['title'], 'code': card['code'],
                'roles': card['roles'], 'runner_up_code': card['runner_up_code'], 'matching_top_keyword_ids': matching,
                'all_keyword_ids': [int(x) for x in metadata.iloc[i].keyword_ids]})
        write_json(self.root / 'synopsis-only-cards.json', [{'card': c['card'], 'overview': c['overview']} for c in cards])
        write_json(self.root / 'sample-keyword-evidence.json', evidence)

    def verify_completion(self):
        seal = read_json(self.root / 'completion-seal.json')
        require(seal['fingerprint'] == self.identity and seal['sources'] == self.cfg['inputs']
                and set(seal['artifacts']) == set(OUTPUTS), 'completion lineage mismatch')
        for name, info in seal['artifacts'].items():
            require(pin(self.root / name) == info, 'output changed: ' + name)

    def run(self):
        self.sources()
        require(not (self.root / 'failure.json').exists(), 'failed run preserved')
        if (self.root / 'completion-seal.json').exists():
            self.verify_completion(); print('VERIFIED_EXISTING_COMPLETION_NO_RECALCULATION'); return
        require(not self.root.exists() or not any(self.root.iterdir()), 'partial run preserved')
        self.root.mkdir(parents=True, exist_ok=True)
        budget = Budget(self.root, self.cfg, self.identity)
        budget.thread.start()
        try:
            self.calculate(); self.sources(); budget.guard(); budget.close()
            write_json(self.root / 'completion-seal.json', {'status': 'NUMERIC_COMPLETE_SYNOPSIS_REVIEW_PENDING',
                'fingerprint': self.identity, 'sources': self.cfg['inputs'], 'artifacts': {name: pin(self.root / name) for name in OUTPUTS}})
            print('NUMERIC_COMPLETE_SYNOPSIS_REVIEW_PENDING')
        except Exception as exc:
            budget.close()
            if not (self.root / 'failure.json').exists():
                write_json(self.root / 'failure.json', {'status': 'FAILED', 'error': str(exc), 'fingerprint': self.identity})
            raise

    def excerpts(self):
        self.sources(); self.verify_completion()
        draft = self.root / 'synopsis-excerpts-draft.json'
        cards = self.root / 'synopsis-only-cards.json'
        output = self.root / 'synopsis-excerpts.json'
        sealpath = self.root / 'synopsis-excerpts-seal.json'
        if sealpath.exists():
            seal = read_json(sealpath)
            require(seal['fingerprint'] == self.identity and seal['draft'] == pin(draft) and seal['source'] == pin(cards)
                    and seal['output'] == pin(output), 'excerpt seal changed')
            print('VERIFIED_EXISTING_EXCERPTS'); return
        require(not output.exists(), 'partial excerpts preserved')
        records = validate_excerpts(read_json(cards), read_json(draft), self.cfg)
        write_json(output, records)
        write_json(sealpath, {'status': 'EXACT_SUBSTRINGS_SEALED_BEFORE_LINKED_REVIEW', 'fingerprint': self.identity,
            'draft': pin(draft), 'source': pin(cards), 'output': pin(output), 'cards': len(records),
            'excerpts': sum(len(row['excerpts']) for row in records), 'semantic_truth_validated': False})
        print('EXACT_SUBSTRINGS_SEALED_BEFORE_LINKED_REVIEW')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--validate-excerpts', action='store_true')
    args = parser.parse_args()
    run = Run()
    run.excerpts() if args.validate_excerpts else run.run()
