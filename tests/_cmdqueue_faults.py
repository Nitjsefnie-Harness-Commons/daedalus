"""Fault-injection controls for test-side command queue readers."""
import contextlib
import inspect
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cmdqueue  # noqa: E402

# A runaway is a wait that never ends, so the bound is on virtual time, not on
# how a wait spends it: any number of sleeps is fine, of any size, including
# none at all. Virtual sleeps cost no real time, so a wait that cannot finish
# reaches this in milliseconds and fails by name instead of hanging.
_RUNAWAY_ELAPSED = _cmdqueue.POLL_DELAY * 1000


def _queued_file(tmp, name='1700000000000_000001.json'):
    queue = Path(tmp) / 'queue'
    queue.mkdir(exist_ok=True)
    queued = queue / name
    queued.write_text(json.dumps({'id': 'queued', 'type': 'reload'}),
                      encoding='utf-8')
    return queue, queued


def _target_key(candidate):
    """Decode path spellings so str and bytes receivers share one key."""
    try:
        return os.fsdecode(os.fspath(candidate))
    except BaseException:
        # An exception from this injector probe is not from the caller's call
        # and must not surface. Cost: this C-level os.fspath call in the test
        # harness swallows a genuine interrupt on a str, bytes, or Path
        # receiver.
        return None


def _plain_read(handle, args, kwargs):
    """A call that creates or truncates is not the read an injector faults.

    The real API has already accepted this call, so its mode is a valid
    spelling and only has to be searched, never parsed. The handle cannot
    answer instead: a truncating `wb+` reports its mode as `rb+`. The search
    must not go through the mode object's own protocol.
    """
    mode = kwargs.get('mode', args[0] if args else 'r')
    return handle.readable() and not any(
        str.__contains__(mode, marker) for marker in 'wax')


def _native_read_handle(original, candidate, args, kwargs):
    """Return a non-readable open for the caller; None for a real read."""
    handle = original(candidate, *args, **kwargs)
    if _plain_read(handle, args, kwargs):
        handle.close()
        return None
    return handle


@contextlib.contextmanager
def _refuse_path_operation(path, operation, failures, clock=None):
    # Path.open and Path.read_text both delegate through io.open.
    read_operation = operation in ('open', 'read_text')
    original = io.open if read_operation else getattr(Path, operation)
    signature = inspect.signature(original)
    target_key = _target_key(path)
    remaining = [failures]
    calls = [0]

    def refused(candidate, *args, **kwargs):
        candidate_key = _target_key(candidate)
        if (read_operation and candidate_key == target_key
                and remaining[0]):
            # Native validation adds one open per faulted call.
            handle = _native_read_handle(original, candidate, args, kwargs)
            if handle is not None:
                return handle
        elif operation != 'open':
            try:
                signature.bind(candidate, *args, **kwargs)
            except TypeError:
                return original(candidate, *args, **kwargs)
        if candidate_key == target_key:
            if remaining[0]:
                remaining[0] -= 1
                calls[0] += 1
                if clock is not None:
                    clock.record_read()
                raise PermissionError(32, 'injected sharing violation')
            # A call the real API refuses performed no operation, so it is
            # neither counted nor recorded as one.
            result = original(candidate, *args, **kwargs)
            if read_operation and not _plain_read(
                    result, args, kwargs):
                return result
            calls[0] += 1
            if clock is not None:
                clock.record_read()
            return result
        return original(candidate, *args, **kwargs)

    if read_operation:
        io.open = refused
    else:
        setattr(Path, operation, refused)
    try:
        yield calls
    finally:
        if read_operation:
            io.open = original
        else:
            setattr(Path, operation, original)


@contextlib.contextmanager
def _virtual_cmdqueue_clock(max_sleeps=None):
    original = _cmdqueue.time
    # A large power-of-two origin exposes sleeps too small to move the clock.
    origin = _cmdqueue.POLL_DELAY * (1 << 24)
    now = [origin]
    events = []
    sleep_count = [0]
    # Read cost exposes stale deadline samples; the fallback avoids underflow.
    read_cost = _cmdqueue.POLL_DELAY / 10 or _cmdqueue.POLL_DELAY

    class Clock:
        def monotonic(self):
            return now[0]

        perf_counter = monotonic

        def record_read(self):
            events.append(('read', read_cost))
            now[0] += read_cost

        def sleep(self, seconds):
            if seconds < 0:
                raise ValueError('sleep length must be non-negative')
            if max_sleeps is not None and sleep_count[0] >= max_sleeps:
                raise AssertionError(
                    f'virtual clock exceeded {max_sleeps} sleeps')
            advanced = now[0] + seconds
            if advanced - origin > _RUNAWAY_ELAPSED:
                raise AssertionError(
                    'virtual clock ran '
                    f'{advanced - origin:.3f}s past its origin')
            sleep_count[0] += 1
            events.append(('sleep', seconds))
            now[0] = advanced

    clock = Clock()
    _cmdqueue.time = clock
    try:
        yield clock, events, origin
    finally:
        _cmdqueue.time = original


@contextlib.contextmanager
def _vanish_during_unlink(path):
    original = Path.unlink
    armed = [True]

    def vanished(candidate, *args, **kwargs):
        if candidate == path and armed[0]:
            armed[0] = False
            original(candidate, *args, **kwargs)
            raise FileNotFoundError(2, 'injected disappearance', str(path))
        return original(candidate, *args, **kwargs)

    Path.unlink = vanished
    try:
        yield
    finally:
        Path.unlink = original


@contextlib.contextmanager
def _vanish_during_read(path, clock, remove_queue=False):
    original = io.open
    target_key = _target_key(path)
    armed = [True]

    def vanished(candidate, *args, **kwargs):
        if _target_key(candidate) == target_key and armed[0]:
            handle = _native_read_handle(original, candidate, args, kwargs)
            if handle is not None:
                return handle
            armed[0] = False
            clock.record_read()
            path.unlink()
            if remove_queue:
                path.parent.rmdir()
            return original(candidate, *args, **kwargs)
        return original(candidate, *args, **kwargs)

    io.open = vanished
    try:
        yield
    finally:
        io.open = original


@contextlib.contextmanager
def _disappear_on_first_open(path):
    original = io.open
    target_key = _target_key(path)
    armed = [True]

    def missing(candidate, *args, **kwargs):
        if _target_key(candidate) == target_key and armed[0]:
            handle = _native_read_handle(original, candidate, args, kwargs)
            if handle is not None:
                return handle
            armed[0] = False
            raise FileNotFoundError(2, 'injected disappearance', str(path))
        return original(candidate, *args, **kwargs)

    io.open = missing
    try:
        yield
    finally:
        io.open = original


@contextlib.contextmanager
def _refuse_first_queue_read(queue):
    original = io.open
    queue_key = _target_key(queue)
    refused_path = [None]

    def refused(candidate, *args, **kwargs):
        candidate_key = _target_key(candidate)
        if (refused_path[0] is None and candidate_key is not None
                and os.path.dirname(candidate_key) == queue_key
                and os.path.splitext(candidate_key)[1] == '.json'):
            handle = _native_read_handle(original, candidate, args, kwargs)
            if handle is not None:
                return handle
            refused_path[0] = candidate_key
        if candidate_key is not None and candidate_key == refused_path[0]:
            refused_path[0] = False
            raise PermissionError(32, 'injected sharing violation')
        return original(candidate, *args, **kwargs)

    io.open = refused
    try:
        yield
    finally:
        io.open = original


def _path_open_failure(path, *args, **kwargs):
    try:
        with path.open(*args, **kwargs):
            pass
    except (FileNotFoundError, PermissionError,
            TypeError, ValueError) as caught:
        return type(caught), str(caught)
    raise AssertionError('Path.open accepted the refused arguments')
