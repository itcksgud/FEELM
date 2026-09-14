"""Local foundation experiment identity and immutable output gates."""
import json
from pathlib import Path
from rec046_common import require, pin, write_json

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/recommendation/experiments/foundation340'
OUT = ROOT / 'outputs/recommendation-evidence/foundation340'
OLD = ROOT / 'outputs/recommendation-evidence/text339'
VARIANTS = ['B', 'R', 'H', 'RH']
FILES = ['foundation340_common.py', 'foundation340_features.py', 'foundation340_prepare.py',
         'foundation340_worker.py', 'foundation340_run.py', 'test_foundation340.py',
         'cold_item_features.py', 'rec047_features.py', 'rec046_common.py',
         'text339_relations.py', 'text339_run.py', 'text339_common.py',
         'text339_features.py', 'text339_prepare.py', 'text339_encode.py']

def config():
    return json.loads((DOC / 'config.json').read_text(encoding='utf-8'))

def fingerprint():
    files = [ROOT / 'scripts' / n for n in FILES] + [DOC / 'EXECUTION.md', DOC / 'config.json']
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in files}

def reviewed():
    record = json.loads((DOC / 'execution-review.json').read_text(encoding='utf-8'))
    require(record['status'] == 'PASS' and record['fingerprint'] == fingerprint(), 'exact independent execution review')
    from text339_common import reviewed as old_reviewed, verify as old_verify
    for p, expected in config()['parents'].items():
        require(pin(ROOT / p) == expected, 'fixed prior experiment identity ' + p)
    old_reviewed(); old_verify('prepared-seal.json')
    return record

def seal(name, paths, **extra):
    require(not (OUT / name).exists(), 'preserve ' + name)
    write_json(OUT / name, {'fingerprint': fingerprint(), 'files': {p: pin(OUT / p) for p in paths}, **extra})

def verify(name):
    record = json.loads((OUT / name).read_text())
    require(record['fingerprint'] == fingerprint(), 'execution drift')
    for p, expected in record['files'].items():
        require(pin(OUT / p) == expected, 'artifact drift ' + p)
    if 'old_prepared' in record:
        require(pin(OLD / 'prepared-seal.json') == record['old_prepared'], 'old prepared parent')
        from text339_common import verify as old_verify
        old_verify('prepared-seal.json')
    for key, parent in [('prepared_seal', 'prepared-seal.json'), ('fit_seal', 'fit-seal.json'), ('catalog_seal', 'catalog-seal.json')]:
        if key in record:
            require(pin(OUT / parent) == record[key], 'parent identity ' + parent)
            verify(parent)
    if name == 'fit-seal.json':
        for variant in VARIANTS:
            verify_model(variant)
    return record

def verify_model(variant):
    folder = OUT / variant / 'model'
    record = json.loads((folder / 'model-seal.json').read_text())
    require(record['fingerprint'] == fingerprint() and record['prepared_seal'] == pin(OUT/'prepared-seal.json'), 'model execution/data parent')
    for p, expected in record['files'].items():
        require(pin(folder / p) == expected, 'model artifact drift ' + variant + '/' + p)
    return record
