#!/usr/bin/env python3
"""What `dashboard/sse.js` reports, forgets, filters and restarts.

The repeated-id drop and the status sequence are held by
`test_dashboard_fanout` and `test_dashboard_eval`, which drive this module
as a harness argument.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _dashshell import run_scenario  # noqa: E402
from _repo import ROOT  # noqa: E402

_SERVER = 'https://example.com'
_OTHER_SERVER = 'https://example.com/other'
_TOKEN = 'tok-abcdefghijklmnop'
_OTHER_TOKEN = 'tok-zyxwvutsrqponmji'
_STREAM = _SERVER + '/stream?tab=dashboard'
_BEARER_OLD = 'Bearer ' + _TOKEN
_BEARER_NEW = 'Bearer ' + _OTHER_TOKEN
# Wide enough that two frames cannot land in the same reading, so "the
# clock moved" is about the code and not about the millisecond.
_STEP_MS = 4000

# Every scenario opens the same way: storage, one planned route, a clock
# the scenario drives, and the namespace the calls are made on.
_OPEN = r"""
(async () => {
localStorage.setItem('daedalus-token', '%s');
localStorage.setItem('daedalus-server', '%s');
drive.route('%s', { stream: true });
const realNow = Date.now;
let offset = 0;
Date.now = () => realNow() + offset;
const sse = await bounded(load('sse.js'), 'sse import',
  _dashnodeStepTimeoutMs);
const push = (payload) => drive.lastScript().push('event: command\ndata: '
  + JSON.stringify(payload) + '\n\n');
""" % (_TOKEN, _SERVER, _STREAM)

_CLOSE = """
})().catch(leave);
"""

# What reached a subscriber, and nothing more. The frame filter is NOT
# re-applied here: a subscriber that re-applies it sees the same thing
# whether or not the module applies it.
_SUBSCRIBE = r"""
const seen = [];
sse.subscribe((e) => seen.push([e.__internal === true, e.type,
  e.kind === undefined ? null : e.kind, e.id === undefined ? null : e.id]));
"""

_UP = r"""
sse.start();
await bounded(settle(), 'stream connect', _dashnodeStepTimeoutMs);
"""

_OPEN_STREAM = _SUBSCRIBE + _UP

# The clock before anything, with the transport log read at the same
# point: nothing requested, so the zero is a client that has not run.
_BEFORE = r"""
report({ lastEventAt: sse.lastEventAt(), reader: typeof sse.lastEventAt });
"""

_AFTER_FRAME = _OPEN_STREAM + r"""
const onConnect = sse.lastEventAt();
offset += %d;
push({ kind: 'event', id: 'e1', type: 'result' });
await bounded(settle(), 'first frame', _dashnodeStepTimeoutMs);
report({ lastEventAt: sse.lastEventAt(), onConnect, seen });
""" % _STEP_MS

_AFTER_FURTHER = _OPEN_STREAM + r"""
const onConnect = sse.lastEventAt();
offset += %d;
push({ kind: 'event', id: 'e1', type: 'result' });
await bounded(settle(), 'first frame', _dashnodeStepTimeoutMs);
const onFirst = sse.lastEventAt();
offset += %d;
push({ kind: 'event', id: 'e2', type: 'result' });
await bounded(settle(), 'second frame', _dashnodeStepTimeoutMs);
report({ lastEventAt: sse.lastEventAt(), onConnect, onFirst, seen });
""" % (_STEP_MS, _STEP_MS)

_EVICT = _OPEN_STREAM + r"""
const frames = () => seen.filter((e) => e[0] === false);
const ids = [];
for (let i = 0; i < %d; i += 1) ids.push('e' + i);
for (const id of ids) push({ kind: 'event', id, type: 'result' });
for (let guard = 0; guard < 8 && frames().length < ids.length; guard += 1) {
  await bounded(settle(), 'fed frames', _dashnodeStepTimeoutMs);
}
const fed = frames().slice();
push({ kind: 'event', id: ids[0], type: 'result' });
await bounded(settle(), 'oldest replayed', _dashnodeStepTimeoutMs);
const afterOldest = frames().slice();
push({ kind: 'event', id: ids[ids.length - 1], type: 'result' });
await bounded(settle(), 'newest replayed', _dashnodeStepTimeoutMs);
report({ fed, afterOldest, seen: frames() });
"""

# Each of `emit`'s three discards, with one real frame between them.
_DISCARDED = _OPEN_STREAM + r"""
const onConnect = sse.lastEventAt();
offset += %d;
drive.lastScript().push('event: command\ndata: {not json\n\n');
await bounded(settle(), 'unparseable frame', _dashnodeStepTimeoutMs);
const afterParse = sse.lastEventAt();
offset += %d;
push({ type: 'result', id: 'cmd-1', code: 'document.title' });
await bounded(settle(), 'broadcast-shaped frame', _dashnodeStepTimeoutMs);
const afterFilter = sse.lastEventAt();
offset += %d;
push({ kind: 'event', id: 'e1', type: 'result' });
await bounded(settle(), 'event frame', _dashnodeStepTimeoutMs);
const afterEvent = sse.lastEventAt();
offset += %d;
push({ kind: 'event', id: 'e1', type: 'result' });
await bounded(settle(), 'replayed frame', _dashnodeStepTimeoutMs);
const script = drive.lastScript();
report({ onConnect, afterParse, afterFilter, afterEvent,
  lastEventAt: sse.lastEventAt(), seen, read: script.settlements });
""" % (_STEP_MS, _STEP_MS, _STEP_MS, _STEP_MS)

# A broadcast eval command reaches this stream too and carries no kind.
_FRAME_KIND = _OPEN_STREAM + r"""
push({ type: 'result', id: 'cmd-1', code: 'document.title' });
await bounded(settle(), 'broadcast-shaped frame', _dashnodeStepTimeoutMs);
const afterBroadcast = seen.slice();
push({ kind: 'event', id: 'e1', type: 'tab-updated', tabId: 3 });
await bounded(settle(), 'event frame', _dashnodeStepTimeoutMs);
report({ seen, afterBroadcast, read: drive.lastScript().settlements });
"""

# A subscriber that throws, and the one behind it.
_THROWING_LISTENER = _UP + _SUBSCRIBE + r"""
sse.subscribe(() => { throw new Error('listener blew up'); });
const heard = [];
sse.subscribe((e) => heard.push(e.id));
push({ kind: 'event', id: 'e1', type: 'result' });
await bounded(settle(), 'first frame', _dashnodeStepTimeoutMs);
push({ kind: 'event', id: 'e2', type: 'result' });
await bounded(settle(), 'second frame', _dashnodeStepTimeoutMs);
report({ heard, seen, read: drive.lastScript().settlements });
"""

# A listener added from inside a listener, on the SSE side this time.
_JOINING_LISTENER = _OPEN_STREAM + r"""
const order = [];
sse.subscribe(() => {
  order.push('first');
  sse.subscribe((e) => order.push('joined:' + e.id));
});
push({ kind: 'event', id: 'e1', type: 'result' });
await bounded(settle(), 'the joining frame', _dashnodeStepTimeoutMs);
report({ order, seen });
"""

# The three directions of the conjunction: the negative first, then a
# changed-token event whose restart is the oracle that the log sees one.
_CHANGED = r"""
localStorage.setItem('daedalus-token', '%s');
window.fire('storage', { key: 'daedalus-token', newValue: '%s' });
await bounded(settle(), 'changed-token storage event',
  _dashnodeStepTimeoutMs);
report();
"""


def _run(body):
    return run_scenario(_OPEN + body + _CLOSE, modules=('sse.js',))


def _bound():
    """`MAX_DISPATCHED_IDS`, read out of the module's own source.

    The property is the eviction, so a literal would pin today's number
    instead. A declaration this reader cannot see fails by name, never as
    a silent zero that would make every id look ancient.
    """
    source = (ROOT / 'dashboard' / 'sse.js').read_text(encoding='utf-8')
    found = re.findall(
        r'const\s+MAX_DISPATCHED_IDS\s*=\s*(\d+)\s*;', source)
    if len(found) != 1:
        raise AssertionError(
            'sse.js does not declare one dispatched-id bound: ' + str(found))
    return int(found[0])


# The bound from both sides, read out of the module rather than restated.
_BOUNDARY = _OPEN_STREAM + r"""
const frames = () => seen.filter((e) => e[0] === false);
const quiet = async (label) => {
  for (let guard = 0; guard < 3; guard += 1) {
    await bounded(settle(), label, _dashnodeStepTimeoutMs);
  }
};
const bound = %d;
const ids = [];
for (let i = 0; i < bound; i += 1) ids.push('e' + i);
for (const id of ids) push({ kind: 'event', id, type: 'result' });
for (let guard = 0; guard < 8 && frames().length < bound; guard += 1) {
  await bounded(settle(), 'the bound frames arriving', _dashnodeStepTimeoutMs);
}
const fed = frames().length;
push({ kind: 'event', id: ids[0], type: 'result' });
await quiet('replay at the bound');
const atBound = frames().length;
push({ kind: 'event', id: 'e' + bound, type: 'result' });
await quiet('the frame past the bound');
const past = frames().length;
push({ kind: 'event', id: ids[0], type: 'result' });
await quiet('replay past the bound');
report({ bound, fed, atBound, past, after: frames().length,
  last: frames()[frames().length - 1][3] });
""" % _bound()


def _assert_one_restart(report):
    """The log holds the start and one restart. A direction that
    restarted when it should not reaches three; one that failed to
    restart reaches one.
    """
    requests = report['requests']
    assert len(requests) == 2, report
    assert report['unplanned'] == [], report
    assert [r['n'] for r in requests] == [1, 2], report
    assert [r['target'] for r in requests] == [_STREAM, _STREAM], report
    assert [r['authorization'] for r in requests] == [_BEARER_OLD,
                                                      _BEARER_NEW], report


def test_the_last_event_at_reads_zero_before_anything_happens(_tmp):
    """`lastEventAt` reporting before the client has run, where the clock
    has no reading and the transport log is empty beside it."""
    report = _run(_BEFORE)
    assert report['reader'] == 'function', report
    assert report['lastEventAt'] == 0, report
    assert report['requests'] == [], report


def test_the_last_event_at_reports_the_connect_and_the_frame_after_it(_tmp):
    """`emit`'s stamp or `run`'s connect stamp missing: the clock does
    not move on a frame.
    The connect's own reading is the liveness of the assertion, so the
    frame's is compared against it rather than against a bare zero."""
    report = _run(_AFTER_FRAME)
    assert report['onConnect'] > 0, report
    assert report['lastEventAt'] > report['onConnect'], report
    assert report['seen'][-1] == [False, 'result', 'event', 'e1'], report
    assert len(report['seen']) == 3, report


def test_the_last_event_at_moves_on_to_a_further_frame(_tmp):
    """A clock written once per connection rather than per frame. The two
    frames carry distinct values, so the third reading separates the two
    behaviours where a single frame cannot."""
    report = _run(_AFTER_FURTHER)
    assert report['onConnect'] > 0, report
    assert report['onFirst'] > report['onConnect'], report
    assert report['lastEventAt'] > report['onFirst'], report
    assert report['seen'][-1] == [False, 'result', 'event', 'e2'], report
    assert len(report['seen']) == 4, report


def test_a_frame_the_module_never_dispatched_does_not_move_the_clock(_tmp):
    """`emit` stamping on the way in rather than on the way to a
    dispatch. The clock is the dashboard's "last event" readout and
    `relTime` renders anything under two seconds as "now", so a frame the
    module threw away reads as a live one.

    All three of `emit`'s discards, and the real frame between them is
    what shows the clock still moves. `errors` is the liveness of the
    first: a frame that never arrived would leave the clock unmoved
    too."""
    report = _run(_DISCARDED)
    statuses = [[True, 'sse-status', None, None]] * 2
    assert report['onConnect'] > 0, report
    assert any('[sse] parse error' in line for line in report['errors']), \
        report['errors']
    assert len(report['read']) == 4, report['read']
    assert report['afterParse'] == report['onConnect'], report
    assert report['afterFilter'] == report['onConnect'], report
    assert report['afterEvent'] > report['afterFilter'], report
    assert report['lastEventAt'] == report['afterEvent'], report
    assert report['seen'] == statuses + [[False, 'result', 'event', 'e1']], \
        report


def test_a_listener_added_during_a_dispatch_joins_it_in_sse_js(_tmp):
    """A listener added during a dispatch joins that dispatch.

    `dispatch` iterates the `Set` itself, and a `Set`'s iterator is
    live: it visits what the set holds at each step rather than what it
    held when iteration began. The subscriber arranges nothing here, so
    this pins the shape the module already has, beside the identical
    one on `app.js`'s `bus.emit`."""
    report = _run(_JOINING_LISTENER)
    assert report['order'] == ['first', 'joined:e1'], report
    assert report['seen'][-1] == [False, 'result', 'event', 'e1'], report


def test_a_throwing_listener_neither_silences_the_rest_nor_escapes(_tmp):
    """`dispatch`'s try/catch around each listener, which nothing else on
    the branch holds. Both halves are asserted because either alone is
    satisfied by a dispatch that drops every listener on the first
    throw: the recorder behind the thrower has to have run, and the
    reader loop that fed the frame has to still be reading afterwards --
    an escaping throw is caught by `run` and ends the stream, which the
    second frame and the absence of a stream error both show."""
    report = _run(_THROWING_LISTENER)
    assert report['heard'] == ['e1', 'e2'], report
    assert report['seen'] == [[False, 'result', 'event', 'e1'],
                              [False, 'result', 'event', 'e2']], report
    assert [chunk['kind'] for chunk in report['read']] == ['chunk', 'chunk'], \
        report['read']
    assert report['errors'] == [
        '[sse] listener error listener blew up [object Object]',
        '[sse] listener error listener blew up [object Object]',
    ], report['errors']


def test_a_frame_without_its_own_kind_never_reaches_a_subscriber(_tmp):
    """`emit` dropping the `payload.kind === 'event'` filter paints raw
    broadcast command results into the event log. Two directions, because
    one positive is satisfied by a module that dispatches everything, and
    the negative's frame is shown to have reached the reader."""
    report = _run(_FRAME_KIND)
    statuses = [[True, 'sse-status', None, None]] * 2
    assert report['afterBroadcast'] == statuses, report
    assert report['seen'] == statuses + [[False, 'tab-updated', 'event',
                                         'e1']], report
    read = report['read']
    assert [entry['kind'] for entry in read] == ['chunk', 'chunk'], report
    assert 'cmd-1' in read[0]['text'], report
    assert 'tab-updated' in read[1]['text'], report


def test_the_oldest_dispatched_id_is_forgotten_and_the_newest_is_not(_tmp):
    """`emit`'s eviction removed, or evicting the newest id instead of
    the
    oldest. `fed` is the oracle: if the frames had not all arrived and
    dispatched, the replay that adds nothing would prove nothing."""
    count = _bound() + 1
    report = _run(_EVICT % count)
    fed = report['fed']
    assert len(fed) == count, report
    assert fed[0][3] == 'e0', report
    assert fed[-1][3] == 'e' + str(count - 1), report
    assert report['afterOldest'] == fed + [[False, 'result', 'event',
                                           'e0']], report
    assert report['seen'] == report['afterOldest'], report


def test_nothing_is_forgotten_until_one_past_the_id_bound(_tmp):
    """`emit`'s `dispatchedIds.size > MAX_DISPATCHED_IDS` written `>=`.
    The eviction property and
    order both hold either way, so a suite asserting only those cannot
    tell them apart -- but at rest the set is documented to hold the
    whole bound, and `>=` leaves it one short.

    The first direction gives the second its meaning: at the bound the
    oldest is still remembered, so its replay is dropped, and one frame
    later the same replay is dispatched. `fed` is the liveness of
    both."""
    bound = _bound()
    report = _run(_BOUNDARY)
    # The report carries every settlement and this scenario reads the
    # whole bound, so a failure prints six numbers, not 500 chunks.
    counts = {key: report[key] for key in
              ('bound', 'fed', 'atBound', 'past', 'after', 'last')}
    assert counts == {'bound': bound, 'fed': bound, 'atBound': bound,
                      'past': bound + 1, 'after': bound + 2,
                      'last': 'e0'}, counts


def test_a_storage_event_with_a_changed_token_restarts_the_client(_tmp):
    """`sse.js`'s `storage` listener refusing the conjunction's positive
    limb. The
    second request is the restart, on the same route and carrying the
    replacement token, so the count is corroborated by what it carries."""
    report = _run(_UP + _CHANGED % (_OTHER_TOKEN, _OTHER_TOKEN))
    _assert_one_restart(report)


def test_a_storage_event_with_the_same_token_does_not_restart(_tmp):
    """`sse.js`'s `storage` listener dropping the `newValue` comparison.
    The log's holding
    exactly two is what shows the same-value event added nothing and the
    changed-token event after it added the one restart."""
    report = _run(_UP + r"""
window.fire('storage', { key: 'daedalus-token', newValue: '%s' });
await bounded(settle(), 'unchanged-token storage event',
  _dashnodeStepTimeoutMs);
""" % (_TOKEN,) + _CHANGED % (_OTHER_TOKEN, _OTHER_TOKEN))
    _assert_one_restart(report)


def test_a_storage_event_on_another_key_does_not_restart(_tmp):
    """`sse.js`'s `storage` listener dropping the `e.key` check. The
    value differs too, so
    the key is the only limb holding. The event is delivered without the
    storage write a browser makes first, which this listener never reads,
    so the restart that follows still lands on the planned route."""
    report = _run(_UP + r"""
window.fire('storage', { key: 'daedalus-server', newValue: '%s' });
await bounded(settle(), 'other-key storage event', _dashnodeStepTimeoutMs);
""" % (_OTHER_SERVER,) + _CHANGED % (_OTHER_TOKEN, _OTHER_TOKEN))
    _assert_one_restart(report)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dash_sse_')


if __name__ == '__main__':
    raise SystemExit(main())
