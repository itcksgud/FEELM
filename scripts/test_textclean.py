"""Critical alignment guard regressions for the follow-up, using synthetic rows."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
from text339_prepare import Writer
from textclean_run import OriginalRows

class AlignmentGuards(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'rows.parquet'
        rng=np.random.default_rng(44);self.x=rng.standard_normal((8200,554)).astype(np.float32);self.y=np.resize(np.arange(1,11)/2,8200)
        writer=Writer(self.path);writer.append(self.x,self.y,37);writer.close()
    def tearDown(self):self.tmp.cleanup()
    def test_batch_borders_do_not_change_alignment(self):
        reader=OriginalRows(self.path);reader.check(self.x[:8190],self.y[:8190],37);reader.check(self.x[8190:],self.y[8190:],37);reader.close()
    def test_changed_label_rejected(self):
        y=self.y.copy();y[8192]=5 if y[8192]!=5 else .5
        with self.assertRaises(RuntimeError):OriginalRows(self.path).check(self.x,y,37)
    def test_changed_structured_feature_rejected(self):
        x=self.x.copy();x[8192,17]+=1
        with self.assertRaises(RuntimeError):OriginalRows(self.path).check(x,self.y,37)
    def test_changed_user_rejected(self):
        with self.assertRaises(RuntimeError):OriginalRows(self.path).check(self.x,self.y,38)
    def test_missing_tail_rejected(self):
        reader=OriginalRows(self.path);reader.check(self.x[:8190],self.y[:8190],37)
        with self.assertRaises(RuntimeError):reader.close()
    def test_text_changes_allowed_only_in_treatment_columns(self):
        x=self.x.copy();x[:,230:338]+=1;x[:,446:554]-=1
        reader=OriginalRows(self.path);reader.check(x,self.y,37);reader.close()

if __name__=='__main__':unittest.main()
