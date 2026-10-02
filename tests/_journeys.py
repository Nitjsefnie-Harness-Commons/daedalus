"""The user journeys the performance ratchet measures.

Not a suite itself — run_tests.py only loads `test_*.py`. `scripts/ci/
journey_budget.py` drives each one in a fresh child process, so this module
has to be runnable from a `__main__` handed nothing but a journey name and
the repository root.

Each journey drives the REAL bridge through `_util.bridge()`, which spawns
`server.py` and polls `/health`, so what is measured is what a user waits
for rather than how long a test function took. Every input is fixed — token,
tab, command id, code, result payload — because a counter measured over
moving inputs is a counter over the harness.

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
import base64
import hashlib
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bridge  # noqa: E402
import _fanout  # noqa: E402
import _mcp_load  # noqa: E402
import _util  # noqa: E402

# The journey whose own bridge spawn carries this credential. A bridge
# refuses a data root a second process holds, and each journey is measured in
# its own child, so nothing here is shared between two runs.
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

# Screenshot: the typed capture command, the store it names, and the read
# back by the path that result carried.
SHOT_TOKEN = 'journeyshot'
SHOT_TAB = 'journeyshotab'
SHOT_ID = 'journey-shot-1'
SHOT_FILE = 'capture.png'
# A real 1x1 PNG rather than an empty body: the store base64-decodes and
# writes what it is given, and empty bytes would skip the decode the capture
# always pays. Written as a literal so the size is a constant rather than
# whatever a compressor emits for the interpreter running it.
SHOT_PNG_B64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQ'
    'GAhKmMIQAAAABJRU5ErkJggg==')

# CDP: a typed command whose result is a real protocol response rather than
# the two-key body the eval journeys carry.
CDP_TOKEN = 'journeycdp'
CDP_TAB = 'journeycdptab'
CDP_ID = 'journey-cdp-1'
CDP_METHOD = 'Runtime.evaluate'
CDP_PARAMS = {'expression': 'document.querySelectorAll("*").length',
              'returnByValue': True, 'awaitPromise': False}
# How many element snapshots the response carries. A `Runtime.evaluate` over
# a real page answers with one entry per matched node, so the count is what
# makes the body the size a CDP response actually is.
CDP_NODES = 200

# Net capture: the largest result body the bridge handles. Every part of the
# POST /result path scales with it — the depth scan reads the raw bytes, the
# parse builds the tree, and the credential pop and re-serialisation walk it
# again before the slot and the delivery copy are written.
NET_TOKEN = 'journeynet'
NET_TAB = 'journeynettab'
NET_ID = 'journey-net-1'
# A few thousand requests, each carrying headers and a body. `DAEDALUS_MAX_
# UNAUTHENTICATED_BODY` is 64 KiB, so a body this size can only be posted
# with the credential in an Authorization header; the journey does that.
NET_REQUESTS = 2000
NET_BODY_CHARS = 1200

# Segment relay: the page-facing routes, which never touch the command
# queue. A fixed job name is safe because every journey is measured in its
# own process against a fresh temporary data root.
SEG_TOKEN = 'journeyseg'
SEG_JOB = 'journeyseg'
SEG_COUNT = 5
SEG_BODY_CHARS = 800

# The startup-only measurement: the same interpreter and the same imports as
# a journey child, with no bridge and no journey. Every instruction counter
# is reported net of this one, so interpreter startup and import cost are
# not part of what the ratchet compares.
STARTUP_ONLY = 'startup-only'

# How many dashboard frames the fan-out journey will step over looking for
# the register's own event. A sync above seeds the registry and publishes one
# event of its own; anything past that is a shape this journey has never
# seen and is refused rather than skipped past.
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
        # delivery id is excluded with them. What remains is what the bridge
        # stored, which is the shape the round trip produced.
        'result': {key: slot[key] for key in
                   ('id', 'tabId', 'result', 'error', 'world', 'ts')
                   if key in slot},
    }


def screenshot(base, docroot):
    """A capture stored the way the extension stores one, and read back.

    The `filename` form is the one that pins. Left to itself the route
    mints `<ms>_<counter>.<fmt>` from a clock and a per-process counter, so
    the `path` the result would carry — and the read-back keyed on it —
    would be a different string on every run.
    """
    del docroot
    status, raw = _bridge.put_command(base, {
        'token': SHOT_TOKEN,
        'tab': SHOT_TAB,
        'id': SHOT_ID,
        'type': 'screenshot',
        # `tab` is routing and is stripped before the command is published,
        # so the browser's own tab identifier travels under its own name.
        'tabId': SHOT_TAB,
    })
    assert status == 200, (status, raw)
    enqueued = json.loads(raw)

    frame = _bridge.read_stream_data(base, SHOT_TOKEN, SHOT_TAB)
    assert frame.get('type') == 'screenshot', frame
    assert frame.get('id') == SHOT_ID, frame
    assert frame.get('_did') == enqueued.get('did'), (frame, enqueued)

    status, stored = _util.post_json(base + '/upload', {
        'token': SHOT_TOKEN,
        'id': SHOT_ID,
        'filename': SHOT_FILE,
        'data': SHOT_PNG_B64,
    })
    assert status == 200, (status, stored)
    assert stored.get('path') == SHOT_ID + '/' + SHOT_FILE, stored

    status, raw = _util.post_json(base + '/result', {
        'token': SHOT_TOKEN,
        'tabId': SHOT_TAB,
        'id': frame['id'],
        'result': {'path': stored['path'], 'size': stored['size']},
        'error': None,
        'ts': 1,
        '_did': frame['_did'],
    })
    assert status == 200, (status, raw)

    # `delivery=` needs no `tab=`: with none the store searches every target
    # directory the token owns, which is how a caller that never knew the
    # browser's tab id reads one delivery back.
    status, slot = _util.get_json(
        base + '/result?token=' + SHOT_TOKEN
        + '&delivery=' + frame['_did'])
    assert status == 200, (status, slot)
    assert slot.get('id') == SHOT_ID, slot
    assert slot.get('result') == {'path': stored['path'],
                                  'size': stored['size']}, slot

    status, served = _util.get(
        base + '/screenshot?token=' + SHOT_TOKEN + '&path=' + stored['path'])
    assert status == 200, status
    assert served == base64.b64decode(SHOT_PNG_B64), served[:16]

    return {
        'journey': 'screenshot',
        'enqueued': {'ok': enqueued.get('ok'),
                     'target': enqueued.get('target')},
        'frame': {key: frame[key] for key in ('type', 'id', 'tabId')
                  if key in frame},
        'upload': {'ok': stored.get('ok'), 'path': stored.get('path'),
                   'size': stored.get('size')},
        'result': {key: slot[key] for key in
                   ('id', 'tabId', 'result', 'error', 'ts') if key in slot},
        'served': len(served),
    }


def _cdp_response():
    """A `Runtime.evaluate` answer shaped like the one a page produces.

    One entry per matched node, each carrying the handful of fields a
    remote object reports. This is the size difference the journey
    exists for: a CDP result is an order of magnitude past the two-key
    body the eval journeys post, so the parse, the credential pop, the
    re-serialisation and both writes are measured on a body of a
    realistic size rather than on a token.

    Built by a fixed formula over the index — never a clock, a uuid or
    `random` — because a counter measured over moving inputs is a
    counter over the harness.
    """
    nodes = []
    for index in range(CDP_NODES):
        node = f'node-{index:04d}'
        nodes.append({
            'nodeId': index + 1,
            'backendNodeId': 1000 + index,
            'nodeType': 1,
            'nodeName': node,
            'localName': node,
            'nodeValue': '',
            'childNodeCount': index % 4,
            'attributes': [f'class={node}-alpha', f'data-index={index}'],
        })
    return {'result': {
        'type': 'object',
        'subtype': 'array',
        'className': 'Array',
        'description': f'NodeList({CDP_NODES})',
        'objectId': 'injected-1',
        'preview': {'type': 'object', 'properties': nodes[:8],
                    'overflow': True},
    }, 'deep': {'nodes': nodes}}


def cdp_result(base, docroot):
    """A typed CDP command answered with a real protocol response.

    The bridge dispatches nothing itself — `tab` only chooses the queue —
    so `method` and `params` travel in the command body and the field
    names matter to the rendering rather than to the route.
    """
    del docroot
    status, raw = _bridge.put_command(base, {
        'token': CDP_TOKEN,
        'tab': CDP_TAB,
        'id': CDP_ID,
        'type': 'cdp',
        'method': CDP_METHOD,
        'params': CDP_PARAMS,
    })
    assert status == 200, (status, raw)
    enqueued = json.loads(raw)

    frame = _bridge.read_stream_data(base, CDP_TOKEN, CDP_TAB)
    assert frame.get('type') == 'cdp', frame
    assert frame.get('id') == CDP_ID, frame
    assert frame.get('method') == CDP_METHOD, frame
    assert frame.get('params') == CDP_PARAMS, frame

    payload = _cdp_response()
    status, raw = _util.post_json(base + '/result', {
        'token': CDP_TOKEN,
        'tabId': CDP_TAB,
        'id': frame['id'],
        'result': payload,
        'error': None,
        'ts': 1,
        '_did': frame['_did'],
    })
    assert status == 200, (status, raw)

    status, slot = _util.get_json(
        base + '/result?token=' + CDP_TOKEN + '&delivery=' + frame['_did'])
    assert status == 200, (status, slot)
    assert slot.get('id') == CDP_ID, slot
    assert slot.get('result') == payload, slot

    # The rendering records the SHAPE of the body that came back, and its
    # two ends rather than all two hundred nodes: enough that a per-entry
    # change moves the number, without a record nobody can read a diff of.
    stored = slot.get('result', {})
    remote = stored.get('result', {})
    nodes = stored.get('deep', {}).get('nodes', [])
    return {
        'journey': 'cdp-result',
        'enqueued': {'ok': enqueued.get('ok'),
                     'target': enqueued.get('target')},
        'frame': {key: frame[key] for key in ('type', 'id', 'method', 'params')
                  if key in frame},
        'result': {
            'id': slot.get('id'),
            'tabId': slot.get('tabId'),
            'error': slot.get('error'),
            'type': remote.get('type'),
            'className': remote.get('className'),
            'description': remote.get('description'),
            'nodes': len(nodes),
            'first': nodes[0] if nodes else None,
            'last': nodes[-1] if nodes else None,
        },
    }


def _net_capture_buffer():
    """The capture buffer the service worker hands back, headers and bodies.

    This is the largest result body the bridge handles, and it is what
    the journey is FOR: the depth scan reads the raw bytes, the parse
    builds the tree, the credential pop and the re-serialisation walk it
    again, and the slot and the delivery copy are both written from it. A
    per-byte regression that a typical-size result hides shows here.

    Two thousand requests at NET_BODY_CHARS apiece is a few megabytes:
    enough to be the large case, small enough that the job's ceiling
    still has room for the other journeys.

    Every entry is built by a fixed formula over the index — never a
    clock, a uuid or `random` — because a counter measured over moving
    inputs is a counter over the harness.
    """
    entries = []
    for index in range(NET_REQUESTS):
        seed = f'request-{index:06d}-body-'
        body = seed * (NET_BODY_CHARS // len(seed) + 1)
        entries.append({
            'requestId': f'journey-{index:06d}',
            'frameId': f'F{index:06d}',
            'loaderId': f'L{index % 8:06d}',
            'url': (f'https://net.journey.example.com/assets/'
                    f'{index % 250:03d}/chunk-{index:06d}.js'),
            'method': 'GET',
            'status': 200,
            'type': 'Script',
            'mimeType': 'application/javascript',
            'initiator': 'parser',
            'headers': {
                'content-type': 'application/javascript; charset=utf-8',
                'content-length': str(NET_BODY_CHARS),
                'cache-control': 'no-cache',
                'server': 'journey-edge',
                'x-request-id': f'req-{index:06d}',
            },
            'body': body[:NET_BODY_CHARS],
        })
    return entries


def net_capture(base, docroot):
    """A capture buffer answered at the size the service worker holds it.

    The credential travels in an `Authorization` header, and that is not
    a style choice: a body-carried token is only accepted for a body
    within `DAEDALUS_MAX_UNAUTHENTICATED_BODY` (64 KiB by default), and
    anything larger with no header is answered 401 without being read.
    The same trap holds for any real PNG, which is why the upload above
    would need it too.
    """
    del docroot
    status, raw = _bridge.put_command(base, {
        'token': NET_TOKEN,
        'tab': NET_TAB,
        'id': NET_ID,
        'type': 'net-capture-get',
        'maxRequests': NET_REQUESTS,
    })
    assert status == 200, (status, raw)
    enqueued = json.loads(raw)

    frame = _bridge.read_stream_data(base, NET_TOKEN, NET_TAB)
    assert frame.get('type') == 'net-capture-get', frame
    assert frame.get('id') == NET_ID, frame

    entries = _net_capture_buffer()
    auth = {'Authorization': 'Bearer ' + NET_TOKEN}
    status, raw = _util.post_json(base + '/result', {
        'token': NET_TOKEN,
        'tabId': NET_TAB,
        'id': frame['id'],
        'result': {'requests': entries},
        'error': None,
        'ts': 1,
        '_did': frame['_did'],
    }, headers=auth, timeout=120)
    assert status == 200, (status, raw)

    status, slot = _util.get_json(
        base + '/result?token=' + NET_TOKEN + '&delivery=' + frame['_did'],
        timeout=120)
    assert status == 200, (status, slot)
    assert slot.get('id') == NET_ID, slot
    stored = slot.get('result', {}).get('requests')
    assert stored == entries, 'the capture that came back is not the one sent'

    # The digest is taken over what the bridge STORED, not over what was
    # posted, so a byte lost on the way through moves the number.
    return {
        'journey': 'net-capture',
        'enqueued': {'ok': enqueued.get('ok'),
                     'target': enqueued.get('target')},
        'frame': {key: frame[key]
                  for key in ('type', 'id', 'maxRequests') if key in frame},
        'capture': {
            'requests': len(stored),
            'body_chars': sum(len(entry['body']) for entry in stored),
            'sha256': hashlib.sha256(canonical(stored)).hexdigest(),
        },
    }


def _load_front_end(base):
    """The MCP front end, loaded on a thread of its own and waited for.

    The bridge excludes its copy of the front end by WAITING for it — the
    `mcp-bootstrap` thread finishes before the journey's first request — and
    the client's copy is excluded the other way round, by having already
    paid it. Neither excludes the CALL: the `exec` round trip below is the
    work, and it runs on the journey's main thread, which counts whatever
    it costs.

    The load has to be off the main thread for it to be excludable at all.
    A main thread is read as the main thread whatever its size, so an import
    on one is the journey's own work by every rule the profiler's thread
    bands apply. Loading it here and waiting is the whole of the asymmetry,
    and it is harness-side: the module the tool call reaches is the same one
    either way.
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

    The round trip runs on the journey's MAIN thread, and that placement
    is part of what this journey measures, not an accident.
    `journey_threads` reads `thread == 1` FIRST and whatever its total, so
    the round trip is counted wherever the journey puts it; a non-main
    slot is instead read by its total against the `front-end-import` band,
    and this journey's own work measured 1,311,350,558 — inside that band
    — so a worker carried roughly three hundred million instructions of it
    straight out of the count. The import above is the one thing here that
    must NOT be counted, and a main thread is read as counted whatever its
    size, which is why `_load_front_end` keeps it on a worker of its own.
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
    the type rather than for the first frame is what makes this journey a
    journey rather than a read of the drain's ordering.
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
    # ordering is the determinism, not a convenience. The bridge's stream
    # loop delivers whatever is queued on its first pass and otherwise idles
    # on a wall-clock tick, so a stream held open across a client round-trip
    # performs a number of idle passes that depends on how long the machine
    # took — the same code and the same events counting different
    # instructions, which is not a count a ratchet can compare. Publishing
    # first means the drain has something on its first pass and the loop
    # never idles. It is also the more honest journey: an event published
    # with no window attached is retained for the next one, which is the
    # fan-out property itself rather than a race against a live reader.
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


def _segment_payload(index):
    """One HLS segment's bytes, as the page would hand them over."""
    seed = f'journey-segment-{index}-'
    return (seed * (SEG_BODY_CHARS // len(seed) + 1))[:SEG_BODY_CHARS].encode()


def segment_relay(base, docroot):
    """A capability minted, five segments stored under it, and the status.

    These are the page-facing routes, so nothing here goes through the
    command queue: the page never holds the bridge token, and the minted
    capability is what authorizes a write. The `sig` is
    `secrets.token_urlsafe(32)` and is not deterministic, so it is
    excluded from the rendering and only `ok` is recorded.
    """
    del docroot
    status, minted = _util.post_json(base + '/segment-job', {
        'token': SEG_TOKEN, 'job': SEG_JOB,
    })
    assert status == 200, (status, minted)
    assert minted.get('ok') is True, minted
    sig = minted.get('sig')
    assert isinstance(sig, str) and sig, minted

    # The header form, not `&sig=`: every in-repo client sends the
    # capability that way, and a query string is the one place a proxy
    # access log would carry it. The body is the raw segment, so the
    # content type is set here rather than defaulted to JSON.
    auth = {'X-Daedalus-Segment-Sig': sig}
    written = []
    for index in range(SEG_COUNT):
        payload = _segment_payload(index)
        status, body = _util.post_json(
            f'{base}/segment?job={SEG_JOB}&seg={index}&total={SEG_COUNT}',
            payload, headers={**auth, 'Content-Type':
                              'application/octet-stream'})
        written.append({'seg': index, 'status': status, 'ok': body.get('ok'),
                        'bytes': len(payload)})
        assert status == 200, (status, body)

    status, seen = _util.get_json(
        f'{base}/segment-status?job={SEG_JOB}', headers=auth)
    assert status == 200, (status, seen)
    assert seen.get('done') == list(range(SEG_COUNT)), seen

    return {
        'journey': 'segment-relay',
        'minted': {'ok': minted.get('ok')},
        'segments': written,
        'status': {'done': seen.get('done'), 'count': seen.get('count')},
    }


JOURNEYS = {
    'command-round-trip': (COMMAND_TOKEN, command_round_trip),
    'mcp-exec': (_mcp_load.TOK, mcp_exec),
    'dashboard-fanout': (DASHBOARD_TOKEN, dashboard_fanout),
    'screenshot': (SHOT_TOKEN, screenshot),
    'cdp-result': (CDP_TOKEN, cdp_result),
    'net-capture': (NET_TOKEN, net_capture),
    'segment-relay': (SEG_TOKEN, segment_relay),
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
