"""Commands that move bytes: screenshots, uploads and segment jobs."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import SEGMENT_SIG_HEADER
from .output import MARK
from .transport import (URL, _http_error_detail, _query_path, api,
                        api_delete, api_raw, token, wait_for_result)


def do_segment_job(args):
    # Minting is idempotent for the owning token, so this both creates a job
    # and re-fetches its capability. The printed sig substitutes for __SIG__
    # in examples/hls-segment-relay.js; it authorizes segment writes to this
    # one job only, which is why the relay script never needs the bridge token.
    res = api('POST', '/segment-job', {'token': token(), 'job': args.job})
    print(res['sig'])


def do_segment_status(args):
    # /segment-status takes the job-scoped capability, not the bridge token.
    # Look the capability up rather than POSTing for it: POST /segment-job
    # MINTS a job that does not exist yet, so asking the status of a mistyped
    # name used to create it and then report zero segments as though the name
    # had been right.
    req = urllib.request.Request(
        _query_path(f'{URL}/segment-job', {'job': args.job}), method='GET',
        headers={'Authorization': f'Bearer {token()}'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            sig = json.loads(r.read())['sig']
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(f'segment-status: no job named "{args.job}"')
        if e.code == 409:
            sys.exit(f'segment-status: job "{args.job}" is owned by a different token')
        sys.exit(f'HTTP {e.code}: {_http_error_detail(e)}')
    except urllib.error.URLError as e:
        sys.exit(f'Connection failed: {e.reason}')
    res = api('GET', _query_path('/segment-status', {'job': args.job}),
              headers={SEGMENT_SIG_HEADER: sig})
    count = res.get('count', 0)
    done = res.get('done', [])
    print(f'Job: {args.job}  Segments: {count}')
    if done:
        full = set(range(min(done), max(done) + 1))
        gaps = sorted(full - set(done))
        if gaps:
            print(f'Gaps ({len(gaps)}): {gaps[:30]}{"..." if len(gaps) > 30 else ""}')
        else:
            print(f'Complete: {min(done)}–{max(done)}')


def do_screenshot(args):
    """Send screenshot command to extension, wait for upload, optionally save to local file."""
    cmd = {'token': token(), 'id': args.id or '_ss', 'code': '', 'tab': 'extension'}
    cmd_payload = {'id': cmd['id'], 'type': 'screenshot'}
    if args.format:
        cmd_payload['format'] = args.format
    if args.quality:
        cmd_payload['quality'] = args.quality
    if args.chrome_tab:
        cmd_payload['tabId'] = int(args.chrome_tab)

    resp = api('PUT', '/command', {**cmd_payload, 'token': token(), 'tab': 'extension'})
    print(f'{MARK["out"]} screenshot {MARK["out"]} {resp.get("target", "?")}')

    timeout = args.timeout or 15
    res = wait_for_result(
        cmd['id'], 'extension', resp.get('did'), timeout)
    if res is None:
        sys.exit(f'Timeout ({timeout}s)')
    if res.get('error'):
        sys.exit(f'Screenshot error: {res["error"]}')
    result = res.get('result', {})
    path = result.get('path', '')
    size = result.get('size', 0)
    print(f'{MARK["in"]} uploaded: {path} ({size} bytes)')
    # Optionally save locally
    if args.output:
        # Fetch the exact file this capture produced. Screenshot ids are
        # reused -- `_ss` is the default one -- so an id names a directory
        # rather than a capture, and asking for it returns whichever
        # invocation finished last.
        selector = {'path': path} if path else {'id': cmd['id']}
        ss_url = _query_path('/screenshot', selector)
        img = api_raw('GET', ss_url)
        with open(args.output, 'wb') as f:
            f.write(img)
        print(f'Saved to {args.output}')


def do_uploads(args):
    """List or delete uploads."""
    if args.delete:
        # A filename names a file inside an id. Without one the bridge has no
        # narrower target than the whole token, which is not what naming a
        # single file asks for.
        if args.filename and not args.id:
            sys.exit('--filename needs --id: '
                     'a filename alone would delete every upload')
        body = {'token': token()}
        if args.id:
            body['id'] = args.id
        if args.filename:
            body['filename'] = args.filename
        api_delete('/upload', body)
        print('Deleted')
        return
    params = {}
    if args.id:
        params['id'] = args.id
    files = api('GET', _query_path('/upload', params))
    if not files:
        print('No uploads')
        return
    for f in files:
        ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(f.get('mtime', 0)))
        print(f'  {f["id"]}/{f["filename"]}  {f["size"]:>8} bytes  {ts}')
    print(f'{len(files)} files')
