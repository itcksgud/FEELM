"""Link sealed blind judgments to frozen assignments, without model fitting."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from rec_ev_033_tastes import pin, read_json, require, sha, write_json
from rec_ev_038_clusters import Run


def classify(native, sufficient, acceptable, group):
    if not sufficient: return 'INSUFFICIENT_OVERVIEW'
    if native < 0: return 'NO_NATIVE_EVIDENCE'
    if group in acceptable: return 'UNIQUE_ACCEPT' if len(acceptable) == 1 else 'AMBIGUOUS_ACCEPT'
    return 'NO_DESCRIPTION_MATCH' if not acceptable else 'ASSIGNMENT_CONFLICT'


def run():
    run = Run(); run.sources(); run.verify_numeric(); run.seal_judgments()
    root = run.root
    dependencies = {n: pin(root / n) for n in ['numeric-seal.json', 'description-seal.json', 'judgment-seal.json',
        'judgments.json', 'evaluation-key.json', 'card-key.json', 'assignments.npz', 'descriptions.json', 'description-key.json']}
    dependencies['analysis_runner'] = pin(Path(__file__))
    seal_path = root / 'evaluation-seal.json'
    names = ['evaluation-linked.parquet', 'evaluation-summary.json', 'evaluation-comparison.csv']
    if seal_path.exists():
        seal = read_json(seal_path)
        require(seal['status'] == 'SEALED_BLIND_EVALUATION_LINK_COMPLETE' and seal['dependencies'] == dependencies
                and set(seal['artifacts']) == set(names), 'evaluation dependency changed')
        for name, spec in seal['artifacts'].items(): require(pin(root / name) == spec, 'evaluation output changed')
        print('VERIFIED_EVALUATION_COMPLETION'); return
    require(not any((root / n).exists() for n in names), 'partial evaluation preserved')
    z = np.load(root / 'assignments.npz', allow_pickle=False)
    methods = z['methods'].tolist(); index = {int(mid): i for i, mid in enumerate(z['movie_ids'])}
    cards = {r['card']: r for r in read_json(root / 'card-key.json') if r['is_evaluation']}
    keys = read_json(root / 'evaluation-key.json')
    method_key = {k['method']: k['actual_method'] for k in keys}
    native_key = {(k['method'], k['native_group']): k['group'] for k in keys}
    rows = []
    for r in read_json(root / 'judgments.json'):
        card = cards[r['card']]; method = method_key[r['method']]
        mi, p = methods.index(method), index[card['movie_id']]
        native = int(z['native'][mi, p]); display = int(z['display'][mi, p])
        group = native_key[(r['method'], native)] if native >= 0 else None
        category = classify(native, r['sufficient'], r['acceptable'], group)
        rows.append({'movie_id': card['movie_id'], 'card': r['card'], 'title': card['title'],
            'method': method, 'anonymous_method': r['method'], 'native_group': native, 'display_group': display,
            'sufficient': r['sufficient'], 'tag_supported': card['tag_supported'], 'acceptable': r['acceptable'],
            'native_anonymous_group': group, 'category': category, 'native_accept': category in {'UNIQUE_ACCEPT', 'AMBIGUOUS_ACCEPT'},
            'unique_accept': category == 'UNIQUE_ACCEPT', 'ambiguous': r['sufficient'] and len(r['acceptable']) > 1,
            'no_description': r['sufficient'] and len(r['acceptable']) == 0, 'reason': r['reason']})
    frame = pd.DataFrame(rows).sort_values(['movie_id', 'method']).reset_index(drop=True)
    require(len(frame) == len(cards) * len(methods) and not frame.duplicated(['movie_id', 'method']).any(), 'linked count')
    sufficient = frame.groupby('movie_id').sufficient.agg(['all', 'any'])
    common = set(sufficient.index[sufficient['all']])
    tags = set(frame.loc[frame.tag_supported, 'movie_id'])
    all_movies = set(frame.movie_id)
    scopes = {'ALL_FIXED_MOVIES': all_movies, 'COMMON_SUFFICIENT': common, 'COMMON_SUFFICIENT_TAG_SUPPORTED': common & tags}
    summaries, comparison = {}, []
    for method in methods:
        own = frame[frame.method == method]; summaries[method] = {}
        for name, movies in scopes.items():
            subset = own[own.movie_id.isin(movies)]; n = len(subset)
            result = {'movies': n, 'category_counts': {str(k): int(v) for k, v in subset.category.value_counts().items()},
                'native_supported': int((subset.native_group >= 0).sum()), 'sufficient': int(subset.sufficient.sum()),
                **{f: int(subset[f].sum()) for f in ['native_accept', 'unique_accept', 'ambiguous', 'no_description']}}
            result.update({f + '_fraction': float(result[f] / n) if n else None for f in ['native_accept', 'unique_accept', 'ambiguous', 'no_description']})
            summaries[method][name] = result
            comparison.append({'method': method, 'scope': name, **{k: v for k, v in result.items() if k != 'category_counts'}})
        summaries[method]['native_groups'] = {str(k): {'evaluation_movies': int((own.native_group == k).sum()),
            'common_sufficient_movies': int(((own.native_group == k) & own.movie_id.isin(common)).sum())} for k in range(8)}
        pairs = {}
        for accepted in own.loc[own.sufficient, 'acceptable']:
            for i, a in enumerate(sorted(accepted)):
                for b in sorted(accepted)[i + 1:]:
                    key = a + '/' + b; pairs[key] = pairs.get(key, 0) + 1
        summaries[method]['coacceptable_pairs'] = pairs
    paired = []
    for scope, mids in scopes.items():
        for i, a in enumerate(methods):
            for b in methods[i + 1:]:
                for metric in ['native_accept', 'unique_accept']:
                    table = frame[frame.movie_id.isin(mids)].pivot(index='movie_id', columns='method', values=metric).astype(int)
                    diff = table[a] - table[b] if mids else pd.Series(dtype=int)
                    paired.append({'scope': scope, 'a': a, 'b': b, 'metric': metric, 'movies': len(diff),
                        'a_only': int((diff > 0).sum()), 'b_only': int((diff < 0).sum()), 'same': int((diff == 0).sum()),
                        'difference_fraction': float(diff.mean()) if len(diff) else None})
    descriptions = {r['packet']: r for r in read_json(root / 'descriptions.json')}
    dstatus = {m: {} for m in methods}
    for key in read_json(root / 'description-key.json'):
        dstatus[key['method']][str(key['native_group'])] = {'packet': key['packet'],
            **{f: descriptions[key['packet']][f] for f in ['description', 'coherence', 'note']}}
    frame.to_parquet(root / names[0], index=False)
    write_json(root / names[1], {'status': 'EXPLORATORY_CLUSTER_PLUS_DESCRIPTION_DIAGNOSTIC',
        'unique_movies': len(cards), 'repeated_method_judgments': len(frame),
        'common_sufficient_movies': len(common), 'common_sufficient_tag_supported_movies': len(common & tags),
        'sufficiency_disagreement_movies': int((sufficient['all'] != sufficient['any']).sum()),
        'methods': summaries, 'paired_movie_counts': paired, 'descriptions': dstatus,
        'human_ground_truth': False, 'recommendation_quality_evaluated': False, 'statistical_winner_claimed': False})
    pd.DataFrame(comparison).to_csv(root / names[2], index=False, encoding='utf-8-sig')
    write_json(seal_path, {'status': 'SEALED_BLIND_EVALUATION_LINK_COMPLETE', 'dependencies': dependencies,
        'artifacts': {n: pin(root / n) for n in names}})
    print('SEALED_BLIND_EVALUATION_LINK_COMPLETE')


if __name__ == '__main__': run()
