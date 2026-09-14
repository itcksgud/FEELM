"""Assign new normalized JSON records with a verified frozen artifact.

python scripts/k8_assign.py --bundle outputs/fixed-k8-discovery/final --input movies.json --output assigned.json
JSON array fields: service_movie_id optional, genre_ids integer list, keyword_ids
integer list, overview string. Absent/null lists and absent/null overview are empty.
Frozen assignment uses no catalog lookup, current size, ratings or training call.
Only load this locally trusted pickle after manifest verification.
"""
import argparse
import pickle
import sys
import numpy as np
import pandas as pd
import sklearn
import scipy
import platform
from k8_common import assign,pin,read,write

def load_bundle(directory):
    from pathlib import Path
    directory=Path(directory);manifest=read(directory/'manifest.json')
    assert pin(directory/'bundle.pkl')==manifest['bundle'],'bundle integrity'
    assert pin(Path(__file__).with_name('k8_common.py'))==manifest['inference_code'],'inference implementation changed'
    assert {'numpy':np.__version__,'sklearn':sklearn.__version__,'scipy':scipy.__version__,'python':platform.python_version()}==manifest['runtime'],'frozen numeric runtime mismatch'
    with (directory/'bundle.pkl').open('rb') as f:return pickle.load(f)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    frame=pd.DataFrame(read(a.input));bundle=load_bundle(a.bundle)
    if frame.empty:result=[]
    else:
        values=assign(frame,bundle);output=pd.DataFrame(values)
        if 'service_movie_id' in frame:output.insert(0,'service_movie_id',frame.service_movie_id)
        output['taste_name']=[bundle['top_names'][str(int(i))] for i in output.taste_id]
        output['group_name']=[bundle['group_names'][str(int(i))] for i in output.group_id]
        output['model_version']=bundle['version'];result=output.to_dict(orient='records')
    write(a.output,result)
