"""Fault-injection controls for test-side command queue readers."""
import ast
import contextlib
import inspect
import io
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cmdqueue  # noqa: E402

# These bound runaways, never virtual pacing or sleep multiplicity.
_RUNAWAY_ELAPSED = _cmdqueue.POLL_DELAY * 1000
_RUNAWAY_WALL = 5.0
_NO_PROGRESS_LIMIT = 200_000
_QUEUE_PROBES = ('is_dir', 'glob')
_PROBES_PER_ATTEMPT = 2
_POLL_HEADROOM = 4


def _poll_budget(timeout):
    """The poll ceiling a correct reader spends on `timeout`, with headroom.

    A poll attempt costs no real time, so this decides nothing about
    machine speed, and it is the backstop for the one shape the virtual
    clock's guards cannot see: each is consulted from inside a call into
    that clock, so a reader that stops calling it — or never entered it —
    is charged by none. `_queueread.POLL_DELAY` aliases this module's.

    Two version axes, measured on 3.11 through 3.14. The `ceil` is
    float-sensitive on the products call sites pass
    (`3 * POLL_DELAY / POLL_DELAY` ceils to 4, not 3), so a budget can
    quietly gain a pass, always toward more headroom. And `_QUEUE_PROBES`
    costs what pathlib's own internals cost: up to 3.12 a selector calls
    `parent.is_dir()`, so one `glob` is two probes there and one on 3.13.
    A control must therefore never assert a count of glob calls; the
    ceiling itself is identical on every version.
    """
    attempts = math.ceil(timeout / _cmdqueue.POLL_DELAY)
    return _PROBES_PER_ATTEMPT * _POLL_HEADROOM * attempts


@contextlib.contextmanager
def _bounded_polls(max_polls, what=None):
    """Refuse the queue probe that would pass `max_polls` polls.

    The patch is process-wide, and that has a failure direction in each
    way. A reader reaching the queue through any other API — `os.listdir`
    behind `Path.exists`, `iterdir`, either nested in another call or off
    an aliased receiver — calls nothing here, spends nothing, and hangs.
    A probe from outside the reader, on this process or on a thread it
    shares, spends the reader's budget and is itself the call that
    raises. `what` names the wait for the first reader of a traceback; it
    cannot name either direction.

    The uncounted direction is not pinned at all. No kept suite reads the
    reader's source to ask which APIs it reaches the queue through: with
    `_poll_queue_reads` rewritten onto `os.listdir`, `test_queued_command.py`
    still passes.
    """
    originals = {name: getattr(Path, name) for name in _QUEUE_PROBES}
    spent = [0]
    where = '' if what is None else f' [{what}]'

    def counted(original):
        def probe(candidate, *args, **kwargs):
            if spent[0] >= max_polls:
                raise AssertionError(
                    f'queue reader reached its poll ceiling of {max_polls} '
                    f'probes, counting {" and ".join(_QUEUE_PROBES)} one '
                    f'each, and was still polling{where}')
            spent[0] += 1
            return original(candidate, *args, **kwargs)
        return probe

    for name, original in originals.items():
        setattr(Path, name, counted(original))
    try:
        yield spent
    finally:
        for name, original in originals.items():
            setattr(Path, name, original)


def _assert_slept_its_attempt_budget(events, attempts, poll_delay):
    """The wait spent (attempts - 1) intervals, however it spent them.

    The cadence across passes is deliberately NOT pinned. The reader has
    no per-pass clock call, so a reader banking its whole wait into one
    sleep, or splitting the total into three unequal chunks, is
    indistinguishable here from one that spreads it evenly, and neither
    is caught.
    """
    total = sum(seconds for kind, seconds in events if kind == 'sleep')
    expected = (attempts - 1) * poll_delay
    assert abs(total - expected) < 1e-9, (attempts, total, expected, events)


def _scope_map(tree):
    """Map every node to the lexical scope that encloses it.

    Module, ClassDef, FunctionDef, AsyncFunctionDef and Lambda are scopes,
    so a name bound in any of them is visible to what they contain. A
    scope node maps to the scope ENCLOSING it, so walking the chain ends
    at the Module rather than looping on it.
    """
    scope_by_node = {}

    class ScopeMap(ast.NodeVisitor):
        def __init__(self):
            self.scope = None

        def visit_scope(self, node):
            scope_by_node[node] = self.scope
            previous, self.scope = self.scope, node
            super().generic_visit(node)
            self.scope = previous

        visit_Module = visit_scope
        visit_ClassDef = visit_scope
        visit_FunctionDef = visit_scope
        visit_AsyncFunctionDef = visit_scope
        visit_Lambda = visit_scope

        def generic_visit(self, node):
            scope_by_node[node] = self.scope
            super().generic_visit(node)

    ScopeMap().visit(tree)
    return scope_by_node


def _callee_name(node):
    """The final identifier a call or value resolves to, or None.

    A subscript and a call can both sit where the callee goes, so both
    read; a name reached through either is still a name this guard has to
    account for.
    """
    func = node.func if isinstance(node, ast.Call) else node
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Subscript):
        return _callee_name(func.value)
    return None


class _ModuleDefault:
    """An omitted wall budget, read from `_RUNAWAY_WALL` as it then stands.

    A default bound in the signature would freeze the constant at import.
    """


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
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _plain_read(handle, args, kwargs):
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
def _virtual_cmdqueue_clock(
        max_sleeps=None,
        wall_budget: float | None | _ModuleDefault = _ModuleDefault()):
    """`wall_budget` has three states: omitted takes the module's value as it
    stands when the control runs, a number is that caller's own, and None
    means no such ceiling applies. `max_sleeps` is off when None.
    """
    budget = _RUNAWAY_WALL if isinstance(wall_budget, _ModuleDefault) \
        else wall_budget
    if budget is not None and (not math.isfinite(budget) or budget < 0):
        raise ValueError('wall budget must be non-negative and finite')
    original = _cmdqueue.time
    wall_started = 0.0 if budget is None else original.perf_counter()
    origin = _cmdqueue.POLL_DELAY * (1 << 24)
    elapsed = [0.0]
    correction = [0.0]
    events = []
    sleep_count = [0]
    no_progress_count = [0]
    read_cost = _cmdqueue.POLL_DELAY / 10 or _cmdqueue.POLL_DELAY

    def accumulated(seconds):
        if seconds == 0:
            return elapsed[0], correction[0]
        adjusted = seconds - correction[0]
        advanced = elapsed[0] + adjusted
        next_correction = (advanced - elapsed[0]) - adjusted
        return advanced, next_correction

    def check_wall_bound():
        if budget is None:
            return
        wall_elapsed = original.perf_counter() - wall_started
        if wall_elapsed >= budget:
            raise AssertionError(
                'virtual clock wall-time bound reached after '
                f'{wall_elapsed:.3f}s (limit {budget:.3f}s)')

    class Clock:
        def monotonic(self):
            check_wall_bound()
            return origin + elapsed[0]

        perf_counter = monotonic

        def record_read(self):
            check_wall_bound()
            events.append(('read', read_cost))
            elapsed[0], correction[0] = accumulated(read_cost)
            no_progress_count[0] = 0

        def sleep(self, seconds):
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError(
                    'sleep length must be non-negative and finite')
            if max_sleeps is not None:
                if sleep_count[0] >= max_sleeps:
                    raise AssertionError(
                        f'virtual clock exceeded {max_sleeps} sleeps')
            check_wall_bound()
            advanced, next_correction = accumulated(seconds)
            if advanced >= _RUNAWAY_ELAPSED:
                raise AssertionError(
                    'virtual clock elapsed guard reached '
                    f'{advanced:.3f}s from its origin')
            progressed = origin + advanced != origin + elapsed[0]
            if not progressed and no_progress_count[0] >= _NO_PROGRESS_LIMIT:
                raise AssertionError(
                    'virtual clock made no progress for '
                    f'{_NO_PROGRESS_LIMIT} sleeps')
            if max_sleeps is not None:
                sleep_count[0] += 1
            no_progress_count[0] = 0 if progressed else (
                no_progress_count[0] + 1)
            events.append(('sleep', seconds))
            elapsed[0] = advanced
            correction[0] = next_correction

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
    with _rewrite_on_first_read(
            path, FileNotFoundError(2, 'injected disappearance', str(path)),
            ()):
        yield


@contextlib.contextmanager
def _rewrite_on_first_read(path, error, rewrites):
    """Rewrite queue files at the moment of the refusal, so only a whole-set
    retry can return the rewritten content."""
    original = io.open
    target_key = _target_key(path)
    armed = [True]

    def refused(candidate, *args, **kwargs):
        if _target_key(candidate) == target_key and armed[0]:
            handle = _native_read_handle(original, candidate, args, kwargs)
            if handle is not None:
                return handle
            armed[0] = False
            for queued, command in rewrites:
                queued.write_text(json.dumps(command), encoding='utf-8')
            raise error
        return original(candidate, *args, **kwargs)

    io.open = refused
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
