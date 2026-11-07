"""Bridge _timing stamps, on with DAEDALUS_DEBUG_TIMING=1."""
import time

from daedalus_bridge.env_config import debug_timing

ENABLED = debug_timing()
_clock = time.time_ns


def _now_us():
    return _clock() // 1000


def drop_client_timing(message):
    message.pop('_timing', None)


def begin(cmd):
    if ENABLED and cmd.get('code'):
        cmd['_timing'] = {'srv_enqueue_us': _now_us()}


def stamp(message, name):
    if not ENABLED:
        drop_client_timing(message)
    elif isinstance(message.get('_timing'), dict):
        message['_timing'][name] = _now_us()
