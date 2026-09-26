#!/usr/bin/env python3
"""What `dashboard/sse.js` reports, forgets and restarts.

Three behaviours, and nothing else. The repeated-event-id drop and the
connecting/connected/reconnecting sequence are already held by
`test_dashboard_fanout` and `test_dashboard_eval`, which drive this module
as a harness argument; re-pinning them here would duplicate a suite a
reviewer is right to keep. What is left is the clock `lastEventAt()`
reports, the bound that forgets the OLDEST dispatched id, and the
`storage` event that restarts the client only on a changed token.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _dashshell import run_scenario  # noqa: E402
from _repo import ROOT  # noqa: E402

_SERVER = 'https://example.com'
_OTHER_SERVER = 'https://other.example'
_TOKEN = 'tok-abcdefghijklmnop'
_OTHER_TOKEN = 'tok-zyxwvutsrqponmji'
_STREAM = _SERVER + '/stream?tab=dashboard'
_BEARER_OLD = 'Bearer ' + _TOKEN
_BEARER_NEW = 'Bearer ' + _OTHER_TOKEN
# Two clock values that cannot coincide, so "a further frame moved the
# clock" is a statement about the code and not about the millisecond the
# runner happened to land in.
_STEP_MS = 4000

# Every scenario opens the same way: the storage and the one planned
# route installed before the import, a clock the scenario drives, and the
# module namespace kept for the calls under test.
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

# The clock read before anything happens, with the transport log read at
# the same point: nothing has been requested, so the zero is a fact about
# a client that has not run rather than a client that cannot report.
_BEFORE = r"""
report({ lastEventAt: sse.lastEventAt(), reader: typeof sse.lastEventAt });
"""

# The connect sets the clock, and the first frame moves it on. Both are
# relative readings: the suite never names a millisecond.
_AFTER_FRAME = r"""
const seen = [];
sse.subscribe((e) => { if (e.kind === 'event') seen.push(e.id); });
sse.start();
await bounded(settle(), 'stream connect', _dashnodeStepTimeoutMs);
const onConnect = sse.lastEventAt();
offset += %d;
push({ kind: 'event', id: 'e1', type: 'result' });
await bounded(settle(), 'first frame', _dashnodeStepTimeoutMs);
report({ lastEventAt: sse.lastEventAt(), onConnect, seen });
""" % _STEP_MS

# A further frame, on its own clock, must move it again; the first
# frame's own reading is what would survive a clock written once.
_AFTER_FURTHER = r"""
const seen = [];
sse.subscribe((e) => { if (e.kind === 'event') seen.push(e.id); });
sse.start();
await bounded(settle(), 'stream connect', _dashnodeStepTimeoutMs);
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

# The whole bound plus one, fed down a single in-process stream, then the
# oldest id and the newest one replayed. `fed` is the oracle: if the fed
# frames did not all arrive and dispatch, the replay that adds nothing
# proves nothing.
_EVICT = r"""
const seen = [];
sse.subscribe((e) => { if (e.kind === 'event') seen.push(e.id); });
sse.start();
await bounded(settle(), 'stream connect', _dashnodeStepTimeoutMs);
const ids = [];
for (let i = 0; i < %d; i += 1) ids.push('e' + i);
for (const id of ids) push({ kind: 'event', id, type: 'result' });
for (let guard = 0; guard < 8 && seen.length < ids.length; guard += 1) {
  await bounded(settle(), 'fed frames', _dashnodeStepTimeoutMs);
}
const fed = seen.slice();
push({ kind: 'event', id: ids[0], type: 'result' });
await bounded(settle(), 'oldest replayed', _dashnodeStepTimeoutMs);
const afterOldest = seen.slice();
push({ kind: 'event', id: ids[ids.length - 1], type: 'result' });
await bounded(settle(), 'newest replayed', _dashnodeStepTimeoutMs);
report({ fed, afterOldest, seen, oldest: ids[0],
  newest: ids[ids.length - 1] });
"""

# A broadcast-shaped eval command reaches this stream too, and carries no
# `kind`. The subscriber here is deliberately plain: re-applying the
# filter inside the harness is the defect this control exists for, and a
# subscriber that re-applies it sees the same thing whether or not the
# module filters.
_FRAME_KIND = r"""
const seen = [];
sse.subscribe((e) => seen.push([e.__internal === true, e.type,
  e.kind === undefined ? null : e.kind]));
sse.start();
await bounded(settle(), 'stream connect', _dashnodeStepTimeoutMs);
push({ type: 'result', id: 'cmd-1', code: 'document.title' });
await bounded(settle(), 'broadcast-shaped frame', _dashnodeStepTimeoutMs);
const afterBroadcast = seen.slice();
push({ kind: 'event', id: 'e1', type: 'tab-updated', tabId: 3 });
await bounded(settle(), 'event frame', _dashnodeStepTimeoutMs);
report({ seen, afterBroadcast, read: drive.lastScript().settlements });
"""


def _run(body):
    return run_scenario(_OPEN + body + _CLOSE, modules=('sse.js',))


def _bound():
    """`MAX_DISPATCHED_IDS`, read out of the module's own source.

    The property under test is the eviction, so a literal here would pin
    today's number instead of the behaviour. A declaration this reader
    cannot see is a failure by name, never a silent zero that would make
    every id look ancient.
    """
    source = (ROOT / 'dashboard' / 'sse.js').read_text(encoding='utf-8')
    found = re.findall(
        r'const\s+MAX_DISPATCHED_IDS\s*=\s*(\d+)\s*;', source)
    if len(found) != 1:
        raise AssertionError(
            'sse.js does not declare one dispatched-id bound: ' + str(found))
    return int(found[0])


def _assert_one_restart(report):
    """The log holds the start and the restart, and nothing else.

    Every direction of the `storage` conjunction ends here with the same
    shape: the initial request on the planned route, and one restart that
    carries the replacement token. A direction that restarted when it
    should not reaches three; one that failed to restart reaches one.
    """
    requests = report['requests']
    assert len(requests) == 2, report
    assert report['unplanned'] == [], report
    assert [r['n'] for r in requests] == [1, 2], report
    assert [r['target'] for r in requests] == [_STREAM, _STREAM], report
    assert [r['authorization'] for r in requests] == [_BEARER_OLD,
                                                      _BEARER_NEW], report


def test_the_last_event_at_reads_zero_before_anything_happens(_tmp):
    """`sse.js:49` reporting anything else before the client has run: the
    clock has no reading yet, and the transport log is empty beside it,
    so the zero belongs to a client that has not started rather than to
    one that cannot report."""
    report = _run(_BEFORE)
    assert report['reader'] == 'function', report
    assert report['lastEventAt'] == 0, report
    assert report['requests'] == [], report


def test_the_last_event_at_reports_the_connect_and_the_frame_after_it(_tmp):
    """`sse.js:53` missing, or `:121` missing: the clock does not move on
    a frame. The connect's own reading is the liveness of the assertion —
    a client that never reported at all would satisfy a bare `== 0` here
    too, so both readings are compared and the frame's is later."""
    report = _run(_AFTER_FRAME)
    assert report['onConnect'] > 0, report
    assert report['lastEventAt'] > report['onConnect'], report
    assert report['seen'] == ['e1'], report


def test_the_last_event_at_moves_on_to_a_further_frame(_tmp):
    """A clock written once per connection rather than per frame. The two
    frames carry distinct values, so the second reading separates the two
    behaviours where a single frame cannot: it is later than the first
    frame's, not merely later than the connect's."""
    report = _run(_AFTER_FURTHER)
    assert report['onConnect'] > 0, report
    assert report['onFirst'] > report['onConnect'], report
    assert report['lastEventAt'] > report['onFirst'], report
    assert report['seen'] == ['e1', 'e2'], report


def test_a_frame_without_its_own_kind_never_reaches_a_subscriber(_tmp):
    """`sse.js:59` dropping the `kind: 'event'` filter. A broadcast eval
    command reaches the dashboard stream too and carries no kind, so a
    filter that stops being applied paints raw command results into the
    event log. Two directions, because one positive is satisfied by a
    module that dispatches everything; the negative's frame is shown to
    have reached the reader, so its silence is the module's and not the
    harness's."""
    report = _run(_FRAME_KIND)
    statuses = [[True, 'sse-status', None], [True, 'sse-status', None]]
    assert report['afterBroadcast'] == statuses, report
    assert report['seen'] == statuses + [[False, 'tab-updated', 'event']], \
        report
    read = report['read']
    assert [entry['kind'] for entry in read] == ['chunk', 'chunk'], report
    assert 'cmd-1' in read[0]['text'], report
    assert 'tab-updated' in read[1]['text'], report


def test_the_oldest_dispatched_id_is_forgotten_and_the_newest_is_not(_tmp):
    """`sse.js:65` removed, or evicting the newest id instead of the
    oldest. The bound is read out of the module, never restated here, so
    this pins the eviction and its ORDER rather than today's number."""
    count = _bound() + 1
    report = _run(_EVICT % count)
    fed = report['fed']
    assert len(fed) == count, report
    assert fed[0] == 'e0', report
    assert fed[-1] == 'e' + str(count - 1), report
    # The oldest is past the bound and replays; the newest is still
    # inside it and does not.
    assert report['afterOldest'] == fed + ['e0'], report
    assert report['seen'] == report['afterOldest'], report
    assert report['oldest'] == 'e0', report
    assert report['newest'] == fed[-1], report


def test_a_storage_event_with_a_changed_token_restarts_the_client(_tmp):
    """`sse.js:159-160` refusing the conjunction's positive limb. The
    second request is the restart, on the same route and carrying the
    replacement token, so the count is corroborated by what it carries."""
    report = _run(r"""
sse.start();
await bounded(settle(), 'stream connect', _dashnodeStepTimeoutMs);
localStorage.setItem('daedalus-token', '%s');
window.fire('storage', { key: 'daedalus-token', newValue: '%s' });
await bounded(settle(), 'changed-token storage event',
  _dashnodeStepTimeoutMs);
report();
""" % (_OTHER_TOKEN, _OTHER_TOKEN))
    _assert_one_restart(report)


def test_a_storage_event_with_the_same_token_does_not_restart(_tmp):
    """`sse.js:159` dropping the `newValue` comparison: the key alone
    would restart. The negative comes first and the changed-token event
    after it, so the log's holding exactly two is what shows the first
    event added nothing and the second added the one restart."""
    report = _run(r"""
sse.start();
await bounded(settle(), 'stream connect', _dashnodeStepTimeoutMs);
window.fire('storage', { key: 'daedalus-token', newValue: '%s' });
await bounded(settle(), 'unchanged-token storage event',
  _dashnodeStepTimeoutMs);
localStorage.setItem('daedalus-token', '%s');
window.fire('storage', { key: 'daedalus-token', newValue: '%s' });
await bounded(settle(), 'changed-token storage event',
  _dashnodeStepTimeoutMs);
report();
""" % (_TOKEN, _OTHER_TOKEN, _OTHER_TOKEN))
    _assert_one_restart(report)


def test_a_storage_event_on_another_key_does_not_restart(_tmp):
    """`sse.js:159` dropping the key check: any storage change would
    restart. The value here is a different one too, so the key is the
    only limb holding; the event is delivered without the storage write a
    browser would have made first, which this listener never reads, so
    the restart that follows still lands on the planned route."""
    report = _run(r"""
sse.start();
await bounded(settle(), 'stream connect', _dashnodeStepTimeoutMs);
window.fire('storage', { key: 'daedalus-server', newValue: '%s' });
await bounded(settle(), 'other-key storage event', _dashnodeStepTimeoutMs);
localStorage.setItem('daedalus-token', '%s');
window.fire('storage', { key: 'daedalus-token', newValue: '%s' });
await bounded(settle(), 'changed-token storage event',
  _dashnodeStepTimeoutMs);
report();
""" % (_OTHER_SERVER, _OTHER_TOKEN, _OTHER_TOKEN))
    _assert_one_restart(report)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dash_sse_')


if __name__ == '__main__':
    raise SystemExit(main())
