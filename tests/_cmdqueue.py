"""Shared helpers for test-side command queues."""
import json
import time


_POLL_DELAY = 0.05
_UNLINK_ATTEMPTS = 25
_UNLINK_RETRY_DELAY = 0.02


def clear_command_queue(directory):
    if directory.is_dir():
        for queued in directory.glob('*.json'):
            for remaining in range(_UNLINK_ATTEMPTS - 1, -1, -1):
                try:
                    queued.unlink()
                    break
                except FileNotFoundError:
                    break
                except PermissionError:
                    if not remaining:
                        raise
                    time.sleep(_UNLINK_RETRY_DELAY)


def wait_for_command(directory, timeout, producer_alive=None):
    deadline = time.monotonic() + timeout
    saw_queue_file = False
    while True:
        now = time.monotonic()
        if now >= deadline:
            return None
        files = sorted(directory.glob('*.json')) if directory.is_dir() else []
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
