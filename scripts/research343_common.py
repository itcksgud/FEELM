"""Stage-specific review gates; historical artifacts are read-only."""
import hashlib
import json
from pathlib import Path
from rec046_common import require, pin, write_json

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'outputs/recommendation-evidence'
OLD=BASE/'text339'
FOUND=BASE/'foundation340'
COMBO=BASE/'combination340'
OUT=BASE/'research343'
DOC=ROOT/'docs/recommendation/experiments/research343'
ANCHORS={
 'text339/prepared-seal.json':'d27709a42bd0d5511a36f3740365637325d59b521c7f62eae04850523782fdd8',
 'foundation340/prepared-seal.json':'9f0550e38566512fc2dad084d3f9381a7197ea3bab05fd0428eef76878acb62e',
 'foundation340/fit-seal.json':'9790dd15f77adf292ed15244ddb0b594d69ad2de553baf0a1ee14114dd99af2a',
 'combination340/fit-seal.json':'702fb7fd8b82dabd607811064ed02fdd6e46cd319e718072a01a00328c1e7282',
 'rec-ev-045/metadata.parquet':'4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d'}

def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def fingerprint(stage):
    names={'gbt':['research343_common.py','research343_fit.py','combination340_worker.py','combination340_models.py','rec046_common.py'],
           'lgb':['research343_common.py','research343_lgb.py','research343_lgb_worker.py','research343_lgb_models.py','combination340_worker.py','rec046_common.py'],
           'evaluation':['research343_common.py','research343_evaluate.py','research343_catalog.py']}[stage]
    paths=[ROOT/'scripts'/n for n in names]+[DOC/'DESIGN.md',DOC/'config.json']
    return {p.relative_to(ROOT).as_posix():pin(p) for p in paths}
def reviewed(stage):
    r=read(DOC/(stage+'-review.json'))
    require(r['status']=='PASS' and r['fingerprint']==fingerprint(stage),'independent exact code review '+stage)
def lock():
    record=read(OUT/'input-lock.json')
    for name,p in record['files'].items(): require(pin(ROOT/name)==p,'input identity '+name)
    return record
def seal(name,files,**extra):
    require(not (OUT/name).exists(),'preserve '+name)
    write_json(OUT/name,{'input_lock':pin(OUT/'input-lock.json'),'files':{n:pin(OUT/n) for n in files},**extra})
def verify(name):
    lock();r=read(OUT/name); require(r['input_lock']==pin(OUT/'input-lock.json'),'same input lock')
    for n,p in r['files'].items(): require(pin(OUT/n)==p,'sealed artifact '+n)
    return r
