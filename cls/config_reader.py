#!/usr/bin/env python

import yaml
import logging, logging.config
from datetime import datetime

class config_reader():
    """     The aim of this class to read config file
    - combine global and local recorder configs
    - set default values if there key is ommited in config file
    - can be used both in main daemon process and child recorders
    """
    def __init__(self, filename, name=None):
        """ Load configuration file.
        """
        try:
            with open(filename) as yaml_data_file:
                txt_data = yaml_data_file.read()     
                cnfg = yaml.load(txt_data, Loader=yaml.FullLoader)
        except:
            logging.exception('Exception in reading config from YAML')
            raise  
        self.data = cnfg
        # setup logger from yaml config file
        logging.config.dictConfig(cnfg['logger'])          
        if name is None:
            name = 'sxvrs_daemon'
        self.mqtt_name = cnfg['mqtt'].get('name', name)
        self.mqtt_server_host = cnfg['mqtt'].get('server_ip','127.0.0.1')
        self.mqtt_server_port = cnfg['mqtt'].get('server_port', 1883)
        self.mqtt_server_keepalive = cnfg['mqtt'].get('server_keepalive',60)
        self.mqtt_login = cnfg['mqtt'].get('login', None)
        self.mqtt_pwd = cnfg['mqtt'].get('pwd', None)
        self.mqtt_topic_daemon_publish = cnfg['mqtt'].get('topic_publish', 'sxvrs/clients/{source_name}')
        self.mqtt_topic_daemon_subscribe = cnfg['mqtt'].get('topic_subscribe', 'sxvrs/daemon/{source_name}')
        # temp storage RAM disk
        # folder name where RAM disk will be mounted
        self.temp_storage_path = cnfg.get('temp_storage_path', '/mnt/ramdisk')
        # Size of the RAM disk in MB
        self.temp_storage_size = cnfg.get('temp_storage_size', 128)
        self._temp_storage_cmd_mount = cnfg.get('temp_storage_cmd_mount', 'mount -t tmpfs -o size={size}m tmpfs {path}')
        self._temp_storage_cmd_unmount = cnfg.get('temp_storage_cmd_unmount', 'umount {path}')

        # set config for each recorder
        self.recorders = {}
        for recorder in cnfg['recorders']:
            self.recorders[recorder] = recorder_configuration(cnfg, recorder)
    
    @property
    def temp_storage_cmd_mount(self):
        return self._temp_storage_cmd_mount.format(temp_storage_path=self.temp_storage_path, temp_storage_size=self.temp_storage_size)
    @property
    def temp_storage_cmd_unmount(self):
        return self._temp_storage_cmd_unmount.format(temp_storage_path=self.temp_storage_path, temp_storage_size=self.temp_storage_size)

class recorder_configuration():
    """ Combines global and local parameter for given redcorder record
    """
    def combine(self, param, default=None):  
        """Function read configuration param from YAML returning local or global value"""
        if param in self.data['recorders'][self.name]:
            return self.data['recorders'][self.name][param]
        else:
            if param in self.data['global']:
                return self.data['global'][param]
            else:
                return default
    def __init__(self, cnfg, name):
        self.data = cnfg
        self.mqtt_topic_recorder_publish = cnfg['mqtt'].get('topic_publish', 'sxvrs/clients/{source_name}')
        self.mqtt_topic_recorder_subscribe = cnfg['mqtt'].get('topic_subscribe', 'sxvrs/daemon/{source_name}')
        # unique name for the recorder instance (used in mqtt and filename template)
        self.name = name 
        # ip adress for the camera (used in stream_url template)
        self.ip = self.combine('ip')
        # stream_url - ffmpeg will connect there (can be formated with {ip} param)
        self._stream_url = self.combine('stream_url')
        # start recording immidiately after creation on object
        self.record_autostart = self.combine('record_autostart', default=False)
        # the duration of the recording into one file
        self.record_time = self.combine('record_time', default=600)
        # maximum storage folder size in GB. If it exceeds, then the oldes files will be removed
        self.storage_max_size = self.combine('storage_max_size', default=10)
        # folder for storing recordings. This is template and can be formated with {name} and {datetime} params
        self._storage_path = self.combine('storage_path', default='storage/{name}')
        # filename for the temp file in RAM disk. This is template and can be formated with {name},{storage_path}  and {datetime} params
        self._filename_temp = self.combine('filename_temp')
        # filename for the snapshot. This is template and can be formated with {name},{storage_path} and {datetime} params
        self._filename_snapshot = self.combine('filename_snapshot', default='{storage_path}/snapshot.jpg')
        # filename for recording. This is template and can be formated with {name},{storage_path} and {datetime} params
        self._filename_video = self.combine('filename_video', default="{storage_path}/{datetime:%Y-%m-%d}/{name}_{datetime:%Y%m%d_%H%M%S}.mp4")
        # shell command for recorder start (used in daemon thread)
        self._cmd_recorder_start = self.combine('cmd_recorder_start', 'python sxvrs_recorder.py -n {name}')
        # shell command to start ffmpeg and read frames (used inside recorder subprocess)
        self._cmd_ffmpeg_read = self.combine('cmd_ffmpeg_read', default='ffmpeg -hide_banner -nostdin -nostats -fflags nobuffer -flags low_delay -fflags +genpts+discardcorrupt -y -i "{stream_url}" -f rawvideo -pix_fmt rgb24 pipe:')
        # shell command to start ffmpeg and write video from collected frames (used inside recorder subprocess)
        self._cmd_ffmpeg_write = self.combine('cmd_ffmpeg_write', default='ffmpeg -hide_banner -nostdin -nostats -y -f rawvideo -vcodec rawvideo -s {width}x{height} -pix_fmt rgb{pixbytes} -r {pixbytes} -i - -an -vcodec mpeg4 "{filename}"')
        # if there is too many errors to connect to video source, then try to sleep some time before new attempts
        self.start_error_atempt_cnt = self.combine('start_error_atempt_cnt', default=10)
        self.start_error_threshold = self.combine('start_error_threshold', default=10)
        self.start_error_sleep = self.combine('start_error_sleep', default=600)
        # ffmpeg buffer frame count
        self.ffmpeg_buffer_frames = self.combine('ffmpeg_buffer_frames', default=16)
        # How many frames will be scipped between motion detection
        self.frame_skip = self.combine('frame_skip', default=5)
        # if on RAM disk there will be too many files, then start to increase frame skiping
        self.throtling_min_mem_size = self.combine('throtling_min_mem_size', default=5)*1024*1024
        # if total size of files exceeds maximum value, then dissable frame saving to RAM folder
        self.throtling_max_mem_size = self.combine('throtling_max_mem_size', default=10)*1024*1024


    def stream_url(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'ip' not in kwargs:
            kwargs['ip'] = self.ip
        return self._stream_url.format(**kwargs)

    def storage_path(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        return self._storage_path.format(**kwargs)
    
    def filename_temp(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        return self._filename_temp.format(**kwargs)
        
    def filename_snapshot(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        return self._filename_snapshot.format(**kwargs)
        
    def filename_video(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        return self._filename_video.format(**kwargs)
    
    def cmd_ffmpeg_read(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        if 'stream_url' not in kwargs:
            kwargs['stream_url'] = self.stream_url()
        return self._cmd_ffmpeg_read.format(**kwargs)
    
    def cmd_ffmpeg_write(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        if 'filename' not in kwargs:
            kwargs['filename'] = self.filename_video()
        return self._cmd_ffmpeg_write.format(**kwargs)

    def cmd_recorder_start(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        if 'filename' not in kwargs:
            kwargs['filename'] = self.filename_snapshot()
        return self._cmd_recorder_start.format(**kwargs)
        