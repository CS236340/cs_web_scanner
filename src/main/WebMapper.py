#!/usr/bin/python

import signal
import sys
import io
import os
import datetime
import urllib3
import certifi
import Queue
import time
import random
from urlparse import urljoin
from lxml import etree
from bs4 import BeautifulSoup

import Profiler
from utilities.Debug import Debug
from utilities.Misc  import Misc
from utilities.Misc  import TimeoutException

from Global import *
debug = global_debug
misc  = Misc()

outQ = coreRX

# System Configurations: ####
CONFIG_FILE  = 'config/mapping.conf'
OUTPUT_DIR   = 'data/mapper/'
MAPPER_SITES_FILE = OUTPUT_DIR+'mapper_sites.txt'
MAPPER_PAGES_FILE = OUTPUT_DIR+'mapper_pages.txt'
MAPPER_LATEST = OUTPUT_DIR+'mapper_latest'
EXE_INTV_MIN = 1
EXE_INTV_MAX = 3600
BASE_URL     = 'cs.technion.ac.il'
START_ADDR   = 'http://www.'+BASE_URL
EXE_INTV_DEFAULT = 0
LIMIT_DEPTH      = 800
CONFS = ['execution_interval']

profiler = None


class PageNotFound(Exception): pass

class WebMapper:
    def __init__(self):
        self.http = misc.run_with_timer(urllib3.PoolManager, {'cert_reqs': 'CERT_REQUIRED', 'ca_certs': certifi.where()}, "PoolManger stuck")
       # self.http = urllib3.PoolManager(cert_reqs = 'CERT_REQUIRED', ca_certs = certifi.where())
        #self.http = misc.run_with_timer(urllib3.PoolManager, (), "PoolManger stuck")
        self.sites_addrs   = set()
        self.visited_pages = set() 
        self.is_data_saved = False
        self.conf          = {}
        self.get_conf_from_file()
        self.max_depth     = 0

    def is_ascii(self, _string):
        try:
            _string.decode('ascii')
        except:
            return False
        else:
            return True

    def save_data(self, datetime_run):
        output_dir = OUTPUT_DIR
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        debug.logger('save_data: '+str(datetime_run))
        #filename = 'mapper_'+datetime.datetime.strftime(datetime_run, '%Y%m%d_%H%M%S')
        #f = open(filepath, 'w')
        #f.write(START_ADDR+'\n')
        #f.write(BASE_URL+'\n')
        with open(MAPPER_SITES_FILE, 'w') as f:
            for site_addr in self.sites_addrs:
                f.write(site_addr+"\n")
        with open(MAPPER_PAGES_FILE, 'w') as f:
            for page_addr in self.visited_pages:
                
                f.write(page_addr+"\n")
        #try:
        #    os.unlink(MAPPER_LATEST)
        #except:
        #    nothing = 0
        #os.symlink(filepath, MAPPER_LATEST)
        debug.logger('Sites in '+MAPPER_SITES_FILE)
        debug.logger('Pages in '+MAPPER_PAGES_FILE)
        
    def close_single_run(self):
        runtime = round(time.time() - self.curr_run_start_time, 3)
        padding = 16
        debug.logger('\n\n-----+++++++++++--------')
        debug.logger('close_single_run:'.ljust(padding))
        debug.logger('Total run time: '.ljust(padding)+str(runtime)+'s')
        debug.logger('Total pages: '.ljust(padding)+str(len(self.visited_pages)))
        debug.logger('Total sites: '.ljust(padding)+str(len(self.sites_addrs)))
        debug.logger('max depth: '.ljust(padding)+str(self.max_depth))
        debug.logger('-----+++++++++++--------\n')
        if not self.is_data_saved:
            self.save_data(self.last_run_datetime)
            self.is_data_saved = True

    def normalize_page(self, page_addr):
        page_addr       = page_addr[7:]
        parts           = page_addr.split('/')
        if parts[0].find('@') != -1:
            parts[0] = parts[0][parts[0].find('@')+1:]
        normalized_page = "http:/"
        for part in parts:
            if part != "":
                normalized_page = normalized_page+"/"+part
        return normalized_page

    '''
    assume page_addr is of format:
        http://site_base/subpage1/.../subpageN
    '''
    def is_page_a_site_home(self, page_addr):
    #    print ("is_page_a_site_home")
        if page_addr[len(page_addr) - 1] == '/':
            page_addr = page_addr[:-1]
        page_addr_parts = page_addr.split("/")
        num_parts = len(page_addr_parts)
     #   print(num_parts)
        if num_parts <= 3:
            return True
        if (num_parts == 4) and (page_addr_parts[3].find('~') == 0):
            return True
        return False      

    def is_technion_page(self, page_addr):   # ToDo: what are the criteria?
        res = False
        if  page_addr.find('technion') != -1:
            res = True       
        return res
    

    def fixed_urljoin(self, urlpart1, urlpart2):
        if urlpart2 == "":
            return urlpart1
        #debug.logger('THIS:::'+urlpart2+':::'+str(len(urlpart2))+':::')
        try:
            while " " == urlpart2[0] or '\n' == urlpart2[0]:
                urlpart2 = urlpart2[1:]
            url = urljoin(urlpart1, urlpart2)
            #splits = str(tmp_url).split("/")
            #splits = filter(lambda x: x!= "..", splits)
            #url    = '/'.join(splits)
        except:
            return None
        return url

    def validate_html_doc(self, html_doc):
        if html_doc.find('404 Not Found') != -1:
            return False
        return True

    def page_contains_base_url(self, page_addr):
        if -1 == page_addr.find(BASE_URL):
            return False
        return True
 
    def is_scannable_page(self, page_addr):
        if page_addr[-5:] != '.html' and page_addr[-4:] != '.htm' and page_addr[-1] != '/' and page_addr[-5:] != 'ac.il':
            return False
        #bad_parts = ['.pdf', '.PDF', '.doc', '.jpg', '.JPG', '.pptx', '.gif', 'mp4', 'ps', 'jigsaw']
        bad_parts = ['jigsaw', 'facebook', 'mailto:']
        for part in bad_parts:
            if page_addr.find(part) != -1:
                return False 
        if not (self.is_ascii(page_addr)):
            return False
        return True

    '''
    Main function: recursively scann sites
    '''
    def map_engine(self, page_addr, depth):
        self.max_depth = max(self.max_depth, LIMIT_DEPTH - depth)
        if depth == 0:
            return
        profiler.snapshot('bp1')
        debug.assrt(depth >= 0, 'extract_link_from_pages: depth='+str(depth))
        if depth == 0:
            return
        ''' Some optimizations: '''
        if (not self.is_scannable_page(page_addr)) or (not self.is_technion_page(page_addr)) or (page_addr in self.visited_pages):
            return
        self.visited_pages.add(page_addr)
        html_doc, optional_err_msg = self.get_html_doc(page_addr)
        ''' we want to continue scraping in case that a connection to web page timed-out or page not found '''
        if html_doc == None or not self.validate_html_doc(html_doc) or optional_err_msg != None:
            err_msg = optional_err_msg if  optional_err_msg != None else 'page not found'
            debug.logger('map_engine: bad page '+page_addr+': '+err_msg, 2)
            return
        profiler.snapshot('initial_checks')
        try:
            soup = misc.run_with_timer(BeautifulSoup, (html_doc, 'html.parser'), "BeautifulSoup failed on page "+page_addr, True)
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except:
            err_msg = str(sys.exc_info()[0])
            debug.logger('map_engine: bad page '+page_addr+': '+err_msg, 2)
            return
        profiler.snapshot('beautifulsoups')
        if not self.page_contains_base_url(page_addr):
             return
        ''' if we got to here then this page is valid, scannable, cs page '''
        if self.is_page_a_site_home(page_addr):
            self.sites_addrs.add(page_addr)
        debug.logger("page_addr="+page_addr)
        link_tags = soup.find_all('a')
        subset = min(100, len(link_tags))
        for link_tag in random.sample(link_tags, subset):
        #for link_tag in link_tags:
            link = link_tag.get('href')
            #print(link.find("#"))
            if not self.is_ascii(link) or link == "None" or link == None or (link.find("#") != -1):
                continue
            full_link = self.fixed_urljoin(page_addr, link)
            if not self.is_ascii(full_link):
                debug.logger('map_engine: bad link: not ASCII')
                continue
            if None == full_link:
                debug.logger('bad link:'+link)
                continue
            # keep into urls mapping file - this file will be used by WebInspector
            #debug.logger(page_addr+" + "+link+" = "+full_link)
            #full_link = link if (link.find("http://") == 0) else page_addr+"/"+link
            #    full_link = link
            #else:
            #    full_link = page_addr+"/"+link
            #print(link_full_addr)
            #debug.logger('anc page: '+page_addr)
            self.map_engine(full_link, depth - 1)

    '''
        gets full page address: http://site
        return source code of the site as string
    '''
    def get_html_doc(self, page_addr):
        page_data = None
        err_msg   = None
        #debug.assrt(page_addr.find("http://") == 0, "get_html_doc: page_addr="+page_addr)
        try:
            profiler.snapshot('bp2')
            request = misc.run_with_timer(self.http.request, ('GET', page_addr), "request for "+page_addr+" failed", True, 5)
            profiler.snapshot('http_request')
        except KeyboardInterrupt:
            raise
        except TimeoutException:
            err_msg = 'http request timeout'
        except :
            err_msg = str(sys.exc_info()[0])
        if err_msg == None:
            if (request != None):
                page_data = str(request.data) 
        #print(request.host)
        #debug.logger('get_html_doc: page_data='+str(page_data)+'. err_msg='+str(err_msg))
        return page_data, err_msg
        
    def run_once(self):
        debug.logger('WebMapper.run_once:') 
        self.reached_depth = False
        self.last_run_datetime = datetime.datetime.now()
        self.curr_run_start_time = time.time()
        try:
            self.map_engine(START_ADDR, LIMIT_DEPTH)
        except SystemExit:
            raise SystemExit
        finally:
            self.close_single_run()

    def check_incoming_work(self):
        try:
            workObj = incomingQ.get(False)
        except Queue.Empty:
            return
        switcher = {
            WorkID.CONFIG_MAPPER: self.config,
            WorkID.STOP_MAPPER:   self.config #TODO
        }
        switcher[workObj.workID](workObj.params)

    def run_in_cont_mode(self):
        debug.logger('WebMapper.run_in_cont_mode:')
        last_run = time.time() - self.conf['execution_interval'] - 10
        while True:
            if (time.time() - last_run) > self.conf['execution_interval']:
                self.run_once()
                last_run = time.time()
            self.check_incoming_work()

    def start_mapping(self):
        interval = self.conf['execution_interval']
        debug.logger('WebMapper.start_mapping: interval='+str(interval))
        if 0 == interval:
            self.run_once()
        else:
            self.run_in_cont_mode()

    ''' Those are general methods for Mapper configuration, you can add more configurations here.
        current possible configurations:
        execution_interval - # minutes before consequence runs. (int)
    '''
    def verify_exe_intv(self, execution_interval):
        return (execution_interval <= EXE_INTV_MAX and (execution_interval >= EXE_INTV_MIN)) or (execution_interval == 0)

    def get_conf_from_file(self):
        conf_file_valid = False
        try:
            with open(CONFIG_FILE, 'r') as f:
              confs = [tuple(i.split(' ')) for i in f]
            conf_file_valid = (set(CONFS) == set([conf[0] for conf in confs]))
            #debug.logger(str(conf_file_valid))
            for c in confs:
                if 2 != len(c):
                    debug.logger('len is not 2')
                    conf_file_valid = False
            confs_dict = {c[0]: c[1] for c in confs}
            debug.logger(confs_dict['execution_interval'])
            conf_file_valid = conf_file_valid and self.verify_exe_intv(int(confs_dict['execution_interval']))
            debug.logger(str(conf_file_valid))
        except IOError:
            debug.logger('got IOError')
            conf_file_valid = False
        except KeyError:
            conf_file_valid = False
        if not conf_file_valid:
            debug.logger('get_conf_from_file: found bad configuration in file '+CONFIG_FILE, 2)
            self.load_default_conf()
        else:
            self.conf = confs_dict
            self.conf['execution_interval'] = int(self.conf['execution_interval'])

    def save_conf_to_file(self):
        debug.assrt(type(self.conf) == type({}), 'self.conf is not dict!')
        f = open(CONFIG_FILE, 'w')
        for k in self.conf.keys():
            f.write(k+" "+str(self.conf[k]))
        f.close()
        debug.logger('WebMapper.save_conf_to_file: data has been saved')

    def load_default_conf(self):
        debug.logger('WebMapper.loading_default_conf: loading default configurations')
        self.conf['execution_interval'] = EXE_INTV_DEFAULT
        self.save_conf_to_file()

    def config(self, execution_interval):
        debug.assrt(type(execution_interval) == type(1), 'WebMapper.config: param isnt int')
        debug.logger('WebMapper.config: execution_interval='+str(execution_interval))
        if not self.verify_exe_intv(execution_interval):
            raise ValueError("interval must be between 1 to 3600")
        self.conf['execution_interval'] = execution_interval
        self.save_conf_to_file()

        

def main_web_mapper():
    global profiler
    try:
        profiler = Profiler.Profiler()
        webMapper = WebMapper()
        webMapper.start_mapping()
    except KeyboardInterrupt:
        debug.logger('got KeyboardInterrupt!')
    except:
        raise
    finally:
        debug.close_debugger()
        profiler.print_stats()
    
if __name__ == "__main__":
    main_web_mapper()
