"""Prepare approved read-only RH inputs, then run one bounded fit without retries."""
import argparse
import datetime
import subprocess
import time
import traceback
import shutil
import os
for key in ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[key] = '4'
import numpy as np
import pandas as pd
from final344_common import *
from combination340_models import Trees

def prepare():
    reviewed('fit'); OUT.mkdir(exist_ok=True, parents=True)
    require(not (OUT / 'input-lock.json').exists(), 'preserve preparation')
    for name, expected in SOURCE_ANCHORS.items():
        require(pin(BASE / name)['sha256'] == expected, 'approved historical source anchor: ' + name)
    pins = {}
    for folder, seal_name, names in [
        (OLD, 'prepared-seal.json', ['ratings.parquet', 'episodes.parquet', 'contexts.json', 'catalog.parquet', 'texts.parquet']),
        (FOUND, 'prepared-seal.json', ['RH/train.parquet', 'RH/score.parquet', 'RH/feature-info.json']),
        (FOUND, 'fit-seal.json', ['predictions.npz']),
        (COMBO, 'fit-seal.json', ['predictions.npz', 'GBT/partition-identity.json'])]:
        record = read(folder / seal_name)
        pins[(folder / seal_name).relative_to(ROOT).as_posix()] = pin(folder / seal_name)
        for name in names:
            p = folder / name; value = pin(p)
            require(value == record['files'][name], 'sealed source: ' + name)
            pins[p.relative_to(ROOT).as_posix()] = value
    for p in [LEGACY / 'roles.csv', BASE / 'rec-ev-045/metadata.parquet',
              LEGACY / 'calibration-seal.json', LEGACY / 'calibration.json',
              BASE / 'final344-availability-audit/result.json',
              BASE / 'final344-availability-audit/existing-rh-input-review.json',
              BASE / 'final344-availability-audit/adapter-review.json',
              BASE / 'final344-availability-audit/training-contribution.json',
              FOUND / 'RH/model/partition-identity.json']:
        pins[p.relative_to(ROOT).as_posix()] = pin(p)
    cfg = read(DOC / 'config.json')
    for family, path in [('FM', FOUND / 'RH/model/native'), ('GBT', COMBO / 'GBT/native')]:
        require(resolved_native(path) == cfg['resolved_reference'][family], 'native full resolved params: ' + family)
        for p in (path / 'metadata').glob('part-*'):
            if p.is_file(): pins[p.relative_to(ROOT).as_posix()] = pin(p)
    require(read(DOC / 'execution.json')['evaluation_scope'] == 'DEVELOPMENT_ONLY', 'approved availability path')
    require(pin(BASE / 'final344-availability-audit/result.json') == read(DOC / 'execution.json')['availability_audit'], 'reviewed availability audit')
    require(pin(LEGACY / 'roles.csv') == {'bytes': 5806, 'sha256': '466b7cede9cb2d67f2bd7fca4fcdada770943bdd9d5924c35ccfdbe6a4cf2cc9'}, 'approved exact reused roles')
    roles = pd.read_csv(LEGACY / 'roles.csv')
    require(roles.uid.is_unique and roles.groupby('role').size().to_dict() == {'calibration': 90, 'comparison': 180}, 'fixed development roles')
    trainuids = pd.read_parquet(OLD / 'ratings.parquet', columns=['uid']).uid.unique()
    require(len(trainuids) == 39859 and not set(trainuids) & set(roles.uid), 'current train/development UID separation')
    shutil.copyfile(LEGACY / 'roles.csv', OUT / 'roles.csv')
    pins[(OUT / 'roles.csv').relative_to(ROOT).as_posix()] = pin(OUT / 'roles.csv')
    write_json(OUT / 'input-lock.json', {'files': pins, 'training_users': 39859, 'training_rows': 4997069,
                                       'development_users': 270, 'evaluation_scope': 'DEVELOPMENT_ONLY',
                                       'evaluation_labels_read': False})
    print('PREPARED', flush=True)

def portable(root, recipe, x):
    if recipe.startswith('GBT'): return Trees(root / 'model/native', range(230)).predict(x)
    a = np.load(root / 'model/portable.npz'); xx = np.asarray(x, np.float64)
    f, w = a['factors'].astype(float), a['linear'].astype(float)
    require(np.array_equal(a['indices'], np.arange(230)), 'FM feature identity')
    return float(a['intercept']) + xx @ w + .5 * ((xx @ f)**2 - (xx * xx) @ (f * f)).sum(1)

def running_containers():
    result = subprocess.run(['docker', 'ps', '--filter', 'name=final344-', '--format', '{{.Names}}'],
                            capture_output=True, text=True, timeout=20, check=True)
    return [n for n in result.stdout.splitlines() if n.startswith('final344-')]

def terminate_container(name):
    """Stop only this experiment's exact container and prove it is no longer running."""
    attempts = []
    for action in (['stop', '--time', '2'], ['kill']):
        try:
            if name not in running_containers(): return {'confirmed_stopped': True, 'attempts': attempts}
        except Exception as e:
            attempts.append({'action': 'inspect-before-' + action[0], 'error': repr(e)})
        try:
            r = subprocess.run(['docker', *action, name], capture_output=True, text=True, timeout=20)
            attempts.append({'action': action, 'returncode': r.returncode})
        except Exception as e:
            attempts.append({'action': action, 'error': repr(e)})
    try:
        stopped = name not in running_containers()
    except Exception as e:
        attempts.append({'action': 'inspect-final', 'error': repr(e)}); stopped = False
    return {'confirmed_stopped': stopped, 'attempts': attempts}

def fit(recipe, seed):
    reviewed('fit'); lock(); cfg = read(DOC / 'config.json')
    require(not (OUT / 'cleanup-blocker.json').exists(), 'unresolved cleanup blocks subsequent attempts')
    require(not running_containers(), 'no overlapping experiment containers')
    require(not any(not (p.parent / 'outcome.json').exists() for p in OUT.glob('*/command.json')), 'unfinished attempt blocks later fits')
    family, iterations = RECIPES[recipe]; require(seed in SEEDS, 'fixed seed')
    fit_id = recipe + '_s' + str(seed)
    if seed != 339:
        verify('selection-seal.json')
        s = read(OUT / 'selection.json')
        require(all(s['selected'].get(f) in RECIPES and RECIPES[s['selected'][f]][0] == f for f in ('FM', 'GBT')), 'both baseline families must support selection')
        for initial in [r + '_s339' for r in RECIPES]:
            verify(initial + '-seal.json')
            require((OUT / initial / 'outcome.json').exists(), 'all four initial attempts completed before extra seeds')
            require(read(OUT / 'selection-seal.json')['evaluation_inputs']['fit_seals'][initial] == pin(OUT / (initial + '-seal.json')), 'selection uses these exact initial fits')
        require(s['selected'].get(family) == recipe, 'only selected recipe may receive extra seeds')
    require(len(list(OUT.glob('*/command.json'))) < 8, 'maximum eight attempts including failures')
    started_fingerprint = fingerprint('fit'); started_lock = pin(OUT / 'input-lock.json')
    require(subprocess.check_output(['docker', 'image', 'inspect', cfg['docker_image'], '--format', '{{.Id}}'], text=True).strip() == cfg['image_id'], 'pinned Docker image')
    root = OUT / fit_id; root.mkdir(exist_ok=False)
    name = 'final344-' + fit_id.lower().replace('_', '-')
    cmd = ['docker', 'run', '--rm', '--name', name, '--network', 'none', '--hostname', 'final344', '--add-host', 'final344:127.0.0.1',
           '-e', 'SPARK_LOCAL_IP=127.0.0.1', '--cpus', '4', '--memory', '12g', '--memory-swap', '12g',
           '--mount', f'type=bind,source={ROOT / "scripts"},target=/scripts,readonly',
           '--mount', f'type=bind,source={FOUND / "RH"},target=/base,readonly',
           '--mount', f'type=bind,source={root},target=/data',
           '--mount', f'type=bind,source={DOC},target=/config,readonly', cfg['docker_image'],
           '/opt/spark/bin/spark-submit', '--master', 'local[4]', '--driver-memory', '8g',
           '--conf', 'spark.sql.shuffle.partitions=8', '--conf', 'spark.sql.adaptive.enabled=false',
           '--conf', 'spark.ui.enabled=false', '/scripts/final344_worker.py', recipe, str(seed)]
    write_json(root / 'command.json', {'command': cmd, 'fingerprint': started_fingerprint,
                                      'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    t = time.monotonic(); status = 'FAILED'; error = None; returncode = None; resource = 'UNKNOWN'
    p = None; cleanup = {'confirmed_stopped': False}
    try:
        with (root / 'run.log').open('x', encoding='utf-8') as log:
            p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            returncode = p.wait(timeout=cfg['fit_timeout_seconds'])
    except subprocess.TimeoutExpired:
        status = 'TIMEOUT'; error = 'worker process exceeded fixed 5400-second budget'
    except BaseException:
        error = traceback.format_exc()
    finally:
        try:
            cleanup = terminate_container(name)
        except Exception:
            cleanup = {'confirmed_stopped': False, 'error': traceback.format_exc()}
        if p is not None and p.poll() is None:
            p.kill()
            try: returncode = p.wait(timeout=10)
            except Exception: pass
        if not cleanup['confirmed_stopped']:
            write_json(OUT / 'cleanup-blocker.json', {'fit_id': fit_id, 'container': name, 'cleanup': cleanup})
            status = 'FAILED'; error = (error or '') + '\nContainer termination unconfirmed; subsequent fits blocked.'
    if returncode == 0 and error is None and cleanup['confirmed_stopped']:
        try:
            require(read(root / 'model/partition-identity.json') == cfg['partition_identity'], 'same ordered RH partitions')
            expected = dict(cfg['resolved_reference'][family]); expected.update(seed=seed, maxIter=iterations)
            require(resolved_native(root / 'model/native') == expected, 'only approved native parameter changes')
            frame = pd.read_parquet(root / 'model/predictions').sort_values('row_id')
            require(np.array_equal(frame.row_id, np.arange(93230)) and np.isfinite(frame.prediction).all(), 'complete finite aligned predictions')
            x = pd.read_parquet(FOUND / 'RH/score.parquet', columns=[f'x{i:03d}' for i in range(230)]).to_numpy(np.float32)
            error_max = float(abs(portable(root, recipe, x) - frame.prediction.to_numpy()).max())
            boundary = None
            if family == 'GBT':
                fixture = np.load(root / 'model/threshold-fixtures.npz')
                boundary = float(abs(portable(root, recipe, fixture['features']) - fixture['predictions']).max())
            require(error_max <= 1e-8 and (boundary is None or boundary <= 1e-8), 'native/portable parity')
            np.save(root / 'predictions.npy', frame.prediction.to_numpy())
            write_json(root / 'checks.json', {'observed_max_error': error_max, 'boundary_max_error': boundary,
                                             'partition_equal': True, 'params_equal': True})
            resource = read(root / 'model/metrics.json')['resource_status']
            status = 'SUCCESS'
        except Exception:
            status = 'AUDIT_FAILED'; error = traceback.format_exc()
    elif error is None:
        error = 'worker exited with code ' + str(returncode) + '; partial files preserved, no retry'
    reviewed('fit'); lock()
    require(started_fingerprint == fingerprint('fit') and started_lock == pin(OUT / 'input-lock.json'), 'no execution drift')
    write_json(root / 'outcome.json', {'status': status, 'recipe': recipe, 'seed': seed, 'fit_id': fit_id,
                                      'resource_status': resource, 'returncode': returncode, 'error': error,
                                      'cleanup': cleanup,
                                      'wrapper_seconds': time.monotonic() - t,
                                      'completed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    seal(fit_id + '-seal.json', [p.relative_to(OUT).as_posix() for p in root.rglob('*') if p.is_file()], execution=started_fingerprint)
    print(fit_id, status, resource, error or '', flush=True)

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['prepare', 'fit'])
    p.add_argument('--recipe', choices=list(RECIPES)); p.add_argument('--seed', type=int, choices=SEEDS)
    a = p.parse_args()
    if a.action == 'prepare': prepare()
    else:
        require(a.recipe is not None and a.seed is not None, 'explicit recipe and seed required')
        fit(a.recipe, a.seed)
