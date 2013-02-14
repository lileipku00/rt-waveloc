import unittest, os, glob
import numpy as np
import am_rt_signal
from numpy.testing import assert_array_almost_equal
from obspy.realtime import RtTrace 
from obspy import read


def suite():
    suite = unittest.TestSuite()
    suite.addTest(RtTests('test_rt_offset'))
    suite.addTest(RtTests('test_rt_scale'))
    suite.addTest(RtTests('test_rt_kurtosis'))
    suite.addTest(RtTests('test_rt_neg_to_zero'))
    suite.addTest(RtTests('test_rt_kurt_grad'))
    return suite

    
class RtTests(unittest.TestCase):

    def setUp(self):
        import obspy.realtime
        rt_dict= obspy.realtime.rttrace.REALTIME_PROCESS_FUNCTIONS
        rt_dict['offset']=(am_rt_signal.offset,0)
        rt_dict['kurtosis']=(am_rt_signal.kurtosis,3)
        rt_dict['neg_to_zero']=(am_rt_signal.neg_to_zero,0)

        # set up traces
        self.data_trace = read('test_data/YA.UV15.00.HHZ.MSEED')[0]
        # note : we are going to produce floating point output, so we need
        # floating point input seismograms
        x=self.data_trace.data.astype(np.float32)
        self.data_trace.data=x
        self.traces = self.data_trace / 3

    def test_rt_offset(self):

        offset=500

        rt_trace=RtTrace()
        rt_trace.registerRtProcess('offset',offset=offset)

        for tr in self.traces:
            rt_trace.append(tr, gap_overlap_check = True)


        diff=self.data_trace.copy()
        diff.data=rt_trace.data-self.data_trace.data
        self.assertAlmostEquals(np.mean(np.abs(diff)),offset)

    def test_rt_scale(self):

        data_trace = self.data_trace.copy()

        fact=1/np.std(data_trace.data)

        data_trace.data *= fact

        rt_trace=RtTrace()
        rt_trace.registerRtProcess('scale',factor=fact)

        for tr in self.traces:
            rt_trace.append(tr, gap_overlap_check = True)

        diff=self.data_trace.copy()
        diff.data=rt_trace.data-data_trace.data
        self.assertAlmostEquals(np.mean(np.abs(diff)),0.0)


    # skip this if not on a machine with a recent waveloc installed
    def test_rt_kurtosis(self):
        from waveloc.filters import rec_kurtosis
        win=3.0
        data_trace = self.data_trace.copy()

        sigma=float(np.std(data_trace.data))
        fact = 1/sigma

        dt=data_trace.stats.delta
        C1=dt/float(win)

        x=data_trace.data
        ktrace=data_trace.copy()
        ktrace.data=rec_kurtosis(x*fact,C1)

        rt_trace=RtTrace()
        rt_trace.registerRtProcess('scale',factor=fact)
        rt_trace.registerRtProcess('kurtosis',win=win)

        for tr in self.traces:
            rt_trace.append(tr, gap_overlap_check = True)
   
        diff=self.data_trace.copy()
        diff.data=rt_trace.data-ktrace.data
        self.assertAlmostEquals(np.mean(np.abs(diff)),0.0)

    def test_rt_neg_to_zero(self):

        data_trace=self.data_trace.copy()
        max_val=np.max(data_trace.data)
        
        rt_trace=RtTrace()
        rt_trace.registerRtProcess('neg_to_zero')

        for tr in self.traces:
            rt_trace.append(tr, gap_overlap_check = True)

        max_val_test=np.max(rt_trace.data)
        min_val_test=np.min(rt_trace.data)
        self.assertEqual(max_val, max_val_test)
        self.assertEqual(0.0, min_val_test)

    def test_rt_kurt_grad(self):
        win=3.0
        data_trace = self.data_trace.copy()

        sigma=float(np.std(data_trace.data))
        fact = 1/sigma

        rt_trace=RtTrace()
        rt_trace_single = RtTrace()

        for rtt in [rt_trace, rt_trace_single]:
            rtt.registerRtProcess('scale',factor=fact)
            rtt.registerRtProcess('kurtosis',win=win)
            rtt.registerRtProcess('differentiate')
            rtt.registerRtProcess('neg_to_zero')

        rt_trace_single.append(data_trace)
        
        for tr in self.traces:
            rt_trace.append(tr, gap_overlap_check = True)

        rt_trace.plot()
        diff=self.data_trace.copy()
        diff.data=rt_trace_single.data - rt_trace.data
        self.assertAlmostEquals(np.mean(np.abs(diff)), 0.0, 5)

if __name__ == '__main__':

  import logging
  logging.basicConfig(level=logging.INFO, format='%(levelname)s : %(asctime)s : %(message)s')
 
  unittest.TextTestRunner(verbosity=2).run(suite())
 
