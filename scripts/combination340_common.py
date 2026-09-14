"""Sealed stage-one selection, a bounded extension, and explicit file provenance."""
import json
from pathlib import Path
from rec046_common import require,pin,write_json
import foundation340_common as foundation

ROOT=foundation.ROOT
DOC=ROOT/'docs/recommendation/experiments/combination340'
OUT=ROOT/'outputs/recommendation-evidence/combination340'
OLD=foundation.OLD
METHODS=['NO_RESPONSE','GBT','ALS']
REFERENCE=ROOT/'outputs/recommendation-evidence/rec-ev-047/evaluation/models/p100/ALS'
FILES=['combination340_common.py','combination340_models.py','combination340_worker.py','combination340_run.py']

def config(): return json.loads((DOC/'config.json').read_text())
def fingerprint():
    return {p.relative_to(ROOT).as_posix():pin(p) for p in [ROOT/'scripts'/n for n in FILES]+[DOC/'EXECUTION.md',DOC/'config.json']}

def verify_foundation_evaluation_code():
    from foundation340_evaluate import evaluation_fingerprint
    actual=evaluation_fingerprint()
    record=json.loads((foundation.OUT/'evaluation-seal.json').read_text())
    review=json.loads((foundation.DOC/'evaluation-code-review.json').read_text())
    require(record['evaluation_code']==actual and review['status']=='PASS' and review['fingerprint']==actual,'stage-one evaluation code and approved contract identity')

def reviewed():
    review=json.loads((DOC/'execution-review.json').read_text())
    require(review['status']=='PASS' and review['fingerprint']==fingerprint(),'independent extension execution review')
    foundation.reviewed(); foundation.verify('evaluation-seal.json')
    verify_foundation_evaluation_code()
    decision=json.loads((foundation.DOC/'result-review.json').read_text())
    require(decision['status']=='PASS' and decision['evaluation_seal']==pin(foundation.OUT/'evaluation-seal.json'),'independent first-stage decision')

def verify_reference():
    expected={'bytes':6110,'sha256':'16552a121ac5da25c916d849b19d903892d7e4cc539695fa705bd8fd3260dba4'}
    require(pin(REFERENCE/'model-seal.json')==expected,'reviewed historical reference model identity')
    record=json.loads((REFERENCE/'model-seal.json').read_text())
    for name,value in record['files'].items(): require(pin(REFERENCE/name)==value,'historical reference actual artifact')

def lock():
    value=json.loads((OUT/'input-lock.json').read_text())
    for name,expected in value['parents'].items(): require(pin(ROOT/name)==expected,'locked parent '+name)
    foundation.verify('evaluation-seal.json'); verify_foundation_evaluation_code(); verify_reference()
    require(value['selected']==json.loads((foundation.OUT/'selection.json').read_text())['selected'],'unchanged stage-one selection')
    return value

def seal(name,files,**extra):
    require(not (OUT/name).exists(),'preserve '+name)
    write_json(OUT/name,{'fingerprint':fingerprint(),'input_lock':pin(OUT/'input-lock.json'),'files':{p:pin(OUT/p) for p in files},**extra})

def verify(name):
    record=json.loads((OUT/name).read_text())
    require(record['fingerprint']==fingerprint() and record['input_lock']==pin(OUT/'input-lock.json'),'execution and data parent')
    lock()
    for path,expected in record['files'].items(): require(pin(OUT/path)==expected,'extension actual artifact '+path)
    if name=='evaluation-seal.json':
        require(record['fit_seal']==pin(OUT/'fit-seal.json') and record['catalog_seal']==pin(OUT/'catalog-seal.json'),'evaluation actual fit/catalog parent links')
        verify('fit-seal.json')
        from combination340_catalog import verify_catalog
        verify_catalog()
        from combination340_evaluate import evaluation_fingerprint
        actual=evaluation_fingerprint(); review=json.loads((DOC/'evaluation-code-review.json').read_text())
        require(record['evaluation_code']==actual and review['status']=='PASS' and review['fingerprint']==actual,'extension evaluation code and review identity')
    return record
