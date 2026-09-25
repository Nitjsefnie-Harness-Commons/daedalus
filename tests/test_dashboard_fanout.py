#!/usr/bin/env python3
"""The dashboard event stream is a fan-out subscription, not addressed.

Several dashboard windows on one token each receive every event published
while they are connected. Registration keeps both alive (neither replaces the
other), the drain keeps a per-connection cursor so removal waits for every
live subscriber, and the client drops a replayed event id. These controls pin
each limb: the exemption is exact-name, the removal is a join over live
cursors, a fresh window starts at the head of the retained queue, a
redelivery after a drain with the same cursor reaches nobody, an expired
entry is taken for everyone, and a publisher cannot forge the bridge's own
event id or kind.
"""
import contextlib
import io
import json
import os
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _service_loader import _load_service  # noqa: E402


DASHBOARD = 'dashboard'


def _service(name):
    """The path-loaded stream service and a drain bound to that copy.

    `stream_service` is loaded by path under a private name, so it is a
    different module object from `daedalus_bridge.stream_service`; the drain
    has to see THIS copy's registry or `register` and the cursor queries
    would not meet.
    """
    service = _load_service(name)
    drain = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'dashboard_drain.py',
        name=f'{name}_drain')
    # setattr, not `drain.stream_service = service`: pyright refuses a
    # direct attribute assignment on a ModuleType.
    setattr(drain, 'stream_service', service)
    return service, drain


def _queue(service, tmp, token):
    """The dashboard event queue directory for `token`."""
    cq = service.command_queue
    name, _ = cq.command_target_names(token, DASHBOARD)
    qdir = Path(tmp) / 'commands' / name
    qdir.mkdir(parents=True, exist_ok=True)
    return qdir


def _write_event(qdir, stem, **fields):
    """Write one dashboard event under an explicit, sortable stem."""
    document = {'id': stem, 'kind': 'event'}
    document.update(fields)
    path = qdir / f'{stem}.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path


@contextlib.contextmanager
def _captured():
    """Capture what the module printed while the block ran."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


def test_two_dashboard_subscriptions_both_receive_one_event(tmp):
    """The RED this suite exists for: a second window must not kill the
    first, and one published event reaches both. On the base the second
    register kills the first, so the first never drains to completion and
    the second sees the event alone."""
    service, drain = _service('fanout_two_windows')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    _a_id, a_killed = service.register(token, DASHBOARD)
    _b_id, b_killed = service.register(token, DASHBOARD)
    service.command_queue.notify_dashboard(
        Path(tmp) / 'commands', token, {'type': 'tabs-synced'})
    a_frames, b_frames = [], []

    delivered_a = drain.drain_dashboard(
        qdir, token, a_killed, command_ttl=90, frame_writer=a_frames.append)
    delivered_b = drain.drain_dashboard(
        qdir, token, b_killed, command_ttl=90, frame_writer=b_frames.append)

    assert not a_killed.is_set(), 'the second window killed the first'
    assert not b_killed.is_set()
    assert delivered_a == 1, delivered_a
    assert delivered_b == 1, delivered_b
    assert [f['type'] for f in a_frames] == ['tabs-synced'], a_frames
    assert [f['type'] for f in b_frames] == ['tabs-synced'], b_frames
    assert service.snapshot() == (2, [DASHBOARD]), service.snapshot()


def test_a_second_dashboard_subscription_logs_no_replacement(tmp):
    """Registering a peer window must not print a REPLACED line. Every
    other named tab still logs one — that is the control beside it."""
    service, _ = _service('fanout_no_replacement_log')
    token = 'tok'
    _a_id, a_killed = service.register(token, DASHBOARD)
    with _captured() as out:
        _b_id, b_killed = service.register(token, DASHBOARD)
    assert not a_killed.is_set()
    assert not b_killed.is_set()
    assert 'REPLACED' not in out.getvalue(), out.getvalue()

    # The other direction of the boundary: an ordinary named tab replaces.
    _c_id, c_killed = service.register(token, 'chrome1')
    with _captured() as out:
        _d_id, d_killed = service.register(token, 'chrome1')
    assert c_killed.is_set(), 'an addressed tab stopped replacing'
    assert not d_killed.is_set()
    assert 'REPLACED tab=chrome1' in out.getvalue(), out.getvalue()


def test_the_dashboard_exemption_is_the_exact_name(tmp):
    """A name that merely starts with (or case-folds to) the dashboard
    target is an ordinary addressed stream: it replaces its predecessor
    and logs the line. A prefix match in the exemption would let these
    through as subscriptions and the control fails."""
    service, _ = _service('fanout_exact_name')
    for tab in ('dashboard2', 'Dashboard', 'dashes'):
        _first_id, first_killed = service.register('tok', tab)
        with _captured() as out:
            _second_id, second_killed = service.register('tok', tab)
        assert first_killed.is_set(), (
            f'{tab!r} did not replace its predecessor')
        assert not second_killed.is_set()
        assert f'REPLACED tab={tab[:8]}' in out.getvalue(), (
            tab, out.getvalue())


def test_removal_waits_for_every_live_subscriber_then_reclaims(tmp):
    """A consumes three entries while B is behind: the files stay. Once B
    consumes them the directory empties. The directory itself is the
    evidence at both points, not a counter."""
    service, drain = _service('fanout_join_removal')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    stems = [f'000000000000{i}_0000000{i}' for i in (1, 2, 3)]
    for stem in stems:
        _write_event(qdir, stem, type='result')
    _a_id, a_killed = service.register(token, DASHBOARD)
    _b_id, b_killed = service.register(token, DASHBOARD)
    a_frames, b_frames = [], []

    assert drain.drain_dashboard(
        qdir, token, a_killed, command_ttl=90,
        frame_writer=a_frames.append) == 3
    assert len(a_frames) == 3, a_frames
    assert b_frames == [], b_frames
    remaining = sorted(p.name for p in qdir.iterdir())
    assert len(remaining) == 3, (
        'the entries were removed while a live peer was behind: '
        f'{remaining}')

    assert drain.drain_dashboard(
        qdir, token, b_killed, command_ttl=90,
        frame_writer=b_frames.append) == 3
    assert len(b_frames) == 3, b_frames
    assert list(qdir.iterdir()) == [], (
        'the entries were not reclaimed once every subscriber held a '
        'cursor at or past them')


def test_a_fresh_subscription_receives_entries_queued_when_it_connected(tmp):
    """Seeded at the head of the retained queue, a window that opens
    receives the events already waiting. Seeding at the end would skip
    them — and the end-to-end stream suite depends on this, because it
    posts a /result and only then opens the stream."""
    service, drain = _service('fanout_fresh_gets_queued')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    _write_event(qdir, '0000000000001_00000001', type='tabs-synced')
    frames = []
    _sub_id, killed = service.register(token, DASHBOARD)

    delivered = drain.drain_dashboard(
        qdir, token, killed, command_ttl=90, frame_writer=frames.append)

    assert delivered == 1, delivered
    assert [f['type'] for f in frames] == ['tabs-synced'], frames


def test_a_second_drain_with_the_same_cursor_delivers_nothing(tmp):
    """Resetting the cursor each drain would redeliver an entry this
    connection already consumed. The entry has to still be on disk across
    the two drains for the second pass to be able to see it, so a peer
    registers but never drains and holds the removal back."""
    service, drain = _service('fanout_same_cursor')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    entry = _write_event(qdir, '0000000000001_00000001', type='result')
    frames = []
    _sub_id, killed = service.register(token, DASHBOARD)
    _peer_id, _peer_killed = service.register(token, DASHBOARD)

    first = drain.drain_dashboard(
        qdir, token, killed, command_ttl=90, frame_writer=frames.append)
    assert entry.exists(), 'the fixture unlinked before the second drain'
    second = drain.drain_dashboard(
        qdir, token, killed, command_ttl=90, frame_writer=frames.append)

    assert first == 1, first
    assert second == 0, second
    assert len(frames) == 1, frames


def test_an_expired_entry_is_removed_for_everyone_and_never_delivered(tmp):
    """The TTL rule takes an expired entry independently of any cursor, so
    neither subscriber receives it and neither leaves it behind."""
    service, drain = _service('fanout_ttl_everyone')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    stale = _write_event(qdir, '0000000000001_00000001', type='result')
    old = time.time() - 500
    os.utime(stale, (old, old))
    _a_id, a_killed = service.register(token, DASHBOARD)
    _b_id, b_killed = service.register(token, DASHBOARD)
    a_frames, b_frames = [], []

    assert drain.drain_dashboard(
        qdir, token, a_killed, command_ttl=90,
        frame_writer=a_frames.append) == 0
    assert drain.drain_dashboard(
        qdir, token, b_killed, command_ttl=90,
        frame_writer=b_frames.append) == 0
    assert a_frames == [], a_frames
    assert b_frames == [], b_frames
    assert not stale.exists(), 'an expired entry was retained'


def test_an_expired_entry_past_a_subscriber_cursor_is_not_delivered(tmp):
    """A subscription whose cursor already holds at or past an entry is
    never handed that entry again, expired or not, and the entry does not
    linger."""
    service, drain = _service('fanout_ttl_past_cursor')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    _write_event(qdir, '0000000000002_00000002', type='result')
    frames = []
    _sub_id, killed = service.register(token, DASHBOARD)
    assert drain.drain_dashboard(
        qdir, token, killed, command_ttl=90,
        frame_writer=frames.append) == 1

    # An earlier-named entry appears, already expired, behind the cursor.
    stale = _write_event(qdir, '0000000000001_00000001', type='result')
    old = time.time() - 500
    os.utime(stale, (old, old))
    assert drain.drain_dashboard(
        qdir, token, killed, command_ttl=90,
        frame_writer=frames.append) == 0

    assert len(frames) == 1, frames
    assert not stale.exists(), 'an expired entry behind the cursor lingered'


def test_notify_dashboard_publishes_its_own_id_and_kind(tmp):
    """The fan-out makes the event id load-bearing (the client dedups on
    it), so a publisher must not be able to override the bridge's own id
    or kind. Reverting the payload order lets both through."""
    service, _ = _service('fanout_notify_own_fields')
    token = 'tok'
    cmd_dir = Path(tmp) / 'commands'
    service.command_queue.notify_dashboard(
        cmd_dir, token,
        {'id': 'forged-id', 'kind': 'not-an-event', 'type': 'result'})
    published, = (cmd_dir / 'tok_dashboard').iterdir()
    document = json.loads(published.read_text(encoding='utf-8'))
    assert document['id'] == published.stem, document
    assert document['kind'] == 'event', document
    assert document['type'] == 'result', document


_DEDUP_HARNESS = _dashnode.DashboardNodeHarness(_dashnode.DOM + r"""
(async () => {
globalThis.window = { addEventListener() {} };
const enc = new TextEncoder();
const frame = (id) => enc.encode(
  'event: command\ndata: '
  + JSON.stringify({ kind: 'event', id, type: 'result' }) + '\n\n');
const chunks = [frame('e1'), frame('e1'), frame('e2')];
let next = 0;
globalThis.fetch = async (target) => ({
  ok: true, body: { getReader: () => ({ read: () => {
    if (next < chunks.length) {
      return Promise.resolve({ done: false, value: chunks[next++] });
    }
    return new Promise(() => {});
  } }) },
});
const sse = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'sse module import', _dashnodeStepTimeoutMs,
);
const seen = [];
sse.subscribe((e) => { if (e.kind === 'event') seen.push(e.id); });
sse.start();
await bounded(settle(), 'first frame', _dashnodeStepTimeoutMs);
await bounded(settle(), 'duplicate frame', _dashnodeStepTimeoutMs);
await bounded(settle(), 'new frame', _dashnodeStepTimeoutMs);
process.stdout.write(JSON.stringify({ seen }));
})().catch(leave);
""", bounded_steps=4, module=True, arguments=(
    ROOT / 'dashboard' / 'sse.js',))


def test_the_client_drops_a_replayed_event_id(_tmp):
    """With fan-out a reconnecting window re-receives whatever a slower
    peer still had queued, so sse.js keeps a bounded set of dispatched
    event ids and drops a frame it already dispatched. The harness drives
    the shipped module: it feeds a duplicate id and a new one, and the
    duplicate must not reach the listener. Deleting the dedup makes the
    seen list [e1, e1, e2]."""
    result = _dashnode.run_dashboard_node(_DEDUP_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['seen'] == ['e1', 'e2'], seen


if __name__ == '__main__':
    raise SystemExit(_util.runner(
        _util.collect(globals()), tmp_prefix='dashboardfanout_'))
