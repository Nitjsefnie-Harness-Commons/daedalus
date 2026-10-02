#!/usr/bin/env python3
"""Where a journey's own work RUNS, as distinct from which thread the
count reads it off. `test_journey_threads.py` owns the classifier; this
file owns the other half of the property that rests on it — that every
journey performs its own work on the main thread, which is read by
position and first, and that the one thing a journey must not count is
loaded on a worker of its own so a signature can name it."""
import contextlib
import contextvars
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    _util,
)


def test_rendering_of_runs_a_journey_on_the_main_thread(tmp):
    """The line that decides the thread, driven rather than read.

    Every journey is measured through `rendering_of`, so a journey that put
    its own work on the main thread would still lose it to a `rendering_of`
    that called it on a worker — the shape issue 1461 describes, and one a
    test of `mcp_exec` alone cannot see. The journey function and the bridge
    are both planted: the recorder below stands where `mcp_exec` stands and
    reports the thread it was called from, and the planted bridge is a
    context manager rather than a process, so nothing is spawned and nothing
    is dialled to reach it.
    """
    del tmp
    journeys = _journey_contract.journeys()
    called = []

    def run(base, docroot):
        del docroot
        called.append((base, threading.current_thread()))
        return {'journey': 'mcp-exec'}

    @contextlib.contextmanager
    def bridge(_directory, env=None, await_mcp=False):
        # The double fails on what it does not model. `await_mcp=True` is
        # what makes a count comparable (see `rendering_of`), and a
        # `rendering_of` that stopped passing it would leave this suite
        # green; `env` carries the planted journey's own token, so a bridge
        # spawned under another credential is refused here too.
        assert await_mcp is True, await_mcp
        assert env == {'DAEDALUS_TOKEN': 'planted', 'TOKEN': ''}, env
        yield 'http://127.0.0.1:1', None

    with _journey_contract.planting(
            journeys, JOURNEYS={'mcp-exec': ('planted', run)}), \
            _journey_contract.planting(journeys._util, bridge=bridge):
        rendering = journeys.rendering_of('mcp-exec')
    assert rendering == {'journey': 'mcp-exec'}, rendering
    assert called and called[0][1] is threading.main_thread(), called


def test_the_mcp_round_trip_runs_on_the_journeys_main_thread(tmp):
    """The placement issue 1461 turns on, recorded rather than read.

    `rendering_of` calls a journey on the main thread (the test above
    drives that), and `role_of` reads thread 1 as `MAIN` whatever its total
    and whatever it executed, so the round trip counts wherever the journey
    puts it — provided the journey puts it there, which is what this one
    pins. The stand-in front end records the thread each tool call was made
    from and refuses any payload that is not the one the journey is supposed
    to send, because a stub that swallows its arguments cannot tell a
    journey that changed what it does from one that only changed where it
    does it.
    """
    del tmp
    journeys = _journey_contract.journeys()
    seen = []

    class FrontEnd:
        """The two tools `mcp_exec` calls, and the thread each ran on."""

        _token = contextvars.ContextVar('journey_thread_test', default='')

        async def exec(self, **sent):
            seen.append(('exec', threading.current_thread()))
            assert sent == {'tab_id': journeys.MCP_TAB,
                            'cmd_id': journeys.MCP_COMMAND_ID,
                            'code': journeys.MCP_CODE, 'wait': False}, sent
            return {'command': {'id': journeys.MCP_COMMAND_ID,
                                '_did': 'journey-did'}}

        async def result(self, **read):
            seen.append(('result', threading.current_thread()))
            assert read == {'tab_id': journeys.MCP_TAB}, read
            return {'id': journeys.MCP_COMMAND_ID,
                    'tabId': journeys.MCP_TAB,
                    'value': journeys.MCP_RESULT, 'error': None}

    def load(_base):
        return FrontEnd()

    def post(_url, body):
        assert body == {'token': journeys._mcp_load.TOK,
                        'tabId': journeys.MCP_TAB,
                        'id': journeys.MCP_COMMAND_ID,
                        'result': journeys.MCP_RESULT, 'error': None,
                        'ts': 1, '_did': 'journey-did'}, body
        return 200, b'{}'

    with _journey_contract.planting(journeys, _load_front_end=load), \
            _journey_contract.planting(journeys._util, post_json=post):
        rendering = journeys.mcp_exec('http://127.0.0.1:1', None)
    assert rendering['journey'] == 'mcp-exec', rendering
    assert {'exec', 'result'} <= {name for name, _thread in seen}, seen
    assert all(thread is threading.main_thread()
               for _name, thread in seen), seen


def test_the_mcp_front_end_is_loaded_off_the_journeys_main_thread(tmp):
    """The one thing this journey must NOT count, kept off the main thread.

    A main thread is read as `MAIN` whatever its total and whatever it
    executed, so an import on one is the journey's own work by every rule
    this classifier applies. Loading it on a worker of its own and waiting
    for it is the whole of the asymmetry, and it costs the count nothing:
    the module the tool call reaches is the same one either way.
    """
    del tmp
    journeys = _journey_contract.journeys()
    loaded = []

    def load(_base):
        loaded.append(threading.current_thread())
        return 'front end'

    with _journey_contract.planting(journeys._mcp_load, _load_mcp=load):
        front = journeys._load_front_end('http://127.0.0.1:1')
    assert front == 'front end', front
    assert loaded and loaded[0] is not threading.main_thread(), loaded


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeyplacement_')


if __name__ == '__main__':
    raise SystemExit(main())
