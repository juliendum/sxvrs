#!/usr/bin/env python

"""     SXVRS Recorder
This script connects to video source stream, for taking snapshots, motion detection and video recording
Main features:
    1) take snapshot from video source
    2) record into file
    3) detect motion by comparing snapshots

Dependencies:
     ffmpeg
"""

__author__      = "Rustem Sharipov"
__copyright__   = "Copyright 2020"
__license__     = "GPL"
__version__     = "0.2.0"
__maintainer__  = "Rustem Sharipov"
__email__       = "zebatus@gmail.com"
__status__      = "Development"

import os, sys, logging
import argparse
import numpy as np
import cv2
from subprocess import Popen, PIPE
from datetime import datetime
from cls.config_reader import config_reader
from cls.misc import get_frame_shape
from cls.StorageManager import StorageManager
from cls.RAM_Storage import RAM_Storage

# Get command line arguments
parser = argparse.ArgumentParser()
parser.add_argument('-n','--name', help='Name of the recorder instance', required=True)
parser.add_argument('-fw','--frame_width', help='The width of the video frames in source stream', required=False)
parser.add_argument('-fh','--frame_height', help='The height of the video frames in source stream', required=False)
parser.add_argument('-fd','--frame_dim', help='The number of dimensions of the video frames in source stream. (By default = 3)', required=False, default=3)
#parser.add_argument('-','--', help='', default='default', required=False)
args = parser.parse_args()
_name = args.name
try:
    _frame_width = int(args.frame_width)
    _frame_height = int(args.frame_height)
    _frame_dim = int(args.frame_dim)
except:
    _frame_width = None
    _frame_height = None
    _frame_dim = 3

# Get running script name
script_path, script_name = os.path.split(os.path.splitext(__file__)[0])
app_label = script_name + f'_{datetime.now():%H%M}'

logger = logging.getLogger(_name)
dt_start = datetime.now()
logging.debug(f"> Start on: '{dt_start}'")

# Load configuration files
cnfg_daemon = config_reader(os.path.join('cnfg' ,'sxvrs.yaml'))
if _name in cnfg_daemon.recorders:
    cnfg = cnfg_daemon.recorders[_name]
else:
    msg = f"Recorder '{_name}' not found in config"
    logging.error(msg)
    raise ValueError(msg)

# Mount RAM storage disk
ram_storage = RAM_Storage(cnfg_daemon)

# calculate frame_size
if _frame_width is None or _frame_height is None or _frame_dim is None:
    frame_shape = get_frame_shape(cnfg.stream_url())
else:
    frame_shape = (_frame_height, _frame_width, _frame_dim)
frame_size = frame_shape[0] * frame_shape[1] * frame_shape[2]
logging.debug(f"frame_shape = {frame_shape}     frame_size = {frame_size}")

# Maintain Storage for the recorded files:
storage = StorageManager(cnfg.storage_path(), cnfg.storage_max_size)
storage.cleanup()
# Force create path for snapshot
storage.force_create_file_path(cnfg.filename_snapshot())
# Force create path for video file
filename_video = cnfg.filename_video()
storage.force_create_file_path(filename_video)

cmd_ffmpeg_read = cnfg.cmd_ffmpeg_read()
logging.debug(f"Execute process to read frames: {cmd_ffmpeg_read}")
ffmpeg_read = Popen(cmd_ffmpeg_read, shell=True, stdout = PIPE, bufsize=frame_size*cnfg.ffmpeg_buffer_frames)
cmd_ffmpeg_write = cnfg.cmd_ffmpeg_write(filename=filename_video, height=frame_shape[0], width=frame_shape[1], pixbytes=frame_shape[2]*8)
logging.debug(f"Execute process to write frames: {cmd_ffmpeg_write}")
ffmpeg_write = Popen(cmd_ffmpeg_write, shell=True, stdin = PIPE, bufsize=frame_size*cnfg.ffmpeg_buffer_frames)
i = 0
while True:
    frame_bytes = ffmpeg_read.stdout.read(frame_size)
    frame_np = (np.frombuffer(frame_bytes, np.uint8).reshape(frame_shape)) 
    if i % cnfg.frame_skip == 0:
        # save frame to snapshot file
        temp_frame_file = cnfg.filename_temp(storage_path=ram_storage.storage_path)
        cv2.imwrite(f'{temp_frame_file}.bmp', frame_np)
    # save frame to video file
    ffmpeg_write.stdin.write(frame_np.tostring())
    dt_end = datetime.now()
    if (dt_start - dt_start).total_seconds() >= cnfg.record_time:
        break
    i += 1

logging.debug(f"> Finish on: '{dt_end}'")