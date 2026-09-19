"""Shared fixtures for the segment-relay suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

`BRIDGE_ENV` is the environment every bridge child of these suites carries:
the credential the suites' requests present, applied per spawn, because
`_util.bridge()` strips every inherited `DAEDALUS_*` variable before applying
its own settings and the caller's `env=`. The `os.environ` writes below keep
serving the suite process's own in-process consumers of the credential.
"""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

# Keep the bridge child's MCP side-thread off the fixed port 8086: several
# bridges run per suite, and the second one to bind 8086 would only log a
# crash, but port 0 removes the collision entirely.
os.environ.setdefault('DAEDALUS_MCP_PORT', '0')

TOK = 'segtok'

# The child env applied at every bridge spawn of these suites: the credential
# the suites' requests present, with `TOKEN` cleared so an ambient one-off
# override cannot shadow it.
BRIDGE_ENV = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}
os.environ.update(BRIDGE_ENV)

# Segment storage lives under the bridge's own data root (<docroot>/segments/)
# since the capability fix; the pre-auth server wrote to a world-shared
# /tmp/hls-segments instead. One test pins that nothing lands there any more;
# that path means something else on Windows, so the /tmp assertion skips
# there.
TMP_SEG_ROOT = Path('/tmp/hls-segments')


def seg_job():
    return 'tt-' + uuid.uuid4().hex[:12]


def mint_job(base, token, job):
    """POST /segment-job and return (status, body)."""
    return _util.post_json(base + '/segment-job', {'token': token, 'job': job})


def post_segment(base, job, sig, segment, payload=b'bytes', total='1'):
    return _util.request(
        base + f'/segment?job={job}&seg={segment}&total={total}&sig={sig}',
        'POST', body=payload,
        headers={'Content-Type': 'application/octet-stream'})
