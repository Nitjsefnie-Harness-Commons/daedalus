#!/usr/bin/env python3
"""Standalone state and lifecycle guarantees for the SSE stream service."""
import threading

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_service(name):
    return _util.load(_util.ROOT / 'stream_service.py', name=name)


def test_register_returns_handles_that_unregister_the_stream(_tmp):
    service = _load_service('stream_service_register')

    stream_id, killed = service.register('tok', 'tab')

    assert isinstance(killed, threading.Event), killed
    assert not killed.is_set()
    assert service.snapshot() == (1, ['tab'])

    service.unregister(stream_id, killed)

    assert service.snapshot() == (0, [])


def test_register_replaces_equal_but_distinct_domain_inputs(_tmp):
    service = _load_service('stream_service_replace')
    first_inputs = tuple(['tok', 'tab'])
    second_inputs = tuple(['tok', 'tab'])
    assert first_inputs == second_inputs
    assert first_inputs is not second_inputs
    first_id, first_killed = service.register(*first_inputs)

    second_id, second_killed = service.register(*second_inputs)

    live, _tabs = service.snapshot()
    assert first_id != second_id
    assert first_killed.is_set()
    assert not second_killed.is_set()
    assert live == 1, live


def test_streams_for_two_tabs_of_one_token_coexist(_tmp):
    service = _load_service('stream_service_domain_inputs')

    _first_id, first_killed = service.register('tok', 'first')
    _second_id, second_killed = service.register('tok', 'second')

    assert not first_killed.is_set()
    assert not second_killed.is_set()
    assert service.snapshot() == (2, ['first', 'second'])


def test_two_tabless_streams_coexist_without_killing_either(_tmp):
    service = _load_service('stream_service_tabless')
    _first_id, first_killed = service.register('tok', '')

    _second_id, second_killed = service.register('tok', '')

    live, _tabs = service.snapshot()
    assert not first_killed.is_set()
    assert not second_killed.is_set()
    assert live == 2, live


def test_unregister_with_stale_kill_event_keeps_replacement(_tmp):
    service = _load_service('stream_service_stale_unregister')
    _first_id, first_killed = service.register('tok', 'tab')
    second_id, second_killed = service.register('tok', 'tab')

    service.unregister(second_id, first_killed)

    live, tabs = service.snapshot()
    assert not second_killed.is_set()
    assert (live, tabs) == (1, ['tab']), (live, tabs)


def test_snapshot_reports_live_count_and_tab_names_including_empty(_tmp):
    service = _load_service('stream_service_snapshot')
    service.register('tok-a', 'shared')
    service.register('tok-b', 'shared')
    service.register('tok-c', '')

    assert service.snapshot() == (3, ['', 'shared'])


def test_delivery_clock_records_each_delivery(_tmp):
    service = _load_service('stream_service_delivery_clock')
    ticks = iter((10.0, 20.0))
    service.time = type(
        'Clock', (), {'time': staticmethod(lambda: next(ticks))})
    assert service.last_delivery_at() is None

    service.record_delivery()
    assert service.last_delivery_at() == 10.0

    service.record_delivery()
    assert service.last_delivery_at() == 20.0


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
