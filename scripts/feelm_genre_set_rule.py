"""One frozen research rule using all known content genres; no fitting."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from feelm_genre_candidates import genre_ids

METHOD = 'RULE_GENRE_SET'
POLICY = 'binary_prototype_cosine_exact_tie_fixed_code_order'


class GenreSetRule:
    def __init__(self, package):
        if package['package_id'] != 'REC040_GENRE_SET_RULE' or package['policy'] != POLICY:
            raise ValueError('unexpected research rule identity')
        self.package = package
        self.codes = package['rule_codes']
        groups = package['rule_genre_groups']
        if (len(self.codes) != 8 or self.codes != sorted(groups) or len(set(self.codes)) != 8
                or any(not isinstance(c, str) or not c for c in self.codes)):
            raise ValueError('expected eight fixed sorted rule codes')
        self.groups = [set(genre_ids(groups[c])) for c in self.codes]
        if any(not gs or len(gs) != len(groups[c]) for c, gs in zip(self.codes, self.groups)):
            raise ValueError('groups must be nonempty without duplicates')
        self.content = set().union(*self.groups)
        if len(self.content) != sum(map(len, self.groups)) or 10770 in self.content:
            raise ValueError('content groups must be disjoint and exclude TV Movie')
        self.sizes = list(map(len, self.groups))

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8')))

    def predict(self, values):
        ids = set(genre_ids(values))
        known = ids & self.content
        unknown = sorted(ids - self.content - {10770})
        counts = [len(known & gs) for gs in self.groups]
        # The input norm is common to all eight groups. Fractions preserve exact ties.
        ranks = [Fraction(n*n, size) for n, size in zip(counts, self.sizes)]
        label, tied, status = -1, [], 'NO_GENRE'
        if known:
            best = max(ranks)
            tied = [i for i, score in enumerate(ranks) if score == best]
            label = tied[0]
            status = 'ASSIGNED_PARTIAL_VOCABULARY' if unknown else 'ASSIGNED'
        elif ids:
            status = 'FORMAT_ONLY' if ids == {10770} else 'NO_KNOWN_CONTENT_GENRE'
        scores = [score / len(known) for score in ranks] if known else [Fraction(0)] * 8
        ordered = sorted(scores, reverse=True)
        return {'package_id': self.package['package_id'], 'method': METHOD,
                'group_code': f'GENRE_SET_{label+1:02d}' if label >= 0 else None,
                'semantic_code': self.codes[label] if label >= 0 else None,
                'native_label': label, 'supported': label >= 0, 'status': status,
                'unknown_genre_ids': unknown, 'format_only': ids == {10770},
                'known_content_count': len(known), 'matched_counts': counts,
                'top_native_labels': tied, 'tie_count': len(tied),
                'winner_score_squared': float(ordered[0]),
                'runner_up_score_squared': float(ordered[1]),
                'score_margin_squared': float(ordered[0] - ordered[1])}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--genres', required=True, help='JSON array of genre IDs')
    args = parser.parse_args()
    print(json.dumps(GenreSetRule.load(args.package).predict(json.loads(args.genres)),
                     ensure_ascii=False, indent=2))
