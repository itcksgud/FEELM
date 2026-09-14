"""Same targets and assigned caps; only strict-past histories and crowd shrinkage vary."""
import os
for key in ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[key] = '4'
import json
import time
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from foundation340_common import *
from foundation340_features import Features, Histories, RATING_COLUMNS

class Writer:
    def __init__(self, path):
        self.schema = pa.schema([('row_id', pa.int64()), ('uid', pa.int32()), ('label', pa.float64())] + [(f'x{i:03d}', pa.float32()) for i in range(230)])
        self.writer = pq.ParquetWriter(path, self.schema, compression='zstd')

    def write(self, x, row_id, uid, label):
        data = dict(row_id=row_id, uid=uid, label=label)
        data.update({f'x{i:03d}': x[:, i] for i in range(230)})
        self.writer.write_table(pa.Table.from_pydict(data, schema=self.schema))

def prepare():
    from text339_common import reviewed as old_reviewed, verify as old_verify
    reviewed(); old_reviewed(); old_verify('prepared-seal.json')
    OUT.mkdir(parents=True, exist_ok=True)
    require(not (OUT / 'B').exists(), 'preserve prepared data')
    meta = pd.read_parquet(ROOT / config()['metadata'])
    catalog = pd.read_parquet(OLD / 'catalog.parquet')
    ratings = pd.read_parquet(OLD / 'ratings.parquet')
    episodes = pd.read_parquet(OLD / 'episodes.parquet')
    ids = catalog.movie_id.to_numpy()
    require(np.array_equal(meta.movie_id, ids), 'same movie metadata')
    hist = Histories(ratings, episodes, ids)
    features = Features(meta, catalog)
    require(not catalog.blocked.to_numpy()[hist.ix].any(), 'no blocked train inputs or targets')
    contexts = json.loads((OLD / 'contexts.json').read_text())
    require(not set(ratings.uid) & {c['uid'] for c in contexts}, 'disjoint train/evaluation users')
    writers = {}
    for v in VARIANTS:
        (OUT / v).mkdir()
        writers[v] = Writer(OUT / v / 'train.parquet')
    source = pq.ParquetFile(OLD / 'train.parquet')
    cols = ['row_id', 'uid', 'label'] + [f'x{i:03d}' for i in range(230)]
    offset = 0; start = time.monotonic(); counts = {'old_h0': 0, 'new_h0': 0, 'new_history_pairs': 0}
    for group in range(source.num_row_groups):
        frame = source.read_row_group(group, columns=cols).to_pandas()
        rows = np.arange(offset, offset + len(frame))
        require(np.array_equal(frame.row_id, rows) and np.array_equal(frame.uid, hist.uid[rows]) and np.array_equal(frame.label, hist.stars[rows]), 'identical original ordered targets and labels')
        baseline = frame[cols[3:]].to_numpy(np.float32)
        r = baseline.copy(); h = np.empty_like(baseline); rh = np.empty_like(baseline)
        for pos in range(0, len(rows), 4096):
            sl = slice(pos, pos + 4096); use = rows[sl]
            old = hist.take(use, strict=False)
            r[sl, RATING_COLUMNS] = features.crowd(*old, shrink=True)[:, np.array(RATING_COLUMNS) - 200]
            new = hist.take(use, strict=True)
            h[sl] = features.batch(*new); rh[sl] = h[sl]
            rh[sl, RATING_COLUMNS] = features.crowd(*new, shrink=True)[:, np.array(RATING_COLUMNS) - 200]
            counts['old_h0'] += int((old[2].sum(1) == 0).sum())
            counts['new_h0'] += int((new[2].sum(1) == 0).sum())
            counts['new_history_pairs'] += int(new[2].sum())
        for v, x in zip(VARIANTS, [baseline, r, h, rh]):
            require(np.isfinite(x).all(), 'finite train matrix')
            writers[v].write(x, rows, frame.uid, frame.label)
        offset += len(frame)
        if group % 20 == 0:
            write_json(OUT / 'run-progress.json', {'stage': 'PREPARING', 'rows': offset, 'total_rows': len(ratings), 'seconds': time.monotonic() - start})
            print('PREPARE', offset, round(time.monotonic() - start, 1), flush=True)
    for writer in writers.values(): writer.writer.close()
    require(offset == 4997069 and counts == {'old_h0': 4114737, 'new_h0': 1051625, 'new_history_pairs': 41750890}, 'label-free predeclared history census')
    score = pd.read_parquet(OLD / 'score.parquet', columns=cols)
    baseline = score[cols[3:]].to_numpy(np.float32); corrected = baseline.copy()
    for c in contexts:
        oi = np.tile(np.pad(c['oi'], (0, 30-c['h'])), (len(c['ei']), 1)).astype(int)
        stars = np.tile(np.pad(c['stars'], (0, 30-c['h'])), (len(c['ei']), 1))
        mask = np.tile(np.arange(30) < c['h'], (len(c['ei']), 1))
        corrected[c['start']:c['stop'], RATING_COLUMNS] = features.crowd(oi, stars, mask, np.array(c['ei']), True)[:, np.array(RATING_COLUMNS)-200]
    for v in VARIANTS:
        writer = Writer(OUT / v / 'score.parquet')
        writer.write(corrected if v in ['R', 'RH'] else baseline, score.row_id, score.uid, score.label)
        writer.writer.close()
        write_json(OUT / v / 'feature-info.json', {'names': features.names, 'indices': {v: list(range(230))}, 'train_rows': len(ratings), 'train_users': int(ratings.uid.nunique()), 'score_rows': len(score)})
    report = {'counts': counts, 'train_rows': offset, 'score_rows': len(score), 'train_users': int(ratings.uid.nunique()),
              'prior_mean': features.prior_mean, 'prior_mass': features.prior_mass, 'prior_movies': features.prior_movies,
              'seconds': time.monotonic()-start, 'target_stars_decoded': 0,
              'old_parent': pin(OLD/'prepared-seal.json'), 'training_target_order_unchanged': True}
    write_json(OUT / 'prepare-report.json', report)
    seal('prepared-seal.json', [f'{v}/{n}' for v in VARIANTS for n in ['train.parquet','score.parquet','feature-info.json']] + ['prepare-report.json'], old_prepared=pin(OLD/'prepared-seal.json'))
    write_json(OUT / 'run-progress.json', {'stage': 'PREPARED', **report})
    print('PREPARED', json.dumps(report), flush=True)

if __name__ == '__main__': prepare()
