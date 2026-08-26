"""Shared helpers for test-side command queues."""
import json
import time


_POLL_DELAY = 0.05
UNLINK_ATTEMPTS = 25
_UNLINK_RETRY_DELAY = 0.02


def clear_command_queue(directory):
    if directory.is_dir():
        for queued in directory.glob('*.json'):
            for remaining in range(UNLINK_ATTEMPTS - 1, -1, -1):
                try:
                    queued.unlink()
                    break
                except FileNotFoundError:
                    break
                except PermissionError:
                    if not remaining:
                        break
                    time.sleep(_UNLINK_RETRY_DELAY)
    if not directory.is_dir():
        return set()
    return {queued.name for queued in directory.glob('*.json')}


def wait_for_command(directory, timeout, producer_alive=None,
                     ignored_names=None):
    deadline = time.monotonic() + timeout
    ignored_names = set(ignored_names or ())
    saw_queue_file = False
    while True:
        now = time.monotonic()
        if now >= deadline:
            return None
        files = (sorted(queued for queued in directory.glob('*.json')
                        if queued.name not in ignored_names)
                 if directory.is_dir() else [])
        if files:
            saw_queue_file = True
            try:
                return json.loads(files[0].read_text(encoding='utf-8'))
            except (FileNotFoundError, PermissionError):
                pass
        if (not saw_queue_file and producer_alive is not None
                and not producer_alive()):
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(_POLL_DELAY, remaining))
