#!/usr/bin/env python3
"""The control for the refusal door `tests/_dashfetch.py` installs.

A fetch double that answers a request it does not model with a success
turns a call the module under test invented into a green run: nothing is
refused, so nothing is reported, and no scenario reads a record that was
never written. Issue #1083 is that shape, on the two suites whose fakes
model their routes by shape rather than from a plan.

The control drives the REAL `dashboard/api.js` through the door: one
route the scenario declared and one it did not, in the same child, so a
door that refused everything cannot pass the declared half and a door
that recorded without refusing cannot pass the other. The real `api.js`
makes both requests, so the status in the refusal is the number the
shipped wrapper read off a response rather than one this suite wrote into
its own assertion.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashfetch  # noqa: E402
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_TWO_ROUTES = _dashnode.DashboardNodeHarness(
    _dashnode.DOM + _dashfetch.DOOR + r"""
(async () => {
const answered = [];
globalThis.fetch = async (target) => {
  const where = String(target);
  answered.push(where);
  if (where === '/tabs') return jsonResponse([{ tabId: '11', age: 0 }]);
  return refuse(where);
};
phase('dashboard module import started');
const { api } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const declared = await bounded(api.get('/tabs'), 'the declared route',
                               _dashnodeStepTimeoutMs);
let refusal = null;
try {
  await bounded(api.get('/invented/by-the-control'), 'an invented route',
                _dashnodeStepTimeoutMs);
} catch (error) { refusal = error.message; }
await bounded(settle(), 'after the refusal', _dashnodeStepTimeoutMs);
process.stdout.write(JSON.stringify({ declared, refusal, answered,
  unplanned: UNPLANNED }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=4, module=True, arguments=(ROOT / 'dashboard' / 'api.js',))


def test_an_unplanned_request_is_refused_and_recorded(_tmp):
    """Both halves, and neither alone.

    The declared route answers and the invented one does not, in the same
    child. The record is compared as a list rather than searched as a
    string, and the exact target is the claim, so a door that recorded
    every request it saw -- declared ones included -- fails here rather
    than passing a substring search.

    This holds the door. The per-suite `unplanned == []` assertions in
    `test_dashboard_sections.py` and `test_dashboard_behaviour.py` are
    what hold the real fakes, and only a module that invents a request
    makes either of them bite.
    """
    del _tmp
    seen = json.loads(_dashnode.run_dashboard_node(_TWO_ROUTES).stdout)
    assert seen['declared'] == [{'tabId': '11', 'age': 0}], seen
    assert seen['answered'] == ['/tabs', '/invented/by-the-control'], seen
    assert seen['unplanned'] == ['/invented/by-the-control'], seen
    assert seen['refusal'] == 'HTTP 599: unplanned request', seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashfetch_')


if __name__ == '__main__':
    raise SystemExit(main())
