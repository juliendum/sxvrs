#!/usr/bin/env python

"""     Simple eXtendable Video Recording Script
Main features:
    1) The aim of this script is to run in a loop such tools like ffmpeg, vlc, openrtsp or any other command line application 
    2) Script able to maintain free space and record files in a loop
    3) Communicate with other software thrue MQTT

Dependencies:
    pip install pyyaml paho-mqtt
"""

__author__      = "Rustem Sharipov"
__copyright__   = "Copyright 2019"
__license__     = "GPL"
__version__     = "0.2.0"
__maintainer__  = "Rustem Sharipov"
__email__       = "zebatus@gmail.com"
__status__      = "Development"


import os, sys, logging
import argparse
import json
import time
import glob
from datetime import datetime
import paho.mqtt.client as mqtt
from subprocess import Popen, PIPE

from cls.CameraThread import camera_create
from cls.config_reader import config_reader
from cls.misc import check_topic
from cls.RAM_Storage import RAM_Storage
from cls.misc import SelectObjectDetector

# Get command line arguments
arg_parser = argparse.ArgumentParser()
arg_parser.add_argument('-http','--with_http_server', help='Determine if it is needed to start http', action='store_true')
args = arg_parser.parse_args()
start_with_http_server = args.with_http_server

# Get running script name
script_path, script_name = os.path.split(os.path.splitext(__file__)[0])
app_label = script_name + f'_{datetime.now():%H%M}'
dt_start = datetime.now()
stored_exception=None
camera_list = [] 

# Load configuration files
cnfg = config_reader(
        os.path.join('cnfg' ,'sxvrs.yaml'), 
        name_daemon = 'sxvrs_daemon',
        log_filename = 'daemon'
    )
# force clear logs on startup for debugging
if cnfg.clear_logs_on_startup:
    for file in glob.glob('logs/*'):
        try:
            os.remove(file)
        except:
            print(f'Can''t remove file: {file}')
    logging.config.dictConfig(cnfg.data['logger']) 
logger = logging.getLogger(f"{script_name}")
logger.debug(f"> Start on: '{dt_start}'")

# Mount RAM storage disk
ram_storage = RAM_Storage(cnfg, logger_name = logger.name)
ram_storage.clear()

# MQTT event listener
def on_mqtt_message(client, userdata, message):
    """Provides reaction on all events received from MQTT broker"""
    try:
        payload = {}
        if len(message.payload)>0:
            payload = json.loads(str(message.payload.decode("utf-8")))
        # topic = list 
        if check_topic(message.topic, "list"):
            names = []
            for camera in camera_list:
                names.append(camera.name)
            mqtt_client.publish(
                        cnfg.mqtt_topic_daemon_publish.format(source_name='list'),            
                        json.dumps(names)
                        )
            logger.debug(f"MQTT publish: {cnfg.mqtt_topic_daemon_publish.format(source_name='list')} [{names}]")
        # topic = daemon
        elif check_topic(message.topic, "daemon"):
            if payload.get('cmd').lower()=='restart':
                try:
                    logger.info("Restart command recieved")
                    mqtt_client.disconnect()
                    args = sys.argv[:]
                    exe = sys.executable
                    logger.info("> "*5 + " Restarting the daemon " + " <"*5)
                    args.insert(0, sys.executable)
                    if sys.platform == 'win32':
                        args = ['"%s"' % arg for arg in args]
                    os.execv(exe, args)
                except:
                    logger.exception("Can't restart daemon")                
        # topic = <recorder_name>
        else:
            for camera in camera_list:
                if check_topic(message.topic, camera.name.lower()):
                    if payload.get('cmd').lower()=='record_start':
                        camera.record_start()
                    elif payload.get('cmd').lower()=='record_stop':
                        camera.record_stop()
                    elif payload.get('cmd').lower()=='status':
                        camera.mqtt_status()
                    elif payload.get('cmd').lower()=='watcher_start':
                        camera.watcher_start()
                    elif payload.get('cmd').lower()=='watcher_stop':
                        camera.watcher_stop()
    except:
        logger.exception(f'Error on_mqtt_message() topic: {message.topic} msg_len={len(message.payload)}')

def on_mqtt_connect(client, userdata, flags, rc):
    client.connection_rc = rc
    if rc==0:
        client.is_connected = True
    else:
        logger.error(f"MQTT connection failure with code={rc}")

# setup MQTT connection
try:
    mqtt_client = mqtt.Client(cnfg.mqtt_name_daemon) #create new instance
    mqtt_client.is_connected = False
    mqtt_client.connection_rc = 3
    mqtt_client.enable_logger(logger)
    mqtt_client.on_message=on_mqtt_message #attach function to callback
    mqtt_client.on_connect=on_mqtt_connect #attach function to callback
    #try to connect to broker in a loop, until server becomes available
    logger.debug(f"host={cnfg.mqtt_server_host}, port={cnfg.mqtt_server_port}, keepalive={cnfg.mqtt_server_keepalive}")
    mqtt_client.loop_start()
    if cnfg.mqtt_login!=None: 
        mqtt_client.username_pw_set(cnfg.mqtt_login, cnfg.mqtt_pwd)
    while mqtt_client.connection_rc==3:
        try:
            logger.info(f"Try to connect to MQTT Server..")                
            mqtt_client.connect(cnfg.mqtt_server_host, 
                port=cnfg.mqtt_server_port,
                keepalive=cnfg.mqtt_server_keepalive
                ) 
            while not mqtt_client.is_connected: # blocking code untill connection
                time.sleep(1)                
        except ConnectionRefusedError:
            mqtt_client.connection_rc==3
            wait = 1
            logger.info(f"Server is offline. Wait {wait} sec before retry")
            time.sleep(wait) # if server is not available, then wait for it
    logger.info(f"Connected to MQTT: {cnfg.mqtt_server_host}")
    mqtt_client.subscribe(cnfg.mqtt_topic_daemon_subscribe.format(source_name='#'))  
    logger.debug(f"MQTT subscribe: {cnfg.mqtt_topic_daemon_subscribe.format(source_name='#')}")
except :
    logger.exception(f"Can't connect to MQTT broker at address: {cnfg.mqtt_server_host}:{cnfg.mqtt_server_port}")
    stored_exception=sys.exc_info()    

_old_excepthook = sys.excepthook
def myexcepthook(exctype, value, traceback):
    global stored_exception
    if exctype == KeyboardInterrupt:
        stored_exception=sys.exc_info()
    _old_excepthook(exctype, value, traceback)
sys.excepthook = myexcepthook

if stored_exception==None:
    logger.info(f'! Script started: "{script_name}" Press [CTRL+C] to exit')
    # create and start all instances from config
    cnt_instanse = 0
    watchers = []
    for recorder, configuration in cnfg.recorders.items():
        camera_list.append(camera_create(recorder, cnfg_daemon=cnfg, cnfg_recorder=configuration, mqtt_client=mqtt_client))
        cnt_instanse += 1
    # Start Object Detector
    object_detector = SelectObjectDetector(cnfg, logger_name = logger.name)
    # Start HTTP web server
    if cnfg.is_http_server and (start_with_http_server or cnfg.http_server_autostart):
        Popen(cnfg.cmd_http_server(), shell=True)
    # Main loop start
    while stored_exception==None:
        try:
            print(f'{datetime.now()}: recorders: {cnt_instanse}     ', end = '\r')
            time.sleep(2)
            if stored_exception:
                break        
        except (KeyboardInterrupt, SystemExit):
            logger.info("MainLoop [CTRL+C] detected. Please wait for termination of all processes.. ")
            stored_exception=sys.exc_info()

    # Stop all instances
    for camera in camera_list:
        camera.stop()
        logger.debug(f"   stoping instance: {camera.name}")
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    if not object_detector is None:
        object_detector.stop_watch()
    logger.info('# Script terminated')

if stored_exception and stored_exception[0]!=KeyboardInterrupt:
    raise Exception(stored_exception[0], stored_exception[1], stored_exception[2])
