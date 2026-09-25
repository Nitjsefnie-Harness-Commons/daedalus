#!/usr/bin/env python3
"""The dashboard event stream is a fan-out subscription, not addressed.

Several dashboard windows on one token each receive every event published
while they are connected. Registration keeps both alive (neither replaces the
other), the drain keeps a per-connection cursor so removal waits for every
live subscriber, and the client drops a replayed event id. The controls below
pin each limb; the drain's stop-and-retry behaviour is in
test_dashboard_drain_blocking.
"""
import itertools
import json
import os
import re
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
# Shared wiring lives in _fanout so the drain-blocking suite uses the same
# by-path loader and queue helpers; imported under their former local names
# so the controls below are unchanged.
from _fanout import (  # noqa: E402
    DASHBOARD,
    captured_stdout as _captured,
    dashboard_queue as _queue,
    service_pair as _service,
    write_event as _write_event,
)


def _order():
    """The real daedalus_bridge.queue_order the loaded command_queue mints
    with. It only enters sys.modules once a by-path command_queue has been
    loaded (which imports it), so it is fetched lazily; the instance is the
    shared, cached one."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from daedalus_bridge import queue_order
    return queue_order


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

    # The other direction: an ordinary named tab still replaces.
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


def test_a_peer_that_unregisters_stops_blocking_removal(tmp):
    """A subscription that disconnects stops being live, so whatever it
    left behind is reclaimed by the next drain: once the only remaining
    live subscriber holds a cursor at or past the entry, it goes."""
    service, drain = _service('fanout_peer_unregister')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    entry = _write_event(qdir, '0000000000001_00000001', type='result')
    _a_id, a_killed = service.register(token, DASHBOARD)
    _b_id, b_killed = service.register(token, DASHBOARD)
    frames = []

    assert drain.drain_dashboard(qdir, token, a_killed, command_ttl=90,
                                 frame_writer=frames.append) == 1
    assert entry.exists(), 'the entry went while a live peer was behind'

    service.unregister(_b_id, b_killed)

    assert drain.drain_dashboard(qdir, token, a_killed, command_ttl=90,
                                 frame_writer=frames.append) == 0
    assert not entry.exists(), 'a departed peer kept blocking removal'


def test_the_removal_join_is_token_scoped(tmp):
    """One token's behind-subscriptions do not block another token's
    reclaim. A subscription on `tok2` that never drained must not keep a
    `tok1` entry that its own only subscriber has already consumed."""
    service, drain = _service('fanout_token_scope')
    q1 = _queue(service, tmp, 'tok1')
    entry = _write_event(q1, '0000000000001_00000001', type='result')
    _a_id, a_killed = service.register('tok1', DASHBOARD)
    _b_id, _b_killed = service.register('tok2', DASHBOARD)  # never drains
    frames = []

    assert drain.drain_dashboard(q1, 'tok1', a_killed, command_ttl=90,
                                 frame_writer=frames.append) == 1
    assert not entry.exists(), (
        "another token's behind-subscription blocked this token's reclaim")


def test_a_never_draining_peer_does_not_starve_the_first_window(tmp):
    """A second window that connects and never drains costs the first
    window nothing: the first still receives, and the queue stays bounded
    by the collector's TTL sweep rather than by the join, because the
    sweep reclaims an expired entry regardless of any cursor."""
    service, drain = _service('fanout_not_starve')
    cq = service.command_queue
    token = 'tok'
    cmd_root = Path(tmp) / 'commands'
    qdir = _queue(service, tmp, token)
    _a_id, a_killed = service.register(token, DASHBOARD)
    _b_id, _b_killed = service.register(token, DASHBOARD)  # never drains
    cq.notify_dashboard(cmd_root, token, {'type': 'result'})
    frames = []

    assert drain.drain_dashboard(qdir, token, a_killed, command_ttl=90,
                                 frame_writer=frames.append) == 1
    assert [f['type'] for f in frames] == ['result'], frames
    remaining = list(qdir.iterdir())
    assert len(remaining) == 1, remaining
    old = time.time() - 500
    os.utime(remaining[0], (old, old))
    cq.collect_expired(cmd_root, 90)
    assert not remaining[0].exists(), (
        'the TTL sweep did not reclaim an entry a peer never consumed')


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


def test_a_failed_frame_write_leaves_the_entry_and_the_cursor(tmp):
    """The frame is written BEFORE any removal, and the cursor advances
    only after that write, so a peer that vanishes mid-write leaves the
    event queued and the cursor where it was: the next drain redelivers
    it. Advancing the cursor before the write, or unlinking before it, both
    destroy the event on a failed write and are caught here."""
    service, drain = _service('fanout_failed_write')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    entry = _write_event(qdir, '0000000000001_00000001', type='result')
    _sub_id, killed = service.register(token, DASHBOARD)
    delivered_frames = []

    def failing(_data):
        raise BrokenPipeError('the peer went away mid-write')

    try:
        drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                              frame_writer=failing)
    except BrokenPipeError:
        pass  # the injected dead peer, not the assertion under test
    else:
        raise AssertionError('the failing write did not propagate')

    assert entry.exists(), 'the entry was unlinked despite a failed write'
    assert delivered_frames == [], delivered_frames

    redelivered = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                        frame_writer=delivered_frames.append)
    assert redelivered == 1, redelivered
    assert [f['type'] for f in delivered_frames] == ['result'], (
        delivered_frames)


def test_a_failed_write_with_a_past_peer_loses_no_event(tmp):
    """The same guarantee when the removal join is satisfiable at write
    time.

    A single-subscriber fixture can never reach the unlink arm on a failed
    write (the current connection is the only subscriber and has not
    advanced yet, so the join is false), so advancing the cursor before the
    write is caught but unlinking before it is not. Here a peer has already
    consumed the event, so the join is satisfiable once this connection
    advances: unlinking before the write would destroy an event that
    nothing could redeliver. Both the advance-before-write and
    unlink-before-write mutations are caught.
    """
    service, drain = _service('fanout_failed_write_peer')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    entry = _write_event(qdir, '0000000000001_00000001', type='result')
    _a_id, a_killed = service.register(token, DASHBOARD)
    _b_id, b_killed = service.register(token, DASHBOARD)
    # The peer consumes the event first: it is now past it, so the join is
    # satisfiable and the unlink arm is reachable.
    assert drain.drain_dashboard(qdir, token, b_killed, command_ttl=90,
                                 frame_writer=lambda _d: None) == 1
    assert entry.exists(), 'the peer removed the event out from under A'

    def failing(_data):
        raise BrokenPipeError('the peer went away mid-write')

    try:
        drain.drain_dashboard(qdir, token, a_killed, command_ttl=90,
                              frame_writer=failing)
    except BrokenPipeError:
        pass  # the injected dead peer, not the assertion under test
    else:
        raise AssertionError('the failing write did not propagate')

    assert entry.exists(), 'the entry was unlinked despite a failed write'
    redelivered = []
    assert drain.drain_dashboard(qdir, token, a_killed, command_ttl=90,
                                 frame_writer=redelivered.append) == 1
    assert [f['type'] for f in redelivered] == ['result'], redelivered


def test_a_foreign_named_dashboard_entry_is_delivered_not_skipped(tmp):
    """A dashboard queue entry whose name is not the bridge's own grammar
    — a stale file from a differently-formatted producer — is handled
    correctly: the drain does not raise on it, and does not silently drop
    it as already-consumed. A fresh subscription delivers it (its name sorts
    after the empty seed cursor), and a following drain does not redeliver
    it."""
    service, drain = _service('fanout_foreign_name')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    _write_event(qdir, 'foreign-event', type='stale')          # no separator
    _write_event(qdir, '1700000000123_notanumber', type='mix')  # mixed grammar
    _sub_id, killed = service.register(token, DASHBOARD)
    frames = []

    delivered = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                      frame_writer=frames.append)
    again = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                  frame_writer=frames.append)

    assert delivered == 2, delivered
    assert sorted(f['type'] for f in frames) == ['mix', 'stale'], frames
    assert again == 0, again


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


def test_this_drain_reclaims_an_expired_entry_despite_a_stuck_peer(tmp):
    """The TTL limb of the unlink disjunction, isolated.

    The drain unlinks an entry when `data is None` (this drain found it
    expired or non-event) OR when every live subscription has passed it. In
    every other expired fixture the second limb is also satisfied, so
    substituting `False` for the first limb leaves them green while the
    runtime stops reclaiming an expired entry whenever a stuck peer holds the
    join false. This fixture holds the join false — a registered peer that
    never drains — so only the drain's own age check can reclaim, which is
    what the TTL-DROP line and the unlink on THIS drain prove.
    """
    service, drain = _service('fanout_ttl_own_drain')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    stale = _write_event(qdir, '0000000000001_00000001', type='result')
    old = time.time() - 500
    os.utime(stale, (old, old))
    _a_id, a_killed = service.register(token, DASHBOARD)
    # This peer never drains, so every_subscription_past is False and only
    # the TTL limb can satisfy the unlink.
    _b_id, _b_killed = service.register(token, DASHBOARD)
    delivered = []

    with _captured() as out:
        assert drain.drain_dashboard(
            qdir, token, a_killed, command_ttl=90,
            frame_writer=delivered.append) == 0

    assert delivered == [], delivered
    assert not stale.exists(), (
        'this drain did not reclaim the expired entry on its own')
    assert 'TTL-DROP' in out.getvalue(), out.getvalue()


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


def test_a_same_millisecond_event_is_delivered_after_the_first(tmp):
    """Two events in one millisecond, the second published after the first
    was consumed: the second is still delivered, and a following drain
    delivers nothing. The published stems must be byte-ordered in publish
    order (`<ms:013d>_<counter:020d>`), which is the sole warrant for the
    cursor's name ordering. On the random-hex stem the second sorts below
    the cursor, is skipped as already-consumed, and is unlinked — the event
    is lost for a connected window and the evidence is removed."""
    service, drain = _service('fanout_same_ms')
    cq = service.command_queue
    token = 'tok'
    cmd_dir = Path(tmp) / 'commands'
    qdir = _queue(service, tmp, token)
    _sub_id, killed = service.register(token, DASHBOARD)
    # Both publishes in one millisecond, so the counter is the sole
    # discriminator between the two names and is the one under test.
    _qo = _order()
    saved_time = _qo.time
    setattr(_qo, 'time', type('T', (), {
        'time': staticmethod(lambda: 1_700_000_000.123)})())
    frames = []
    try:
        cq.notify_dashboard(cmd_dir, token, {'type': 'first'})
        first = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                      frame_writer=frames.append)
        cq.notify_dashboard(cmd_dir, token, {'type': 'second'})
        second = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                       frame_writer=frames.append)
        third = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                      frame_writer=frames.append)
    finally:
        setattr(_qo, 'time', saved_time)

    assert first == 1, first
    assert second == 1, (
        'the same-millisecond event published after the first was lost: '
        f'{second}')
    assert third == 0, third
    assert [f['type'] for f in frames] == ['first', 'second'], frames
    first_id, second_id = frames[0]['id'], frames[1]['id']
    # A stem-format mismatch is reported as a format failure, distinct from
    # the delivery assertion above, so a wrong field width is never masked
    # by (or mask) a lost event.
    for stem in (first_id, second_id):
        assert re.fullmatch(r'\d{13}_\d{20}', stem), (
            f'stem format failure: {stem!r} is not <ms:013d>_<counter:020d>')
    assert first_id < second_id, (first_id, second_id)


def test_two_events_across_the_counter_overflow_are_both_delivered(tmp):
    """The counter-boundary control, AT the boundary that matters.

    Seed the shared counter at 999999 — the first value at which the
    original six-digit counter field overflowed: 999999 formats as six
    digits but 1000000 as seven, and '1' < '9', so byte order inverted and
    the second event fell below the first window's cursor and was dropped.
    The widened twenty-digit field keeps the order across that transition.
    (A high seed such as 10**19 would not distinguish the widths: both
    values already format to twenty digits and ascend either way — the
    overflow is only observable at the six-to-seven transition.) Both
    events are delivered, neither file is unlinked, and the stem-format
    expectation still holds at the seeded value.
    """
    service, drain = _service('fanout_counter_boundary')
    cq = service.command_queue
    token = 'tok'
    cmd_dir = Path(tmp) / 'commands'
    qdir = _queue(service, tmp, token)
    _sub_id, killed = service.register(token, DASHBOARD)
    _qo = _order()
    saved_time, saved_counter = _qo.time, _qo._seq_counter
    setattr(_qo, 'time', type('T', (), {
        'time': staticmethod(lambda: 1_700_000_000.123)})())
    setattr(_qo, '_seq_counter', itertools.count(999999))  # overflow point
    frames = []
    try:
        cq.notify_dashboard(cmd_dir, token, {'type': 'first'})
        first = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                      frame_writer=frames.append)
        cq.notify_dashboard(cmd_dir, token, {'type': 'second'})
        second = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                       frame_writer=frames.append)
        third = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                      frame_writer=frames.append)
    finally:
        setattr(_qo, 'time', saved_time)
        setattr(_qo, '_seq_counter', saved_counter)

    assert first == 1, first
    assert second == 1, (
        'the event published across the counter overflow was lost: '
        f'{second}')
    assert third == 0, third
    assert [f['type'] for f in frames] == ['first', 'second'], frames
    first_id, second_id = frames[0]['id'], frames[1]['id']
    for stem in (first_id, second_id):
        assert re.fullmatch(r'\d{13}_\d{20}', stem), (
            f'stem format failure at the boundary: {stem!r}')
    assert first_id < second_id, (
        'the overflow inverted the byte order: '
        f'{first_id!r} !< {second_id!r}')


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
