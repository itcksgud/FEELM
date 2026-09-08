"""Inference-only research candidates. No training, network, ratings, or service mutations."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np


def genre_ids(values):
    if not isinstance(values, (list, tuple, np.ndarray)) or np.ndim(values) != 1:
        raise ValueError('genre IDs must be a one-dimensional ordered list')
    if any(not isinstance(v, (int, np.integer)) or isinstance(v, (bool, np.bool_)) or v <= 0 for v in values):
        raise ValueError('genre IDs must be positive integers; no coercion')
    return list(dict.fromkeys(map(int, values)))


class GenreCandidates:
    def __init__(self, package):
        self.package = package
        if package['package_id'] != 'REC039_FROM_REC038':
            raise ValueError('unexpected research package identity')
        self.ids = genre_ids(package['genre_ids'])
        self.idf = np.asarray(package['idf'], dtype=float)
        self.centers = np.asarray(package['raw_centers'], dtype=float)
        order = package['raw_cluster_order']
        if (len(self.ids) != 19 or len(package['genre_ids']) != 19 or self.idf.shape != (19,)
                or self.centers.shape != (8, 19) or not np.isfinite(self.idf).all()
                or not np.isfinite(self.centers).all() or not (self.idf > 0).all()
                or not (self.centers >= 0).all()
                or len(order) != 8 or any(type(v) is not int for v in order) or sorted(order) != list(range(8))):
            raise ValueError('invalid saved feature/model descriptor')
        self.inverse = np.argsort(order)
        self.lookup = {g: i for i, g in enumerate(self.ids)}
        self.rule_codes = package['rule_codes']
        groups = package['rule_genre_groups']
        self.rule = {g: code for code, gs in groups.items() for g in genre_ids(gs)}
        if (len(self.rule_codes) != 8 or len(set(self.rule_codes)) != 8 or set(groups) != set(self.rule_codes)
                or len(self.rule) != sum(map(len, groups.values())) or 10770 in self.rule):
            raise ValueError('invalid rule descriptor')

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8')))

    def predict(self, values, method):
        if method not in {'RULE_GENRE', 'KM_GENRE'}:
            raise ValueError('unknown candidate method')
        ids = genre_ids(values)
        unknown = [g for g in ids if g not in self.lookup]
        format_only = bool(ids) and all(g == 10770 for g in ids)
        label, status = -1, 'NO_GENRE'
        if ids and method == 'RULE_GENRE':
            content = [g for g in ids if g != 10770]
            if not content:
                status = 'FORMAT_ONLY'
            elif content[0] not in self.rule:
                status = 'UNMAPPED_GENRE'
            else:
                label = self.rule_codes.index(self.rule[content[0]])
                status = 'ASSIGNED_PARTIAL_VOCABULARY' if unknown else 'ASSIGNED'
        elif ids:
            x = np.zeros(19)
            for g in ids:
                if g in self.lookup: x[self.lookup[g]] = self.idf[self.lookup[g]]
            norm = np.linalg.norm(x)
            if norm == 0:
                status = 'NO_KNOWN_GENRE'
            else:
                x /= norm
                raw = int(np.argmin(np.sum((self.centers - x) ** 2, axis=1)))
                label = int(self.inverse[raw])
                status = 'ASSIGNED_PARTIAL_VOCABULARY' if unknown else 'ASSIGNED'
        prefix = 'RULE' if method == 'RULE_GENRE' else 'GENRE_KM'
        return {'package_id': self.package['package_id'], 'method': method,
                'group_code': f'{prefix}_{label+1:02d}' if label >= 0 else None,
                'native_label': label, 'supported': label >= 0, 'status': status,
                'unknown_genre_ids': unknown, 'format_only': format_only}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--genres', required=True, help='JSON array of ordered genre IDs, e.g. [28,12]')
    args = parser.parse_args()
    model = GenreCandidates.load(args.package)
    values = json.loads(args.genres)
    print(json.dumps([model.predict(values, m) for m in ['RULE_GENRE', 'KM_GENRE']], ensure_ascii=False, indent=2))
