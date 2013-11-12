import logging, pika,time, glob, h5py
import multiprocessing
from obspy.core import UTCDateTime, Trace
from obspy.realtime import RtTrace
from cPickle import dumps, loads, dump
from rtwl_io import rtwlGetConfig, rtwlParseCommandLine, setupRabbitMQ

import os
import numpy as np
#os.system('taskset -p 0xffff %d' % os.getpid())

class rtwlPointStacker(object):
    def __init__(self,wo, do_dump=False):
    
        # create a lock for the ttimes matrix
        self.ttimes_lock = multiprocessing.Lock()
        
        self.wo = wo
        self.do_dump = do_dump
        self.dt = wo.opdict['dt']
        
        # call _load_ttimes once to set up ttimes_matrix from existing files
        # if info receives an instruction that files have changed, then
        # _load_ttimes will be relaunched internally
        self._load_ttimes()
        
        # create a process for listening to info
        p_info = multiprocessing.Process(
                            name='info_monitor',
                            target=self._receive_info, 
                            )
        p_info.start()          

        # nsta ndependent process for filling up points exchange
        # note : prepare processors for all stations in config file
        # there may be fewer stations present in the ttimes_matrix
        # note : so long as there are no incoming data, none will be distributed
        #        distribution itself happens with a lock on ttimes_matrix
        #        and so should not happen while ttimes_matrix is being reloaded
        for sta in wo.sta_list :
            p = multiprocessing.Process(
                            name='distrib_%s'%sta,
                            target=self._do_distribute, 
                            args=(sta,)
                            )
            p.start()  
              
        # create a processor
        # make sure no updating is happening before starting processor
        # processor will not update after this
        with self.ttimes_lock:
            sta_list = self.sta_list
            npts = self.npts
        rtwlPointProcessor(wo,sta_list,npts,do_dump)

    
    def _load_ttimes(self):
        
        # initialize the travel-times 
        ttimes_fnames=glob.glob(self.wo.ttimes_glob)
        
        with self.ttimes_lock :
       
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
        
            # if do_dump, then dump ttimes_matrix to file for debugging
            if self.do_dump:
                filename='pointproc_ttimes.dump'
                f=open(filename,'w')
                dump(self.ttimes_matrix,f,-1)
                f.close()
        
        
    def _do_distribute(self,sta):
        proc_name = multiprocessing.current_process().name
        
        # queue setup
        connection, channel = setupRabbitMQ('DISTRIBUTE') 
    
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
        channel.start_consuming()
        logging.log(logging.INFO,"Received STOP signal : %s"%proc_name)

    def _callback_distribute(self, ch, method, properties, body):
        if body=='STOP' :
            sta=method.routing_key
            ch.basic_ack(delivery_tag = method.delivery_tag)
            # pass on to next exchange
            for ip in xrange(self.npts):
                ch.basic_publish(exchange='points',
                            routing_key='%s.%d'%(sta,ip),
                            body='STOP',
                            properties=pika.BasicProperties(delivery_mode=2,)
                            )
            ch.stop_consuming()
            for tag in ch.consumer_tags:
                ch.basic_cancel(tag)
        else:
            # unpack data packet 
            tr=loads(body)
            
            sta=method.routing_key
            # get info to access ttimes_matrix
            # so long as it is not being modified right now
            with self.ttimes_lock:
                npts = self.npts
                ista = self.sta_list.index(sta)

                for ip in xrange(npts):
                    # do shift
                    tr.stats.starttime -= np.round(self.ttimes_matrix[ista][ip]/self.dt) * self.dt
                    ch.basic_publish(exchange='points',
                            routing_key='%s.%d'%(sta,ip),
                            body=dumps(tr,-1),
                            properties=pika.BasicProperties(delivery_mode=2,)
                            )
                
            logging.log(logging.DEBUG,
                            " [D] Sent %r" % 
                            ("Distributed data for station %s"%sta))
            # acknowledge all ok            
            ch.basic_ack(delivery_tag = method.delivery_tag)

    def _receive_info(self):
        proc_name = multiprocessing.current_process().name
        connection, channel = setupRabbitMQ('INFO')

        # bind to the info fanout
        result = channel.queue_declare(exclusive=True)
        info_queue_name = result.method.queue
        channel.queue_bind(exchange='info', queue=info_queue_name)
    
        logging.log(logging.INFO, " [+] Ready to receive info ...")
    
        consumer_tag=channel.basic_consume(self._callback_info,
                      queue=info_queue_name,
                      #no_ack=True
                      )
        
        channel.start_consuming()
        logging.log(logging.INFO,"Received STOP signal : %s"%proc_name)
    
    
  
    def _callback_info(self,ch, method, properties, body):
    
        logging.log(logging.INFO, "info : rtwl_pointproc received %s"%body)

        if body=='STOP':
            ch.stop_consuming()
            for tag in ch.consumer_tags:
                ch.basic_cancel(tag)
        elif body == 'SYN_TTIMES_CHANGED' :
            self._load_ttimes()
            logging.log(logging.INFO, "info : rtwl_pointproc reloaded ttimes")
        else :
            logging.log(logging.INFO, "info : no action taken")

                
        
class rtwlPointProcessor(object):
    def __init__(self,wo,sta_list,npts, do_dump = False) :
        
        # basic parameters
        self.point_lock = multiprocessing.Lock()
        
        self.do_dump = do_dump
        self.wo = wo
        self.sta_list=sta_list
        self.nsta = len(sta_list)
        self.npts = npts
        self.max_length = self.wo.opdict['max_length']
        self.safety_margin = self.wo.opdict['safety_margin']
        self.dt = self.wo.opdict['dt']
        self.last_common_end_stack = {}
        for ip in xrange(self.npts):
            self.last_common_end_stack[ip] = UTCDateTime(1970,1,1)
        
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


        # rt streams must be indexed by station name
        self.pt_sta_dict = {}
        for sta in sta_list:
            self.pt_sta_dict[sta] = RtTrace(max_length=self.max_length)
               
        
        # independent process (for parallel calculation)
        # get number of processes automatically somehow (config file ?)
        # for now set to 10
        nprocs = 10
        for i in xrange(nprocs):
            p = multiprocessing.Process(
                                        name='proc_%d'%i,
                                        target=self._do_proc,
                                        )
            p.start()
        
            
    def _do_proc(self):
        proc_name = multiprocessing.current_process().name
    
        # queue setup
        connection, channel = setupRabbitMQ('POINTPROC')
        
        # create one queue
        result = channel.queue_declare(exclusive=True)
        queue_name = result.method.queue

        # bind to all stations            
        channel.queue_bind(exchange='points', 
                                    queue=queue_name, 
                                    routing_key='#',
                                    )
                                    
        consumer_tag=channel.basic_consume(
                                    self._callback_proc,
                                    queue=queue_name,
                                    #no_ack=True
                                    )
        
        channel.basic_qos(prefetch_count=1)
        channel.start_consuming()
        logging.log(logging.INFO,"Received STOP signal : %s"%proc_name)

    def _callback_proc(self, ch, method, properties, body):
        if body=='STOP' :
            sta = method.routing_key.split('.')[0]
            ip = int(method.routing_key.split('.')[1])
            
            ch.basic_ack(delivery_tag = method.delivery_tag)
            
            # pass on to next exchange
            ch.basic_publish(exchange='stacks',
                            routing_key='%d'%ip,
                            body='STOP',
                            properties=pika.BasicProperties(delivery_mode=2,)
                            )
            ch.stop_consuming()
            for tag in ch.consumer_tags:
                ch.basic_cancel(tag)
        else:
            
            sta = method.routing_key.split('.')[0]
            ista=self.sta_list.index(sta)
            ip = int(method.routing_key.split('.')[1])

            # unpack data packet
            tr=loads(body)
            
            # add shifted trace
            with self.point_lock:
                self.point_rt_list[ip][ista].append(tr, 
                                        gap_overlap_check = True
                                        )
                if self.do_dump:
                    if ip==0 and ista==0 :
                        f=open('pointproc_point00.dump','w')
                        dump(self.point_rt_list[ip][ista], f, -1)
                        f.close()

            # update stack if possible
            self._updateStack(ip,ch)
            
            # acknowledge all ok
            ch.basic_ack(delivery_tag = method.delivery_tag)


    def _updateStack(self,ip,ch):
        
        UTCDateTime.DEFAULT_PRECISION=2
        
        nsta=self.nsta
        # get common start-time for this point
        with self.point_lock :
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
                            ("%d"%ip, "Stacked data for point %s : %s - %s"
                            %(tr.stats.station, 
                            tr.stats.starttime.isoformat(), 
                            tr.stats.endtime.isoformat())))
                            

 
def rtwlStop(wo):
    connection, channel = setupRabbitMQ('INFO')
    #sendPoisonPills(channel,wo)
    time.sleep(4)
    connection.close()
       
def rtwlStart(wo):
    """
    Starts processing.
    """
    
    rtwlPointStacker(wo)
    


    

    
if __name__=='__main__':

    # read config file
    wo=rtwlGetConfig('rtwl.config')
    
    # parse command line
    options=rtwlParseCommandLine()

    if options.debug:
        logging.basicConfig(
                #filename='rtwl_pointproc.log',
                level=logging.DEBUG, 
                format='%(levelname)s : %(asctime)s : %(message)s'
                )
    else:
        logging.basicConfig(
                #filename='rtwl_pointproc.log',
                level=logging.INFO, 
                format='%(levelname)s : %(asctime)s : %(message)s'
                ) 

    if options.start:          
        rtwlStart(wo) # start
            
    if options.stop:
        rtwlStop(wo)
 


    
    
    
    
    
    
                        