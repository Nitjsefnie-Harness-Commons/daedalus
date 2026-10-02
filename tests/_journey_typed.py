"""The typed-command and page-facing journeys the ratchet measures.

Not a suite itself — run_tests.py only loads `test_*.py`, and
`tests/_journeys.py` is what runs a journey. This module is the other
half of that registry: the journeys that are NOT the eval round trip, so
that `_journeys.py` holds the registry, the three original journeys and
the CLI rather than growing past the ceiling `tests/test_file_sizes.py`
holds every other module to.

Each journey drives the REAL bridge through `_util.bridge()`, plays the
extension itself — there is no browser and no Chrome in the counted
process — and returns a plain JSON-able dict. `sha256_of` over that dict
is the shape contract, so every input here is fixed: token, tab, command
id, code, result payload. A counter measured over moving inputs is a
counter over the harness.

The journeys run on the journey's MAIN thread, because `rendering_of`
calls them there and `role_of` reads thread 1 as `MAIN` at any size. A
worker is reserved for setup that must be excluded, and nothing in this
module uses one.
"""
import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bridge  # noqa: E402
import _util  # noqa: E402

# Screenshot: the typed capture command, the store it names, and the read
# back by the path that result carried.
SHOT_TOKEN = 'journeyshot'
SHOT_TAB = 'journeyshotab'
# Chrome's own tab id, which is NOT the routing tab. The bridge strips
# `tab` before publishing, so a sender that put its browser tab there
# would arrive with nothing; keeping the two distinct is what makes the
# rendering say which field actually travelled. It is a STRING because
# `path_safety.unsafe_component` refuses a non-string, and POST /result
# checks `tabId` with it — a bare int is answered 400.
SHOT_CHROME_TAB = '1458'
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
# Two thousand requests, each carrying headers and a body: a 3.3 MB
# envelope. The unauthenticated-body ceiling is 64 KiB, so a body this
# size can only be posted with the credential in an Authorization header,
# and the journey does that.
NET_REQUESTS = 2000
NET_BODY_CHARS = 1200

# Segment relay: the page-facing routes, which never touch the command
# queue. A fixed job name is safe because every journey is measured in
# its own process against a fresh temporary data root.
SEG_TOKEN = 'journeyseg'
SEG_JOB = 'journeyseg'
SEG_COUNT = 5
SEG_BODY_CHARS = 800


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
        'tabId': SHOT_CHROME_TAB,
    })
    assert status == 200, (status, raw)
    enqueued = json.loads(raw)

    frame = _bridge.read_stream_data(base, SHOT_TOKEN, SHOT_TAB)
    assert frame.get('type') == 'screenshot', frame
    assert frame.get('id') == SHOT_ID, frame
    assert frame.get('tabId') == SHOT_CHROME_TAB, frame
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
        'tabId': SHOT_CHROME_TAB,
        'id': frame['id'],
        'result': {'path': stored['path'], 'size': stored['size']},
        'error': None,
        'ts': 1,
        '_did': frame['_did'],
    })
    assert status == 200, (status, raw)

    # `delivery=` needs no `tab=`: with none the store searches every
    # `deliveries/<token>_*` directory the token owns, which is the only
    # way to read this back — the result was filed under a tab id the
    # caller knows as a number, not as a name.
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

    ITS RECORDED BUDGET IS NEAR A BILLION INSTRUCTIONS, and that is not
    a defect to go looking for. This journey's request thread runs to
    roughly 47 million, which is at or above `SERVE_FROM` (10,000,000) —
    the band `role_of` reads as uvicorn's serve loop. So `uvicorn-serve`
    cannot be excluded here without excluding the very work the journey
    exists to measure, and the bridge's constant serve loop is counted
    alongside it. The front end's import IS excluded, which is what a
    reader comparing this journey with `net-capture` will find the
    asymmetry in: the two journeys swapped which constant they keep. Same
    rule either way — a journey's exclusion list may never cover work the
    journey itself caused — and the same deal of counting a constant
    rather than dropping the band (issue 1461, bridge side).
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

    # The header is not decoration at this size either. A body-carried
    # token is only accepted below `DAEDALUS_MAX_UNAUTHENTICATED_BODY`
    # (64 KiB), and this body is 42 KB today with 35% of the ceiling to
    # spare — a handful more nodes and the same code starts answering 401
    # unread, which would fail the journey rather than measure the bridge.
    payload = _cdp_response()
    status, raw = _util.post_json(base + '/result', {
        'token': CDP_TOKEN,
        'tabId': CDP_TAB,
        'id': frame['id'],
        'result': payload,
        'error': None,
        'ts': 1,
        '_did': frame['_did'],
    }, headers={'Authorization': 'Bearer ' + CDP_TOKEN})
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

    Two thousand requests at NET_BODY_CHARS apiece is a 3.3 MB envelope:
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


def _capture_digest(entries):
    """A digest of the buffer that came back, over a fixed spelling.

    Deliberately NOT `_journeys.canonical`, for two reasons. The bytes the
    bridge SERVES are the wrong source: they carry `resultGeneration` and
    `roundtrip_ms`, which are per-run by design, so digesting them makes
    the rendering different on every run — the one thing a rendering may
    not be. And re-serialising to reach a canonical form would mean
    keeping a second copy of that spelling in step with the first.

    Spelling each entry out field by field has neither problem. Every
    field present is covered, so a new one in the buffer moves the digest
    without this being edited, and the spelling is two separators and a
    `sorted`.
    """
    rows = []
    for entry in entries:
        flat = {key: value for key, value in entry.items()
                if key != 'headers'}
        rows.append('\t'.join(
            [f'{key}={flat[key]}' for key in sorted(flat)]
            + [f'headers={sorted(entry["headers"].items())}']))
    return hashlib.sha256('\n'.join(rows).encode('utf-8')).hexdigest()


def net_capture(base, docroot):
    """A capture buffer answered at the size the service worker holds it.

    The credential travels in an `Authorization` header, and that is not
    a style choice: a body-carried token is only accepted for a body
    within `DAEDALUS_MAX_UNAUTHENTICATED_BODY` (64 KiB by default), and
    anything larger with no header is answered 401 without being read.
    The same trap holds for any real PNG, which is why the upload above
    would need it too.

    ITS RECORDED BUDGET IS DOMINATED BY A CONSTANT, and a reader who sees
    a multi-billion figure for one journey should know that before
    looking for a bug. The request thread this journey puts to work runs
    to billions of instructions, which is at or above `IMPORT_FROM`
    (1,000,000,000) — the band `role_of` reads as the MCP front end's
    import. So `front-end-import` cannot be excluded here without
    excluding the very work the journey exists to measure, and the
    bridge's own one-off bootstrap import counts alongside it. That is
    the deal this journey makes deliberately (issue 1461, bridge side):
    the constant is counted so the per-byte work is too. A per-byte
    regression that pushed the request further up would still move the
    total, because a larger number in the same band is still counted —
    the band only decides inclusion here, never exclusion.
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

    # The buffer is read back through `delivery=` and compared whole, so a
    # byte the bridge lost or altered on the way through fails the journey
    # rather than being absorbed into the count.
    status, slot = _util.get_json(
        base + '/result?token=' + NET_TOKEN + '&delivery=' + frame['_did'],
        timeout=120)
    assert status == 200, (status, slot)
    assert slot.get('id') == NET_ID, slot
    stored = slot.get('result', {}).get('requests')
    assert stored == entries, 'the capture that came back is not the one sent'

    return {
        'journey': 'net-capture',
        'enqueued': {'ok': enqueued.get('ok'),
                     'target': enqueued.get('target')},
        'frame': {key: frame[key]
                  for key in ('type', 'id', 'maxRequests') if key in frame},
        'capture': {
            'requests': len(stored),
            'body_chars': sum(len(entry['body']) for entry in stored),
            'sha256': _capture_digest(stored),
        },
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
        # The status first: `post_json` parses the body, and a refusal
        # that is not JSON raises out of the helper before the pairing
        # that would have named it ever gets built.
        assert status == 200, (status, body)
        written.append({'seg': index, 'status': status, 'ok': body.get('ok'),
                        'bytes': len(payload)})

    # Read the stored bytes back off the data root rather than leaving
    # `bytes` as what was posted. A write that stored the wrong number of
    # bytes renders identically otherwise, and the size is the one number
    # here a truncation or a doubling would move.
    for row in written:
        stored_path = (Path(docroot) / 'segments' / SEG_JOB
                       / f'{row["seg"]:06d}.ts')
        assert stored_path.is_file(), stored_path
        stored = stored_path.read_bytes()
        assert stored == _segment_payload(row['seg']), row['seg']
        assert len(stored) == row['bytes'], (row['seg'], len(stored))

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
