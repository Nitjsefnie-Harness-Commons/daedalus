#!/usr/bin/env python3
"""Delivery-loop guarantees for the GET /stream route module."""
import json
import threading

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_route(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'stream_route.py', name=name)


class _FrameSink:
    """A wfile stand-in that records frames and keepalive comments."""

    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.chunks = []
        self.frames = []
        self.keepalives = 0
        self._cond = threading.Condition()

    def write(self, data):
        if self.fail_at == 'write':
            raise BrokenPipeError('write failed')
        text = data.decode('utf-8')
        with self._cond:
            self.chunks.append(data)
            if text.startswith('event: command\n'):
                self.frames.append(text.split('\ndata: ', 1)[1].strip())
            elif text == ': keepalive\n\n':
                self.keepalives += 1
            self._cond.notify_all()

    def flush(self):
        if self.fail_at == 'flush':
            raise BrokenPipeError('flush failed')

    def await_frames(self, count):
        """Block until `count` frames have arrived. Deliberately unbounded."""
        with self._cond:
            while len(self.frames) < count:
                self._cond.wait()

    def ids(self):
        return [json.loads(frame).get('id') for frame in self.frames]

    def tags(self):
        return [json.loads(frame).get('chromeTab') for frame in self.frames]


def _one_tick(route, sink, tmp, token, tab, *, keepalive=15, clock=None):
    """Run serve_stream for exactly one delivery tick, then age it out.

    The clock seam ends the loop on the second iteration, so every drain the
    shape owns has run once when the call returns. A missing frame is then an
    assertion rather than a wait nobody ends.
    """
    steps = iter(clock if clock is not None else (0.0, 0.0, 0.0, 0.0))
    route._now = lambda: next(steps, 5000.0)
    targets = route.resolve_targets(tmp, token, tab)
    assert targets is not None, 'the fixture targets were refused'
    route.serve_stream(
        sink, cmd_dir=tmp, token=token, tab=tab, targets=targets,
        killed_event=threading.Event(), command_ttl=90, keepalive=keepalive,
        max_age=3600, client_label='test')


def test_resolve_targets_refuses_an_unsafe_derived_name(tmp):
    route = _load_route('stream_route_unsafe_target')

    assert route.resolve_targets(Path(tmp), 'tok', '..evil') is None


def test_resolve_targets_names_the_four_paths_under_cmd_dir(tmp):
    route = _load_route('stream_route_target_paths')
    root = Path(tmp)

    targets = route.resolve_targets(root, 'tok', 'chrome1')

    assert targets.queue == root / 'tok_chrome1', targets.queue
    assert targets.legacy == root / 'tok_chrome1.json', targets.legacy
    assert targets.broadcast_queue == root / 'tok', targets.broadcast_queue
    assert targets.broadcast_legacy == root / 'tok.json', (
        targets.broadcast_legacy)
    assert targets.legacy_name == 'tok_chrome1.json', targets.legacy_name


def test_the_loop_waits_on_the_registry_event_publication_sets(tmp):
    """Issue 213: severing the wake link fails this, without timing."""
    route = _load_route('stream_route_wake')
    cq = route.command_queue
    root = Path(tmp)

    class Recording(threading.Event):
        def __init__(self):
            super().__init__()
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            return super().wait(timeout)

    ev = Recording()
    with cq._cmd_events_lock:
        cq._cmd_events['waketok'] = ev
    sink = _FrameSink()
    killed = threading.Event()
    targets = route.resolve_targets(root, 'waketok', 'extension')
    worker = threading.Thread(
        target=route.serve_stream, args=(sink,), kwargs={
            'cmd_dir': root, 'token': 'waketok', 'tab': 'extension',
            'targets': targets, 'killed_event': killed, 'command_ttl': 90,
            'keepalive': 15, 'max_age': 3600, 'client_label': 'test'})
    worker.start()
    try:
        cq.enqueue(root, 'waketok', 'extension', {'id': 'x', 'code': '1'})
        sink.await_frames(1)
    finally:
        killed.set()
        ev.set()
        worker.join()
        with cq._cmd_events_lock:
            cq._cmd_events.pop('waketok', None)

    assert ev.waits >= 1, 'the loop never waited on the registry event'
    assert sink.ids() == ['x'], sink.frames


def test_the_extension_stream_delivers_every_queue_it_owns(tmp):
    route = _load_route('stream_route_extension_queues')
    cq = route.command_queue
    root = Path(tmp)
    cq.enqueue(root, 'tok', 'extension', {'id': 'ext-queue'})
    cq.enqueue(root, 'tok', 'other', {'id': 'per-tab'})
    cq.enqueue(root, 'tok', '', {'id': 'broadcast-queue'})
    (root / 'tok_extension.json').write_text(
        '{"id": "ext-legacy"}', encoding='utf-8')
    (root / 'tok_other.json').write_text(
        '{"id": "tab-legacy"}', encoding='utf-8')
    (root / 'tok.json').write_text(
        '{"id": "broadcast-legacy"}', encoding='utf-8')
    sink = _FrameSink()

    _one_tick(route, sink, root, 'tok', 'extension')

    assert sink.ids() == [
        'ext-queue', 'ext-legacy', 'per-tab', 'broadcast-queue',
        'tab-legacy', 'broadcast-legacy'], sink.ids()
    assert sink.tags() == [
        None, None, 'other', None, 'other', None], sink.tags()


def test_a_named_tab_stream_reads_its_own_and_the_broadcast_targets(tmp):
    route = _load_route('stream_route_named_tab')
    cq = route.command_queue
    root = Path(tmp)
    cq.enqueue(root, 'tok', 'chrome1', {'id': 'tab-queue'})
    cq.enqueue(root, 'tok', '', {'id': 'broadcast-queue'})
    (root / 'tok_chrome1.json').write_text(
        '{"id": "tab-legacy"}', encoding='utf-8')
    (root / 'tok.json').write_text(
        '{"id": "broadcast-legacy"}', encoding='utf-8')
    sink = _FrameSink()

    _one_tick(route, sink, root, 'tok', 'chrome1')

    assert sink.ids() == [
        'tab-queue', 'broadcast-queue', 'tab-legacy',
        'broadcast-legacy'], sink.ids()


def test_a_dashboard_stream_leaves_the_broadcast_legacy_file_alone(tmp):
    route = _load_route('stream_route_dashboard')
    cq = route.command_queue
    root = Path(tmp)
    cq.enqueue(root, 'tok', 'dashboard', {'id': 'dash-event'})
    broadcast = root / 'tok.json'
    broadcast.write_text('{"id": "broadcast-legacy"}', encoding='utf-8')
    sink = _FrameSink()

    _one_tick(route, sink, root, 'tok', 'dashboard')

    assert sink.ids() == ['dash-event'], sink.ids()
    assert broadcast.exists(), 'the dashboard stole the broadcast command'


def test_a_killed_stream_leaves_the_loop_without_a_frame(tmp):
    route = _load_route('stream_route_killed')
    cq = route.command_queue
    root = Path(tmp)
    cq.enqueue(root, 'tok', 'extension', {'id': 'never'})
    sink = _FrameSink()
    killed = threading.Event()
    killed.set()
    targets = route.resolve_targets(root, 'tok', 'extension')

    route.serve_stream(
        sink, cmd_dir=root, token='tok', tab='extension', targets=targets,
        killed_event=killed, command_ttl=90, keepalive=15, max_age=3600,
        client_label='test')

    assert sink.frames == [], sink.frames


def test_an_aged_out_stream_leaves_the_loop_without_a_frame(tmp):
    route = _load_route('stream_route_max_age')
    cq = route.command_queue
    root = Path(tmp)
    cq.enqueue(root, 'tok', 'extension', {'id': 'never'})
    clock = iter((0.0, 0.0))
    route._now = lambda: next(clock, 5000.0)
    sink = _FrameSink()
    targets = route.resolve_targets(root, 'tok', 'extension')

    route.serve_stream(
        sink, cmd_dir=root, token='tok', tab='extension', targets=targets,
        killed_event=threading.Event(), command_ttl=90, keepalive=15,
        max_age=3600, client_label='test')

    assert sink.frames == [], sink.frames


def test_a_write_error_ends_the_loop_and_is_not_raised(tmp):
    route = _load_route('stream_route_write_error')
    cq = route.command_queue
    root = Path(tmp)
    cq.enqueue(root, 'tok', 'extension', {'id': 'doomed'})
    sink = _FrameSink(fail_at='write')
    targets = route.resolve_targets(root, 'tok', 'extension')

    route.serve_stream(
        sink, cmd_dir=root, token='tok', tab='extension', targets=targets,
        killed_event=threading.Event(), command_ttl=90, keepalive=15,
        max_age=3600, client_label='test')

    assert sink.frames == [], sink.frames


def test_keepalive_is_written_when_the_clock_passes_the_interval(tmp):
    route = _load_route('stream_route_keepalive')
    root = Path(tmp)
    route.command_queue.enqueue(root, 'tok', 'extension', {'id': 'only'})
    sink = _FrameSink()

    _one_tick(route, sink, root, 'tok', 'extension',
              keepalive=15, clock=(0.0, 0.0, 0.0, 20.0))

    assert sink.keepalives == 1, sink.chunks
    assert sink.chunks[-1] == b': keepalive\n\n', sink.chunks


def test_no_keepalive_before_the_clock_reaches_the_interval(tmp):
    route = _load_route('stream_route_keepalive_early')
    root = Path(tmp)
    route.command_queue.enqueue(root, 'tok', 'extension', {'id': 'only'})
    sink = _FrameSink()

    _one_tick(route, sink, root, 'tok', 'extension',
              keepalive=15, clock=(0.0, 0.0, 0.0, 14.0))

    assert sink.keepalives == 0, sink.chunks


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
