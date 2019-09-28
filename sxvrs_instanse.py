#!/usr/bin/env python

""" Video Recording Instanse
The instanse is running in separate thread
"""
import os
import logging
import json
from datetime import datetime
from threading import Thread, Event
import subprocess
from operator import itemgetter
try:
    from os import scandir
except ImportError:
    from scandir import scandir  # use scandir PyPI module on Python < 3.5


class vr_thread(Thread):
    """
    Each Video Recording Instanse must be run in separate thread
    """  
    def read_config(self, param):  
        """Function read configuration param from YAML returning local or global value"""
        if param in self.cnfg['sources'][self.name]:
            return self.cnfg['sources'][self.name][param]
        else:
            return self.cnfg['global'][param]

    def __init__(self, name, cnfg, mqtt_client):
        """Init and assigning params before run"""
        Thread.__init__(self)
        self.state_msg = 'stopped'
        self._stop_event = Event()
        self._record_start_event = Event()
        self._record_stop_event = Event()
        self.recording = False
        self.name = name
        self.cnfg = cnfg
        self.mqtt_client = mqtt_client
        self.ip = self.read_config('ip')
        self.stream_url = self.read_config('stream_url')
        self.record_autostart = self.read_config('record_autostart')
        self.record_time = self.read_config('record_time')
        self.storage_max_size = self.read_config('storage_max_size')
        self.storage_path = self.read_config('storage_path')
        self.filename = self.read_config('filename')
        self.cmd_before = self.read_config('cmd_before')
        self.cmd = self.read_config('cmd')
        self.cmd_after = self.read_config('cmd_after')
    
    def record_start(self):
        """ Start recording, if it is not started yet """
        self._record_start_event.set()
        logging.debug(f'  receve "record_start" event for thread {self.name}')

    def record_stop(self):
        """ Stop recording, if it is not started yet """
        self._record_stop_event.set()
        logging.debug(f'  receve "record_stop" event for thread {self.name}')

    def stop(self, timeout=None):
        """ Stop the thread. """        
        self._stop_event.set()
        logging.debug(f'  receve "stop" event for thread {self.name}')
        Thread.join(self, timeout)

    def shell_execute(self, cmd, path):
        filename = self.filename.format(storage_path=path, name=self.name, datetime=datetime.now())
        stream_url = self.stream_url.format(ip=self.ip)
        cmd = cmd.format(filename=filename, ip=self.ip, stream_url=stream_url, record_time=self.record_time)
        logging.debug(f'shell_execute: {cmd}')
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, universal_newlines=True)
        return process

    def run(self):
        """Starting thread loop"""
        self.mqtt_client.subscribe(self.cnfg['mqtt']['topic_subscribe'].format(source_name=self.name))  
        i = 0 
        while not self._stop_event.isSet():     
            if self.record_autostart or self._record_start_event.isSet():
                self.recording = True
            if self._record_stop_event.isSet():
                self.recording = False
            if self.recording:
                # Force create path
                path = self.storage_path.format(name=self.name, datetime=datetime.now())
                if not os.path.exists(path):
                    logging.debug(f'  path not existing: {path} \n try to create it..')
                    try:
                        os.makedirs(path)
                    except:
                        logging.exception(f'Can''t create path {path}')
                # force cleanup {path} by {storage_max_size}
                self.clear_storage(os.path.dirname(self.storage_path.format(name=self.name, datetime=datetime.now())))
                # take snapshot
                #self.mqtt_client.publish(self.cnfg['mqtt']['topic_publish'].format(source_name=self.name),json.dumps({'status':'snapshot'}))
                # run cmd before start
                if self.cmd_before!=None and self.cmd_before!='':
                    process = self.shell_execute(self.cmd_before, path)
                # run cmd
                if self.cmd!=None and self.cmd!='':
                    process = self.shell_execute(self.cmd, path)
                    self.state_msg = 'started'
                    self.mqtt_client.publish(self.cnfg['mqtt']['topic_publish'].format(source_name=self.name),json.dumps({'status':self.state_msg }))
                    try:
                        process.wait(self.record_time)
                    except subprocess.TimeoutExpired:
                        logging.debug(f'/t {self.name}: process.wait TimeoutExpired {self.record_time}')
                    logging.debug(f'/t process execution finished')
                    self.state_msg = 'restarting'
                    self.mqtt_client.publish(self.cnfg['mqtt']['topic_publish'].format(source_name=self.name),json.dumps({'status':self.state_msg}))
                # run cmd after finishing
                if self.cmd_after!=None and self.cmd_after!='':
                    process = self.shell_execute(self.cmd_after, path)
            i += 1
            logging.debug(f'Running thread {self.name} iteration #{i}')
            self._stop_event.wait(1)

    def clear_storage(self, cleanup_path):
        """function removes old files in Camera folder. This gives ability to write files in neverending loop, when old records are rewritedby new ones"""
        try:            
            max_size = self.storage_max_size*1024*1024*1024
            logging.debug("Start storage cleanup on path: {0} (Max size: {1:.2f} GB)".format(cleanup_path, max_size/1024/1024/1024))
            self.file_list = []
            self.folder_size(cleanup_path)
            # sort list of files by datetime value (DESC)
            self.file_list = sorted(self.file_list, key=itemgetter('dt'), reverse=True)
            # calculate cumulative size
            i = 0
            cumsum = 0
            for item in self.file_list:
                cumsum += item['size']
                item['cumsum'] = cumsum
                if(cumsum > max_size):
                    i = i + 1               
                    logging.info("Removing file {}: {}".format(i, item['file']))
                    os.remove(item['file'])
                    self.mqtt_client.publish(self.cnfg['mqtt']['topic_publish'].format(source_name=self.name)
                        , json.dumps({
                                        'status': self.state_msg,
                                        'deleted': item['file']
                                        })
                    )
            # remove empty directories
            for (_path, _dirs, _files) in os.walk(cleanup_path, topdown=False):
                if _files or _dirs: continue # skip remove
                try:
                    os.rmdir(_path)
                    logging.debug(F'Remove empty folder: {_path}')
                except OSError:
                    logging.exception('Folder not empty :')
        except:
            logging.exception("Storage Cleanup Error")

    def folder_size(self, path='.'):
        total = 0
        for entry in scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
                row = {
                            'file': entry.path,
                            'size': entry.stat().st_size,
                            'dt': entry.stat().st_ctime,
                         }
                self.file_list.append(row)
            elif entry.is_dir(follow_symlinks=False):
                total += self.folder_size(entry.path)
        return total

def vr_create(name, cnfg, mqtt_client):
    vr = vr_thread(name, cnfg, mqtt_client)
    vr.start()
    return vr