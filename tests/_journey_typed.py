"""The typed-command and page-facing journeys the ratchet measures.

Not a suite itself — run_tests.py only loads `test_*.py`, and
`tests/_journeys.py` is what runs a journey. This module is the other half
of that registry.

Each journey drives the REAL bridge through `_util.bridge()`, plays the
extension itself — there is no browser and no Chrome in the counted
process — and returns a plain JSON-able dict. `sha256_of` over that dict is
the shape contract, so every input here is fixed: token, tab, command id,
code, result payload. A counter measured over moving inputs is a counter
over the harness.

The journeys run on the journey's MAIN thread, because `rendering_of` calls
them there and `role_of` reads thread 1 as `MAIN` at any size. A worker is
reserved for setup that must be excluded, and nothing in this module uses
one.
"""
import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bridge  # noqa: E402
import _util  # noqa: E402

SHOT_TOKEN = 'journeyshot'
SHOT_TAB = 'journeyshotab'


def _high_entropy(size, seed):
    """`size` deterministic bytes that look like compressed media.

    A PNG's IDAT and an HLS segment's transport stream are both
    high-entropy, so a stand-in for either has to be: a body of one
    repeated block would compress against any future transport and would
    read as padding where the real thing is not. A linear congruential
    generator over fixed moduli gives a body no two runs can differ on and
    no clock, uuid or `random` can put in — the same requirement every
    other fixed input in this module is under.
    """
    words = []
    state = seed & 0xFFFFFFFF
    for _ in range(size // 4):
        state = (1103515245 * state + 12345) & 0xFFFFFFFF
        words.append(state.to_bytes(4, 'big'))
    return b''.join(words)


# Chrome's own tab id, which is NOT the routing tab. The bridge strips `tab`
# before publishing, so a sender that put its browser tab there would arrive
# with nothing; keeping the two distinct is what makes the rendering say
# which field actually travelled. It is a STRING because
# `path_safety.unsafe_component` refuses a non-string, and POST /result
# checks `tabId` with it — a bare int is answered 400.
SHOT_CHROME_TAB = '1458'
SHOT_ID = 'journey-shot-1'
SHOT_FILE = 'capture.png'
# The size of the capture this journey hands the bridge, in bytes of image.
# The basis is `captureVisibleTab` — extension/worker/capture.js photographs
# the ACTIVE tab of a window with `format: 'png'` and posts the base64 of the
# data URL with the prefix stripped, and a 1280x720 viewport of ordinary web
# content is a PNG in the low hundreds of kilobytes. 192 KiB sits inside that
# band rather than at its edge, and it is the number the tolerance and the
# recorded count are measured against: a payload past the size the product
# produces is a number nobody measured, and a payload under it is the
# defect this journey was resized to remove.
SHOT_CAPTURE_BYTES = 196608
# A capture's body is base64, so the JSON envelope is 4/3 of this: 256 KiB,
# which is four times `DAEDALUS_MAX_UNAUTHENTICATED_BODY`. That is why the
# upload below carries its credential in a header — at a 1x1 PNG the body
# form was accepted, and at a real capture's size the same call is answered
# 401 without the body ever being read.
SHOT_CAPTURE_B64 = base64.b64encode(
    _high_entropy(SHOT_CAPTURE_BYTES, 0x5EEDC0DE)).decode('ascii')
# A body this size takes longer to hand over than `request`'s 10 s default
# allows on a loaded runner, and a timeout here would read as a journey that
# failed rather than as one that waited.
SHOT_TIMEOUT = 120

# CDP: a typed command whose result is a real protocol response rather than
# the two-key body the eval journeys carry.
CDP_TOKEN = 'journeycdp'
CDP_TAB = 'journeycdptab'
CDP_ID = 'journey-cdp-1'
CDP_METHOD = 'Runtime.evaluate'
CDP_PARAMS = {'expression': 'document.querySelectorAll("*").length',
              'returnByValue': True, 'awaitPromise': False}
# How many element snapshots the response carries. A `Runtime.evaluate` over a
# real page answers with one entry per matched node, so the count is what
# makes the body the size a CDP response actually is.
CDP_NODES = 200

# Net capture: the largest result BODY any of these journeys posts, and every
# part of the POST /result path scales with it — the depth scan reads the raw
# bytes, the parse builds the tree, and the credential pop and
# re-serialisation walk it again before the slot and the delivery copy are
# written. It is the body this journey measures, not the whole of its
# recorded budget; `net_capture`'s docstring says what rides along with it.
NET_TOKEN = 'journeynet'
NET_TAB = 'journeynettab'
NET_ID = 'journey-net-1'
# Two thousand requests, each carrying headers and a body: a 3.3 MB envelope,
# which only an Authorization header can carry — see `net_capture`.
NET_REQUESTS = 2000
NET_BODY_CHARS = 1200

# Segment relay: the page-facing routes, which never touch the command
# queue. A fixed job name is safe because every journey is measured in
# its own process against a fresh temporary data root.
SEG_TOKEN = 'journeyseg'
SEG_JOB = 'journeyseg'
SEG_COUNT = 5
# The size of one HLS segment, in bytes. An HLS segment is a few seconds of
# one encode, so its length is set by the stream a page happens to be
# watching rather than by anything in this tree — a typical encode lands
# within an order of magnitude of a megabyte per segment, and that is the
# band the size below is taken from.
#
# The caps it has to sit under, all of which it does by three orders of
# magnitude: `DAEDALUS_MAX_BODY_SIZE` (64 MiB) bounds the request, so a
# segment over it is a refusal and a journey that asserted one would be
# measuring the refusal rather than the relay; `DAEDALUS_MAX_SEGMENTS_PER_JOB`
# (10000) bounds the count; `DAEDALUS_MAX_SEGMENT_JOB_SIZE` (4 GiB) bounds
# the job, and this journey's whole job is five segments.
SEG_BODY_BYTES = 1048576
# ONE body, posted once per segment. The relay path is measured per request,
# and a body regenerated per index would put megabytes of harness work into
# a count the bridge is not responsible for — work no page does either, since
# a page relays bytes it was handed. Nothing downstream of `POST /segment`
# reads a segment's content, so what the count is sensitive to is its length.
SEG_BODY = _high_entropy(SEG_BODY_BYTES, 0x5EC7A1)
# Five megabytes of POST is past `request`'s 10 s default on a loaded runner,
# and a timeout there would read as a failed journey rather than a slow one.
SEG_TIMEOUT = 300


def screenshot(base, docroot):
    """A capture stored the way the extension stores one, and read back.

    The `filename` form is the one that pins. Left to itself the route
    mints `<ms>_<counter>.<fmt>` from a clock and a per-process counter, so
    the `path` the result would carry — and the read-back keyed on it —
    would be a different string on every run.

    The capture is a real capture's SIZE, sized from `SHOT_CAPTURE_BYTES`,
    and the credential travels in an `Authorization` header because of it:
    the base64 of that capture is four times
    `DAEDALUS_MAX_UNAUTHENTICATED_BODY`, so the same call made in the body
    form this journey used at a 1x1 PNG is now answered 401 without the
    body being read. A journey that asserted that would be measuring the
    refusal rather than the store.

    ITS RECORDED BUDGET IS AN ORDER OF MAGNITUDE ABOVE `command-round-trip`'s,
    and that is a constant rather than a defect. This journey excludes the
    front end's import and nothing else, so the bridge's uvicorn serve loop
    is counted alongside its own work: measured here at 87 million
    instructions against 6.6 million on the request thread doing the
    capture, the loop is the larger share of the total. Both figures are a
    developer box's, about 12.7% hot against the runner that records the
    budgets and on a different toolchain, so they are not the runner's.

    The exclusion list is the import alone. Under size bands this was the
    only robust choice: its own request thread measured 6,603,084, only
    1.51x below the serve floor, so growth would have reclassified that
    thread as the serve loop sitting beside the bridge's constant one, and
    `classify` refuses two threads of an excluded role rather than dropping
    one (issue 1461, bridge side). Roles now come from what a thread
    executed, so that growth can no longer happen: this journey's own work
    is a `request` thread at any size and no journey excludes `request`
    (issue 1466). The list is unchanged because it is the one the recorded
    count was measured under, not because the band argument still holds.
    """
    del docroot
    status, raw = _bridge.put_command(base, {
        'token': SHOT_TOKEN,
        'tab': SHOT_TAB,
        'id': SHOT_ID,
        'type': 'screenshot',
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
        'id': SHOT_ID,
        'filename': SHOT_FILE,
        'data': SHOT_CAPTURE_B64,
    }, headers={'Authorization': 'Bearer ' + SHOT_TOKEN}, timeout=SHOT_TIMEOUT)
    assert status == 200, (status, stored)
    assert stored.get('path') == SHOT_ID + '/' + SHOT_FILE, stored
    assert stored.get('size') == SHOT_CAPTURE_BYTES, stored

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
    # `deliveries/<token>_*` directory the token owns, which is the only way
    # to read this back — the result was filed under a tab id the caller
    # knows as a number, not as a name.
    status, slot = _util.get_json(
        base + '/result?token=' + SHOT_TOKEN
        + '&delivery=' + frame['_did'])
    assert status == 200, (status, slot)
    assert slot.get('id') == SHOT_ID, slot
    assert slot.get('result') == {'path': stored['path'],
                                  'size': stored['size']}, slot

    status, served = _util.get(
        base + '/screenshot?token=' + SHOT_TOKEN + '&path=' + stored['path'],
        timeout=SHOT_TIMEOUT)
    assert status == 200, status
    assert served == base64.b64decode(SHOT_CAPTURE_B64), served[:16]

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

    One entry per matched node, each carrying the handful of fields a remote
    object reports. This is the size difference the journey exists for: a CDP
    result is an order of magnitude past the two-key body the eval journeys
    post, so the parse, the credential pop, the re-serialisation and both
    writes are measured on a body of a realistic size rather than on a token.

    Every entry is a fixed formula over the index — never a clock, a uuid or
    `random`.
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

    The bridge dispatches nothing itself — `tab` only chooses the queue — so
    `method` and `params` travel in the command body and the field names
    matter to the rendering rather than to the route.

    ITS RECORDED BUDGET IS NEAR A BILLION INSTRUCTIONS, and that is not a
    defect to go looking for. The bridge's constant serve loop is counted
    alongside this journey's own work, and the front end's import IS
    excluded, which is what a reader comparing this journey with
    `net-capture` will find the asymmetry in: the two journeys swapped which
    constant they keep. Same rule either way — a journey's exclusion list may
    never cover work the journey itself caused — and the same deal of
    counting a constant rather than dropping it. Under size bands this
    journey's request thread was itself barred from the serve role by
    sitting at 47 million, above the floor that role started at; a role now
    comes from what the thread executed, so that bar is gone (issue 1466)
    and the list is the one the recorded count was measured under.
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

    This is the body the journey is FOR: the depth scan reads the raw bytes,
    the parse builds the tree, the credential pop and the re-serialisation
    walk it again, and the slot and the delivery copy are both written from
    it, so a per-byte regression a typical-size result hides shows here.

    Two thousand requests at NET_BODY_CHARS apiece is a 3.3 MB envelope —
    enough to be the large case. Every entry is a fixed formula over the
    index, never a clock, a uuid or `random`.
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
    `roundtrip_ms`, which are per-run by design, so digesting them makes the
    rendering different on every run — the one thing a rendering may not be.
    And re-serialising to reach a canonical form would mean keeping a second
    copy of that spelling in step with the first.

    Spelling each entry out field by field has neither problem: every field
    present is covered, so a new one in the buffer moves the digest without
    this being edited.
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

    The credential travels in an `Authorization` header, and that is not a
    style choice: a body-carried token is only accepted for a body within
    `DAEDALUS_MAX_UNAUTHENTICATED_BODY` (64 KiB by default), and anything
    larger with no header is answered 401 without being read. A real capture
    is megabytes, so the same trap holds for any real PNG.
    ITS RECORDED BUDGET IS DOMINATED BY A CONSTANT, so a reader who sees a
    multi-billion figure for one journey is not looking at an error. The
    request thread this journey puts to work runs to billions of
    instructions, which under size bands landed in the import band — so the
    front end's import could not be excluded here without excluding the very
    work the journey exists to measure, and the bridge's own one-off
    bootstrap import counts alongside it (issue 1461, bridge side). That
    coupling is gone: the request thread is a `request` thread whatever it
    costs, and `front-end-import` is a role no importing request thread
    can claim: its signature is the init symbol of the compiled core that
    `mcp==2.2.0` pulls and the bridge's own request path never loads, so
    the two can no longer be the same thread (issue 1466).
    The list is left as it was because it is the one the recorded count
    was measured under, and the constant is still counted so the per-byte
    work is counted beside it.
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

    # Read back through `delivery=` and compared whole, so a byte the bridge
    # lost or altered on the way through fails the journey rather than being
    # absorbed into the count.
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


def segment_payload(index):
    """One HLS segment's bytes, as the page would hand them over.

    The same body at every index, for the reason `SEG_BODY` records.
    """
    del index
    return SEG_BODY


def segment_relay(base, docroot):
    """A capability minted, five segments stored under it, and the status.

    These are the page-facing routes, so nothing here goes through the command
    queue: the page never holds the bridge token, and the minted capability
    is what authorizes a write. The `sig` is `secrets.token_urlsafe(32)` and
    is not deterministic, so it is excluded from the rendering and only `ok`
    is recorded.

    Each segment is a real segment's SIZE, sized from `SEG_BODY_BYTES` and
    cited there. At the 800 characters this journey used to post, no thread
    of this journey rose above the background every child shares, so its own
    work was not separable from it at all and the count was a measurement of
    the harness's plumbing rather than of the relay.

    ITS RECORDED BUDGET IS AN ORDER OF MAGNITUDE ABOVE `command-round-trip`'s,
    and for the same reason `screenshot`'s is: only the front end's import
    is excluded, so the bridge's uvicorn serve loop is counted as the
    constant it is. The alternative — excluding the serve role, as
    `command-round-trip` does — used to put this journey's own work, on any
    growth that lifted its request thread past the serve floor, into a role
    the bridge's constant serve loop already occupied, and `classify`
    refuses two threads of an excluded role rather than dropping one: the
    count comes back unavailable and the gate exits 1 (issue 1461, bridge
    side). A role comes from what the thread executed now, so no growth can
    put this journey's own work in one, and it counts whatever it costs
    (issue 1466). The import alone stays the robust list.
    """
    status, minted = _util.post_json(base + '/segment-job', {
        'token': SEG_TOKEN, 'job': SEG_JOB,
    })
    assert status == 200, (status, minted)
    assert minted.get('ok') is True, minted
    sig = minted.get('sig')
    assert isinstance(sig, str) and sig, minted

    # The header form, not `&sig=`: every in-repo client sends the capability
    # that way, and a query string is the one place a proxy access log would
    # carry it. The body is the raw segment, so the content type is set here
    # rather than defaulted to JSON.
    auth = {'X-Daedalus-Segment-Sig': sig}
    written = []
    for index in range(SEG_COUNT):
        payload = segment_payload(index)
        status, body = _util.post_json(
            f'{base}/segment?job={SEG_JOB}&seg={index}&total={SEG_COUNT}',
            payload, headers={**auth, 'Content-Type':
                              'application/octet-stream',
                              }, timeout=SEG_TIMEOUT)
        # The status first: `post_json` parses the body, and a refusal that is
        # not JSON raises out of the helper before the pairing that would
        # have named it ever gets built.
        assert status == 200, (status, body)
        written.append({'seg': index, 'status': status, 'ok': body.get('ok'),
                        'bytes': len(payload)})

    # Read the stored bytes back off the data root rather than leaving `bytes`
    # as what was posted: a write that stored the wrong number of bytes
    # renders identically otherwise.
    for row in written:
        stored_path = (Path(docroot) / 'segments' / SEG_JOB
                       / f'{row["seg"]:06d}.ts')
        assert stored_path.is_file(), stored_path
        stored = stored_path.read_bytes()
        assert stored == segment_payload(row['seg']), row['seg']
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
