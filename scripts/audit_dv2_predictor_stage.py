"""Read-only, independently formulated full predictor-selection result audit.

Future labels are intentionally open in this post-execution audit. This code
never selects new criteria, modifies stage outputs or calls a trained model.
It prints reproducible aggregate evidence; native-head parity is reviewed
separately and recorded in the combined result ledger.
"""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

REFERENCE = Path('C:/higher/projects/FEELM-standalone')
MAIN = REFERENCE / '.codex-tmp/fixed-k8-discovery-v2-20260913'
BASE = MAIN / 'outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2'
VARIANTS = ['original', 'shrink20', 'shrink100', 'min20']
EXPECTED_SEAL = '245c69bc2efdc96377e75f0b8529e6016c2dcc205328f94235c8aff60e8012d7'


def pin(path):
    return {'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def audit():
    stage = BASE / 'predictor'
    seal_path = BASE / 'predictor-seal.json'
    assert pin(seal_path)['sha256'] == EXPECTED_SEAL
    seal = read(seal_path)
    for name, wanted in seal['files'].items():
        assert pin(BASE / name) == wanted
    assert {p.name for p in stage.iterdir() if p.is_file()} == {
        Path(name).name for name in seal['files']}
    errors = pd.read_parquet(stage / 'observed-errors.parquet')
    requests = pd.read_parquet(stage / 'same-candidate-requests.parquet')
    details = read(stage / 'candidate-details.json')
    detail_map = {(x['uid'], x['predictor']): x for x in details}
    assert len(detail_map) == len(details) == len(requests)
    contexts = {(c['uid'], c['cap']): c for c in read(BASE / 'prepare/contexts.json')}
    frame = pd.read_parquet(BASE / 'prepare/catalog.parquet')
    eligible = set(np.load(BASE / 'prepare/quality-order.npy').tolist())
    roles = read(BASE / 'prepare/roles.json')
    source = REFERENCE / 'outputs/recommendation-evidence'
    source_roles = pd.read_csv(source / 'final344/roles.csv')
    assert set(roles['validation']) == set(source_roles.loc[source_roles.role.eq('calibration'), 'uid'])
    assert set(roles['verification']) == set(source_roles.loc[source_roles.role.eq('comparison'), 'uid'])
    assert len(roles['validation']) == 90 and len(roles['verification']) == 180
    assert set(errors.uid) <= set(roles['validation'])
    assert not set(errors.uid) & set(roles['verification'])
    assert set(requests.uid) == set(errors.uid)
    assert set(errors.variant) == set(requests.predictor) == set(VARIANTS)
    assert not errors.duplicated(['uid', 'index', 'variant']).any()
    assert not requests.duplicated(['uid', 'predictor']).any()
    # Support is independently mapped from original train IDs and actual factor IDs.
    old_catalog = pd.read_parquet(source / 'text339/catalog.parquet', columns=['movie_id', 'train_count'])
    count_by_id = dict(zip(old_catalog.movie_id, old_catalog.train_count))
    factor_ids = set(pd.read_parquet(source / 'combination340/ALS/item-factors', columns=['id']).id)
    train_count = np.zeros(len(frame), np.int64)
    has_factor = np.zeros(len(frame), bool)
    for i, row in enumerate(frame.itertuples()):
        if row.mapping_status == 'MATCHED' and pd.notna(row.movielens_movie_id):
            mid = int(row.movielens_movie_id)
            train_count[i] = count_by_id.get(mid, 0)
            has_factor[i] = mid in factor_ids
    np.testing.assert_array_equal(errors.clipped, np.clip(errors.prediction, .5, 5))
    np.testing.assert_allclose(errors.squared_error, (errors.clipped-errors.actual)**2, rtol=0, atol=1e-14)
    np.testing.assert_allclose(errors.absolute_error, abs(errors.clipped-errors.actual), rtol=0, atol=1e-14)
    assert np.isfinite(errors[['actual', 'prediction', 'clipped', 'squared_error', 'absolute_error']]).all().all()
    for row in errors.itertuples():
        context = contexts[row.uid, 10]
        truth = dict(zip(context['target'], context['ratings']))
        assert row.index in eligible and row.index not in context['viewed']
        assert row.actual == truth[row.index]
        assert row.service_movie_id == int(frame.service_movie_id.iloc[row.index])
        assert row.train_count == train_count[row.index]
        assert row.als_available == bool(has_factor[row.index] and has_factor[context['history']].any())
    for uid in set(errors.uid):
        context = contexts[uid, 10]
        expected_targets = set(context['target']) & eligible
        original = errors[errors.uid.eq(uid) & errors.variant.eq('original')]
        for variant in VARIANTS:
            current = errors[errors.uid.eq(uid) & errors.variant.eq(variant)]
            assert set(current['index']) == expected_targets
            for column in ['index', 'actual', 'train_count', 'als_available']:
                np.testing.assert_array_equal(current.sort_values('index')[column], original.sort_values('index')[column])
    for row in requests.itertuples():
        detail = detail_map[row.uid, row.predictor]
        ix = np.asarray(detail['candidate_indices'], int)
        values = np.asarray(detail['candidate_predictions'], float)
        context = contexts[row.uid, 10]
        assert row.cap == 10 and row.profile_state == 'VALID'
        assert len(ix) == len(set(ix)) == row.candidate_count
        assert set(ix) <= eligible and not set(ix) & set(context['viewed'])
        assert np.isfinite(values).all()
        available = has_factor[ix] & bool(has_factor[context['history']].any())
        weight = available.astype(float)
        if row.predictor.startswith('shrink'):
            weight *= train_count[ix] / (train_count[ix] + int(row.predictor[6:]))
        elif row.predictor == 'min20':
            weight *= train_count[ix] >= 20
        branches = np.where(weight == 1, 'ALS', np.where(weight > 0, 'ALS_GBT_SHRINK', 'GBT'))
        np.testing.assert_array_equal(detail['candidate_als_weights'], weight)
        np.testing.assert_array_equal(detail['candidate_branches'], branches)
        assert row.als_rows == int((weight > 0).sum())
        assert row.gbt_rows == int((weight < 1).sum())
        assert row.model_calls == (len(ix) + 8191)//8192
        assert len(ix) <= row.budget and row.global_fill == 0
        order = np.lexsort((frame.service_movie_id.iloc[ix].to_numpy(), -values))[:10]
        ranked = ix[order]
        for actual, expected in [(row.ranked, ranked), (row.ranked_prediction, values[order]),
                                 (row.ranked_branch, branches[order]), (row.ranked_als_weight, weight[order]),
                                 (row.ranked_train_count, train_count[ranked]),
                                 (row.ranked_als_available, available[order])]:
            np.testing.assert_array_equal(actual, expected)
        assert row.original_als_low20_top1 == int(len(order)>0 and available[order[0]] and train_count[ranked[0]]<=20)
        assert row.low_ml20_top1 == int(len(order)>0 and train_count[ranked[0]]<=20)
        assert row.low_ml20_slots == int((train_count[ranked]<=20).sum())
        assert row.candidate_low_ml20 == int((train_count[ix]<=20).sum())
        tmdb = frame.raw_vote_count_number.to_numpy(float)
        np.testing.assert_array_equal(row.ranked_tmdb_count, tmdb[ranked])
        assert row.low_tmdb20_top1 == int(len(order)>0 and tmdb[ranked[0]]<=20)
        assert detail['candidate_indices'] == detail_map[row.uid, 'original']['candidate_indices']
    summary = []
    for variant in VARIANTS:
        a = errors[errors.variant.eq(variant)]
        low = a[a.train_count<=20]
        b = requests[requests.predictor.eq(variant)]
        summary.append(dict(variant=variant, rows=len(a), users=int(a.uid.nunique()),
            macro_mse=float(a.groupby('uid').squared_error.mean().mean()), micro_mse=float(a.squared_error.mean()),
            macro_mae=float(a.groupby('uid').absolute_error.mean().mean()), low_rows=len(low), low_users=int(low.uid.nunique()),
            low_macro_mse=float(low.groupby('uid').squared_error.mean().mean()), low_micro_mse=float(low.squared_error.mean()),
            original_als_low20_top1=int(b.original_als_low20_top1.sum()), all_low_ml20_top1=int(b.low_ml20_top1.sum()),
            low_tmdb20_top1=int(b.low_tmdb20_top1.sum()), out_of_rating_range=int(((a.prediction<.5)|(a.prediction>5)).sum())))
    for actual, expected in zip(summary, read(stage/'summary.json')):
        assert actual.keys() == expected.keys()
        for key in actual:
            if isinstance(actual[key], str):
                assert actual[key] == expected[key]
            else:
                assert abs(actual[key]-expected[key]) < 1e-14
    by = {x['variant']:x for x in summary}
    original = by['original']
    sufficient = original['low_rows']>=30 and original['low_users']>=10
    alternatives, winner = [], 'original'
    for variant in ['shrink100', 'shrink20', 'min20']:
        a = by[variant]
        evidence = sufficient and original['original_als_low20_top1']>0
        gates = dict(global_mse=a['macro_mse']<=1.02*original['macro_mse'],
                     low_mse=sufficient and a['low_macro_mse']<=1.05*original['low_macro_mse'],
                     low_top1_drop=evidence and a['original_als_low20_top1']<=.8*original['original_als_low20_top1'])
        passed = all(gates.values())
        alternatives.append({'variant':variant, 'gates':gates, 'sufficient_low_error_evidence':sufficient,
                             'sufficient_low_exposure_evidence':evidence, 'pass':passed})
        if winner == 'original' and passed:
            winner = variant
    decision = read(stage/'decision.json')
    assert winner == decision['selected'] == 'shrink100'
    assert alternatives == decision['alternatives']
    assert decision['status'] == 'TECHNICAL_PREDICTOR_ALTERNATIVE'
    assert decision['selection_user_profile_states'] == {'VALID':62, 'ACTUAL_NO_HISTORY':28}
    for name, wanted in seal['files'].items():
        assert pin(BASE/name) == wanted
    assert pin(seal_path)['sha256'] == EXPECTED_SEAL
    return {'status':'PASS', 'scope':'All saved predictor errors, support, candidate routes/ranks and declared selection gates',
            'script':pin(Path(__file__)), 'stage_seal':pin(seal_path), 'stage_files':seal['files'],
            'observed_rows':len(errors), 'requests':len(requests), 'selection_users':90, 'check_users':180,
            'active_selection_users':62, 'no_history_selection_users':28, 'selected':winner,
            'summary':summary, 'alternatives':alternatives,
            'label_boundary':'Post-execution audit; future labels intentionally read only to verify saved observations and errors',
            'trained_model_calls_by_this_audit':0,
            'limits':['Macro MSE improves while micro MSE slightly worsens; fixed macro gates are correctly used.',
                      'The selected alternative is first passing in frozen order, not an assertion of best MSE among alternatives.',
                      'This stage is reused-development predictor diagnostics, not discovery satisfaction or fresh-test evidence.']}


if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=False, allow_nan=False))
