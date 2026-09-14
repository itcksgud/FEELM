"""Frozen final FM/GBT experiment: source identity and stage-specific review gates."""
import hashlib
import json
from pathlib import Path
from rec046_common import require, pin, write_json

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/recommendation-evidence'
OLD, FOUND, COMBO, LEGACY = [BASE / n for n in ('text339', 'foundation340', 'combination340', 'research343')]
OUT = BASE / 'final344'
DOC = ROOT / 'docs/recommendation/experiments/final344'
PLAN = ROOT / 'docs/recommendation/plans/final-fm-gbt'
RECIPES = {'FM150': ('FM', 150), 'FM300': ('FM', 300), 'GBT60': ('GBT', 60), 'GBT120': ('GBT', 120)}
SEEDS = (339, 344, 345)
SOURCE_ANCHORS = {
    'text339/prepared-seal.json': 'd27709a42bd0d5511a36f3740365637325d59b521c7f62eae04850523782fdd8',
    'foundation340/prepared-seal.json': '9f0550e38566512fc2dad084d3f9381a7197ea3bab05fd0428eef76878acb62e',
    'foundation340/fit-seal.json': '9790dd15f77adf292ed15244ddb0b594d69ad2de553baf0a1ee14114dd99af2a',
    'combination340/fit-seal.json': '702fb7fd8b82dabd607811064ed02fdd6e46cd319e718072a01a00328c1e7282',
    'rec-ev-045/metadata.parquet': '4d838874938115be7a4b1f629a920dd196e082b655b75d559d71039e52eb7d8d',
}
PLAN_PINS = {
    'README.md': 'f5f2b2dbfdbb85fcefc84957e41feab5f181066a46ed92aacb09fef35f22a63e',
    'DESIGN.md': '721d765dd78c89b60320da90e4eee2fc6446202aed8221692e4d7ad421bb5ea4',
    'EVIDENCE.md': '30ab877230928ff7bceb69219aff365478fdfc6926c2a6876877e1053a0e04e4',
    'config.json': '7ac43a357a2788a900d5e291ea2381e28a8cd63dc21568d6a7b951ea752477bb',
    'review.json': 'fcef1953f4461fe1a482069bf1611fea14a1c78263dda15761e94c31068e0a61',
}

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def plan_guard():
    for name, expected in PLAN_PINS.items():
        require(pin(PLAN / name)['sha256'] == expected, 'approved design identity: ' + name)

def fingerprint(stage):
    names = {
        'fit': ['final344_fit.py', 'final344_worker.py', 'final344_adapter.py', 'test_final344_adapter.py',
                'combination340_models.py', 'foundation340_features.py', 'cold_item_features.py',
                'text339_relations.py', 'rec047_features.py'],
        'evaluation': ['final344_evaluate.py', 'test_final344_evaluate.py'],
        'catalog': ['final344_catalog.py', 'combination340_models.py', 'foundation340_features.py',
                    'cold_item_features.py', 'text339_relations.py', 'rec047_features.py'],
    }[stage] + ['final344_common.py', 'rec046_common.py']
    paths = [ROOT / 'scripts' / n for n in names] + [DOC / n for n in ('config.json', 'execution.json', 'IMPLEMENTATION.md')]
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in paths}

def reviewed(stage):
    plan_guard()
    r = read(DOC / (stage + '-review.json'))
    require(r['status'] == 'PASS' and r['fingerprint'] == fingerprint(stage), 'independent exact-code review: ' + stage)

def lock():
    r = read(OUT / 'input-lock.json')
    for name, expected in r['files'].items():
        require(pin(ROOT / name) == expected, 'locked input: ' + name)
    return r

def seal(name, files, **extra):
    require(not (OUT / name).exists(), 'preserve seal: ' + name)
    write_json(OUT / name, {'input_lock': pin(OUT / 'input-lock.json'),
                           'files': {n: pin(OUT / n) for n in files}, **extra})

def verify(name):
    r = read(OUT / name)
    require(r['input_lock'] == pin(OUT / 'input-lock.json'), 'same input lock')
    for n, expected in r['files'].items():
        require(pin(OUT / n) == expected, 'sealed artifact: ' + n)
    if any(name.startswith(recipe + '_s') for recipe in RECIPES):
        require(r.get('execution') == fingerprint('fit'), 'fit was produced by the currently reviewed code')
    for n, expected in r.get('execution', {}).items():
        require(pin(ROOT / n) == expected, 'sealed execution source: ' + n)
    if 'evaluation_inputs' in r:
        e = r['evaluation_inputs']
        require(e['execution'] == fingerprint('evaluation'), 'same evaluation implementation')
        require(e['execution_contract'] == pin(DOC / 'execution.json') and e['roles'] == pin(OUT / 'roles.csv'), 'same evaluation contract and roles')
        require(e['labels'] == pin(OLD / 'labels.parquet'), 'same development labels')
        audits = [OUT / 'model-audit-seal.json', OUT / 'final-model-audit-seal.json']
        matches = [p for p in audits if p.exists() and pin(p) == e['model_audit']]
        require(len(matches) == 1 and read(matches[0])['status'] == 'PASS', 'preserved exact model audit parent')
        for fit_id, expected in e['fit_seals'].items():
            verify(fit_id + '-seal.json')
            require(pin(OUT / (fit_id + '-seal.json')) == expected, 'same evaluated fit parent')
    if name == 'selection-seal.json':
        parent = verify('selection-calibration-seal.json')
        require(r['calibration_seal'] == pin(OUT / 'selection-calibration-seal.json'), 'same selection calibration parent')
        require(parent['evaluation_inputs'] == r['evaluation_inputs'], 'selection and calibration use identical sources')
    return r

def resolved_native(path):
    files = [p for p in (Path(path) / 'metadata').glob('part-*') if p.is_file()]
    require(len(files) == 1, 'one native metadata file')
    m = read(files[0])
    return dict(m['defaultParamMap']) | m['paramMap']
