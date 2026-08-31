"""Shared helpers for test-side command queues."""
import json
import time


POLL_DELAY = 0.05
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


def _poll_queue_reads(directory, count, timeout, *, ignored_names=(),
                      producer_alive=None, retry_vanished,
                      check_deadline_before_read):
    deadline = time.monotonic() + timeout
    ignored_names = set(ignored_names)
    saw_queue_file = False
    denied = None
    while True:
        if (check_deadline_before_read
                and time.monotonic() >= deadline):
            return None, denied
        files = (sorted(queued for queued in directory.glob('*.json')
                        if queued.name not in ignored_names)
                 if directory.is_dir() else [])
        ready = bool(files) if count is None else len(files) == count
        if ready:
            saw_queue_file = True
            selected = files[:1] if count is None else files
            try:
                return [json.loads(queued.read_text(encoding='utf-8'))
                        for queued in selected], denied
            except FileNotFoundError:
                if not retry_vanished:
                    raise
            except PermissionError as exc:
                denied = exc
        if (not saw_queue_file and producer_alive is not None
                and not producer_alive()):
            return None, denied
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, denied
        time.sleep(min(POLL_DELAY, remaining))


def wait_for_command(directory, timeout, producer_alive=None,
                     ignored_names=None):
    commands, _denied = _poll_queue_reads(
        directory, None, timeout, ignored_names=ignored_names or (),
        producer_alive=producer_alive, retry_vanished=True,
        check_deadline_before_read=True)
    return commands[0] if commands is not None else None


def wait_for_commands(directory, count, timeout):
    commands, _denied = _poll_queue_reads(
        directory, count, timeout, retry_vanished=True,
        check_deadline_before_read=True)
    return commands
