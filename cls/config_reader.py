#!/usr/bin/env python

import yaml
import os, sys, shutil
import logging, logging.config
from datetime import datetime
import importlib

from cls.misc import check_package_is_installed

def dict_templ_replace(dictionary, **kwargs):
    """ Function runs over dictionary keys and replace template values
    """
    for key, value in dictionary.items():
        if value and isinstance(value, dict):
            dictionary[key] = dict_templ_replace(value, **kwargs)
        else:
            if isinstance(value, str):
                dictionary[key] = dictionary[key].format(**kwargs)
    return dictionary

class config_reader():
    """     The aim of this class to read config file
    - combine global and local recorder configs
    - set default values if there key is ommited in config file
    - can be used both in main daemon process and child recorders
    """
    def __init__(self, filename, name_daemon=None, name_http=None, log_filename='sxvrs'):
        """ Load configuration file.
        """
        self.tensorflow_is_installed = check_package_is_installed('tensorflow')        
        self.logger = logging.getLogger(f"config_reader")
        try:
            # if there is no configuration file then create new one
            if not os.path.isfile(filename):
                shutil.copy('misc/default_config.yaml','cnfg')
                os.rename('cnfg/default_config.yaml', filename) 
            with open(filename) as yaml_data_file:
                txt_data = yaml_data_file.read()     
                cnfg = yaml.load(txt_data, Loader=yaml.FullLoader)
        except:
            self.logger.exception('Exception in reading config from YAML')
            raise  
        self.data = cnfg
        # setup logger from yaml config file
        cnfg['logger'] = dict_templ_replace(cnfg['logger'], log_filename=log_filename)
        logging.config.dictConfig(cnfg['logger'])      
        self.clear_logs_on_startup = cnfg.get('clear_logs_on_startup', False)     
        name_daemon = 'sxvrs_daemon' if name_daemon is None else name_daemon
        name_http = 'sxvrs_daemon' if name_http is None else name_http
        self.mqtt_name_daemon = cnfg['mqtt'].get('name_daemon', name_daemon)
        self.mqtt_name_http = cnfg['mqtt'].get('name_http', name_http)
        self.mqtt_server_host = cnfg['mqtt'].get('server_host','127.0.0.1')
        self.mqtt_server_port = cnfg['mqtt'].get('server_port', 1883)
        self.mqtt_server_keepalive = cnfg['mqtt'].get('server_keepalive',60)
        self.mqtt_login = cnfg['mqtt'].get('login', None)
        self.mqtt_pwd = cnfg['mqtt'].get('pwd', None)
        self.mqtt_topic_daemon_publish = cnfg['mqtt'].get('daemon_publish', 'sxvrs/clients/{source_name}')
        self.mqtt_topic_daemon_subscribe = cnfg['mqtt'].get('daemon_subscribe', 'sxvrs/daemon/{source_name}')
        self.mqtt_topic_client_publish = cnfg['mqtt'].get('client_publish', 'sxvrs/daemon/{source_name}')
        self.mqtt_topic_client_subscribe = cnfg['mqtt'].get('client_subscribe', 'sxvrs/clients/{source_name}')
        # temp storage RAM disk
        # folder name where RAM disk will be mounted
        self.temp_storage_path = cnfg.get('temp_storage_path', '/dev/shm/sxvrs')
        # Size of the RAM disk in MB
        self.temp_storage_size = cnfg.get('temp_storage_size', 128)
        self._temp_storage_cmd_mount = cnfg.get('temp_storage_cmd_mount', None) # 'mount -t tmpfs -o size={temp_storage_size}m tmpfs {temp_storage_path}'
        self._temp_storage_cmd_unmount = cnfg.get('temp_storage_cmd_unmount', 'umount {path}')
        # set config for each recorder
        self.recorders = {}
        for recorder in cnfg['recorders']:
            self.recorders[recorder] = recorder_configuration(self, cnfg, recorder)        
        # Object Detectors
        self.is_object_detector_cloud = 'object_detector_cloud' in cnfg
        if self.is_object_detector_cloud:
            self.object_detector_cloud_url = cnfg['object_detector_cloud'].get('url') # url of the cloud API
            self.object_detector_cloud_key = cnfg['object_detector_cloud'].get('key') # obtain your personal key from cloud server
            self.object_detector_timeout = cnfg['object_detector_cloud'].get('timeout', 300) # in seconds
            self.object_detector_min_score = cnfg['object_detector_cloud'].get('min_score', 30) # min score from 0..100
            # object detector watch folder for new files, will sleep if there is no any new file (seconds)
            self.object_detector_sleep_time= cnfg['object_detector_local'].get('sleep_time', 0.5)
        self.is_object_detector_local = 'object_detector_local' in cnfg
        if self.is_object_detector_local:
            self._object_detector_local_model_path = cnfg['object_detector_local'].get('model_path', 'models/{model_name}/frozen_inference_graph.pb')
            self.object_detector_local_model_name = cnfg['object_detector_local'].get('model_name', 'not_defined')
            self.object_detector_local_gpu = cnfg['object_detector_local'].get('gpu', 0) # 0 means dissable GPU
            self.object_detector_timeout = cnfg['object_detector_local'].get('timeout', 30) # in seconds
            self.object_detector_min_score = cnfg['object_detector_local'].get('min_score', 30) # min score from 0..100
            # tensorflow per_process_gpu_memory_fraction param can limit usage of GPU memory
            self.tensorflow_per_process_gpu_memory_fraction = cnfg['object_detector_local'].get('tensorflow_per_process_gpu_memory_fraction', None)
            # object detector watch folder for new files, will sleep if there is no any new file (seconds)
            self.object_detector_sleep_time= cnfg['object_detector_local'].get('sleep_time', 0.5)
        if self.object_detector_min_score == 0:
            self.object_detector_min_score = 0.01
        # HTTP Server configs
        self.is_http_server = 'http_server' in cnfg
        if self.is_http_server:
            self.http_server_autostart = cnfg['http_server'].get('autostart', False) # if set, then daemon will start http server, otherwice it is possible to start manually
            self.http_server_host = cnfg['http_server'].get('host', '0.0.0.0')
            self.http_server_port = cnfg['http_server'].get('port', '8282')
            self._http_server_cmd = cnfg['http_server'].get('cmd', 'python sxvrs_http.py')
            self.http_refresh_img_speed= cnfg['http_server'].get('refresh_img_speed', 30) # image refresh speed (in seconds)
    @property
    def temp_storage_cmd_mount(self):
        if self._temp_storage_cmd_mount is None:
            return None
        else:
            return self._temp_storage_cmd_mount.format(temp_storage_path=self.temp_storage_path, temp_storage_size=self.temp_storage_size)
    @property
    def temp_storage_cmd_unmount(self):
        if self._temp_storage_cmd_unmount is None:
            return None
        else:
            return self._temp_storage_cmd_unmount.format(temp_storage_path=self.temp_storage_path, temp_storage_size=self.temp_storage_size)
    @property
    def object_detector_local_model_filename(self):
        return self._object_detector_local_model_path.format(model_name=self.object_detector_local_model_name)
    @property
    def is_object_detection(self):
        return self.is_object_detector_cloud or (self.is_object_detector_local and self.tensorflow_is_installed)
    def cmd_http_server(self, **kwargs):
        return self._http_server_cmd.format(**kwargs)

class recorder_configuration():
    """ Combines global and local parameter for given redcorder record
    """
    def combine(self, param, default=None, group=None):  
        """Function read configuration param from YAML returning local or global value"""
        if group is None:
            if param in self.data['recorders'][self.name]:
                return self.data['recorders'][self.name][param]
            else:
                if param in self.data['global']:
                    return self.data['global'][param]
                else:
                    return default
        else:
            if group in self.data['recorders'][self.name] and param in self.data['recorders'][self.name][group]:
                return self.data['recorders'][self.name][group][param]
            else:
                if group in self.data['global'] and param in self.data['global'][group]:
                    return self.data['global'][group][param]
                else:
                    return default
    def __init__(self, parent, cnfg, name):
        self.parent = parent
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
        # If recording not started, thread will sleep for {recorder_sleep_time} sec. Increase of this value, will cause in delay for response when changing state frop rec stopped to rec started
        self.recorder_sleep_time = self.combine('recorder_sleep_time', default=5)
        # If camera is in inactive state (can not be pinged) than try to check and ping it again every {camera_ping_interval} sec
        self.recorder_ping_interval = self.combine('ping_interval', default=30)
        # # If size of the image coming from IP camera is too big, then you can resize it
        self.resize_frame = not self.combine('resize_frame', default=None) is None     
        if self.resize_frame:            
            self.resize_frame_width = self.combine('width', group='resize_frame', default=None)
            self.resize_frame_height = self.combine('height', group='resize_frame', default=None)
        # Take snapshot every <snapshot_time> seconds
        self.snapshot_time = self.combine('snapshot_time', default=5)
        # maximum storage folder size in GB. If it exceeds, then the oldes files will be removed
        self.storage_max_size = self.combine('storage_max_size', default=10)
        # folder for storing recorded files. This is template and can be formated with {name} and {datetime} params
        self._storage_path = self.combine('storage_path', default='storage/{name}')
        # filename for the temp file in RAM disk. This is template and can be formated with {name},{storage_path}  and {datetime} params
        self._filename_temp = self.combine('filename_temp', default="{temp_storage_path}/{name}_{frame_num}_{datetime:%H%M%S.%f}")
        # filename for the snapshot. This is template and can be formated with {name},{storage_path} and {datetime} params
        self._filename_snapshot = self.combine('filename_snapshot', default='{storage_path}/snapshot.jpg')
        # filename for recording. This is template and can be formated with {name},{storage_path} and {datetime} params
        self._filename_video = self.combine('filename_video', default="{storage_path}/{datetime:%Y-%m-%d}/{name}_{datetime:%Y%m%d_%H%M%S}.mp4")
        # shell command to just take snapshot
        #self._cmd_take_snapshot = self.combine('cmd_take_snapshot', 'ffmpeg -hide_banner -nostdin -nostats -flags low_delay -fflags +genpts+discardcorrupt -y -i "{stream_url}" -vframes 1 "{filename}"')
        self._cmd_take_snapshot = self.combine('cmd_take_snapshot', 'python sxvrs_recorder.py -n {name} --snapshot_mode -fh {frame_height} -fw {frame_width} -fc {frame_channels}')
        # shell command for recorder start (used in daemon thread)
        self._cmd_recorder_start = self.combine('cmd_recorder_start', 'python sxvrs_recorder.py -n {name} -fh {frame_height} -fw {frame_width} -fc {frame_channels}')
        # shell command to start ffmpeg and read frames (used inside recorder subprocess)
        self._cmd_ffmpeg_read = self.combine('cmd_ffmpeg_read', default='ffmpeg -hide_banner -nostdin -nostats -flags low_delay -fflags +genpts+discardcorrupt -y -i "{stream_url}" -f rawvideo -pix_fmt rgb24 pipe:')
        # shell command to start ffmpeg and write video from collected frames (used inside recorder subprocess)
        self._cmd_ffmpeg_write = self.combine('cmd_ffmpeg_write', default='ffmpeg -hide_banner -nostdin -nostats -y -f rawvideo -vcodec rawvideo -s {width}x{height} -pix_fmt rgb{pixbytes} -r 5 -i - -an -c:v libx264 -crf 26 -preset fast "{filename}"')
        # if there is too many errors to connect to video source, then try to sleep some time before new attempts
        self.start_error_atempt_cnt = self.combine('start_error_atempt_cnt', default=10)
        self.start_error_threshold = self.combine('start_error_threshold', default=10)
        self.start_error_sleep = self.combine('start_error_sleep', default=600)
        # ffmpeg buffer frame count
        self.ffmpeg_buffer_frames = self.combine('ffmpeg_buffer_frames', default=16)
        # How many frames will be skipped between motion detection
        self.frame_skip = self.combine('frame_skip', default=5)
        # To detect duplicate frames comparing hash of frame miniature, it is possible to define frame_comparing_width for this miniature
        self.frame_comparing_width = self.combine('frame_comparing_width', default=32)
        # If there are too many files on RAM disk, then start to increase frame skipping (throttling) (means that motion_detection or object detection are not fast enough to process files)
        self.throttling_min_mem_size = self.combine('throttling_min_mem_size', default=32)*1024*1024
        # If total size of files exceeds maximum value, then disable frame saving to RAM folder (means that new frames are not added for processing if memory reaches max size)
        self.throttling_max_mem_size = self.combine('throttling_max_mem_size', default=64)*1024*1024
        ### watcher params ###        
        # before motion detection, image is resized for reducing of calculations
        self.motion_detector_max_image_height = self.combine('max_image_height', group='motion_detector', default=128)
        self.motion_detector_max_image_width = self.combine('max_image_width', group='motion_detector', default=128)
        # number of frames to remember for the background (selected randomly)
        self.motion_detector_bg_frame_count = self.combine('bg_frame_count', group='motion_detector', default=5)
        # threshold for binarized image difference in motion detector
        self.motion_detector_threshold = self.combine('motion_detector_threshold', group='motion_detector', default=15)
        # If defined <contour_detection> then it will try to detect motion by detecting contours inside the frame (slightly cpu expensive operation)
        _motion_detector = self.combine('motion_detector', default=[])  
        self.is_motion_detection =  self.combine('enabled', group='motion_detector', default=False)
        # sleep_time while looking for a new file ready for detection
        self.motion_detection_sleep_time =  self.combine('sleep_time', group='motion_detector', default=1)
        self.is_motion_contour_detection = 'contour_detection' in _motion_detector
        if self.is_motion_contour_detection:
            _motion_contour_detection = self.combine('contour_detection', group='motion_detector', default=[])
            # to trigger motion event, motion contour area must have minimum size
            self.motion_contour_min_area = _motion_contour_detection.get('min_area', "0.5%")
            # if changes are too big (i.e. all image is changed) then ignore it
            self.motion_contour_max_area = _motion_contour_detection.get('max_area', "50%")
            # if there are too many contours, than there is an interference (such as rain, snow etc..)
            self.motion_contour_max_count = _motion_contour_detection.get('max_count', '100')
        # if <contour_detection> is not enabled, then trigger detect event by difference threshold
        self.detect_by_diff_threshold = self.combine('detect_by_diff_threshold', group='motion_detector', default=5)
        # min_frames_changes: 4 - how many frames must be changed, before triggering for the motion start
        self.motion_min_frames_changes = self.combine('min_frames_changes', group='motion_detector', default=3)
        # max_frames_static: 2 - how many frames must be static, before assume that there is no motion anymore
        self.motion_max_frames_static = self.combine('max_frames_static', group='motion_detector', default=2)
        # blur_size: 15 - blur image before compaing with background
        self.motion_blur_size = self.combine('blur_size', group='motion_detector', default=15)
        # if set debug filename, then write snapshots there
        self._filename_debug = self.combine('filename_debug', group='motion_detector')
        self._filename_debug_bg = self.combine('filename_debug_bg', group='motion_detector')
        # if set {filename_last_motion} then will save last detected motion into this file
        self._filename_last_motion = self.combine('filename_last_motion', group='motion_detector', default='{storage_path}/last_motion.jpg')
        # motion detector watch folder for files with detected objects (seconds)
        self.object_watch_delay = self.combine('object_watch_delay', group='motion_detector', default=0.5)
        # if there are too many motiondetection events without object detection, then start throttling of object detection
        self.object_throttling = self.combine('object_throttling', group='motion_detector', default=10)
        if self.object_throttling < 1:
            self.object_throttling = 1
        self.memory_remember_time = self.combine('remember_time', group='memory', default=300)
        # if two objects are shifted less than <move_threshold> value then it is the same objects (value in pixel)
        self.memory_move_threshold = self.combine('move_threshold', group='memory', default=20)
        # if average from heigh and width is changed less than <size_similarity> % then it is the same object
        self.memory_size_similarity = self.combine('size_similarity', group='memory', default=60)
        # if area of intersection of two objects is greater than <area_intersect> then it is the same object
        self.memory_area_intersect = self.combine('area_intersect', group='memory', default=50)
        # the list of objects to be remembered. Empty array means any object will be remembered
        self.memory_objects = self.combine('objects', group='memory', default=[])
        # the list of objects to be excluded from remembering
        self.memory_objects_exclude = self.combine('objects_exclude', group='memory', default=[])        
        # determine interval to sending mqtt status
        self.send_status_interval = self.combine('send_status_interval', default=30)
        ### ObjectDetection block ###
        #_object_detector = self.combine('object_detector', default=[])  
        #self.is_object_detection = (not _object_detector is None) and len(_object_detector)>0  > move to entire configuration
        ### Action block ###
        self.actions = {}
        for action in self.combine('actions', default=[]):
            self.actions[action] = action_configuration(self, cnfg, recorder_name=self.name, action_name=action)

    def filename_debug(self, **kwargs):
        try:
            if 'name' not in kwargs:
                kwargs['name'] = self.name
            if 'datetime' not in kwargs:
                kwargs['datetime'] = datetime.now()
            if 'storage_path' not in kwargs:
                kwargs['storage_path'] = self.storage_path()
            return self._filename_debug.format(**kwargs)
        except:
            return None

    def filename_debug_bg(self, **kwargs):
        try:
            if 'name' not in kwargs:
                kwargs['name'] = self.name
            if 'datetime' not in kwargs:
                kwargs['datetime'] = datetime.now()
            if 'storage_path' not in kwargs:
                kwargs['storage_path'] = self.storage_path()
            return self._filename_debug_bg.format(**kwargs)
        except:
            return None

    def filename_last_motion(self, **kwargs):
        try:
            if 'name' not in kwargs:
                kwargs['name'] = self.name
            if 'datetime' not in kwargs:
                kwargs['datetime'] = datetime.now()
            if 'storage_path' not in kwargs:
                kwargs['storage_path'] = self.storage_path()
            return self._filename_last_motion.format(**kwargs)
        except:
            return None

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
        if self._filename_temp is None:
            return None
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'temp_storage_path'not in kwargs:
            kwargs['temp_storage_path'] = self.parent.temp_storage_path
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        return self._filename_temp.format(**kwargs)
        
    def filename_snapshot(self, **kwargs):
        if self._filename_snapshot is None:
            return None
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        return self._filename_snapshot.format(**kwargs)
        
    def filename_video(self, **kwargs):
        if self._filename_video is None:
            return None
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        return self._filename_video.format(**kwargs)
    
    def cmd_ffmpeg_read(self, **kwargs):
        if self._cmd_ffmpeg_read is None:
            return None
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
        if self._cmd_ffmpeg_write is None:
            return None
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
        if self._cmd_recorder_start is None:
            return None
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        if 'filename' not in kwargs:
            kwargs['filename'] = self.filename_snapshot()
        return self._cmd_recorder_start.format(**kwargs)

    def cmd_take_snapshot(self, **kwargs):
        if self._cmd_take_snapshot is None:
            return None
        if 'name' not in kwargs:
            kwargs['name'] = self.name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.storage_path()
        if 'stream_url' not in kwargs:
            kwargs['stream_url'] = self.stream_url()
        if 'filename' not in kwargs:
            kwargs['filename'] = self.filename_snapshot()
        return self._cmd_take_snapshot.format(**kwargs)
        
class action_configuration():
    """ Combines global and local parameter for given action record
    """
    def combine(self, param, default=None, group=None):  
        """Function read configuration param from YAML returning local or global value"""
        try:
            if group is None:
                if 'actions' in self.data['recorders'][self.recorder_name] and param in self.data['recorders'][self.recorder_name]['actions'][self.name]:
                    if param in self.data['recorders'][self.recorder_name]['actions'][self.name]:
                        return self.data['recorders'][self.recorder_name]['actions'][self.name][param]
                    else:
                        return default
                else:
                    if param in self.data['global']['actions'][self.name]:
                        return self.data['global']['actions'][self.name][param]
                    else:
                        return default
            else:
                if 'actions' in self.data['recorders'][self.recorder_name] and group in self.data['recorders'][self.recorder_name]['actions'][self.name] and param in self.data['recorders'][self.recorder_name]['actions'][self.name][group]:
                    if param in self.data['recorders'][self.recorder_name]['actions'][self.name][group]:
                        return self.data['recorders'][self.recorder_name]['actions'][self.name][group][param]
                    else:
                        return default
                else:
                    if group in self.data['global']['actions'][self.name] and param in self.data['global']['actions'][self.name][group]:
                        return self.data['global']['actions'][self.name][group][param]
                    else:
                        return default
        except:
            self.parent.parent.logger.error('param=%s default=%s, group=%s', param, default, group)
            raise
    def __init__(self, parent, cnfg, recorder_name, action_name):
        try:
            self.parent = parent
            self.data = cnfg
            self.recorder_name = recorder_name
            self.name = action_name
            # each action must have type
            self.type = self.combine('type')
            # each action can define area. If object inside this area, the action will be triggered
            self.area = self.combine('area', default = [])
            # the score of detected objects
            self.score = self.combine('score', default = 50)
            # the list of objects
            self.objects = self.combine('objects', default = [])
            # the list of objects to be excluded from action
            self.objects_exclude = self.combine('objects_exclude', default = [])
            # determine if we action must remember detected objects, and trigger only on new ones
            self.use_memory = self.combine('use_memory', default = False)
            #   for type = 'draw','copy','move','log'
            #if 'file' in cnfg:
            self._file_source = self.combine('source', group='file', default='{filename}')
            self._file_target = self.combine('target', group='file', default='{filename}')
            if isinstance(self._file_source, dict) or isinstance(self._file_target, dict):
                raise Exception('Filename must be a string. Please wrap with ""')
            #   for type = 'draw'
            # used for width of the drawing box border
            self.brush_size = self.combine('brush_size', default = 1)
            # quality for JPEG compression
            self.jpeg_quality = self.combine('jpeg_quality', default = 90)
            #   for type = 'mail'
            self.user = self.combine('user')
            self.password = self.combine('password')
            self.subject = self.combine('subject')
            self.mail_from = self.combine('mail_from')
            self.mail_to = self.combine('mail_to')
        except:
            self.parent.parent.logger.error("Action '%s' configuration error for recorder '%s'", action_name, recorder_name)
            raise

    def file_source(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.parent.name
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.parent.storage_path()
        if 'recorder_name' not in kwargs:
            kwargs['name'] = self.recorder_name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        return self._file_source.format(**kwargs)

    def file_target(self, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = self.parent.name
        if 'storage_path' not in kwargs:
            kwargs['storage_path'] = self.parent.storage_path()
        if 'recorder_name' not in kwargs:
            kwargs['name'] = self.recorder_name
        if 'datetime' not in kwargs:
            kwargs['datetime'] = datetime.now()
        return self._file_target.format(**kwargs)        
