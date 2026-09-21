"""Commands that move bytes: screenshots, uploads and segment jobs."""
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import SEGMENT_SIG_HEADER
from .output import MARK
from .result_view import relative_upload_path
from .transport import (URL, _http_error_detail, _query_path, api,
                        api_delete, api_raw, token, wait_for_result)


def do_segment_job(args):
    # Minting is idempotent. The sig substitutes for __SIG__ in
    # examples/hls-segment-relay.js, authorizing only this job's writes.
    res = api('POST', '/segment-job', {'token': token(), 'job': args.job})
    print(res['sig'])


def do_segment_status(args):
    # Look up the job capability: POST would create a mistyped job name.
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
            sys.exit(f'segment-status: job "{args.job}" '
                     'is owned by a different token')
        sys.exit(f'HTTP {e.code}: {_http_error_detail(e)}')
    except urllib.error.URLError as e:
        sys.exit(f'Connection failed: {e.reason}')
    # URLError is an OSError, so its clause must stay above this one for
    # the reason-only form to survive; the tuple is the fallback order.
    except (OSError, http.client.HTTPException, ValueError, TypeError) as e:
        # The 200 never became the bridge's JSON object: an HTML page, a
        # body cut off before its declared length, or a JSON value that is
        # not an object. No answer arrived, and it exits like a refused
        # connection.
        sys.exit(f'Connection failed: {e}')
    res = api('GET', _query_path('/segment-status', {'job': args.job}),
              headers={SEGMENT_SIG_HEADER: sig})
    count = res.get('count', 0)
    done = res.get('done', [])
    print(f'Job: {args.job}  Segments: {count}')
    if done:
        full = set(range(min(done), max(done) + 1))
        gaps = sorted(full - set(done))
        if gaps:
            print(f'Gaps ({len(gaps)}): {gaps[:30]}'
                  f'{"..." if len(gaps) > 30 else ""}')
        else:
            print(f'Complete: {min(done)}–{max(done)}')


def do_screenshot(args):
    cmd = {'token': token(), 'id': args.id or '_ss', 'code': '',
           'tab': 'extension'}
    cmd_payload = {'id': cmd['id'], 'type': 'screenshot'}
    if args.format:
        cmd_payload['format'] = args.format
    if args.quality:
        cmd_payload['quality'] = args.quality
    if args.chrome_tab is not None:
        cmd_payload['tabId'] = args.chrome_tab

    resp = api('PUT', '/command',
               {**cmd_payload, 'token': token(), 'tab': 'extension'})
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
    shown_path = relative_upload_path(path)
    print(f'{MARK["in"]} uploaded: {shown_path} ({size} bytes)')
    if args.output:
        # Reused ids select whichever capture finished last; the path selects
        # this invocation's file.
        selector = {'path': path} if path else {'id': cmd['id']}
        ss_url = _query_path('/screenshot', selector)
        img = api_raw('GET', ss_url)
        with open(args.output, 'wb') as f:
            f.write(img)
        print(f'Saved to {args.output}')


def do_uploads(args):
    if args.delete:
        # Without an id, the bridge would delete the token's entire namespace.
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
