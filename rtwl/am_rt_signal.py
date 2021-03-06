import sys
import numpy as np
import scipy.stats as ss
from itertools import islice
from obspy.core.trace import Trace, UTCDateTime
from obspy.realtime.rtmemory import RtMemory

# submitted to obspy.realtime
# not not modify
#def offset(trace, offset=0.0, rtmemory_list=None):
#    """
#    Add the specified offset to the data.
#
#    :type trace: :class:`~obspy.core.trace.Trace`
#    :param trace: :class:`~obspy.core.trace.Trace` object to append to this RtTrace
#    :type offset: float, optional
#    :param offset: offset (default is 0.0)
#    :type rtmemory_list: list of :class:`~obspy.realtime.rtmemory.RtMemory`, optional
#    :param rtmemory_list: Persistent memory used by this process for specified trace
#    :rtype: Numpy :class:`numpy.ndarray`
#    :return: Processed trace data from appended Trace object
#    """
#
#    if not isinstance(trace, Trace):
#        msg = "Trace parameter must be an obspy.core.trace.Trace object."
#        raise ValueError(msg)
#
#    trace.data += offset
#    return trace.data
# end submitted code

def neg_to_zero(trace, rtmemory_list=None):
    """
    Set all negative values to zero

    :type trace: :class:`~obspy.core.trace.Trace`
    :param trace: :class:`~obspy.core.trace.Trace` object to append to this RtTrace
    :type rtmemory_list: list of :class:`~obspy.realtime.rtmemory.RtMemory`, optional
    :param rtmemory_list: Persistent memory used by this process for specified trace
    :rtype: Numpy :class:`numpy.ndarray`
    :return: Processed trace data from appended Trace object
    """

    if not isinstance(trace, Trace):
        msg = "Trace parameter must be an obspy.core.trace.Trace object."
        raise ValueError(msg)

    trace.data[trace.data < 0.0] = 0.0
    return trace.data

def mean(trace, win=1.0, rtmemory_list=None):
    """
    Calculate recursive mean. win is a window length
    """

    if not isinstance(trace, Trace):
        msg = "Trace parameter must be an obspy.core.trace.Trace object."
        raise ValueError(msg)
    
    # if this is the first appended trace, the rtmemory_list will be None
    if not rtmemory_list:
        rtmemory_list = [RtMemory()]

    # deal with case of empty trace
    sample = trace.data
    dt = trace.stats.delta
    if np.size(sample) < 1:
        return sample

    # get simple info from trace
    npts=len(sample)

    # prepare the output array
    mu1 = np.empty(npts, sample.dtype)

    # prepare the rt memory
    rtmemory_mu1 = rtmemory_list[0]

    if not rtmemory_mu1.initialized:
        memory_size_input  = 1
        memory_size_output = 0
        rtmemory_mu1.initialize(sample.dtype, memory_size_input,\
                                memory_size_output, 0, 0)

    # initialize from memory

    mu1_last = rtmemory_mu1.input[0]

    C1 = dt/float(win)
    a1 = 1-C1

    # do recursive mean
    for i in xrange(npts):
        mu1[i] = a1*mu1_last + C1*sample[i]
        mu1_last=mu1[i]

    # save to memory
    rtmemory_mu1.input[0] = mu1_last

    return mu1

def variance(trace, win=1.0, rtmemory_list=None):
    """
    Calculate recursive variance. Win is a window in seconds.
    """

    if not isinstance(trace, Trace):
        msg = "Trace parameter must be an obspy.core.trace.Trace object."
        raise ValueError(msg)
    
    # if this is the first appended trace, the rtmemory_list will be None
    if not rtmemory_list:
        rtmemory_list = [RtMemory(), RtMemory()]

    # deal with case of empty trace
    # are going to need double precision here
    sample = trace.data.astype('float64')
    if np.size(sample) < 1:
        return sample

    # get simple info from trace
    npts=len(sample)
    dt=trace.stats.delta

    # prepare the output array
    mu2 = np.empty(npts, sample.dtype)

    # prepare the rt memory
    rtmemory_mu1 = rtmemory_list[0]
    rtmemory_mu2 = rtmemory_list[1]

    if not rtmemory_mu1.initialized:
        memory_size_input  = 1
        memory_size_output = 0
        rtmemory_mu1.initialize(sample.dtype, memory_size_input,\
                                memory_size_output, 0, 0)

    if not rtmemory_mu2.initialized:
        memory_size_input  = 1
        memory_size_output = 0
        rtmemory_mu2.initialize(sample.dtype, memory_size_input,\
                                memory_size_output, sample[0]*sample[0], 0)
    # initialize from memory


    mu1_last = rtmemory_mu1.input[0]
    mu2_last = rtmemory_mu2.input[0]

    C1 = dt/float(win)
    a1 = 1-C1
    C2 = (1.0 - a1*a1)/2.0

    # do recursive mean
    for i in xrange(npts):
        mu1 = a1*mu1_last + C1*sample[i]
        mu1_last=mu1
        dx2 = (sample[i]-mu1_last)*(sample[i]-mu1_last)
        mu2[i] = a1*mu2_last + C2*dx2
        mu2_last=mu2[i]

    # save to memory
    rtmemory_mu1.input[0] = mu1_last
    rtmemory_mu2.input[0] = mu2_last


    return mu2

def dx2(trace, win=1.0, rtmemory_list=None):
    """
    Calculate recursive variance. C is a scaling constant
    """

    if not isinstance(trace, Trace):
        msg = "Trace parameter must be an obspy.core.trace.Trace object."
        raise ValueError(msg)
    
    # if this is the first appended trace, the rtmemory_list will be None
    if not rtmemory_list:
        rtmemory_list = [RtMemory(), RtMemory()]

    # deal with case of empty trace
    # are going to need double precision here
    sample = trace.data.astype('float64')
    if np.size(sample) < 1:
        return sample

    # get simple info from trace
    npts=len(sample)
    dt=trace.stats.delta

    # prepare the output array
    dx2 = np.empty(npts, sample.dtype)

    # prepare the rt memory
    rtmemory_mu1 = rtmemory_list[0]
    rtmemory_mu2 = rtmemory_list[1]

    if not rtmemory_mu1.initialized:
        memory_size_input  = 1
        memory_size_output = 0
        rtmemory_mu1.initialize(sample.dtype, memory_size_input,\
                                memory_size_output, 0, 0)

    if not rtmemory_mu2.initialized:
        memory_size_input  = 1
        memory_size_output = 0
        rtmemory_mu2.initialize(sample.dtype, memory_size_input,\
                                memory_size_output, sample[0]*sample[0], 0)
    # initialize from memory


    mu1_last = rtmemory_mu1.input[0]
    mu2_last = rtmemory_mu2.input[0]

    C1 = dt/float(win)
    a1 = 1.0-C1
    C2 = (1.0 - a1*a1)/2.0

    # do recursive mean
    for i in xrange(npts):
        mu1 = a1*mu1_last + C1*sample[i]
        mu1_last=mu1
        dx2[i] = (sample[i]-mu1_last)*(sample[i]-mu1_last)
        mu2 = a1*mu2_last + C2*dx2[i]
        dx2[i] = dx2[i] / mu2_last
        mu2_last=mu2

    # save to memory
    rtmemory_mu1.input[0] = mu1_last
    rtmemory_mu2.input[0] = mu2_last


    return dx2


def convolve(trace, conv_signal=None, rtmemory_list=None):
    """
    Convolve data with a (complex) signal (conv_signal). 

    Note that the signal length should be odd. For a signal of (2N+1) points,
    the resulting output trace will be time shifted by -N*dt where dt is the
    sampling rate of the trace. You may want to consider shifting the trace
    starttime by the same amount before appending to this RtTrace ::

        tshift = N*dt
        tr.stats.starttime -= tshift
        someRtTrace.append(tr)

    :type trace: :class:`~obspy.core.trace.Trace`
    :param trace: :class:`~obspy.core.trace.Trace` object to append to this RtTrace
    :type conv_signal: :class:`numpy.ndarray`, optional
    :param conv_signal: signal with which to perform convolution
    :type rtmemory_list: list of :class:`~obspy.realtime.rtmemory.RtMemory`, optional
    :param rtmemory_list: Persistent memory used by this process for specified trace
    :rtype: Numpy :class:`numpy.ndarray`
    :return: Processed trace data from appended Trace object
    """

    if not isinstance(trace, Trace):
        msg = "Trace parameter must be an obspy.core.trace.Trace object."
        raise ValueError(msg)

    if conv_signal == None :
        return trace

    if not rtmemory_list:
        rtmemory_list=[RtMemory()]

    # deal with case of empty trace
    sample = trace.data
    if np.size(sample) < 1:
        return sample

    flen=len(conv_signal)
    flen2=(flen-1)/2
    mem_size=3*flen2+1

    rtmemory=rtmemory_list[0]
    if not rtmemory.initialized:
        first_trace=True
        memory_size_input  = mem_size
        memory_size_output = 0
        rtmemory.initialize(sample.dtype, memory_size_input,\
                                memory_size_output, 0, 0)


    # make an array of the right dimension
    x=np.empty(len(sample)+mem_size, dtype=complex)
    # fill it up partly with the memory, partly with the new data
    x[0:mem_size]=rtmemory.input[:]
    x[mem_size:]=sample[:]

    # do the convolution
    x_new = np.real(np.convolve(x,conv_signal,'same'))
    i_start=mem_size-flen2
    i_end=i_start+len(sample)
    
    # put new data into memory for next trace
    
    rtmemory.updateInput(sample)

    return x_new[i_start:i_end]

def _window(seq, n=2):
    "Returns a sliding window (of width n) over data from the iterable"
    "   s -> (s0,s1,...s[n-1]), (s1,s2,...,sn), ...                   "
    it = iter(seq)
    result = tuple(islice(it, n))
    if len(result) == n:
        yield result    
    for elem in it:
        result = result[1:] + (elem,)
        yield result


def sw_kurtosis(trace, win=3.0, rtmemory_list=None):
    """
    Compute kurtosis using a sliding window method. Calls scipy.stats.kurtosis

    :type trace: :class:`~obspy.core.trace.Trace`
    :param trace: :class:`~obspy.core.trace.Trace` object to append to this RtTrace
    :type win: float
    :param win: sliding window length in seconds
    :type rtmemory_list: list of :class:`~obspy.realtime.rtmemory.RtMemory`, optional
    :param rtmemory_list: Persistent memory used by this process for specified trace
    :rtype: Numpy :class:`numpy.ndarray`
    :return: Processed trace data from appended Trace object
    """

    if not isinstance(trace, Trace):
        msg = "Trace parameter must be an obspy.core.trace.Trace object."
        raise ValueError(msg)

    if not rtmemory_list:
        rtmemory_list=[RtMemory()]

    # deal with case of empty trace
    sample = trace.data
    if np.size(sample) < 1:
        return sample

    # get info from trace
    dt=trace.stats.delta
    npts=int(np.round(win/float(dt)))

    # set up temporary array for kurtosis and output array
    k_array=np.empty((len(sample),npts),dtype=sample.dtype)
    x_out=np.empty(len(sample))

    rtmemory=rtmemory_list[0]
    mem_size=npts-1
    if not rtmemory.initialized:
        sample_start=sample[0:mem_size]
        first_trace=True
        memory_size_input  = mem_size
        memory_size_output = 0
        rtmemory.initialize(sample.dtype, memory_size_input, memory_size_output)
        rtmemory.input=sample_start[::-1]


    # make an array of the right dimension
    x=np.empty(len(sample)+mem_size, dtype=sample.dtype)
    # fill it up partly with the memory, partly with the new data
    x[0:mem_size]=rtmemory.input[:]
    x[mem_size:]=sample[:]

    # do the sliding window kurtosis
    windows=_window(x,npts)
    i=0
    for w in windows:
        k_array[i,0:npts]=w
        i=i+1
    xout = ss.kurtosis(k_array,axis=1)

    
    # put new data into memory for next trace
    
    rtmemory.updateInput(sample)

    return xout

