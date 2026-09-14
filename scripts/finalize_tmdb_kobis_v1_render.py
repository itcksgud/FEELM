"""Preserve the initial draft, then adopt the visually checked figure-01 layout.

No new statistics or source reads beyond pinned file verification. The initial
analysis script/design and all numerical outputs are unchanged. The v2 companion
is retained as historical material; REPORT.md and data/ are the canonical paths.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs/research/tmdb-kobis-v1-20260913'
OUT = ROOT / 'outputs/recommendation-evidence/tmdb-kobis-v1-20260913'
HISTORY = OUT / 'render-history'


def pin(path):
    path = Path(path).resolve()
    return {'path': str(path), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n',
                          encoding='utf-8')


def main():
    manifest_path = OUT / 'manifest.json'
    initial = json.loads(manifest_path.read_text('utf-8'))
    require('render_fix' not in initial, 'do not overwrite a finalized manifest')
    require(not HISTORY.exists(), 'preserve any earlier render-history')
    for record in initial['outputs']:
        require(pin(record['path']) == record, 'initial output changed: ' + record['path'])
    original_csv = [r for r in initial['outputs'] if Path(r['path']).suffix == '.csv']
    require(len(original_csv) == 6, 'six numerical CSV files')

    v2_manifest_path = DOC / 'data-v2/manifest.json'
    v2 = json.loads(v2_manifest_path.read_text('utf-8'))
    for record in v2['outputs']:
        require(pin(record['path']) == record, 'v2 output changed: ' + record['path'])
    require(v2['new_statistical_analysis'] is False and
            v2['raw_ratings_rescanned'] is False, 'render-only companion')

    # The original draft is archived before any canonical file is changed.
    HISTORY.mkdir(parents=True)
    historical = []
    for source, target_name in [
        (manifest_path, 'initial-manifest.json'),
        (DOC / 'REPORT.md', 'initial-REPORT.md'),
        (DOC / 'data/01-counts-and-ratios.png', 'initial-01-counts-and-ratios.png'),
        (DOC / 'data/01-counts-and-ratios.svg', 'initial-01-counts-and-ratios.svg'),
    ]:
        target = HISTORY / target_name
        source_pin = pin(source)
        shutil.copyfile(source, target)
        target_pin = pin(target)
        require(source_pin['sha256'] == target_pin['sha256'], 'exact draft archive')
        historical.append({'original': source_pin, 'archived': target_pin})

    replacement_sources = []
    for suffix in ['png', 'svg']:
        name = '01-counts-and-ratios.' + suffix
        source = DOC / 'data-v2' / name
        target = DOC / 'data' / name
        replacement_sources.append(pin(source))
        shutil.copyfile(source, target)
        require(pin(source)['sha256'] == pin(target)['sha256'], 'exact checked render copy')

    final_outputs = [pin(r['path']) for r in initial['outputs']]
    changed = [Path(old['path']).name for old, new in zip(initial['outputs'], final_outputs)
               if old != new]
    require(sorted(changed) == ['01-counts-and-ratios.png', '01-counts-and-ratios.svg'],
            'only the two figure-01 exports may change')
    require(original_csv == [pin(r['path']) for r in original_csv], 'all CSV hashes unchanged')

    provenance = {
        'utc': datetime.now(timezone.utc).isoformat(),
        'reason': 'Initial draft figure-01 y-axis title extended beyond the original canvas.',
        'correction': 'Use bbox_inches=tight and pad_inches=.18; same CSVs, plotting function and axes.',
        'scope': 'Only current draft figure-01 PNG/SVG replaced. Other figures, REPORT and six CSVs unchanged.',
        'producer_visual_review': 'PASS: corrected PNG includes the full y-axis title; labels, legend, and footer readable.',
        'independent_result_review': 'PENDING; this rendering check is producer self-review.',
        'historical_draft': historical,
        'replacement_sources': replacement_sources,
        'render_script': pin(ROOT / 'scripts/render_tmdb_kobis_v1_figures_v2.py'),
        'finalize_script': pin(__file__),
        'retained_companion_manifest': pin(v2_manifest_path),
        'retained_companion_report': pin(DOC / 'REPORT-v2.md'),
        'canonical_report': str(DOC / 'REPORT.md'),
        'canonical_figures': str(DOC / 'data'),
        'numerical_csv_hashes_unchanged': True,
        'verified_numerical_csvs': original_csv,
        'raw_ratings_rescanned': False,
        'new_statistical_analysis': False,
        'new_network_requests': 0,
        'model_calls': 0,
    }
    dump(HISTORY / 'render-fix.json', provenance)
    final = deepcopy(initial)
    final['outputs'] = final_outputs
    final['render_fix'] = {
        'record': pin(HISTORY / 'render-fix.json'),
        'initial_manifest': pin(HISTORY / 'initial-manifest.json'),
        'canonical_report': str(DOC / 'REPORT.md'),
        'canonical_figure_directory': str(DOC / 'data'),
        'numerical_csv_hashes_unchanged': True,
        'changed_files': changed,
        'independent_result_review': 'PENDING',
    }
    dump(manifest_path, final)
    for record in final['outputs']:
        require(pin(record['path']) == record, 'final output pin')
    print(json.dumps({'status': 'RENDER_FIX_COMPLETE_NUMERICAL_OUTPUTS_UNCHANGED',
                      'manifest': pin(manifest_path), 'changed': changed}, indent=2))


if __name__ == '__main__':
    main()
