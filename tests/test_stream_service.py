#!/usr/bin/env python3
"""Standalone state and lifecycle guarantees for the SSE stream service."""
import threading
import time

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_service(name):
    return _util.load(_util.ROOT / 'stream_service.py', name=name)


def test_register_returns_id_event_and_makes_stream_live(_tmp):
    service = _load_service('stream_service_register')

    stream_id, killed = service.register(('tok', 'tab'), 'tab')

    live, _tabs = service.snapshot()
    assert isinstance(stream_id, int), (
        f'register returned non-integer stream id: {stream_id!r}')
    assert isinstance(killed, threading.Event), killed
    assert not killed.is_set()
    assert live == 1, live


def test_register_replaces_an_existing_stream_with_the_same_key(_tmp):
    service = _load_service('stream_service_replace')
    first_id, first_killed = service.register(('tok', 'tab'), 'tab')

    second_id, second_killed = service.register(('tok', 'tab'), 'tab')

    live, _tabs = service.snapshot()
    assert first_id != second_id
    assert first_killed.is_set()
    assert not second_killed.is_set()
    assert live == 1, live


def test_two_tabless_streams_coexist_without_killing_either(_tmp):
    service = _load_service('stream_service_tabless')
    _first_id, first_killed = service.register(None, '')

    _second_id, second_killed = service.register(None, '')

    live, _tabs = service.snapshot()
    assert not first_killed.is_set()
    assert not second_killed.is_set()
    assert live == 2, live


def test_unregister_with_stale_kill_event_keeps_replacement(_tmp):
    service = _load_service('stream_service_stale_unregister')
    _first_id, first_killed = service.register(('tok', 'tab'), 'tab')
    second_id, second_killed = service.register(('tok', 'tab'), 'tab')

    service.unregister(second_id, first_killed)

    live, tabs = service.snapshot()
    assert not second_killed.is_set()
    assert (live, tabs) == (1, ['tab']), (live, tabs)


def test_snapshot_reports_live_count_and_tab_names_including_empty(_tmp):
    service = _load_service('stream_service_snapshot')
    service.register(('tok-a', 'first'), 'first')
    service.register(('tok-b', 'second'), 'second')
    service.register(None, '')

    assert service.snapshot() == (3, ['', 'first', 'second'])


def test_delivery_clock_is_unset_until_delivery_is_recorded(_tmp):
    service = _load_service('stream_service_delivery_clock')
    assert service.last_delivery_at() is None
    before = time.time()

    service.record_delivery()

    delivered_at = service.last_delivery_at()
    assert isinstance(delivered_at, float), (
        f'delivery clock stayed unset: {delivered_at!r}')
    assert before <= delivered_at <= time.time(), delivered_at


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
