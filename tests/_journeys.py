"""The user journeys the performance ratchet measures.

Not a suite itself — run_tests.py only loads `test_*.py`. `scripts/ci/
journey_budget.py` drives each one in a fresh child process, so this module
has to be runnable from a `__main__` handed nothing but a journey name and
the repository root.

Each journey drives the REAL bridge through `_util.bridge()`, which spawns
`server.py` and polls `/health`, so what is measured is what a user waits for
rather than how long a test function took. Every input is fixed — token, tab,
command id, code, result payload — because a counter measured over moving
inputs is a counter over the harness.

What a journey RETURNS is its observable rendering, and the harness records
the sha256 of that rendering. A sha is the shape contract: a journey that
starts reporting a different frame, a different event type or a different
result body is no longer the journey the recorded count belongs to, so the
harness refuses to compare rather than re-baselining itself. Delivery ids,
timestamps, uuids and the temp path a bridge was spawned under are excluded
by hand here, at the one place the shapes are known.
"""
import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# Its own siblings, and the repository root above them: the command TTL's
# default is read from `daedalus_bridge.env_config`, and this module is
# imported by suites that put `tests/` on the path without the root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1]))
import _bridge  # noqa: E402
import _fanout  # noqa: E402
import _journey_typed  # noqa: E402
import _mcp_load  # noqa: E402
import _util  # noqa: E402
from daedalus_bridge.env_config import CMD_TTL_DEFAULT  # noqa: E402

# The journey whose own bridge spawn carries this credential. A bridge refuses
# a data root a second process holds, and each journey is measured in its own
# child, so nothing here is shared between two runs.
COMMAND_TOKEN = 'journeycmd'
DASHBOARD_TOKEN = 'journeydash'

COMMAND_TAB = 'journeytab'
DASHBOARD_TAB = 'journeyfan'
DASHBOARD_URL = 'https://journey.example.com/panel'
DASHBOARD_TITLE = 'journey panel'

COMMAND_ID = 'journey-command-1'
COMMAND_CODE = '1 + 1'
COMMAND_RESULT = {'answer': 42, 'label': 'journey'}
COMMAND_WORLD = 'page-main'

MCP_TAB = 'journeymcp'
MCP_COMMAND_ID = 'journey-mcp-1'
MCP_CODE = '2 + 2'
MCP_RESULT = {'answer': 4, 'label': 'journey-mcp'}

# `journey_counters`' own two constants; the reasoning that makes the
# subtraction necessary is beside the constants there. Both are pseudo-
# journeys: neither is in `NAMES`, neither produces a shape sha, and no count
# is recorded for either.
STARTUP_ONLY = 'startup-only'
BRIDGE_ONLY = 'bridge-only'
BRIDGE_TOKEN = 'journeybase'

# The shape of a dashboard session, which is what the fan-out journey carries.
#
# Every tab the extension has open is re-POSTed to `/sync-tabs` in full every
# 30 seconds, and every one of those posts publishes a `tabs-synced` event to
# every connected dashboard window. So a window left open sees one
# `tabs-synced` per heartbeat for as long as it is open, and the fan-out it
# receives is a function of the SESSION's length rather than of one request.
FANOUT_TABS = 10
# The extension's own figure for a session: the comment on
# `scheduleRegisterAllTabs` in `extension/worker/registry.js` calls "a 10-url
# open_tabs" the case its coalescing was written for. Each sync posts the
# WHOLE list, so this is also what makes a `tabs-synced` event the size one
# really is rather than a single-entry fixture.
FANOUT_HEARTBEATS = 20
# `chrome.alarms.create('daedalus-heartbeat', { periodInMinutes: 0.5 })` in
# `extension/background.js` arms the heartbeat and its `registerAllTabs`
# posts the entire tab list every time it fires: two syncs a minute, so the
# count above is the ten minutes a panel is kept open while a task is driven
# through it.
#
# One `/register` on top of those, because a tab whose url or title changed
# publishes a `tab-updated` of its own, and that is the event this journey
# has always read its way to.
FANOUT_REGISTRATIONS = 1
# `CMD_TTL_DEFAULT`, imported at the top of this file, is the command TTL's
# default read from the product rather than written out here. It is a product
# constant and this module does not set it — a harness that bought its own
# headroom by overriding one would be measuring a bridge configured unlike
# every other — so its default is the ceiling the budget below is measured
# against. A literal here would drift apart from it the moment the default
# moves, in a number that now chooses between two sentences rather than
# decorating one.

# The frames a run of this shape publishes, and how far past them the read
# will step before calling a shape it has never seen a failure. Every event
# above is published BEFORE the subscription opens, so the first pass of the
# drain delivers all of them.
FANOUT_EVENTS = FANOUT_HEARTBEATS + FANOUT_REGISTRATIONS
FANOUT_MAX_FRAMES = FANOUT_EVENTS + 1


def command_round_trip(base, docroot):
    """A command enqueued, delivered, answered, and read back as a result."""
    del docroot
    status, raw = _bridge.put_command(base, {
        'token': COMMAND_TOKEN,
        'tab': COMMAND_TAB,
        'id': COMMAND_ID,
        'code': COMMAND_CODE,
    })
    assert status == 200, (status, raw)
    enqueued = json.loads(raw)

    frame = _bridge.read_stream_data(base, COMMAND_TOKEN, COMMAND_TAB)
    assert frame.get('id') == COMMAND_ID, frame
    assert frame.get('code') == COMMAND_CODE, frame
    assert frame.get('_did') == enqueued.get('did'), (frame, enqueued)

    status, raw = _util.post_json(base + '/result', {
        'token': COMMAND_TOKEN,
        'tabId': COMMAND_TAB,
        'id': frame['id'],
        'result': COMMAND_RESULT,
        'error': None,
        'world': COMMAND_WORLD,
        'ts': 1,
        '_did': frame['_did'],
    })
    assert status == 200, (status, raw)

    status, slot = _util.get_json(
        base + '/result?token=' + COMMAND_TOKEN + '&tab=' + COMMAND_TAB)
    assert status == 200, (status, slot)
    assert slot.get('id') == COMMAND_ID, slot
    assert slot.get('result') == COMMAND_RESULT, slot
    assert slot.get('world') == COMMAND_WORLD, slot
    return {
        'journey': 'command-round-trip',
        'enqueued': {'ok': enqueued.get('ok'),
                     'target': enqueued.get('target')},
        'frame': {key: frame[key] for key in ('type', 'id', 'code')
                  if key in frame},
        # resultGeneration is a uuid and roundtrip_ms is a clock reading; the
        # delivery id is excluded with them. What remains is the shape the
        # round trip produced.
        'result': {key: slot[key] for key in
                   ('id', 'tabId', 'result', 'error', 'world', 'ts')
                   if key in slot},
    }


def _load_front_end(base):
    """The MCP front end, loaded on a thread of its own and waited for.

    The bridge excludes its copy of the front end by WAITING for it — the
    `mcp-bootstrap` thread finishes before the journey's first request — and
    the client's copy is excluded the other way round, by having already paid
    it. Neither excludes the CALL: the `exec` round trip below is the work,
    and it runs on the journey's main thread, which counts whatever it costs.

    The load has to be off the main thread for it to be excludable at all: a
    main thread is read as the main thread whatever it executed, so an
    import on one is the journey's own work by every rule the profiler's
    thread classifier applies. The asymmetry is harness-side — the module the
    tool call reaches is the same one either way.
    """
    box = {}

    def load():
        try:
            box['mod'] = _mcp_load._load_mcp(base)
        except Exception as exc:  # pylint: disable=broad-except
            box['error'] = exc

    worker = threading.Thread(target=load, name='journey-front-end')
    worker.start()
    worker.join()
    if 'error' in box:
        raise box['error']
    return box['mod']


def mcp_exec(base, docroot):
    """One MCP tool call, answered by this thread as the extension would.

    The send is unwaited, so the bridge's own answer hands this thread the
    command's bytes and the answer goes out without polling the queue; the
    result is then read back through the `result` tool, which is the read an
    unwaited send leaves behind.

    The round trip runs on the journey's MAIN thread, and that placement is
    part of what this journey measures, not an accident. `journey_threads`
    reads `thread == 1` FIRST and whatever it executed, so the round trip is
    counted wherever the journey puts it. A worker is instead read by what it
    ran — the front end's import, told by a symbol only that dependency
    tree initialises, or an asyncio event loop — so this
    journey's own work measured 1,311,350,558 could be moved out of the
    count by nothing it does. The import is the one thing here that must NOT
    be counted, which is why `_load_front_end` keeps it on a worker of its
    own.
    """
    del docroot
    mod = _load_front_end(base)
    # The token is a ContextVar the tools read per request; this is what
    # daedalus_mcp.auth.BearerAuth does, and the journey does not pass
    # through the auth middleware to have it set for it.
    mod._token.set(_mcp_load.TOK)

    async def round_trip():
        sent = await mod.exec(tab_id=MCP_TAB, cmd_id=MCP_COMMAND_ID,
                              code=MCP_CODE, wait=False)
        queued = sent.get('command')
        assert queued, sent
        status, _ = _util.post_json(base + '/result', {
            'token': _mcp_load.TOK, 'tabId': MCP_TAB, 'id': queued['id'],
            'result': MCP_RESULT, 'error': None, 'ts': 1,
            '_did': queued['_did']})
        assert status == 200, status
        return await mod.result(tab_id=MCP_TAB)

    read = asyncio.run(round_trip())
    assert read.get('value') == MCP_RESULT, read
    assert read.get('error') is None, read
    return {
        'journey': 'mcp-exec',
        'queued': {'id': MCP_COMMAND_ID, 'code': MCP_CODE},
        'tool': {'id': read.get('id'), 'tabId': read.get('tabId'),
                 'value': read.get('value'),
                 'error': read.get('error')},
    }


def _read_all(response, wanted):
    """Every frame of a run of a known shape, and the one that was read for.

    The fan-out subscription receives every event published while it is
    connected, so a session of `FANOUT_EVENTS` events arrives as exactly that
    many frames. Reading all of them rather than stepping over them to the
    first `tab-updated` is what makes the journey a measurement of the
    DRAIN — the per-connection cursor stepping over a session's events and
    each one leaving only once every window has passed it — rather than of
    the ordering of the first two.
    """
    frames = []
    for _ in range(FANOUT_MAX_FRAMES):
        frame = _bridge.next_stream_data(response, timeout=30)
        if frame.get('type') in wanted:
            return frames + [frame], frames
        frames.append(frame)
    raise AssertionError(
        f'none of the wanted {sorted(wanted)} events arrived in '
        f'{FANOUT_MAX_FRAMES} frames; got '
        f'{[frame.get("type") for frame in frames]}')


def _dashboard_tabs():
    """The tab list a heartbeat re-POSTs, at the size a session carries.

    One tab is the journey's own and carries the ids the assertions below are
    written against; the rest are the siblings a real window has open, and
    they are what makes each `/sync-tabs` the size the extension's own
    comment calls a session rather than a single-entry fixture.
    """
    tabs = [{'tabId': DASHBOARD_TAB, 'url': DASHBOARD_URL,
             'title': DASHBOARD_TITLE}]
    for index in range(1, FANOUT_TABS):
        tabs.append({'tabId': f'{DASHBOARD_TAB}-{index:02d}',
                     'url': f'https://journey.example.com/page/{index:02d}',
                     'title': f'journey page {index:02d}'})
    return tabs


def dashboard_fanout(base, docroot):
    """A session's fan-out: every event a dashboard window receives."""
    del docroot
    tabs = _dashboard_tabs()
    synced = []
    # The clock the TTL runs on, read here so the refusal below can carry
    # the evidence rather than assert a cause. `monotonic`, because this is
    # a DURATION and a wall clock can step.
    started = time.monotonic()
    # /register is update-only, so the registry has to hold the tab before it
    # can publish anything, and every sync above publishes an event of its
    # own — which is why a session is a RUN of syncs rather than one, sized
    # by the two counts whose basis is stated above.
    for _ in range(FANOUT_HEARTBEATS):
        status, body = _util.post_json(base + '/sync-tabs', {
            'token': DASHBOARD_TOKEN, 'tabs': tabs,
        })
        assert status == 200, (status, body)
        synced.append(body)

    # EVERY event is published BEFORE the subscription opens, and that
    # ordering is the determinism, not a convenience. The bridge's stream loop
    # delivers whatever is queued on its first pass and otherwise idles on a
    # wall-clock tick, so a stream held open across a client round-trip
    # performs a number of idle passes that depends on how long the machine
    # took — the same code and the same events counting different
    # instructions, which is not a count a ratchet can compare. It is also the
    # more honest journey: an event published with no window attached is
    # retained for the next one, which is the fan-out property itself.
    #
    # THAT RETENTION HAS A CEILING, and the resize made it load-bearing.
    # `command_queue.gc_loop` sweeps every `min(30, DAEDALUS_CMD_TTL)`
    # seconds and `remove_expired` unlinks anything older than the TTL —
    # regardless of a subscription, and before the fan-out drain ever sees
    # it. So the precondition this journey now has is that the whole window
    # above, from the FIRST publish to the read below, completes inside the
    # TTL, whose default is `CMD_TTL_DEFAULT` — the product's own, read at
    # the top of this file rather than written here. At two events that was
    # free; at FANOUT_EVENTS round trips under callgrind it is a real
    # budget, and it is the one thing that could make this journey fail
    # on a slow runner.
    #
    # Neither side of it moves for this journey: the TTL keeps its product
    # default for the reason stated above, and the event count is not cut to
    # fit it, because a session sized to an infrastructure ceiling rather
    # than to a product basis is the padding error this journey was resized
    # to remove. So the ceiling is left visible and the shortfall is named
    # in the assertion below, and whether it holds is a measurement.
    registered = None
    for _ in range(FANOUT_REGISTRATIONS):
        status, body = _util.post_json(base + '/register', {
            'token': DASHBOARD_TOKEN,
            'tabId': DASHBOARD_TAB,
            'url': DASHBOARD_URL,
            'title': DASHBOARD_TITLE,
        })
        assert status == 200, (status, body)
        registered = body

    connection, response = _bridge.stream_response(
        base, DASHBOARD_TOKEN, _fanout.DASHBOARD)
    try:
        frames, syncs = _read_all(response, {'tab-updated'})
    finally:
        response.close()
        connection.close()

    # ONE clock read, because the printed figure and the branch that decides
    # between two sentences used to be two reads: at the boundary this run
    # could print `89.9s` and then say it was over. The cause is in the
    # message because it is otherwise an assertion this journey has not
    # measured — whether this loop spent longer than the TTL is a fact only
    # this run knows — so the reader is handed both numbers that decide it,
    # the window the loop took and the ceiling it has to fit inside.
    elapsed = time.monotonic() - started
    assert len(syncs) == FANOUT_HEARTBEATS, (
        f'only {len(syncs)} of the {FANOUT_HEARTBEATS} syncs this session '
        'published were still in the queue when the window attached. Every '
        'event is published before the subscription opens, and '
        '`DAEDALUS_CMD_TTL` is the mechanism DESIGNED to remove one in '
        f'between — publishing the session took {elapsed:.1f}s against a '
        f'ceiling of {CMD_TTL_DEFAULT:g}s'
        + (' — over it, so that is the cause.'
           if elapsed > CMD_TTL_DEFAULT else
           ' — inside it, so the designed mechanism did not do this and '
           'this message no longer knows what did.')
        + ' Shorten the session rather than raise the TTL: this journey does '
        'not set it, and a session sized to an infrastructure ceiling is the '
        'padding this journey was resized to remove. Saw: '
        f'{[f.get("type") for f in syncs]}')
    assert all(frame.get('kind') == 'event' for frame in syncs), syncs
    assert all(frame.get('count') == FANOUT_TABS for frame in syncs), syncs
    frame = frames[-1]
    assert frame.get('tabId') == DASHBOARD_TAB, frame
    return {
        'journey': 'dashboard-fanout',
        'synced': {'ok': synced[-1].get('ok'),
                   'count': synced[-1].get('count')},
        'registered': registered,
        'events': [frame.get('type') for frame in frames],
        'event': {'kind': frame.get('kind'), 'type': frame.get('type'),
                  'tabId': frame.get('tabId'), 'url': frame.get('url'),
                  'title': frame.get('title')},
    }


def bridge_only(base, docroot):
    """The bridge, spawned and idle: the fixed background, and no journey.

    Nothing here performs a journey's work, and that is the whole of it. The
    bridge is the same one, under the same credential and the same readiness
    wait a journey is measured under, so the constant this measures is the
    one every journey pays.
    """
    del base, docroot
    return {'journey': BRIDGE_ONLY}


JOURNEYS = {
    'command-round-trip': (COMMAND_TOKEN, command_round_trip),
    'mcp-exec': (_mcp_load.TOK, mcp_exec),
    'dashboard-fanout': (DASHBOARD_TOKEN, dashboard_fanout),
    'screenshot': (_journey_typed.SHOT_TOKEN, _journey_typed.screenshot),
    'cdp-result': (_journey_typed.CDP_TOKEN, _journey_typed.cdp_result),
    'net-capture': (_journey_typed.NET_TOKEN, _journey_typed.net_capture),
    'segment-relay': (_journey_typed.SEG_TOKEN, _journey_typed.segment_relay),
}
NAMES = tuple(JOURNEYS)

# The marker the child prints its record behind. The bridge's own output and
# whatever a dependency decides to print share this stream, so the reader
# looks for the marker rather than taking the last line.
RECORD_MARKER = '##JOURNEY## '


def bridge_env(token):
    """The child environment a journey's bridge is spawned with."""
    return {'DAEDALUS_TOKEN': token, 'TOKEN': ''}


def _against_a_fresh_bridge(token, run):
    """`run(base, docroot)` against a bridge this call spawns and tears down.

    `await_mcp=True` is what makes the count comparable: the bridge's
    `mcp-bootstrap` thread imports the front end beside the journey and
    readiness deliberately does not wait for it, so a round that started
    while that import was mid-flight would carry whichever slice of it
    happened to run alongside. The wait puts the import wholly inside every
    round or wholly outside all of them.
    """
    with tempfile.TemporaryDirectory(prefix='journey_') as directory:
        with _util.bridge(directory, env=bridge_env(token),
                          await_mcp=True) as fixture:
            base, docroot = fixture
            return run(base, docroot)


def rendering_of(name):
    """Run one journey against a fresh bridge and return its rendering."""
    return _against_a_fresh_bridge(*JOURNEYS[name])


def bridge_only_rendering():
    """The fixed background, off a bridge that did no journey's work.

    The same scaffolding a journey runs under, which is the point: a
    constant measured from a bridge spawned any other way is a different
    constant.
    """
    return _against_a_fresh_bridge(BRIDGE_TOKEN, bridge_only)


def canonical(rendering):
    """The bytes a rendering is hashed as: sorted, no incidental spacing."""
    return json.dumps(rendering, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def sha256_of(rendering):
    return hashlib.sha256(canonical(rendering)).hexdigest()


def _print_record(name, rendering):
    print(RECORD_MARKER + json.dumps(
        {'journey': name, 'sha256': sha256_of(rendering),
         'rendering': rendering}, sort_keys=True, ensure_ascii=False),
        flush=True)


def _establish_counted_boundary():
    """Zero callgrind's counters at the one fixed point before journey work.

    The counted region is this whole child from `exec`, so interpreter
    startup and the compile of the whole import closure are inside it, on
    this process's own thread, which no journey excludes. A counted child
    reaches this line after that closure has run and before anything is
    spawned, which is the one point where the interpreter's own cost can
    leave the measurement: the bridge is spawned below and is instrumented
    from birth either way, because the valgrind argv is unchanged and it
    never issues a request of its own.

    A client request cannot be issued from pure Python -- it is `asm
    volatile` inside a GNU statement expression -- so the job compiles
    `scripts/ci/callgrind_boundary.c` and names the result here.

    Inert when the variable is unset, which is the one state this cannot
    refuse: a developer running a journey by hand is in exactly the same
    state as a counted run whose helper failed to build, and nothing in
    the child tells the two apart. So the parent refuses instead --
    `journey_child_env.boundary_refusal`, read by the callgrind counter
    before it spawns -- and everything else here refuses with the cause
    named, because a count that silently included the import again would
    not be comparable with the one beside it.
    """
    path = os.environ.get('DAEDALUS_CALLGRIND_BOUNDARY')
    if not path:
        return

    def refuse(cause):
        print(f'counted boundary unavailable: {cause}', file=sys.stderr)
        raise SystemExit(3)

    try:
        helper = ctypes.CDLL(path)
    except OSError as failure:
        refuse(f'the helper does not load: {failure}')
    try:
        zero = helper.daedalus_cg_zero_stats
    except AttributeError as failure:
        refuse(f'the helper carries no daedalus_cg_zero_stats: {failure}')
    try:
        zero()
    except Exception as failure:  # pylint: disable=broad-except
        # The call crosses into a shared object, so its failure modes are
        # whatever that object raises rather than this module's to enumerate.
        refuse(f'daedalus_cg_zero_stats did not issue: {failure}')


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journey', required=True,
                        help=f'one of {", ".join(NAMES)}, or '
                             f'{STARTUP_ONLY} or {BRIDGE_ONLY}')
    parser.add_argument('--root', type=Path, default=_util.ROOT)
    return parser


def main(argv=None):
    """Run one journey in this process and print its record."""
    _establish_counted_boundary()
    args = _parser().parse_args(argv)
    if args.journey == STARTUP_ONLY:
        return 0
    if args.journey == BRIDGE_ONLY:
        # No record, and deliberately: this run is a constant to subtract,
        # not a journey whose rendering anyone can compare.
        bridge_only_rendering()
        return 0
    if args.journey not in JOURNEYS:
        print(f'no journey named {args.journey!r}', file=sys.stderr)
        return 2
    _print_record(args.journey, rendering_of(args.journey))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
