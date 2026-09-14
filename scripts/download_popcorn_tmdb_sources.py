"""Acquire two public data files, without API credentials or model execution."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import requests

root=Path(__file__).resolve().parents[1]/'outputs/recommendation-evidence/popcorn-tmdb-full-audit'
sources={
    'movie_ids_09_10_2026.json.gz':'https://files.tmdb.org/p/exports/movie_ids_09_10_2026.json.gz',
    'tmdb-kaggle-20260911.zip':'https://www.kaggle.com/api/v1/datasets/download/asaniczka/tmdb-movies-dataset-2023-930k-movies',
    'adult_movie_ids_09_10_2026.json.gz':'https://files.tmdb.org/p/exports/adult_movie_ids_09_10_2026.json.gz',
}
records=[]
manifest=root/'sources.json'
existing={r['name']:r for r in json.loads(manifest.read_text())} if manifest.exists() else {}
for name,url in sources.items():
    target=root/name
    if name in existing:
        with target.open('rb') as f:
            assert hashlib.file_digest(f,'sha256').hexdigest()==existing[name]['sha256']
        records.append(existing[name])
        continue
    h=hashlib.sha256()
    size=0
    start=datetime.now(timezone.utc).isoformat()
    with requests.get(url,stream=True,timeout=(15,40)) as r:
        r.raise_for_status()
        if name.endswith('.zip'):
            assert 'zip' in r.headers.get('Content-Type','')
        with target.open('xb') as f:
            for block in r.iter_content(1024*1024):
                size+=len(block)
                assert size<=1024**3, 'Unexpected >1 GiB download'
                f.write(block)
                h.update(block)
        expected=r.headers.get('Content-Length')
        assert expected is None or int(expected)==size
        records.append({'name':name,'source_url':url,'started_at':start,
            'finished_at':datetime.now(timezone.utc).isoformat(),'bytes':size,
            'sha256':h.hexdigest(),'last_modified':r.headers.get('Last-Modified')})
    print(json.dumps(records[-1]),flush=True)
with manifest.open('w',encoding='utf-8') as f:
    json.dump(records,f,indent=2)
    f.write('\n')
