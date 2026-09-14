"""Policy edge cases, without model fits or real evaluation labels."""
import numpy as np
from policy341 import recommend

def main():
    ids=np.array([10,20,30,40,50,60,70,80,90,100]); scores=np.array([5,4.8,4.6,4.4,4.1,4,3.9,3.8,3.7,3.6])
    sim=np.array([.8,.7,.75,.5,.3,.8,.3,.8,.4,.9])
    base,_,_=recommend(scores,ids,sim,True,'P0')
    assert base.tolist()==list(range(9))
    for name in ['P1_010','P1_020','P2_010','P2_020']:
        selected,roles,blocks=recommend(scores,ids,sim,True,name)
        assert selected[:2].tolist()==[0,1] and selected[2]==3 and roles[2]=='DISCOVERY'
        assert len(selected)==len(set(selected))==9
        fallback,roles,blocks=recommend(scores,ids,sim,False,name)
        assert np.array_equal(fallback,base) and 'DISCOVERY' not in roles
        small,roles,blocks=recommend(scores[:3],ids[:3],sim[:3],True,name)
        assert small.tolist()==[0,1,2] and all(b['fallback'] for b in blocks)
    poor=scores.copy(); poor[3:]=scores[2]-.31-np.arange(7)/10
    a,_,_=recommend(poor,ids,sim,True,'P1_010'); b,_,_=recommend(poor,ids,sim,True,'P2_010')
    assert a[2]==3 and b[2]==2
    missing=np.full(len(sim),np.nan)
    assert np.array_equal(recommend(scores,ids,missing,False,'P2_010')[0],base)
    # Exact candidate-score and similarity boundaries, with representable increments.
    boundary_sim=np.array([.8,.8,.5,.25]); boundary_scores=np.array([5,4.8,4.6,4.6-.3])
    x,_,_=recommend(boundary_scores,ids[:4],boundary_sim,True,'P2_020'); assert x[2]==3
    boundary_scores[3]=np.nextafter(4.6-.3,-np.inf)
    x,_,_=recommend(boundary_scores,ids[:4],boundary_sim,True,'P2_020'); assert x[2]==2
    tied=np.array([5,4.8,4.6,4.4,4.4]); tied_ids=np.array([1,2,3,50,40]); tied_sim=np.array([.8,.8,.8,.4,.4])
    x,_,_=recommend(tied,tied_ids,tied_sim,True,'P2_010'); assert tied_ids[x[2]]==40
    for policy,third,candidate in [('P2_010',.7,.6),('P2_020',.7,.5)]:
        ss=np.array([.8,.8,third,candidate]); pp=np.array([5,4.8,4.6,4.4])
        assert recommend(pp,ids[:4],ss,True,policy)[0][2]==3
        ss[3]+=1e-10
        assert recommend(pp,ids[:4],ss,True,policy)[0][2]==2
    for n in [0,1,2]: assert len(recommend(scores[:n],ids[:n],sim[:n],False,'P0')[0])==n
    print('PASS policy fallback/connection/score-boundary/tie/no-duplicates/partial-slot tests')

if __name__=='__main__':main()
