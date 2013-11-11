import logging, pika,time, glob, h5py
import multiprocessing
import numpy as np
from obspy.core import UTCDateTime, Trace
from obspy.realtime import RtTrace
from cPickle import dumps, loads
from rtwl_io import rtwlGetConfig, rtwlParseCommandLine

class rtwlPointStacker(object):
    def __init__(self,wo):
    
        self.dt = wo.opdict['dt']
        # initialize the travel-times        
        ttimes_fnames=glob.glob(wo.ttimes_glob)
        
        # get basic lengths by reading the first file
        f=h5py.File(ttimes_fnames[0],'r')
    
        # copy the x, y, z data over
        self.x = np.array(f['x'][:])
        self.y = np.array(f['y'][:])
        self.z = np.array(f['z'][:])
        f.close()
        
        # read the files
        ttimes_list = []
        self.sta_list=[]
        for fname in ttimes_fnames:
            f=h5py.File(fname,'r')
            # update the list of ttimes
            ttimes_list.append(np.array(f['ttimes']))
            sta=f['ttimes'].attrs['station']
            f.close()
            # update the list of station names
            self.sta_list.append(sta)
        
        # stack the ttimes into a numpy array
        self.ttimes_matrix=np.vstack(ttimes_list)
        (self.nsta,self.npts) = self.ttimes_matrix.shape
        
        # queue setup
        #self.connection, self.channel = _setupRabbitMQ()

        # nsta ndependent process for filling up points exchange
        for sta in self.sta_list :
            p = multiprocessing.Process(
                            name='distrib_%s'%sta,
                            target=self._do_distribute, 
                            args=(sta,)
                            )
            p.start()  
              
        # create a processor
        rtwlPointProcessor(wo,self.sta_list,self.npts)

    def _do_distribute(self,sta):
        proc_name = multiprocessing.current_process().name
        
        # queue setup
        connection, channel = _setupRabbitMQ() 
    
        # create one queue
        result = channel.queue_declare(exclusive=True)
        queue_name = result.method.queue

        # bind to all stations        
        channel.queue_bind(exchange='proc_data', 
                                    queue=queue_name, 
                                    routing_key=sta)
                                    
        consumer_tag=channel.basic_consume(
                                    self._callback_distribute,
                                    queue=queue_name,
                                    #no_ack=True
                                    )
        
        channel.basic_qos(prefetch_count=1)
        try:
            channel.start_consuming()
        except UserWarning:
            logging.log(logging.INFO,"Received STOP signal from %s"%proc_name)
            channel.basic_cancel(consumer_tag)

    def _callback_distribute(self, ch, method, properties, body):
        if body=='STOP' :
            sta=method.routing_key
            ch.basic_ack(delivery_tag = method.delivery_tag)
            ch.stop_consuming()
            # pass on to next exchange
            for ip in xrange(self.npts):
                ch.basic_publish(exchange='points',
                            routing_key='%s.%d'%(sta,ip),
                            body='STOP',
                            properties=pika.BasicProperties(delivery_mode=2,)
                            )
            raise UserWarning
        else:
            # unpack data packet 
            tr=loads(body)
            sta=method.routing_key
            ista=self.sta_list.index(sta)
            for ip in xrange(self.npts):
                # do shift
                tr.stats.starttime -= np.round(self.ttimes_matrix[ista][ip]/self.dt) * self.dt
                ch.basic_publish(exchange='points',
                            routing_key='%s.%d'%(sta,ip),
                            body=dumps(tr,-1),
                            properties=pika.BasicProperties(delivery_mode=2,)
                            )
            logging.log(logging.INFO,
                            " [D] Sent %r" % 
                            ("Distributed data for station %s"%sta))
            # acknowledge all ok            
            ch.basic_ack(delivery_tag = method.delivery_tag)
                
        
class rtwlPointProcessor(object):
    def __init__(self,wo,sta_list,npts) :
        
        # basic parameters
        self.wo = wo
        self.sta_list=sta_list
        self.nsta = len(sta_list)
        self.npts = npts
        self.max_length = self.wo.opdict['max_length']
        self.safety_margin = self.wo.opdict['safety_margin']
        self.dt = self.wo.opdict['dt']
        self.last_common_end_stack = [UTCDateTime(1970,1,1) for i in xrange(self.npts)]
        
        # need nsta streams for each point we test (nsta x npts)
        # for shifted waveforms
        self.point_rt_list=[[RtTrace(max_length=self.max_length) \
                for ista in xrange(self.nsta)] for ip in xrange(self.npts)]

        # register processing of point-streams here
        for sta_list in self.point_rt_list:
            for rtt in sta_list:
                # This is where we would scale for distance (given pre-calculated
                # distances from each point to every station)
                rtt.registerRtProcess('scale', factor=1.0)

        # need npts streams to store the point-stacks
        self.stack_list=[RtTrace(max_length=self.max_length) for ip in xrange(self.npts)]
        
        # register stack procesing here
        for rtt in self.stack_list:
            # This is where we would add or lower weights if we wanted to
            rtt.registerRtProcess('scale', factor=1.0)
        # rt streams must be indexed by station name
        self.pt_sta_dict = {}
        for sta in sta_list:
            self.pt_sta_dict[sta] = RtTrace(max_length=self.max_length)
               
        
        # independent process (for parallel calculation)
        for ip in xrange(self.npts):
#        for ip in xrange(1000):
            p = multiprocessing.Process(
                                        name='proc_%d'%ip,
                                        target=self._do_proc,
                                        args=(ip,))
            p.start()
        
            
    def _do_proc(self,ip):
        proc_name = multiprocessing.current_process().name
    
        # queue setup
        connection, channel = _setupRabbitMQ()
        
        # create one queue
        result = channel.queue_declare(exclusive=True)
        queue_name = result.method.queue

        # bind to all stations            
        channel.queue_bind(exchange='points', 
                                    queue=queue_name, 
                                    routing_key='*.%d'%ip)
                                    
        consumer_tag=channel.basic_consume(
                                    self._callback_proc,
                                    queue=queue_name,
                                    #no_ack=True
                                    )
        
        channel.basic_qos(prefetch_count=1)
        try:
            channel.start_consuming()
        except UserWarning:
            logging.log(logging.INFO,"Received UserWarning from %s"%proc_name)
            channel.basic_cancel(consumer_tag)

    def _callback_proc(self, ch, method, properties, body):
        if body=='STOP' :
            sta = method.routing_key.split('.')[0]
            ip = int(method.routing_key.split('.')[1])
            
            ch.basic_ack(delivery_tag = method.delivery_tag)
            ch.stop_consuming()
            # pass on to next exchange
            ch.basic_publish(exchange='stacks',
                            routing_key='%d'%ip,
                            body='STOP',
                            properties=pika.BasicProperties(delivery_mode=2,)
                            )
            raise UserWarning
        else:
            
            sta = method.routing_key.split('.')[0]
            ista=self.sta_list.index(sta)
            ip = int(method.routing_key.split('.')[1])

            # unpack data packet
            tr=loads(body)
            
            # add shifted trace
            self.point_rt_list[ip][ista].append(tr, gap_overlap_check = True)

            # update stack if possible
            self._updateStack(ip,ch)
            
            # acknowledge all ok
            ch.basic_ack(delivery_tag = method.delivery_tag)


    def _updateStack(self,ip,ch):
        
        UTCDateTime.DEFAULT_PRECISION=2
        
        nsta=self.nsta
        # get common start-time for this point
        common_start=max([self.point_rt_list[ip][ista].stats.starttime \
                 for ista in xrange(nsta)])
        common_start=max(common_start,self.last_common_end_stack[ip])
        # get list of stations for which the end-time is compatible
        # with the common_start time and the safety buffer
        ista_ok=[ista for ista in xrange(nsta) if 
                (self.point_rt_list[ip][ista].stats.endtime - common_start) 
                > self.safety_margin]
        
        # if got data for all stations then stack (TODO : make this more robust)
        if len(ista_ok)==self.nsta:
            
            # get common end-time
            common_end=min([ self.point_rt_list[ip][ista].stats.endtime for ista in ista_ok])
            self.last_common_end_stack[ip]=common_end+self.dt
            
            # prepare stack
            c_list=[]
            for ista in ista_ok:
                tr=self.point_rt_list[ip][ista].copy()
                tr.trim(common_start, common_end)
                c_list.append(np.array(tr.data[:]))
            tr_common=np.vstack(c_list)
            
            # do stack
            stack_data = np.sum(tr_common, axis=0)
            
            # prepare trace for passing up
            stats={'station':'%d'%ip, 'npts':len(stack_data), 'delta':self.dt, \
                'starttime':common_start}
            tr=Trace(data=stack_data,header=stats)
            
            # send the stack on to the stacks exchange
            message=dumps(tr,-1)
            ch.basic_publish(exchange='stacks',
                            routing_key='%d'%ip,
                            body=message,
                            properties=pika.BasicProperties(delivery_mode=2,)
                            )
            logging.log(logging.INFO,
                            " [P] Sent %r:%r" % 
                            ("%d"%ip, "Stacked data for point %s"
                            %(tr.stats.station)))
                            
def _setupRabbitMQ():
    # set up rabbitmq
    connection = pika.BlockingConnection(
                        pika.ConnectionParameters(
                        host='localhost'))
    channel = connection.channel()
    
    # set up exchanges for data and info
    channel.exchange_declare(exchange='stacks',exchange_type='topic')
    channel.exchange_declare(exchange='points',exchange_type='topic')
    channel.exchange_declare(exchange='info',    exchange_type='fanout')
    channel.exchange_declare(exchange='proc_data',exchange_type='topic')
    
    return connection, channel   
 
def rtwlStop(wo):
    connection, channel = _setupRabbitMQ()
    #sendPoisonPills(channel,wo)
    time.sleep(4)
    connection.close()
       
def rtwlStart(wo):
    """
    Starts processing.
    """

    # set up process to listen to info
    p_info = multiprocessing.Process(name='receive_info',target=receive_info)                     
    p_info.start()
    
    rtwlPointStacker(wo)
    


    
def receive_info():
    proc_name = multiprocessing.current_process().name
    connection, channel = _setupRabbitMQ()

    # bind to the info fanout
    result = channel.queue_declare(exclusive=True)
    info_queue_name = result.method.queue
    channel.queue_bind(exchange='info', queue=info_queue_name)
    
    print " [+] Ready to receive info ..."
    
    consumer_tag=channel.basic_consume(callback_info,
                      queue=info_queue_name,
                      #no_ack=True
                      )
    
    try:
        channel.start_consuming()
    except UserWarning:
        logging.log(logging.INFO,"Received UserWarning from %s"%proc_name)
        channel.stop_consuming()
        channel.basic_cancel(consumer_tag)
    
  
def callback_info(ch, method, properties, body):
    
    if body=='STOP':
        logging.log(logging.INFO, "rtwl_pointproc received poison pill")
        raise UserWarning


    
if __name__=='__main__':

    # read config file
    wo=rtwlGetConfig('rtwl.config')
    
    # parse command line
    options=rtwlParseCommandLine()

    if options.debug:
        logging.basicConfig(level=logging.DEBUG, format='%(levelname)s : %(asctime)s : %(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(levelname)s : %(asctime)s : %(message)s')
    

    if options.start:          
        rtwlStart(wo) # start
            
    if options.stop:
        rtwlStop(wo)
 


    
    
    
    
    
    
                        