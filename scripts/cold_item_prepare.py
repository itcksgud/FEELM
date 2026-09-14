"""Movie-level holdout before all rating-derived histories; closed future labels."""
from __future__ import annotations
import argparse
import gc
import json
import zipfile
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from cold_item_common import *
from cold_item_features import Features

def population():
    metadata = pd.read_parquet(ROOT / config()['metadata'])
    ids = metadata.movie_id.to_numpy(np.int64)
    vcat = np.load(OLD / 'validation/catalog.npz')
    vc = vcat['counts'][-1]
    cat = np.load(OLD / 'evaluation/catalog.npz')
    counts = cat['counts'][-1]
    require(np.array_equal(ids, cat['movie_ids']) and np.array_equal(ids, vcat['movie_ids']), 'catalog aligned')
    part = movie_partition(metadata, vc)
    observations = pd.read_parquet(READY / 'development-observations.parquet')
    origin = config()['origin']
    users = pd.read_parquet(READY / 'user-origin-counts.parquet')
    users = users[(users.origin == origin) & (users.role == 'evaluation') & (users.targets > 0)]
    obs = observations[observations.uid.isin(users.uid)].copy()
    obs['index'] = np.searchsorted(ids, obs.movie_id.to_numpy())
    require((obs['index'] < len(ids)).all() and np.array_equal(ids[obs['index']], obs.movie_id), 'observation catalog identities')
    before = obs[(obs.timestamp < origin) & (part[obs['index']] == 'W') & (counts[obs['index']] > 0)].copy()
    before['warm_total'] = before.groupby('uid')['movie_id'].transform('size')
    before = before.sort_values(['uid', 'timestamp', 'movie_id'], ascending=[True, False, True]).groupby('uid', sort=False).head(30)
    target = obs[(obs.timestamp >= origin) & (obs.timestamp < origin + H) & (part[obs['index']] == 'E')]
    target = target.sort_values(['uid', 'timestamp', 'movie_id'])
    return metadata, ids, part, vc, counts, users, before, target

def preflight():
    cfg = config()
    for name, expected in cfg['sources'].items():
        require(pin(ROOT / name) == expected, 'input drift')
    require(not (OUT / 'preflight.json').exists(), 'preserve preflight')
    OUT.mkdir(exist_ok=True, parents=True)
    meta, ids, part, vc, counts, users, before, target = population()
    npre = before.groupby('uid').size()
    rows = []
    for k in KS:
        valid = set(npre[npre >= k].index) & set(target.uid)
        a = target[target.uid.isin(valid)]
        rows.append({'k': k, 'users': len(valid), 'movies': int(a.movie_id.nunique()),
                     'observations': len(a), 'users_with_at_least_two_targets': int((a.groupby('uid').size() >= 2).sum())})
    pd.DataFrame({'movie_id': ids, 'tmdb_id': meta.tmdb_id, 'partition': part,
                  'pre_v_count': vc, 'reference_t_count': counts}).to_parquet(OUT / 'movie-partition.parquet', index=False)
    report = {'score_population': rows, 'movie_partition': {p: int((part == p).sum()) for p in ['W','V','E']},
              'target_stars_decoded': 0, 'partition_file': pin(OUT / 'movie-partition.parquet')}
    write_json(OUT / 'preflight.json', report)
    print(json.dumps(report), flush=True)

class Writer:
    def __init__(self, path):
        self.schema = pa.schema([('row_id', pa.int64()), ('uid', pa.int32()), ('label', pa.float64())] + [(f'x{i:03d}', pa.float32()) for i in range(398)])
        self.writer = pq.ParquetWriter(path, self.schema, compression='zstd')
        self.pending = []
        self.size = self.offset = 0
    def append(self, x, y, uid):
        self.pending.append((x, np.asarray(y), uid)); self.size += len(y)
        if self.size >= 8192: self.flush()
    def flush(self):
        if not self.size: return
        x = np.vstack([a[0] for a in self.pending]); y = np.concatenate([a[1] for a in self.pending])
        data = {'row_id': np.arange(self.offset, self.offset + len(y)),
                'uid': np.concatenate([np.full(len(a[1]), a[2]) for a in self.pending]), 'label': y}
        data.update({f'x{i:03d}': x[:, i] for i in range(398)})
        self.writer.write_table(pa.Table.from_pydict(data, schema=self.schema))
        self.offset += len(y); self.pending = []; self.size = 0
    def close(self): self.flush(); self.writer.close()

def prepare():
    reviewed()
    require(not (OUT / 'train.parquet').exists(), 'preserve prepared data')
    meta, ids, part, vc, counts, users, before, target = population()
    partition = pd.read_parquet(OUT / 'movie-partition.parquet')
    require(np.array_equal(partition.partition.to_numpy(), part), 'fixed movie mask')
    origin = config()['origin']
    train = pd.read_parquet(OLD / 'evaluation/ratings.parquet')
    require((train.timestamp < origin).all() and all(role(int(u)) == 'train' for u in train.uid.unique()), 'legal train users/time')
    ix = np.searchsorted(ids, train.movie_id.to_numpy())
    train = train.loc[part[ix] == 'W'].copy()
    train['index'] = np.searchsorted(ids, train.movie_id.to_numpy())
    train = train.sort_values(['uid', 'timestamp', 'movie_id'])
    train['window'] = window_start(train.timestamp.to_numpy(), origin)
    require(not train.duplicated(['uid', 'movie_id']).any(), 'unique training rows')
    train[['uid','movie_id','timestamp','rating']].to_parquet(OUT / 'ratings.parquet', index=False)
    features = Features(meta, config()['shrink_strength'])
    writer = Writer(OUT / 'train.parquet'); episodes = []
    for number, (uid, a) in enumerate(train.groupby('uid', sort=False)):
        ix, ts, r, wins = a['index'].to_numpy(), a.timestamp.to_numpy(), a.rating.to_numpy(), a.window.to_numpy()
        starts = np.r_[0, np.flatnonzero(np.diff(wins)) + 1]; ends = np.r_[starts[1:], len(a)]
        for lo, hi in zip(starts, ends):
            w = int(wins[lo]); k = assigned_k(int(uid), w, int(lo))
            previous = np.lexsort((ids[ix[:lo]], -ts[:lo]))[:k]
            require((ts[previous] < w).all(), 'input before episode')
            writer.append(features.transform(ix[previous], r[previous], ix[lo:hi]), r[lo:hi], int(uid))
            episodes.append((int(uid), w, k, int(lo), int(hi-lo)))
        if number % 2000 == 0: print(f'features: {number} users', flush=True)
    writer.close(); require(writer.offset == len(train), 'one target per permitted rating')
    train_rows, train_users = len(train), int(train.uid.nunique())
    del train; gc.collect()
    pd.DataFrame(episodes, columns=['uid','origin','k','pre_count','targets']).to_parquet(OUT / 'episodes.parquet', index=False)
    eligible = set(target.uid) & set(before.uid)
    before = before[before.uid.isin(eligible)]
    inputs = {(int(u),int(m)) for u,m in before[['uid','movie_id']].itertuples(index=False, name=None)}
    stars = {}
    with zipfile.ZipFile(ROOT / config()['archive']) as z, z.open('ml-32m/ratings.csv') as f:
        require(f.readline().strip() == b'userId,movieId,rating,timestamp', 'archive header')
        for row in f:
            ub, rest = row.split(b',',1); u = int(ub)
            if u not in eligible: continue
            mb, rb, tb = rest.strip().split(b','); m,t = int(mb), int(tb)
            if t < origin and (u,m) in inputs:
                value = float(rb); require(value*2 in range(1,11), 'half star O')
                require((u,m) not in stars, 'unique input'); stars[(u,m)] = value
    require(len(stars) == len(inputs), 'all requested input stars')
    writer = Writer(OUT / 'score.parquet'); contexts = []
    groups = {int(u): a for u,a in before.groupby('uid')}; info = users.set_index('uid').to_dict('index')
    for uid, a in target.groupby('uid', sort=True):
        uid = int(uid)
        if uid not in groups: continue
        o = groups[uid]; ei = a['index'].to_numpy()
        for k in KS:
            if len(o) < k: continue
            use = o.head(k); oi = use['index'].to_numpy(); ov = [stars[(uid,int(m))] for m in use.movie_id]
            require((part[oi] == 'W').all() and (part[ei] == 'E').all() and (counts[oi] > 0).all() and (counts[ei] > 0).all(), 'direct reference support')
            start = writer.offset + writer.size
            writer.append(features.transform(oi, ov, ei), np.zeros(len(ei)), uid)
            contexts.append({'uid':uid,'k':k,'start':start,'stop':start+len(ei),'oi':oi.tolist(),'stars':ov,'ei':ei.tolist(),
                             'input_timestamps':use.timestamp.to_list(),'target_timestamps':a.timestamp.to_list(),
                             'pre_all':int(info[uid]['pre_all']),'pre_warm':int(o.warm_total.iloc[0]),'activity':info[uid]['activity']})
    writer.close(); require(bool(contexts) and writer.offset == contexts[-1]['stop'], 'score contexts')
    write_json(OUT / 'contexts.json', contexts)
    np.savez_compressed(OUT / 'catalog.npz', movie_ids=ids, pre_v_counts=vc, reference_counts=counts, partition=part)
    write_json(OUT / 'feature-info.json', {'names':features.names,'indices':features.indices,'train_rows':train_rows,
              'train_users':train_users,'score_rows':writer.offset,'decoded_input_ratings':len(stars),'target_stars_decoded':0})
    files = ['preflight.json','movie-partition.parquet','train.parquet','ratings.parquet','episodes.parquet','score.parquet','contexts.json','catalog.npz','feature-info.json']
    write_json(OUT / 'prepared-seal.json', {'fingerprint':fingerprint(),'files':{n:pin(OUT/n) for n in files},'target_stars_decoded':0})
    print('PREPARED', train_rows, len(contexts), writer.offset, flush=True)

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('action', choices=['preflight','prepare']);a=p.parse_args()
    preflight() if a.action=='preflight' else prepare()
