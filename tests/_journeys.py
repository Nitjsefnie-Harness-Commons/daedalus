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
import hashlib
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bridge  # noqa: E402
import _fanout  # noqa: E402
import _journey_typed  # noqa: E402
import _mcp_load  # noqa: E402
import _util  # noqa: E402

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

# The startup-only measurement: the same interpreter and the same imports as a
# journey child, with no bridge and no journey. Every instruction counter is
# reported net of this one, so interpreter startup and import cost are not
# part of what the ratchet compares.
STARTUP_ONLY = 'startup-only'

# How many dashboard frames the fan-out journey will step over looking for the
# register's own event. A sync above seeds the registry and publishes one event
# of its own; anything past that is a shape this journey has never seen and is
# refused rather than skipped past.
FANOUT_MAX_FRAMES = 8


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


def _read_until(response, wanted_type):
    """The first dashboard frame whose `type` is `wanted_type`.

    The fan-out subscription receives every event published while it is
    connected, so the sync that seeds the registry arrives first. Reading for
    the type rather than the first frame is what makes this a journey rather
    than a read of the drain's ordering.
    """
    for _ in range(FANOUT_MAX_FRAMES):
        frame = _bridge.next_stream_data(response, timeout=30)
        if frame.get('type') == wanted_type:
            return frame
    raise AssertionError(
        f'no {wanted_type!r} event arrived in {FANOUT_MAX_FRAMES} frames')


def dashboard_fanout(base, docroot):
    """A tab registered, and the event every dashboard window receives."""
    del docroot
    # /register is update-only, so the registry has to hold the tab before it
    # can publish anything. Seeding through /sync-tabs publishes an event of
    # its own, which is what the read below steps over.
    status, body = _util.post_json(base + '/sync-tabs', {
        'token': DASHBOARD_TOKEN,
        'tabs': [{'tabId': DASHBOARD_TAB, 'url': DASHBOARD_URL,
                  'title': DASHBOARD_TITLE}],
    })
    assert status == 200, (status, body)

    # BOTH events are published BEFORE the subscription opens, and that
    # ordering is the determinism, not a convenience. The bridge's stream loop
    # delivers whatever is queued on its first pass and otherwise idles on a
    # wall-clock tick, so a stream held open across a client round-trip
    # performs a number of idle passes that depends on how long the machine
    # took — the same code and the same events counting different
    # instructions, which is not a count a ratchet can compare. It is also the
    # more honest journey: an event published with no window attached is
    # retained for the next one, which is the fan-out property itself.
    status, body = _util.post_json(base + '/register', {
        'token': DASHBOARD_TOKEN,
        'tabId': DASHBOARD_TAB,
        'url': DASHBOARD_URL,
        'title': DASHBOARD_TITLE,
    })
    assert status == 200, (status, body)

    connection, response = _bridge.stream_response(
        base, DASHBOARD_TOKEN, _fanout.DASHBOARD)
    try:
        frame = _read_until(response, 'tab-updated')
    finally:
        response.close()
        connection.close()

    assert frame.get('kind') == 'event', frame
    assert frame.get('tabId') == DASHBOARD_TAB, frame
    return {
        'journey': 'dashboard-fanout',
        'registered': body,
        'event': {'kind': frame.get('kind'), 'type': frame.get('type'),
                  'tabId': frame.get('tabId'), 'url': frame.get('url'),
                  'title': frame.get('title')},
    }


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


def rendering_of(name):
    """Run one journey against a fresh bridge and return its rendering.

    `await_mcp=True` is what makes the count comparable: the bridge's
    `mcp-bootstrap` thread imports the front end beside the journey and
    readiness deliberately does not wait for it, so a round that started
    while that import was mid-flight would carry whichever slice of it
    happened to run alongside. The wait puts the import wholly inside every
    round or wholly outside all of them.
    """
    token, run = JOURNEYS[name]
    with tempfile.TemporaryDirectory(prefix='journey_') as directory:
        with _util.bridge(directory, env=bridge_env(token),
                          await_mcp=True) as fixture:
            base, docroot = fixture
            return run(base, docroot)


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


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journey', required=True,
                        help=f'one of {", ".join(NAMES)}, or '
                             f'{STARTUP_ONLY}')
    parser.add_argument('--root', type=Path, default=_util.ROOT)
    return parser


def main(argv=None):
    """Run one journey in this process and print its record."""
    args = _parser().parse_args(argv)
    if args.journey == STARTUP_ONLY:
        return 0
    if args.journey not in JOURNEYS:
        print(f'no journey named {args.journey!r}', file=sys.stderr)
        return 2
    _print_record(args.journey, rendering_of(args.journey))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
