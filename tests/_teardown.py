"""Bounded retry for a teardown a still-held file blocks (issue 313).

Windows holds a per-test directory past the last test from outside this
process, where no in-process fix reaches. Exhaustion is reported rather than
raised so a suite whose tests passed still passes, and only PermissionError
is retried -- any other exception reaches the caller unchanged.
"""
import time

ATTEMPTS = 8
BACKOFF_START = 0.05
BACKOFF_CAP = 2.0


def settle(cleanup, *, sleep=time.sleep):
    """Run cleanup, retrying PermissionError until the attempts run out."""
    delay = BACKOFF_START
    failure: PermissionError
    for attempt in range(ATTEMPTS):
        try:
            cleanup()
        except PermissionError as exc:
            failure = exc
            if attempt == ATTEMPTS - 1:
                break
            sleep(delay)
            delay = min(delay * 2, BACKOFF_CAP)
        else:
            return True
    where = getattr(failure, 'filename', None) or failure
    print(f'  WARN  teardown abandoned after {ATTEMPTS} attempts: {where}: '
          f'{type(failure).__name__}: {failure.strerror or failure}')
    return False
