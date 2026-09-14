"""Independent arithmetic fixtures, not a replay of real evaluation outcomes."""
import numpy as np
from research343_evaluate import affine,quality

def main():
    x=np.array([0.,1.,2.,3.]);a=affine(x,2+.5*x,np.ones(4))
    assert abs(a['a']-2)<1e-12 and abs(a['b']-.5)<1e-12
    assert affine(x,5-x,np.ones(4))['b']==0
    assert affine(np.ones(4),x,np.ones(4))['a']==1.5
    # User 1 has one row; user 2 has three. Each receives total weight one.
    a=affine(np.array([0.,0.,0.,0.]),np.array([1.,5.,5.,5.]),np.array([1.,1/3,1/3,1/3]))
    assert abs(a['a']-3)<1e-12
    y=np.array([.5,5.,2.,4.]);p=np.array([1.,4.,2.,3.]);ids=np.arange(4)
    q=quality(y,p,ids,2);assert q['stars']==4.5 and abs(q['ndcg']-1)<1e-12 and q['low']==0
    assert np.isnan(quality(np.array([.5,.5]),np.arange(2),np.arange(2),2)['ndcg'])
    assert np.isnan(quality(np.array([5.]),np.array([1.]),np.array([1]),2)['stars'])
    q=quality(y,p,ids,4,2);assert q['stars']==1.25 and q['both_low']==1
    q=quality(y,np.zeros(4),np.array([4,1,2,3]),1);assert q['stars']==5
    print('PASS calibration weighting, monotonicity, constant case, missing denominator, raw ranking, page arithmetic')

if __name__=='__main__':main()
