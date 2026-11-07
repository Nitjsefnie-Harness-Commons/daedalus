#!/usr/bin/env python3
"""The MCP side of exec timing: `timing_view.render` and `exec`."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _noderun import run_node_argv  # noqa: E402
from _stream_fake import require_node  # noqa: E402
from _mainworldharness import run_timed_eval  # noqa: E402
from _relayharness import run_timed_relay  # noqa: E402
from test_exec_timing_bridge import _probe  # noqa: E402

sys.path.insert(0, str(_util.ROOT))
from daedalus_mcp import timing_view, tools_eval  # noqa: E402

_T = 1_700_000_000_000  # server wall clock, ms
_LEAD = 5000  # the browser clock runs this far ahead of the server's


def _stamps(server, browser):
    """Real elapsed ms after `_T` as each clock stamps them."""
    timing = {name: (_T + ms) * 1000 for name, ms in server.items()}
    timing.update({name: _T + ms + _LEAD for name, ms in browser.items()})
    return timing


def _legs(view):
    return [(leg['leg'], leg['clock'], leg['ms'], leg['raw_ms'],
             leg['clamped']) for leg in view['legs']]


def test_the_offset_comes_from_the_causality_bounds_and_applies_once(tmp):
    """Issue Expected 2: a browser clock 5000 ms ahead is estimated, and
    every cross-clock leg reads its real elapsed time."""
    del tmp
    timing = _stamps(
        {'srv_enqueue_us': 0, 'srv_write_us': 1, 'srv_result_us': 24},
        {'sw_recv': 3, 'sw_tick': 4.5, 'sw_probe_call': 3.5, 'pg_probe': 5,
         'sw_probe_done': 6, 'sw_eval_call': 6.5, 'pg_eval_start': 8,
         'pg_eval_end': 20, 'sw_eval_done': 21, 'sw_post': 22})
    view = timing_view.render(timing, (_T + 25) * 1000)
    assert view['offset'] == {'estimate_ms': 5000.0, 'uncertainty_ms': 2.0,
                              'lo_ms': 4998.0, 'hi_ms': 5002.0,
                              'consistent': True}, view
    expected = [('srv_enqueue_us', 'srv_write_us', 'server', 1.0),
                ('srv_write_us', 'sw_recv', 'offset', 2.0),
                ('sw_recv', 'sw_probe_call', 'browser', 0.5),
                ('sw_probe_call', 'pg_probe', 'browser', 1.5),
                ('pg_probe', 'sw_probe_done', 'browser', 1.0),
                ('sw_probe_done', 'sw_eval_call', 'browser', 0.5),
                ('sw_eval_call', 'pg_eval_start', 'browser', 1.5),
                ('pg_eval_start', 'pg_eval_end', 'browser', 12.0),
                ('pg_eval_end', 'sw_eval_done', 'browser', 1.0),
                ('sw_eval_done', 'sw_post', 'browser', 1.0),
                ('sw_post', 'srv_result_us', 'offset', 2.0),
                ('srv_result_us', 'mcp_read_us', 'server', 1.0)]
    assert _legs(view) == [(f'{a} -> {b}', clock, ms, ms, False)
                           for a, b, clock, ms in expected], view
    assert (view['world'], view['unreached'], view['tick_ms']) == (
        'page-main', [], 1.5), view
    assert (view['parts_ms'], view['total_ms']) == (25.0, 25.0), view


def test_a_negative_leg_is_clamped_with_its_raw_value_beside_it(tmp):
    """Crossed bounds render, marked inconsistent; a negative leg renders
    0 beside its raw value, and the parts exceed the total by as much."""
    del tmp
    timing = _stamps({'srv_enqueue_us': 0, 'srv_write_us': 10,
                      'srv_result_us': 20},
                     {'sw_recv': 0, 'sw_probe_call': 1, 'sw_probe_failed': 2,
                      'sw_cdp_attach': 3, 'sw_cdp_ready': 2.75,
                      'sw_cdp_eval_done': 5, 'sw_post': 30})
    view = timing_view.render(timing, (_T + 21) * 1000)
    assert view['offset'] == {'estimate_ms': 5000.0, 'uncertainty_ms': -10.0,
                              'lo_ms': 5010.0, 'hi_ms': 4990.0,
                              'consistent': False}, view
    legs = _legs(view)
    assert legs[1] == ('srv_write_us -> sw_recv', 'offset', 0.0, -10.0,
                       True), legs
    assert legs[5] == ('sw_cdp_attach -> sw_cdp_ready', 'browser', 0.0,
                       -0.2, True), legs
    assert legs[-2] == ('sw_post -> srv_result_us', 'offset', 0.0, -10.0,
                        True), legs
    assert [leg[4] for leg in legs].count(True) == 3, legs
    assert (view['parts_ms'], view['total_ms']) == (41.2, 21.0), view
    assert (view['world'], view['unreached']) == ('cdp', []), view


def test_stamps_a_result_does_not_carry_render_as_unknown(tmp):
    """A `_timing` no producer shaped renders what it can, never raising."""
    del tmp
    for timing in ([], {'srv_enqueue_us': 'x', 'sw_recv': True}):
        view = timing_view.render(timing, 5)
        assert view == {'world': None, 'offset': None, 'legs': [],
                        'unreached': ['srv_enqueue_us', 'srv_write_us',
                                      'sw_recv', 'sw_probe_call', 'sw_post',
                                      'srv_result_us'],
                        'tick_ms': None, 'parts_ms': 0, 'total_ms': None
                        }, view
    cross = timing_view.render({'srv_enqueue_us': 0, 'sw_recv': 1}, 1000)
    assert _legs(cross) == [
        ('srv_enqueue_us -> sw_recv', 'offset', None, None, False),
        ('sw_recv -> mcp_read_us', 'offset', None, None, False)], cross
    assert cross['total_ms'] == 1.0, cross


# What each producer world posts in the vm harnesses, and how it renders.
_WORLDS = {
    'eval-plain': ('page-main', [], [
        'sw_probe_call', 'pg_probe', 'sw_probe_done', 'sw_eval_call',
        'pg_eval_start', 'pg_eval_end', 'sw_eval_done']),
    'eval-timeout': ('page-main', ['pg_eval_start', 'pg_eval_end',
                                   'sw_eval_done'], [
        'sw_probe_call', 'pg_probe', 'sw_probe_done', 'sw_eval_call',
        'sw_eval_failed']),
    'eval-probe-hang': ('cdp', [], [
        'sw_probe_call', 'sw_probe_failed', 'sw_cdp_attach', 'sw_cdp_ready',
        'sw_cdp_eval_done']),
    'relay': ('relay', [], [
        'sw_probe_call', 'sw_probe_failed', 'sw_cdp_attach', 'sw_relay_send',
        'cs_recv', 'pg_recv', 'pg_done', 'cs_result', 'sw_relay_result']),
}


def test_every_stamp_a_producer_takes_is_one_the_view_renders(tmp):
    """Issue Expected 1: the real producers' stamps render as each world's
    legs in path order, unreached ones marked, and are exactly the stamps
    the view consumes."""
    bridge = json.loads(_probe(tmp, '1')['eval']['slot'])['_timing']
    emitted = set(bridge) | {'mcp_read_us'}
    for world, (name, unreached, path) in _WORLDS.items():
        run = (run_timed_relay('1') if world == 'relay'
               else run_timed_eval(world, '1'))
        posted = json.loads(run['bodies'][0])['_timing']
        browser = {key: value for key, value in posted.items()
                   if not key.startswith('srv_')}
        emitted |= set(browser)
        view = timing_view.render({**bridge, **browser}, 1)
        chain = ['srv_enqueue_us', 'srv_write_us', 'sw_recv', *path,
                 'sw_post', 'srv_result_us', 'mcp_read_us']
        assert [leg['leg'] for leg in view['legs']] == [
            f'{a} -> {b}' for a, b in zip(chain, chain[1:])], (world, view)
        assert (view['world'], view['unreached']) == (name, unreached), view
    assert emitted == timing_view.CONSUMED, emitted ^ timing_view.CONSUMED


# The probe and the eval as the page runs them, loaded from evaluate.js so
# their own lines are the ones measured: untimed, then timed.
_PAGE_SIDE = r"""
const fs = require('fs');
const vm = require('vm');
const file = require('path').resolve('extension/worker/evaluate.js');
const out = {};
for (const page of ['plain', 'csp', 'poison']) {
  const context = { performance: { timeOrigin: 1600000000000.25, now() {
    if (page === 'poison') throw new Error('page killed performance.now');
    return 0.5;
  } } };
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), context,
    { filename: file });
  if (page === 'csp') context.Function = () => { throw new EvalError(); };
  out[page] = [context._canUseMainWorldEval(),
    context._canUseMainWorldEval(true),
    context._executeMainWorldEval('1+1'),
    context._executeMainWorldEval('1+1', true)];
}
process.stdout.write(JSON.stringify(out));
"""


def test_the_page_side_probe_and_eval_stamp_only_when_timed(tmp):
    """Untimed, the probe answers a bare boolean and the eval envelope has no
    stamp; timed, both add the page's clock, which a throwing clock leaves
    out without changing the answer.
    """
    del tmp
    result = run_node_argv(require_node(), ['-e', _PAGE_SIDE],
                            _util.ROOT)
    assert result.returncode == 0, result
    stamp, error = 1600000000000.75, {'e': 'page killed performance.now'}
    evaluated = {'r': 2, 'ms': 0}
    timed = {**evaluated, 'pg_eval_start': stamp, 'pg_eval_end': stamp}
    assert json.loads(result.stdout) == {
        'plain': [True, {'ok': True, 'pg_probe': stamp}, evaluated, timed],
        'csp': [False, {'ok': False, 'pg_probe': stamp}, evaluated, timed],
        'poison': [True, {'ok': True}, error, error]}, result.stdout


class _Bridge:
    """The bridge calls a waited exec makes."""

    def __init__(self, body):
        self.body = body

    def checked_timeout(self, timeout):
        return timeout

    async def put(self, path, payload):
        del path, payload
        return {'did': 'd1', 'command': {}}

    async def poll_result(self, tab, timeout, **expect):
        del tab, timeout, expect
        return dict(self.body)


class _Server:
    def tool(self):
        return lambda function: function


_RESULT = {'token': 'tok', 'id': 'c', 'tabId': '7', 'result': '4',
           'error': None, 'ts': 1, 'world': 'page-main', 'exec_ms': 0,
           'resultGeneration': 'g', 'deliveryId': 'd1', 'roundtrip_ms': 9}


def test_exec_renders_timing_only_for_a_result_that_carries_it(tmp):
    """Issue Expected 3: untimed, the base bytes and no clock read; timed,
    `timing` replaces `_timing`. The flag is set against each arm."""
    del tmp
    reads = []
    clock = time.time_ns

    def counted():
        reads.append(1)
        return clock()

    def run(body, flag):
        tools = tools_eval.register(_Server(), _Bridge(body))
        with mock.patch.object(time, 'time_ns', counted), mock.patch.dict(
                os.environ, {'DAEDALUS_DEBUG_TIMING': flag}):
            return asyncio.run(tools['exec']('c', '2 + 2', '7'))

    assert json.dumps(run(_RESULT, '1')) == (
        '{"id": "c", "tabId": "7", "error": null, "ts": 1, "world": '
        '"page-main", "exec_ms": 0, "resultGeneration": "g", "deliveryId": '
        '"d1", "roundtrip_ms": 9, "value": 4}'), reads
    assert not reads
    timed = run({**_RESULT, '_timing': {'srv_enqueue_us': 1}}, '0')
    assert len(reads) == 1, reads
    assert '_timing' not in timed, timed
    assert timed['timing']['legs'][0]['leg'] == (
        'srv_enqueue_us -> mcp_read_us'), timed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='timing_view_')


if __name__ == '__main__':
    raise SystemExit(main())
