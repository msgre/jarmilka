# -*- coding: utf-8 -*-

import logging
import os
import random
import re
import shlex
import subprocess
import time

import RPi.GPIO as GPIO

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

from pyudev import Context

DEBUG = False

RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
LIGHT_PURPLE = '\033[94m'
PURPLE = '\033[95m'
END = '\033[0m'

# hard coded defaults
INPUT_USB  = '/devices/platform/soc/20980000.usb/usb1/1-1/1-1.2/1-1.2.1/1-1.2.1:1.0'
OUTPUT_USB = '/devices/platform/soc/20980000.usb/usb1/1-1/1-1.2/1-1.2.4/1-1.2.4.1/1-1.2.4.1:1.0'

LED_1_PIN = 2
LED_2_PIN = 3
LED_BUTTON_PIN = 4

BUTTON_PIN = 15

WAIT = 0.3

def setup_gpio():
    """
    Setup GPIO pins on which LEDs and button switch are connected.
    """
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # led output
    GPIO.setup(LED_1_PIN, GPIO.OUT)
    GPIO.setup(LED_2_PIN, GPIO.OUT)
    GPIO.setup(LED_BUTTON_PIN, GPIO.OUT)

    # button input
    GPIO.setup(BUTTON_PIN, GPIO.IN, GPIO.PUD_UP)

    # switch leds off
    reset_led()

def reset_led(which=None):
    """
    Switch off all or only selected LEDs.
    """
    if not which:
        GPIO.output(LED_1_PIN, 0)
        GPIO.output(LED_2_PIN, 0)
        GPIO.output(LED_BUTTON_PIN, 0)
    else:
        for pin in which:
            GPIO.output(pin, 0)

def get_usb_devices(context):
    """
    Get info about monitored USB devices.
    """
    out = []
    for device in context.list_devices(subsystem='usb'):
        if device.device_path not in [INPUT_USB, OUTPUT_USB]:
            continue
        out.append(device.device_path)
    return out


USB_DRIVE_RE = re.compile(r'^(/dev/[0-9a-z]+) (/media/usb[0-9]+) .+$')

def get_mounted_drives():
    """
    Get info about mounted devices.
    """
    with open('/etc/mtab') as f:
        out = [USB_DRIVE_RE.search(i).groups() for i in f.readlines() if USB_DRIVE_RE.search(i)]
    return out


def call_command(cmd, cwd=None, timeout=None, wait=False):
    """
    Helper function for calling given command.
    """
    args = shlex.split(cmd)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, cwd=cwd)

    if timeout:
        t = 0
        delta = 0.1
        while t < timeout:
            time.sleep(delta)
            t = t + 0.1
            if p.poll() is not None:
                break
        if p.poll() is None:
            p.kill()

    if wait:
        (stdout, stderr) = p.communicate()
        return p.returncode == 0, stdout.strip()
    return None, None


SOUNDS_DIR = '/application/sounds/'
SOUNDS = {
    'one':
        ['one_01.wav', 'one_02.wav', 'one_03.wav',],
    'two':
        ['two_01.wav', 'two_02.wav', 'two_03.wav',],
    'button':
        ['button_01.wav', 'button_02.wav', 'button_03.wav',],
    'done':
        ['done_01.wav', 'done_02.wav', 'done_03.wav', 'done_04.wav',],
    'processing':
        ['processing_01.wav', 'processing_02.wav', 'processing_03.wav',],
    'eject':
        ['eject_01.wav', 'eject_02.wav',],
    'problem':
        ['problem_01.wav', 'problem_02.wav', 'problem_03.wav',],
}

def play(sound):
    idx = random.randint(0, len(SOUNDS[sound])-1)
    path = os.path.join(SOUNDS_DIR, SOUNDS[sound][idx])
    command = '/usr/bin/aplay %s' % path
    call_command(command)


# ------------------------------------------

DATA = {}
FROM_DRIVE = None
FROM_DEVICE = None
FROM_PATH = None
TO_DRIVE = None
TO_DEVICE = None
TO_PATH = None

def update_state_data(state, **kwargs):
    global DATA
    for k, v in kwargs.items():
        if state not in DATA:
            DATA[state] = {}
        DATA[state][k] = v

def set_state(state, data=None):
    logger.debug("Setting state %s with data %s." % (state, str(data)))
    if data:
        update_state_data(state, **data)
    return state


# empty

PHOTO_RE = re.compile(r'^\d{3}___\d{2}$') # ie "119___06"

def set_state_empty(silent=False):
    reset_led()
    if not silent:
        play('one')
    return set_state('empty', {'blink': [LED_1_PIN]})

def process_state_empty(state, context, data, silent=False):
    devices = get_usb_devices(context)
    if INPUT_USB in devices:
        logger.debug("Source USB device recognised.")
        GPIO.output(LED_1_PIN, 1)
        mounted = get_mounted_drives()
        logger.debug("List of mounted drives: %s" % ", ".join(map(str, mounted)))
        if len(mounted) == 1:
            logger.info("Source USB device mounted.")
            global FROM_DEVICE
            global FROM_DRIVE
            global FROM_PATH
            global TO_PATH
            FROM_DEVICE = mounted[0][0]
            FROM_DRIVE = mounted[0][1]
            # recognise type of source media
            if 'DCIM' not in os.listdir(FROM_DRIVE):
                # source drive is something unknown
                logger.debug("Source drive doesn't have DCIM directory. Does it really came from camera/phone?")
                state = set_state_problem()
            files = os.listdir(os.path.join(FROM_DRIVE, 'DCIM'))
            if 'Camera' in files:
                # source drive is from phone
                logger.info("Source drive seems like Android phone.")
                FROM_PATH = 'DCIM/Camera'
                TO_PATH = 'originaly/telefon'
                state = set_state_connected_1()
            elif any([i for i in files if PHOTO_RE.match(i)]):
                # source drive is from camera
                logger.info("Source drive seems like Camera.")
                FROM_PATH = 'DCIM'
                TO_PATH = 'originaly/fotak'
                state = set_state_connected_1()
            else:
                # source drive is something unknown
                logger.warn("Source drive wasn't recognised.")
                logger.warn("File list: %s" % ", ".join(map(str, files)))
                state = set_state_problem()

    elif OUTPUT_USB in devices:
        logger.info("Wrong order of mounting: expect connecting of source drive, but destination was mounted first.")
        reset_led()
        mounted = get_mounted_drives()
        if len(mounted) == 1:
            state = set_state_problem()
    return state

# connected_1

def set_state_connected_1(silent=False):
    if not silent:
        play('two')
    return set_state('connected_1', {'blink': [LED_2_PIN]})

def process_state_connected_1(state, context, data, silent=False):
    devices = get_usb_devices(context)
    if OUTPUT_USB in devices:
        logger.debug("Destination USB device recognised.")
        GPIO.output(LED_2_PIN, 1)
        mounted = get_mounted_drives()
        logger.debug("List of mounted drives: %s" % ", ".join(map(str, mounted)))
        if len(mounted) == 2:
            logger.info("Destination USB device mounted.")
            mounted = [i for i in mounted if i[1] != FROM_DRIVE]
            global TO_DEVICE
            global TO_DRIVE
            TO_DEVICE = mounted[0][0]
            TO_DRIVE = mounted[0][1]
            # recognise type of destination media
            files = os.listdir(TO_DRIVE)
            if 'fotky' in files and \
               'videa' in files and \
               'originaly' in files:
                # destination drive is USB HDD
                logger.info("Destination drive seems like known USB HDD.")
                state = set_state_connected_2()
            else:
                # there is something as destination drive, but it doesn't look familiar
                logger.warn("Destination drive wasn't recognised.")
                logger.warn("File list: %s" % ", ".join(map(str, files)))
                state = set_state_problem()

    elif INPUT_USB not in devices:
        logger.info("Wrong order of mounting: expect connecting of destination drive, instead source was unmounted.")
        reset_led()
        state = set_state_empty()
    return state

# connected_2

def set_state_connected_2(silent=False):
    if not silent:
        play('button')
    return set_state('connected_2', {'blink': [LED_BUTTON_PIN]})

def process_state_connected_2(state, context, data, silent=False):
    devices = get_usb_devices(context)
    if INPUT_USB in devices and OUTPUT_USB in devices:
        logger.debug('Both devices (source and destination) prepared, waiting for button.')
        GPIO.output(LED_BUTTON_PIN, 1)
        if not GPIO.input(BUTTON_PIN):
            logger.info('Button pressed.')
            state = set_state_processing()
    else:
        logger.warn('Some of the device is not connected, devices %s' % ", ".join(devices))
        reset_led([LED_2_PIN])
        state = set_state_connected_1()
    return state

# problem

def set_state_problem(silent=False):
    if not silent:
        play('problem')
    return set_state('problem')

def process_state_problem(state, context, data, silent=False):
    state = set_state_filled()
    return state

# processing

def set_state_processing(silent=False):
    if not silent:
        play('processing')
    return set_state('processing')

def process_state_processing(state, context, data, silent=False):
    source = os.path.join(FROM_DRIVE, FROM_PATH) + '/'
    destination = os.path.join(TO_DRIVE, TO_PATH) + '/'

    # copy files
    command = 'sudo /usr/bin/rsync -r %s %s' % (source, destination)
    logger.debug('Copying files: %s' % command)
    call_command(command, wait=True)

    # unmount devices
    logger.debug('Unmounting device %s' % FROM_DEVICE)
    call_command('sudo umount %s' % FROM_DEVICE, wait=True)
    logger.debug('Unmounting device %s' % TO_DEVICE)
    call_command('sudo umount %s' % TO_DEVICE, wait=True)

    state = set_state_done()
    return state

# done

def set_state_done(silent=False):
    if not silent:
        play('done')
    return set_state('done', {'blink': [LED_BUTTON_PIN], 'pause': 10, 'i': 0})

def process_state_done(state, context, data, silent=False):
    """
    cekani na tlacitko
    counter
    dokola "hotovo"
    blikani
    """
    logger.debug('Processing is done, waiting for button press.')
    if not GPIO.input(BUTTON_PIN):
        logger.info('Button pressed.')
        state = set_state_filled()
    else:
        if data['i'] > data['pause']:
            play('done')
            update_state_data('done', i=0)
        else:
            update_state_data('done', i=data['i'] + WAIT)
    return state

# filled

def set_state_filled(silent=False):
    reset_led()
    if not silent:
        play('eject')

    return set_state('filled')

def process_state_filled(state, context, data, silent=False):
    devices = get_usb_devices(context)
    mounted = get_mounted_drives()

    if len(devices) == 0 and len(mounted) == 0:
        state = set_state_empty()
    elif mounted:
        for device in mounted:
            logger.debug('Unmounting device %s' % device[0])
            call_command('sudo umount %s' % device[0], wait=True)

    return state


setup_gpio()

context = Context()

STATES = {
        'empty': process_state_empty,
        'connected_1': process_state_connected_1,
        'connected_2': process_state_connected_2,
        'problem': process_state_problem,
        'processing': process_state_processing,
        'done': process_state_done,
        'filled': process_state_filled,
}

blink = 0
state = set_state_filled(silent=True)
state = STATES[state](state, context, DATA.get(state, None), silent=True)
if state == 'filled':
    state = set_state_filled()

while True:
    state = STATES[state](state, context, DATA.get(state, None))
    if state in DATA and 'blink' in DATA[state]:
        for pin in DATA[state]['blink']:
            GPIO.output(pin, blink)
    time.sleep(WAIT)
    blink = 0 if blink else 1
