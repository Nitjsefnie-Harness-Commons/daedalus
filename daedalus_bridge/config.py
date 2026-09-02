"""Bridge startup paths and environment-derived settings."""
import os
import pathlib

from daedalus_bridge.env_config import (
    env_flag, env_int, env_positive_float)
from daedalus_bridge import result_store


if 'DAEDALUS_DIR' not in os.environ:
    raise SystemExit('DAEDALUS_DIR env var required (e.g. /srv/daedalus)')
if 'DAEDALUS_PORT' not in os.environ:
    raise SystemExit('DAEDALUS_PORT env var required (e.g. 8081)')


BASE = pathlib.Path(os.environ['DAEDALUS_DIR'])
CMD_DIR = BASE / 'commands'
RES_DIR = BASE / 'results'
DELIVERY_DIR = result_store.delivery_root(RES_DIR)
UPLOAD_DIR = BASE / 'uploads'
SEG_DIR = BASE / 'segments'
DASHBOARD_DIR = pathlib.Path(__file__).resolve().parent.parent / 'dashboard'
# 0 lets the kernel pick a free port; the startup line always prints the
# port actually bound, so an ephemeral deployment (and the test fixture)
# never has to choose a number another process could take first.
PORT = env_int('DAEDALUS_PORT', 0, 0, 65535)


# Stream lifetime ceiling. A stream's liveness is policed by the keepalive write
# (a dead peer raises on the next one) and by replacement-on-reconnect, so this
# is only a last-resort ceiling on a wedged connection — not a rollover timer.
STREAM_MAX_AGE = env_positive_float('DAEDALUS_STREAM_MAX_AGE', 3600)
STREAM_KEEPALIVE = env_positive_float('DAEDALUS_STREAM_KEEPALIVE', 15)
# Maximum bytes read from any HTTP request body; override for larger relays.
MAX_BODY_SIZE = env_int('DAEDALUS_MAX_BODY_SIZE', 64 * 1024 * 1024, 0)

# Per-target delivery results are retained until consumed or evicted. This is
# separate from the in-memory retry-dedup bound: a delivery may be evicted
# from disk while its id remains remembered as accepted.
MAX_DELIVERY_RESULTS = env_int(
    'DAEDALUS_MAX_DELIVERY_RESULTS', 1024, 0)

# How deeply a JSON request body may nest containers. Without this the depth
# actually enforced was whatever the running interpreter's recursion limit
# happened to be, so one body got two answers: refused as too deeply nested on
# 3.11 through 3.13, parsed on 3.14. The bound is checked against the raw bytes
# before json.loads is asked to build anything, so the answer is this
# repository's and the same everywhere. The ceiling is far below any
# interpreter's recursion limit; nothing this bridge accepts nests near it.
MAX_JSON_DEPTH = env_int('DAEDALUS_MAX_JSON_DEPTH', 100, 1, 500)

# How large a body the bridge will read from a request that has not
# authenticated before sending it. A body token cannot be checked without
# reading the body, so every JSON route used to materialize and parse whatever
# was declared -- up to MAX_BODY_SIZE -- on its way to answering 401, and
# concurrent workers multiplied that. The Bearer header decides first; this is
# the window in which the older body-token form still works, sized so that a
# request nobody has authenticated is never the expensive one.
MAX_UNAUTHENTICATED_BODY = env_int(
    'DAEDALUS_MAX_UNAUTHENTICATED_BODY', 64 * 1024, 0)

# Per-phase timing for the segment write path, read once at import and inert
# when off. Committed with the fix rather than removed after measuring it: the
# next regression on this path needs the same attribution, and rebuilding it by
# hand in a REPL measures something other than what the bridge runs.
DEBUG_TIMING = env_flag('DAEDALUS_DEBUG_TIMING')


# Per-operation socket deadline for a request. A peer that declares a body and
# then stops sending held its worker for as long as it kept the socket open,
# so opening connections was enough to grow the thread count without ever
# authenticating. It bounds each read and write rather than the request as a
# whole, so an upload that keeps arriving is never cut off for being large.
REQUEST_TIMEOUT = env_positive_float('DAEDALUS_REQUEST_TIMEOUT', 60)

# Hard ceiling on concurrently-served connections. The deadline above bounds
# how long one worker lives; this bounds how many exist at once, which the
# deadline alone cannot do — a fast enough arrival rate outruns any deadline.
MAX_REQUEST_WORKERS = env_int(
    'DAEDALUS_MAX_REQUEST_WORKERS', 256, 1, 4096)
# Fixed HLS quotas copied into each trusted job record when its capability is
# minted. A page-visible job signature cannot alter these per-request.
MAX_SEGMENT_INDEX = env_int('DAEDALUS_MAX_SEGMENT_INDEX', 99999, 0)
MAX_SEGMENTS_PER_JOB = env_int(
    'DAEDALUS_MAX_SEGMENTS_PER_JOB', 10000, 0)
MAX_SEGMENT_JOB_SIZE = env_int(
    'DAEDALUS_MAX_SEGMENT_JOB_SIZE', 4 * 1024 * 1024 * 1024, 0)

# Command files expire when no SSE reader claims them. The queue itself lives
# in server.py; this value is shared by its producer and expiry worker.
CMD_TTL = env_positive_float('DAEDALUS_CMD_TTL', 90)
