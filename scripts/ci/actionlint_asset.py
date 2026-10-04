#!/usr/bin/env python3
"""How the pinned actionlint asset is ASKED FOR, and how long to wait.

The ask, and the bounds that belong to the ask: the size the transfer is
cut off at is this module's, because this is what asked for the bytes.
Naming the asset, checking the digest, unpacking and recording what was
installed are the installer's separate questions.

`RELEASE`, `ACTIONLINT_VERSION` and `MAX_TRANSFER` are written down by
the installer, which puts this directory on `sys.path` and then imports
this module, and hands the three over on the way in rather than being
imported back — that direction would be a cycle, and a pin written down
twice drifts.
"""
import http.client
import ssl
import time
import urllib.error
import urllib.request


# The installer writes all three here as it imports this module.
RELEASE = ''
ACTIONLINT_VERSION = ''
MAX_TRANSFER = 0

# One socket operation, not the transfer: a mirror dribbling a byte a
# minute can hold the step for as long as it likes, so the size bound
# the installer writes down is the one that is real.
DOWNLOAD_TIMEOUT = 30
# A connect or a read that fails is the network reaching the asset and not
# the asset answering wrong — the digest check in the installer is what
# covers that. A verdict is never retried — the size refusal, the digest
# mismatch — and what else is not worth a second ask is decided in
# `_worth_asking_again`.
#
# The bound, and it is the attempt count: an attempt costs one timeout
# per address each host in the redirect chain resolves to, summed over
# the hosts rather than multiplied across them. Today's counts are a
# DNS answer and change; the job's own `timeout-minutes` is the ceiling
# above this.
DOWNLOAD_ATTEMPTS = 3
# What a retry can change. A body cut short mid-transfer raises
# IncompleteRead, an HTTPException and not an OSError.
TRANSIENT_ERRORS = (OSError, http.client.HTTPException)
# The gap a retry leaves before it asks again, and the ceiling that gap
# stops at. Asking back to back spends every attempt inside the same few
# milliseconds, so a 503 the server would have cleared on its own is
# asked again before it can clear — measured at 0.004 s across all three
# attempts, against a Retry-After the server had already sent.
#
# Neither number is measured, and what they are chosen against is that a
# retry is worth having only if the wait can clear what the failure
# reported, and that the wait must not become the failure. The ceiling
# bounds the header, not this module's own wait — 2 s then 4 s never
# reaches it — because the header is the one input here a server sets to
# whatever it likes. What 10 buys is that the worst case stays a fraction
# of the tightest `timeout-minutes` any job gives the installer: two gaps
# at it is 20 s of that job's 1200 s.
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

    `Retry-After` is delay-seconds or an HTTP-date, and only the first
    is used: a date is the same window in a form this code has no clock
    to compare it against, so it falls back to the backoff rather than
    becoming a number nobody here could have checked.

    Only a string counts. A bare OSError carries no headers at all, and
    anything else the mapping hands back is not a delay-seconds value
    this can honour. Narrowing to `str` first is what lets the parse
    below catch `ValueError` alone: `int()` on a string raises nothing
    else, so the `TypeError` arm would be a branch nothing can reach.
    """
    headers = getattr(why, 'headers', None)
    told = headers.get('Retry-After') if headers is not None else None
    if not isinstance(told, str):
        return None
    try:
        seconds = int(told)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _pause_seconds(attempt, why):
    """How long to wait before the next ask: the longer of the two.

    The growth is this module's own, and a `Retry-After` the failing
    status carried is the other candidate — so the wait is whichever of
    the two is longer, not whichever arrived last. A server that asked
    for longer than the growth we would have waited anyway gets its
    window, because it knows when it will answer and this code does not;
    a server that asked for less is not the reason we wait, and waiting
    its window would only bring the next ask forward. The ceiling is
    applied last, so it binds whichever of the two survives.
    """
    window = RETRY_BACKOFF_SECONDS * 2 ** attempt
    told = _retry_after_seconds(why)
    if told is not None:
        window = max(window, told)
    return min(window, RETRY_BACKOFF_CAP_SECONDS)


def fetch(name):
    """The release asset's bytes, bounded in size, on a per-read timeout.

    The attempt is repeated while it fails transiently, and the last
    failure propagates as it does without the retry, so the traceback
    naming `fetch` and `urlopen` is what a reader of a dead install step
    still gets.
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
