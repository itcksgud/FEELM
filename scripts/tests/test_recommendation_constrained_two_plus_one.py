import unittest
import numpy as np
from recommendation_constrained_two_plus_one import policy
class ConstrainedTwoPlusOneTest(unittest.TestCase):
 def test_top_two_are_immutable(self):
  top=np.array([1,2,3,4]);u=np.array([1,2,3,4]);score=np.array([4.,3.,2.,1.]);nov=np.array([0.,0.,0.,1.]);g=np.eye(5);known=np.ones(5,dtype=bool)
  self.assertEqual([1,2],policy(top,score,u,nov,g,known,.1,4,.75)[:2].tolist())
 def test_unknown_genre_cannot_gain_diversity(self):
  top=np.array([1,2,3,4]);u=top.copy();score=np.ones(4);nov=np.zeros(4);g=np.zeros((5,2));g[1]=[1,0];g[2]=[1,0];g[4]=[0,1];known=np.array([False,True,True,False,True])
  self.assertEqual(4,int(policy(top,score,u,nov,g,known,.1,4,.75)[2]))
if __name__=='__main__':unittest.main()
