"""Research T2/D1 selection and missing-label bounds; never treats scores as probabilities."""
from __future__ import annotations
import math
import numpy as np

POLICIES = ('CONNECTED', 'T3_GUARD')


def select_policy(order, scores, taste, discovery, excluded, policy):
    if policy not in POLICIES:
        raise ValueError('unknown discovery policy')
    order = np.asarray(order, dtype=np.int64)
    scores = np.asarray(scores, dtype=np.float64)
    taste, discovery, excluded = map(lambda a: np.asarray(a,dtype=bool), (taste,discovery,excluded))
    if (order.ndim!=1 or scores.shape!=order.shape or len(set(order.tolist()))!=len(order)
            or taste.ndim!=1 or discovery.shape!=taste.shape or excluded.shape!=taste.shape
            or (order<0).any() or (order>=len(taste)).any() or not np.isfinite(scores).all()
            or (np.diff(scores)>0).any() or (taste&discovery).any()):
        raise ValueError('invalid ordered pool or role masks')
    available = ~excluded[order]
    t = [(int(p),float(s)) for p,s in zip(order[available],scores[available]) if taste[p]]
    d = [(int(p),float(s)) for p,s in zip(order[available],scores[available]) if discovery[p]]
    top_t=t[:3]; best_d=d[0] if d else None
    reference=t[2] if len(t)>=3 else None
    result={'policy':policy, 'taste2': [p for p,s in t[:2]], 'selected':[], 'types':[],
            'T_count':len(t),'D_structural_count':len(d), 'valid_three':False,
            'reference_position':reference[0] if reference else None,
            'reference_score':reference[1] if reference else None,
            'best_D_position':best_d[0] if best_d else None,
            'best_D_score':best_d[1] if best_d else None,
            'guard_D_count':sum(s>=reference[1] for p,s in d) if policy=='T3_GUARD' and reference else None,
            'guard_status':'NOT_REQUESTED' if policy=='CONNECTED' else ('REFERENCE_AVAILABLE' if reference else 'NO_T3_REFERENCE')}
    if len(t)<2:
        result.update(status='INSUFFICIENT_TASTE',selected=[p for p,s in t],types=['TASTE']*len(t))
    elif not d:
        result.update(status='NO_DISCOVERY_T3' if reference else 'NO_DISCOVERY_NO_T3',
                      selected=[p for p,s in top_t],types=['TASTE']*len(top_t),valid_three=bool(reference))
    elif policy=='CONNECTED' or (reference and best_d[1]>=reference[1]):
        result.update(status='T2_D1',selected=[p for p,s in t[:2]]+[best_d[0]],
                      types=['TASTE','TASTE','DISCOVERY'],valid_three=True)
        if policy=='T3_GUARD': result['guard_status']='PASS'
    elif reference:
        result.update(status='D_SCORE_GUARD_T3',selected=[p for p,s in top_t],types=['TASTE']*3,
                      valid_three=True,guard_status='LOWER_THAN_T3')
    else:
        result.update(status='NO_T3_REFERENCE',selected=[p for p,s in t[:2]],types=['TASTE']*2)
    return result


def quality_bounds(values, required):
    """Bounds concern missing labels of selected slots, not missing slots."""
    if len(values)!=required:
        return {'available':False,'known':0,'unknown':0,'mean_low':None,'mean_high':None,
                'min_low':None,'min_high':None,'harm_low':None,'harm_high':None}
    known=[float(v) for v in values if v is not None]
    if any(not math.isfinite(v) or not 0<=v<=1 for v in known):
        raise ValueError('Q must be in [0,1] or unknown')
    missing=required-len(known)
    harm=any(v<=.2 for v in known)
    return {'available':True,'known':len(known),'unknown':missing,
            'mean_low':sum(known)/required,'mean_high':(sum(known)+missing)/required,
            'min_low':0.0 if missing else min(known),'min_high':min(known) if known else 1.0,
            'harm_low':int(harm),'harm_high':int(harm or missing>0)}


def opportunity(values, eligible_count):
    if eligible_count<len(values) or any(not math.isfinite(v) or not 0<=v<=1 for v in values):
        raise ValueError('invalid opportunity labels/count')
    low=sum(v<=.2 for v in values); high=sum(v>=.8 for v in values)
    unknown=eligible_count-len(values)
    state=('NO_ELIGIBLE' if not eligible_count else 'OBSERVED_HIGH_EXISTS' if high else
           'HIGH_EXISTENCE_UNKNOWN' if unknown else 'FULLY_LABELED_NO_HIGH')
    return {'eligible':eligible_count,'known':len(values),'unknown':unknown,'known_low':low,
            'known_high':high,'known_nonlow':len(values)-low,'high_existence':state}
