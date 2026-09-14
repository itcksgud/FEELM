"""Layout-only v2 export from sealed CSVs; preserves initial analysis output."""
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
import pandas as pd

import report_tmdb_kobis_v1 as source

ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/'docs/research/tmdb-kobis-v1-20260913'
DATA=DOC/'data'
NEW=DOC/'data-v2'
REPORT=DOC/'REPORT-v2.md'
OUT=ROOT/'outputs/recommendation-evidence/tmdb-kobis-v1-20260913'


def main():
    source.require(not NEW.exists() and not REPORT.exists(),'preserve previous layout versions')
    manifest=json.loads((OUT/'manifest.json').read_text('utf-8'))
    for record in manifest['outputs']:
        source.require(source.pin(record['path'])==record,'original output pin '+record['path'])
    paths=[DATA/n for n in ['tmdb-kobis-pairs.csv','movielens-same-film-rating-sd.csv',
        'count-and-rating-relations.csv','between-film-rating-distributions.csv']]+[DOC/'REPORT.md',OUT/'manifest.json',ROOT/'scripts/report_tmdb_kobis_v1.py']
    before=[source.pin(p) for p in paths]
    tk=pd.read_csv(paths[0]);b=pd.read_csv(paths[1]);counts=pd.read_csv(paths[2]);dispersion=pd.read_csv(paths[3])
    source.require(len(tk)==371 and len(b)==337 and int(b.ml_within_film_sd_supported.sum())==334,'same populations')
    NEW.mkdir(parents=True)
    original_save=Figure.savefig
    def export_with_bounds(figure,path,*args,**kwargs):
        # Include labels outside the canvas bounding box; no data/axis changes.
        kwargs['bbox_inches']='tight';kwargs['pad_inches']=.18
        return original_save(figure,path,*args,**kwargs)
    Figure.savefig=export_with_bounds
    old_dir=source.DATA
    try:
        source.DATA=NEW
        source.plot_all(tk,b,counts,dispersion)
    finally:
        source.DATA=old_dir
        Figure.savefig=original_save
    original_text=(DOC/'REPORT.md').read_text('utf-8')
    final_text=original_text
    for name in ['01-counts-and-ratios','02-scale-imbalance','03-rating-relations','04-rating-dispersion']:
        final_text=final_text.replace(f'(data/{name}.png)',f'(data-v2/{name}.png)')
    final_text=final_text.replace('PNG와 같은 이름의 SVG도 `data/`에','PNG와 같은 이름의 SVG도 `data-v2/`에')
    final_text += '\n그림의 바깥쪽 축 제목을 포함하도록 여백만 보완한 v2 문서다; 원 통계·초기 보고서·봉인은 보존했다. [렌더링 기록](data-v2/manifest.json).\n'
    REPORT.write_text(final_text,encoding='utf-8')
    source.require(before==[source.pin(p) for p in paths],'source values preserved')
    source.dump(NEW/'manifest.json',{'status':'LAYOUT_V2_GENERATED_PENDING_VISUAL_REVIEW','inputs':before,
        'script':source.pin(__file__),'outputs':[source.pin(p) for p in sorted(NEW.iterdir())]+[source.pin(REPORT)],
        'changes':'bbox_inches=tight and pad_inches=.18 only; same plotting function, CSV values and axes',
        'new_statistical_analysis':False,'raw_ratings_rescanned':False,'new_network_requests':0,'model_calls':0})
    print(NEW)


if __name__=='__main__':main()
