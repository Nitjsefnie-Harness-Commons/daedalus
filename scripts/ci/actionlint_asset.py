#!/usr/bin/env python3
"""How the pinned actionlint asset is ASKED FOR, and how long to wait.

Split out of `scripts/ci/install_lint_tools.py`. This module's question
is one — whether a second ask could answer differently, and when it may
be made — and naming the asset, verifying the bytes it served,
unpacking it and recording what was installed are a different question
that happened to share the file.

The path this module is found by is put on `sys.path` by the installer,
which is also where `RELEASE`, `ACTIONLINT_VERSION` and `MAX_TRANSFER` are
written down. It hands them over below rather than this module importing
it back, which would be a cycle, and a pin written down here as well is a
pin that drifts.
"""
import http.client
import ssl
import time
import urllib.error
import urllib.request


# Written by the installer on import, not here. The empty values are
# never read: nothing asks for the asset before that call has run, and a
# module that imported the installer back would be the cycle above.
RELEASE = ''
ACTIONLINT_VERSION = ''
MAX_TRANSFER = 0

# One socket operation, not the transfer: a mirror dribbling a byte a
# minute can hold the step for as long as it likes, so the size bound
# below is the bound that is real.
DOWNLOAD_TIMEOUT = 30
# A connect or a read that fails is the network reaching the asset and not
# the asset answering wrong — the digest check below is what covers that. A
# verdict is never retried — the size refusal, the digest mismatch — and
# what else is not worth a second ask is decided in `_worth_asking_again`.
DOWNLOAD_ATTEMPTS = 3
# What a retry can change. A body cut short mid-transfer raises
# IncompleteRead, an HTTPException and not an OSError.
TRANSIENT_ERRORS = (OSError, http.client.HTTPException)
# The gap a retry leaves before it asks again, and the ceiling that gap
# stops at. Asking back to back spends every attempt inside the same few
# milliseconds, so a 503 the server would have cleared on its own is
# asked again before it can clear — measured at 0.004 s across all three
# attempts, against a Retry-After the server had already sent. The
# ceiling is what keeps the wait from becoming the failure: it is a
# fraction of the tightest `timeout-minutes` any job gives the installer,
# so the job's own bound stays the outer one and a header asking for
# longer than that cannot park a runner past it.
RETRY_BACKOFF_SECONDS = 2
RETRY_BACKOFF_CAP_SECONDS = 10


def _worth_asking_again(why):
    """Whether a second ask could answer differently: a status of 500 or
    above is the server failing rather than answering, and a certificate
    that does not verify is the same failure in the handshake. It arrives
    wrapped on the h.request() path and bare on the getresponse() and read()
    path, which is why the test below falls back rather than reaching for
    `reason` directly.
    """
    # Unwrapping an HTTPError rebinds `why` to its status message, a string,
    # and this test then declines nothing — a 404 is asked again.
    if isinstance(why, urllib.error.HTTPError):
        return why.code >= 500
    unwrapped = getattr(why, 'reason', why)
    return not isinstance(unwrapped, ssl.SSLCertVerificationError)


def _retry_after_seconds(why):
    """The delay in seconds a failing status asked for, or None.

    `Retry-After` is either delay-seconds or an HTTP-date, and only the
    first is used: a date is the same window in a form this code has no
    clock to compare it against, so it falls back to the backoff rather
    than being turned into a number nobody here could have checked. A
    bare OSError carries no headers at all, and a value that is not a
    non-negative integer is no delay this can honour.
    """
    headers = getattr(why, 'headers', None)
    told = headers.get('Retry-After') if headers is not None else None
    try:
        seconds = int(told)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _pause_seconds(attempt, why):
    """How long to wait before the next ask, and whose number that is.

    The growth is this module's own. A `Retry-After` the failing status
    carried REPLACES it rather than adding to it, because the server
    knows when it will answer and this code does not; asking sooner than
    the server asked to be asked is the mistake the wait exists to stop.
    The ceiling is applied last so it binds whichever of the two is
    larger.
    """
    window = RETRY_BACKOFF_SECONDS * 2 ** attempt
    told = _retry_after_seconds(why)
    if told is not None:
        window = max(window, told)
    return min(window, RETRY_BACKOFF_CAP_SECONDS)


def fetch(name):
    """The release asset's bytes, bounded in size, on a per-read timeout.

    `timeout` bounds one socket operation, not the transfer, so a mirror
    dribbling a byte a minute can hold the step for the whole download;
    MAX_TRANSFER is the bound that is real.

    The attempt is repeated while it fails transiently, and the last failure
    propagates as it does without the retry, so the traceback naming `fetch`
    and `urlopen` is what a reader of a dead install step still gets. A retry
    waits first, for the window `_pause_seconds` names, so the asks are
    not three in the same few milliseconds.
    """
    url = f'{RELEASE}/v{ACTIONLINT_VERSION}/{name}'
    limit = MAX_TRANSFER
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            with urllib.request.urlopen(
                    url, timeout=DOWNLOAD_TIMEOUT) as source:
                payload = source.read(limit + 1)
        except TRANSIENT_ERRORS as why:
            if (attempt + 1 == DOWNLOAD_ATTEMPTS
                    or not _worth_asking_again(why)):
                raise
            time.sleep(_pause_seconds(attempt, why))
            continue
        if len(payload) > limit:
            raise SystemExit(f'{url} served more than {limit} bytes')
        return payload
