"""Server-side stamps of an eval command's `_timing` object.

On only under `DAEDALUS_DEBUG_TIMING=1`, snapshotted at import. Off, nothing
here reads a clock or adds a key, so command and result bytes stay as they
were. Stamps are integer microseconds of the server wall clock.
"""
import time

from daedalus_bridge.env_config import debug_timing

ENABLED = debug_timing()
_clock = time.time_ns


def _now_us():
    return _clock() // 1000


def begin(cmd):
    """Open `_timing` on an eval command, stamped with its enqueue instant."""
    if ENABLED and cmd.get('code'):
        cmd['_timing'] = {'srv_enqueue_us': _now_us()}


def stamp(message, name):
    """Add `name` to the `_timing` object a command or result carries."""
    if ENABLED and isinstance(message.get('_timing'), dict):
        message['_timing'][name] = _now_us()
