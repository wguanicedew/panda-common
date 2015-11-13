import logging, logging.handlers, string
import logger_config
import threading
import httplib
import urllib
import json
import time

# encodings
JSON = 'json'
URL = 'url'

# set TZ for timestamp
import os
os.environ['TZ'] = 'UTC'

# logger map
loggerMap = {}

# wrapper to avoid duplication of loggers with the same name
def getLoggerWrapper(loggerName):
    global loggerMap
    if not loggerName in loggerMap:
        loggerMap[loggerName] = logging.getLogger(loggerName)
    return loggerMap[loggerName]


# a thread to send a record to a web server
class _Emitter (threading.Thread):
    # constructor
    def __init__(self,host,port,url,method,data,semaphore):
        threading.Thread.__init__(self)
        self.host   = host
        self.port   = port
        self.url    = url
        self.method = method
        self.data   = data
        self.semaphore = semaphore

    def getData(self, src, chunk_size=1024):
        """
        Use this function for debug purposes in order to print 
        out the response from the server
        """
        d = src.read(chunk_size)
        while d:
            yield d
            d = src.read(chunk_size)

    # main
    def run(self):
        # send the record to the Web server as an URL-encoded dictionary
        try:
            h = httplib.HTTPConnection(self.host, self.port)
            url = self.url
            if self.method == "GET":
                if (string.find(url, '?') >= 0):
                    sep = '&'
                else:
                    sep = '?'
                url = url + "%c%s" % (sep, self.data)
            h.putrequest(self.method, url)
            if self.method == "POST":
                h.putheader("Content-length", str(len(self.data)))
            h.endheaders()
            if self.method == "POST":
                h.send(self.data)
            response = h.getresponse()    # can't do anything with the result
            #for s in self.getData(response, 1024):
            #    print s

        except:
            pass
        self.semaphore.release()
        

class _PandaHTTPLogHandler(logging.Handler):
    """
    Customized HTTP handler for Python logging module.
    A class which sends records to a Web server, using either GET or
    POST semantics.
    """

    def __init__(self, host, url, port=80, urlprefix='', method="POST", encoding=URL):
        """
        Initialize the instance with the host, the request URL, and the method
        ("GET" or "POST")
        """

        logging.Handler.__init__(self)
        method = string.upper(method)
        if method not in ["GET", "POST"]:
            raise ValueError, "method must be GET or POST"
        self.host = host
        self.url = url
        self.port = port
        self.urlprefix = urlprefix
        self.method = method
        self.encoding = encoding
        # create lock for params, cannot use createLock()
        self.mylock = threading.Lock()
        # semaphore to limit the number of concurrent emitters
        if logger_config.daemon.has_key('nemitters'):
            self.mySemaphore = threading.Semaphore(int(logger_config.daemon['nemitters']))
        else:
            self.mySemaphore = threading.Semaphore(10)
        # parameters
        self.params = {}
        self.params['PandaID'] = -1
        self.params['User'] = 'unknown'
        self.params['Type'] = 'unknown'
        self.params['ID'] = 'tester'

    def mapLogRecord(self, record):
        """
        Default implementation of mapping the log record into a dict
        that is sent as the CGI data. Overwrite in your class.
        Contributed by Franz Glasner.
        """
        newrec = record.__dict__
        for p in self.params:
            newrec[p] = self.params[p]
        maxParamLength = 4000
        # truncate the message
        try:
            newrec['msg'] = newrec['msg'][:maxParamLength]
        except:
            pass
        try:
            newrec['message'] = newrec['message'][:maxParamLength]
        except:
            pass
        return newrec

    def emit(self, record):
        """
        Emit a record.

        Send the record to the Web server as an URL-encoded dictionary
        """
        # encode data
        # Panda logger is going to be migrated. Until this is completed we need to support the old and new logger
        # The new logger needs to be json encoded and use POST method
        
        if self.encoding == JSON:
            mapLogDict = self.mapLogRecord(record)
            mapLogString = '{'
            for key, value in mapLogDict.iteritems():
                if key not in ["relativeCreated", "process", "module", "funcName", "message"]:
                    continue
                if isinstance(key, basestring):
                    mapLogString = mapLogString + '\"{0}\": '.format(key)
                else:
                    mapLogString = mapLogString + '{0}: '.format(key)
                
                if isinstance(value, basestring):
                    mapLogString = mapLogString + '\"{0}\", '.format(value)
                else:
                    mapLogString = mapLogString + '{0}, '.format(value)
            mapLogString = mapLogString[:-2] + '}' 
            

            arr=[{
                  "headers":{"timestamp" : int(time.time())*1000, "host" : "%s:%s"%(self.url, self.port)},
                  "body": "%s"%self.mapLogRecord(record)
                 }]
            data = json.dumps(arr)
        else:
            data = urllib.urlencode(self.mapLogRecord(record))
        
        # try to lock Semaphore
        if self.mySemaphore.acquire(False):
            # start Emitter
            _Emitter(self.host, self.port, self.urlprefix, self.method, data, self.mySemaphore).start()

    def setParams(self, params):
        for pname in params.keys():
            self.params[pname] =params[pname]

    # acquire lock
    def lockHandler(self):
        self.mylock.acquire()

    # release lock
    def releaseHandler(self):
        self.mylock.release()


# setup logger
_pandalog = getLoggerWrapper('panda')
_pandalog.setLevel(logging.DEBUG)
_txtlog = getLoggerWrapper('panda.log')
_weblog = getLoggerWrapper('panda.mon')
_newweblog = getLoggerWrapper('panda.mon_new')
_formatter = logging.Formatter('%(asctime)s %(name)-12s: %(levelname)-8s %(message)s')

if len(_weblog.handlers) < 2:
    
    _allwebh = _PandaHTTPLogHandler(logger_config.daemon['loghost'],'http://%s'%logger_config.daemon['loghost'],
                                    logger_config.daemon['monport-apache'], logger_config.daemon['monurlprefix'],
                                    logger_config.daemon['method'], logger_config.daemon['encoding'])
    _allwebh.setLevel(logging.DEBUG)
    _allwebh.setFormatter(_formatter)
    
    if logger_config.daemon.has_key('loghost_new'):
        _newwebh = _PandaHTTPLogHandler(logger_config.daemon['loghost_new'],'http://%s'%logger_config.daemon['loghost_new'],
                                        logger_config.daemon['monport-apache_new'], logger_config.daemon['monurlprefix'],
                                        logger_config.daemon['method_new'], logger_config.daemon['encoding_new'])
        _newwebh.setLevel(logging.DEBUG)
        _newwebh.setFormatter(_formatter)
    
    _txth = logging.FileHandler('%s/panda.log'%logger_config.daemon['logdir'])
    _txth.setLevel(logging.DEBUG)
    _txth.setFormatter(_formatter)
    
    _weblog.addHandler(_txth)   # if http log doesn't have a text handler it doesn't work
    _weblog.addHandler(_allwebh)
    if logger_config.daemon.has_key('loghost_new'):
        _weblog.addHandler(_newwebh)

# no more HTTP handler
del _PandaHTTPLogHandler


class PandaLogger:
    """
    Logger and monitoring data collector for Panda.
    Custom fields added to the logging:

    user     Who is running the app
    PandaID  Panda job ID (if applicable)
    ID       General usage ID (eg. pilot ID, scheduler ID). A string.
    type     Message type
    """
    
    def __init__(self, pid=0, user='', id='', type=''):
        self.params = {}
        self.params['PandaID'] = pid
        self.params['ID'] = id
        self.params['User'] = user
        self.params['Type'] = type

    def getLogger(self, lognm):
        logh = getLoggerWrapper("panda.log.%s"%lognm)
        logh.propagate = False
        txth = logging.FileHandler('%s/panda-%s.log'%(logger_config.daemon['logdir'],lognm))
        txth.setLevel(logging.DEBUG)
        txth.setFormatter(_formatter)
        logh.addHandler(txth)
        return logh

    def getHttpLogger(self, lognm):
        httph = getLoggerWrapper('panda.mon.%s'%lognm)
        return httph

    def setParams(self, params):
        for pname in params.keys():
            self.params[pname] = params[pname]
        _allwebh.setParams(self.params)

    def getParam(self, pname):
        return self.params[pname]

    # acquire lock for HTTP handler
    def lock(self):
        _allwebh.lockHandler()

    # release lock
    def release(self):
        _allwebh.releaseHandler()
        
