"""Fault-injection controls for test-side command queue readers."""
import contextlib
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cmdqueue  # noqa: E402

# A runaway sleeps without advancing the clock; a valid wait may sleep any
# number of times, because every positive sleep consumes its deadline.
_STALLED_SLEEP_LIMIT = 1000


@contextlib.contextmanager
def _refuse_path_operation(path, operation, failures, clock=None):
    original = getattr(Path, operation)
    signature = inspect.signature(original)
    remaining = [failures]
    calls = [0]

    def refused(candidate, *args, **kwargs):
        if operation == 'open' and candidate == path and remaining[0]:
            # Native validation adds one open per faulted call.
            original(candidate, *args, **kwargs).close()
        elif operation != 'open':
            try:
                signature.bind(candidate, *args, **kwargs)
            except TypeError:
                return original(candidate, *args, **kwargs)
        if candidate == path:
            calls[0] += 1
            if clock is not None:
                clock.record_read()
            if remaining[0]:
                remaining[0] -= 1
                raise PermissionError(32, 'injected sharing violation')
        return original(candidate, *args, **kwargs)

    setattr(Path, operation, refused)
    try:
        yield calls
    finally:
        setattr(Path, operation, original)


@contextlib.contextmanager
def _virtual_cmdqueue_clock(max_sleeps=None):
    original = _cmdqueue.time
    # A large power-of-two scale keeps even subnormal polling delays distinct.
    origin = _cmdqueue.POLL_DELAY * (1 << 24)
    now = [origin]
    events = []
    sleep_count = [0]
    stalled = [0]
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
            stalled[0] = 0 if seconds else stalled[0] + 1
            if stalled[0] >= _STALLED_SLEEP_LIMIT:
                raise AssertionError(
                    f'virtual clock made no progress in {stalled[0]} sleeps')
            sleep_count[0] += 1
            events.append(('sleep', seconds))
            now[0] += seconds

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
    original = Path.open
    armed = [True]

    def vanished(candidate, *args, **kwargs):
        if candidate == path and armed[0]:
            original(candidate, *args, **kwargs).close()
            armed[0] = False
            clock.record_read()
            candidate.unlink()
            if remove_queue:
                candidate.parent.rmdir()
            return original(candidate, *args, **kwargs)
        return original(candidate, *args, **kwargs)

    Path.open = vanished
    try:
        yield
    finally:
        Path.open = original


@contextlib.contextmanager
def _disappear_on_first_open(path):
    original = Path.open
    armed = [True]

    def missing(candidate, *args, **kwargs):
        if candidate == path and armed[0]:
            original(candidate, *args, **kwargs).close()
            armed[0] = False
            raise FileNotFoundError(2, 'injected disappearance', str(path))
        return original(candidate, *args, **kwargs)

    Path.open = missing
    try:
        yield
    finally:
        Path.open = original


@contextlib.contextmanager
def _refuse_first_queue_read(queue):
    original = Path.open
    refused_path = [None]

    def refused(candidate, *args, **kwargs):
        if (refused_path[0] is None and candidate.parent == queue
                and candidate.suffix == '.json'):
            original(candidate, *args, **kwargs).close()
            refused_path[0] = candidate
        if candidate == refused_path[0]:
            refused_path[0] = False
            raise PermissionError(32, 'injected sharing violation')
        return original(candidate, *args, **kwargs)

    Path.open = refused
    try:
        yield
    finally:
        Path.open = original


def _path_open_failure(path, *args, **kwargs):
    try:
        with path.open(*args, **kwargs):
            pass
    except (FileNotFoundError, PermissionError,
            TypeError, ValueError) as caught:
        return type(caught), str(caught)
    raise AssertionError('Path.open accepted the refused arguments')
