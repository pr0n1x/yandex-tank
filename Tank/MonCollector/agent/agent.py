#! /usr/bin/python
''' The agent bundle, contains all metric classes and agent running code '''
from optparse import OptionParser
from subprocess import Popen, PIPE
import ConfigParser
import base64
import commands
import logging
import os
import re
import socket
import sys
import time
from threading import Thread
import traceback


class AbstractMetric:
    ''' Parent class for all metrics '''

    def columns(self):
        ''' methods should return list of columns provided by metric class '''
        raise NotImplementedError()

    def check(self):
        ''' methods should return list of values provided by metric class '''
        raise NotImplementedError()


class CpuLa(AbstractMetric):
    def columns(self, ):
        return ['System_la1', 'System_la5', 'System_la15']

    def check(self, ):
        loadavg_str = open('/proc/loadavg', 'r').readline().strip()
        return map(str, loadavg_str.split()[:3])


class CpuStat(AbstractMetric):
    ''' read /proc/stat and calculate amount of time
        the CPU has spent performing different kinds of work.
    '''

    def __init__(self, ):
        # cpu data
        self.check_prev = None
        self.check_last = None

        # csw, int data
        self.current = None
        self.last = None

    def columns(self, ):
        columns = ['System_csw', 'System_int',
                   'CPU_user', 'CPU_nice', 'CPU_system', 'CPU_idle', 'CPU_iowait',
                   'CPU_irq', 'CPU_softirq', 'System_numproc', 'System_numthreads']
        return columns

    def check(self, ):

        # Empty symbol for no data
        empty = ''

        # resulting data array
        result = []

        # Context switches and interrups. Check.
        try:
            # TODO: change to simple file reading
            output = Popen('cat /proc/stat | grep -E "^(ctxt|intr|cpu) "',
                           shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        except Exception:
            result.append([empty] * 9)
        else:
            err = output.stderr.read()
            if err:
                result.extend([empty] * 9)
            else:
                info = output.stdout.read()

                # CPU. Fetch data
                cpus = info.split("\n")[0].split()[1:8]
                fetch_cpu = lambda: map(float, cpus)

                # Context switches and interrupts. Fetch data
                data = []
                for line in info.split("\n")[1:3]:
                    if line:
                        data.append(line.split()[1])
                fetch_data = lambda: map(float, data)

                # Context switches and interrups. Analyze.
                if self.last:
                    self.current = fetch_data()
                    delta = []
                    cnt = 0
                    for _ in self.current:
                        delta.append(self.current[cnt] - self.last[cnt])
                        cnt += 1
                    self.last = self.current
                    result.extend(map(str, delta))
                else:
                    self.last = fetch_data()
                    result.extend([empty] * 2)
                #                logger.debug("Result: %s" % result)

                # CPU. analyze.
                #                logger.debug("CPU start.")
                if self.check_prev is not None:
                    self.check_last = fetch_cpu()
                    delta = []
                    cnt = 0
                    sum_val = 0
                    for _ in self.check_last:
                        column_delta = self.check_last[cnt] - self.check_prev[cnt]
                        sum_val += column_delta
                        delta.append(column_delta)
                        cnt += 1

                    cnt = 0
                    for _ in self.check_last:
                        result.append(str((delta[cnt] / sum_val) * 100))
                        cnt += 1
                    self.check_prev = self.check_last
                else:
                    self.check_prev = fetch_cpu()
                    result.extend([empty] * 7)
                    #                logger.debug("Result: %s" % result)

        # Numproc, numthreads
        # TODO: change to simple file reading
        command = ['ps ax | wc -l', "cat /proc/loadavg | cut -d' ' -f 4 | cut -d'/' -f2"]
        for cmd2 in command:
            try:
                output = Popen(cmd2, shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE)
            except Exception:
                result.append(empty)
            else:
                err = output.stderr.read()
                if err:
                    result.append(empty)
                else:
                    result.append(str(int(output.stdout.read().strip()) - 1))
        return result


def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


class Custom(AbstractMetric):
    ''' custom metrics: call and tail '''

    def __init__(self, call, tail):
        self.call = call
        self.tail = tail
        self.diff_values = {}

    def columns(self, ):
        cols = []
        for el in self.tail:
            cols.append("Custom:" + el)
        for el in self.call:
            cols.append("Custom:" + el)
        return cols

    def check(self, ):
        res = []
        for el in self.tail:
            cmnd = base64.b64decode(el.split(':')[1])
            output = Popen(['tail', '-n', '1', cmnd], stdout=PIPE).communicate()[0]
            res.append(self.diff_value(el, output.strip()))
        for el in self.call:
            cmnd = base64.b64decode(el.split(':')[1])
            output = Popen(cmnd, shell=True, stdin=PIPE, stdout=PIPE).stdout.read()
            res.append(self.diff_value(el, output.strip()))
        return res

    def diff_value(self, config_line, value):
        if not is_number(value):
            #logging.warning("Non-numeric result string, defaulting value to 0: %s", value)
            value = 0
        params = config_line.split(':')
        if len(params) > 2 and int(params[2]):
            if config_line in self.diff_values:
                diffvalue = float(value) - self.diff_values[config_line]
            else:
                diffvalue = 0
            self.diff_values[config_line] = float(value)
            value = diffvalue
        return str(value)


class Disk(AbstractMetric):
    def __init__(self):
        self.read = 0
        self.write = 0
        self.devs = self._get_devs()

    def columns(self, ):
        return ['Disk_read', 'Disk_write']

    def check(self, ):
        # cluster size
        size = 512

        # Current data
        read, writed = 0, 0

        try:
            with open("/proc/diskstats") as mfd:
                stat = mfd.readlines()
            logging.info("Stats: %s", stat)

            for el in stat:
                data = el.split()
                if data[2] in self.devs:
                    read += int(data[5])
                    writed += int(data[9])

            if self.read or self.write:
                result = [str(size * (read - self.read)), str(size * (writed - self.write))]
            else:
                result = ['', '']

            self.read, self.write = read, writed

        except Exception, exc:
            logging.error("%s: %s", exc, traceback.format_exc(exc))
            result = ['', '']
        return result

    def _get_devs(self):
        with open("/proc/mounts") as mfd:
            mounts = mfd.readlines()
        logging.info("Mounts: %s", mounts)
        devs = []
        for mount in mounts:
            if mount.startswith("/dev"):
                parts = mount.split(" ")
                devs.append(os.path.realpath(parts[0]).split(os.sep)[-1])
        logging.info("Devs: %s", devs)
        return devs


class Mem(AbstractMetric):
    """
    Memory statistics
    """
    empty = ''

    def __init__(self):
        self.name = 'advanced memory usage'
        self.nick = ('used', 'buff', 'cach', 'free', 'dirty')
        self.vars = ('MemUsed', 'Buffers', 'Cached', 'MemFree', 'Dirty', 'MemTotal')

    #        self.open('/proc/meminfo')

    def columns(self):
        columns = ['Memory_total', 'Memory_used', 'Memory_free', 'Memory_shared', 'Memory_buff', 'Memory_cached']
        logging.info("Start. Columns: %s" % columns)
        return columns

    def check(self):
        result = []
        try:
            #TODO: change to simple file reading
            output = Popen('cat /proc/meminfo', shell=True, stdout=PIPE, stderr=PIPE)
        except Exception, e:
            logging.error("%s: %s" % (e.__class__, str(e)))
            result.append([self.empty] * 9)
        else:
            data = {}
            err = output.stderr.read()
            if err:
                result.extend([self.empty] * 9)
                logging.error(err.rstrip())
            else:
                info = output.stdout.read()

                for name in self.vars:
                    data[name] = 0

                for l in info.splitlines():
                    if len(l) < 2:
                        continue
                    [name, raw_value] = l.split(':')
                    #                    print "name: %s " % name
                    if name in self.vars:
                        #                       print name, raw_value
                        data.update({name: long(raw_value.split()[0]) / 1024.0})
                        #                print data
                data['MemUsed'] = data['MemTotal'] - data['MemFree'] - data['Buffers'] - data['Cached']
            result = [data['MemTotal'], data['MemUsed'], data['MemFree'], 0, data['Buffers'], data['Cached']]
            return map(str, result)


class NetRetrans(AbstractMetric):
    ''' read netstat output and
    calculate tcp segment retransmition derivative '''

    def __init__(self, ):
        self.retr_second = None
        self.retr_first = None
        self.fetch = None
        self.delta = []

    def columns(self, ):
        return ['Net_retransmit', ]

    def check(self, ):
        self.fetch = lambda: int(commands.getoutput('netstat -s | grep "segments retransmited" | awk \'{print $1}\''))
        if self.retr_second is not None:
            self.retr_first = self.fetch()
            self.delta = []
            self.delta.append(str(self.retr_first - self.retr_second))
            self.retr_second = self.retr_first
            return self.delta
        else:
            # first check
            self.retr_second = self.fetch()
            return ['0', ]


class NetTcp(AbstractMetric):
    ''' Read ss util output and count TCP socket's number grouped by state '''

    def __init__(self, ):
        self.fields = ['Net_closewait', 'Net_estab', 'Net_listen', 'Net_timewait', ]
        self.keys = ['CLOSE-WAIT', 'ESTAB', 'LISTEN', 'TIME-WAIT', ]

    def columns(self, ):
        return self.fields

    def check(self, ):
        '''
        * check is there TCP connections in "field" state in last check
        if note set it to 0.
        * make output ordered as "fields" list
        '''
        fetch = lambda: commands.getoutput("ss -an | cut -d' ' -f 1 | tail -n +2 | sort | uniq -c")
        data = {}
        result = []
        raw_lines = fetch().split('\n')
        for line in raw_lines:
            value = line.split()
            data[value[1].strip()] = int(value[0].strip())
        for field in self.keys:
            if field in data:
                result.append(str(data[field]))
            else:
                result.append('0')
        return result


class NetTxRx(AbstractMetric):
    ''' Get upped iface names and read they Tx/Rx counters in bytes '''

    def __init__(self, ):
        self.prev_rx = 0
        self.prev_tx = 0

    def columns(self, ):
        return ['Net_tx', 'Net_rx', ]

    def check(self, ):
        '''
        get network interface name which have ip addr
        which resolved fron  host FQDN.
        If we have network bonding or need to collect multiple iface
        statistic beter to change that behavior.
        '''
        data = commands.getoutput("/sbin/ifconfig -s | awk '{rx+=$8; tx+=$4} END {print rx, tx}'")
        logging.debug("TXRX output: %s", data)
        (rx, tx) = data.split(" ")
        rx = int(rx)
        tx = int(tx)

        if self.prev_rx == 0:
            t_tx = 0
            t_rx = 0
        else:
            t_rx = rx - self.prev_rx
            t_tx = tx - self.prev_tx
        self.prev_rx = rx
        self.prev_tx = tx

        return [str(t_rx), str(t_tx)]


class Net(AbstractMetric):
    def __init__(self):
        self.recv = 0
        self.send = 0
        self.rgx = re.compile('\S+\s(\d+)\s(\d+)')

    def columns(self, ):
        return ['Net_recv', 'Net_send']

    def check(self, ):
        # Current data
        recv, send = 0, 0

        # TODO: change to simple file reading
        cmnd = "cat /proc/net/dev | tail -n +3 | cut -d':' -f 1,2 --output-delimiter=' ' | awk '{print $1, $2, $10}'"
        logging.debug("Starting: %s", cmnd)
        try:
            stat = Popen([cmnd], stdout=PIPE, stderr=PIPE, shell=True)
        except Exception, exc:
            logging.error("Error getting net metrics: %s", exc)
            result = ['', '']

        else:
            err = stat.stderr.read()
            if err:
                logging.error("Error output: %s", err)
                result = ['', '']
            else:
                for elm in stat.stdout:
                    match = self.rgx.match(elm)
                    if match:
                        recv += int(match.group(1))
                        send += int(match.group(2))
                        logging.debug("Recv/send: %s/%s", recv, send)
                    else:
                        logging.debug("Not matched: %s", elm)
                if self.recv:
                    result = [str(recv - self.recv), str(send - self.send)]
                else:
                    result = ['', '']

        self.recv, self.send = recv, send
        logging.debug("Result: %s", result)
        return result


# ===========================

def write(mesg):
    ''' console writing wraper '''
    sys.stdout.write('%s\n' % mesg)
    sys.stdout.flush()


def setup_logging():
    ''' Logging params '''
    fname = os.path.dirname(__file__) + "_agent.log"
    level = logging.DEBUG

    fmt = "%(asctime)s - %(filename)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(filename=fname, level=level, format=fmt)

    logging.info('Start agent')


def fixed_sleep(slp_interval, ):
    ''' sleep 'interval' exclude processing time part '''
    global t_after
    if t_after is not None:
        t_delta = time.time() - t_after
        t_after = time.time()
        logging.debug('slp_interval:%s, t_delta:%s, slp_interval * 2 - t_delta = %s', slp_interval, t_delta,
                      slp_interval * 2 - t_delta)
        if (t_delta > slp_interval) & (slp_interval * 2 - t_delta > 0):
            time.sleep(slp_interval * 2 - t_delta)
        else:
            if slp_interval * 2 - t_delta < 0:
                logging.warn('[negative sleep time]')
            else:
                time.sleep(slp_interval)
    else:
        # first cycle iter
        t_after = time.time()
        time.sleep(slp_interval)


unixtime = lambda: str(int(time.time()))


class AgentWorker(Thread):
    dlmtr = ';'

    def __init__(self):
        Thread.__init__(self)
        self.c_interval = 1
        self.tails = None
        self.calls = None
        self.metrics_collected = []
        self.c_host = None
        self.c_local_start = None
        self.c_start = None
        self.daemon = True  # Thread auto-shutdown
        self.finished = False
        # metrics we know about
        self.known_metrics = {
            'cpu-la': CpuLa(),
            'cpu-stat': CpuStat(),
            'mem': Mem(),
            'net-retrans': NetRetrans(),
            'net-tx-rx': NetTxRx(),
            'net-tcp': NetTcp(),
            'disk': Disk(),
            'net': Net(),
        }


    def run(self):
        logging.info("Start polling thread")
        global t_after
        t_after = None
        header = []

        sync_time = str(self.c_start + (int(time.time()) - self.c_local_start))
        header.extend(['start', self.c_host, sync_time])  # start compile init header

        # add metrics from config file to header
        for metric_name in self.metrics_collected:
            if metric_name:
                header.extend(self.known_metrics[metric_name].columns())

        # add custom metrics from config file to header
        custom = Custom(self.calls, self.tails)
        header.extend(custom.columns())

        sys.stdout.write(self.dlmtr.join(header) + '\n')
        sys.stdout.flush()

        logging.debug(self.dlmtr.join(header))

        # check loop
        while not self.finished:
            logging.debug('Start check')
            line = []
            sync_time = str(self.c_start + (int(time.time()) - self.c_local_start))
            line.extend([self.c_host, sync_time])

            # known metrics
            for metric_name in self.metrics_collected:
                try:
                    data = self.known_metrics[metric_name].check()
                    if len(data) != len(self.known_metrics[metric_name].columns()):
                        raise RuntimeError("Data len not matched columns count: %s" % data)
                except Exception, e:
                    logging.error('Can\'t fetch %s: %s', metric_name, e)
                    data = [''] * len(self.known_metrics[metric_name].columns())
                line.extend(data)

            logging.debug("line: %s" % line)
            # custom commands
            line.extend(custom.check())

            # print result line
            try:
                row = self.dlmtr.join(line)
                logging.debug("str: %s" % row)
                sys.stdout.write(row + '\n')
                sys.stdout.flush()
            except Exception, e:
                logging.error('Failed to convert line %s: %s', line, e)

            fixed_sleep(self.c_interval)


if __name__ == '__main__':
    pass

    #def tmp():
    # default params
    def_cfg_path = 'agent.cfg'
    c_interval = 1
    c_host = socket.getfqdn()
    c_local_start = int(time.time())

    setup_logging()
    logging.info("Start agent at host: %s\n" % c_host)


    # parse options
    parser = OptionParser()
    parser.add_option('-c', '--config', dest='cfg_file', type='str',
                      help='Config file path, default is: ./' + def_cfg_path,
                      default=def_cfg_path)

    parser.add_option('-t', '--timestamp', dest='timestamp', type='int',
                      help='Caller timestamp for synchronization', default=c_local_start)
    (options, args) = parser.parse_args()

    c_start = options.timestamp
    logging.debug("Caller timestamp: %s", options.timestamp)

    # parse cfg file
    config = ConfigParser.ConfigParser()
    config.readfp(open(options.cfg_file))

    # metric section
    metrics_collected = []
    if config.has_option('metric', 'names'):
        metrics_collected = config.get('metric', 'names').split(',')

    # main section
    if config.has_section('main'):
        if config.has_option('main', 'interval'):
            c_interval = config.getfloat('main', 'interval')
        if config.has_option('main', 'host'):
            c_host = config.get('main', 'host')
        if config.has_option('main', 'start'):
            c_start = config.getint('main', 'start')

    logging.info('Agent params: %s, %s' % (c_interval, c_host))

    # custom section
    calls = []
    tails = []
    if config.has_section('custom'):
        if config.has_option('custom', 'tail'):
            tails += config.get('custom', 'tail').split(',')
        if config.has_option('custom', 'call'):
            calls += config.get('custom', 'call').split(',')

    worker = AgentWorker()

    # populate
    worker.c_start = c_start
    worker.c_local_start = c_local_start
    worker.c_host = c_host
    worker.metrics_collected = metrics_collected
    worker.calls = calls
    worker.tails = tails
    worker.c_interval = c_interval

    worker.start()

    logging.debug("Ckeck for stdin shutdown command")
    cmd = sys.stdin.read()
    if cmd:
        logging.info("Stdin cmd received: %s", cmd)
        worker.finished = True
