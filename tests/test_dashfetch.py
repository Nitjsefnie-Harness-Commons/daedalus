#!/usr/bin/env python3
"""The control for the refusal door `tests/_dashfetch.py` installs.

A fetch double that answers a request it does not model with a success
turns a call the module under test invented into a green run: nothing is
refused, so nothing is reported, and no scenario reads a record that was
never written. Issue #1083 is that shape, on the two suites whose fakes
model their routes by shape rather than from a plan.

The control drives the REAL `dashboard/api.js` through the door, so the
status in the refusal below is the number the shipped wrapper read off a
response rather than one this suite wrote into its own assertion.
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

    The declared route and the invented one are made in the same child, so
    a door that refused everything cannot pass the first and a door that
    recorded without refusing cannot pass the second. The record is
    compared as a list rather than searched as a string, and the exact
    target is the claim, so a door that recorded every request it saw --
    declared ones included -- fails here rather than passing a substring
    search.

    This holds the door. The per-suite `unplanned == []` assertions are
    what hold the real fakes, and only a module that invents a request
    makes either of them bite.
    """
    del _tmp
    seen = json.loads(_dashnode.run_dashboard_node(_TWO_ROUTES).stdout)
    assert seen['declared'] == [{'tabId': '11', 'age': 0}], seen
    assert seen['answered'] == ['/tabs', '/invented/by-the-control'], seen
    assert seen['unplanned'] == ['/invented/by-the-control'], seen
    assert seen['refusal'] == 'HTTP 599: unplanned request', seen


# The refusal's header bag is keyed: `api.js` reads `content-type` and
# nothing else, and a bag that answered every name would hand a module a
# header this transport never modelled.
def test_a_response_header_the_refusal_does_not_model_fails_by_name(_tmp):
    """The response half of "fail on what you do not model".

    `tests/_dashshell.py` keys its bag for the same reason, and a
    transport that answered `application/json` to a name nobody modelled
    would let a section reach for a header the assertion cannot see. The
    read is the real one: `api.js`'s own `r.headers.get('content-type')`,
    not a call this suite invented.
    """
    del _tmp
    seen = json.loads(_dashnode.run_dashboard_node(_HEADER_PROBE).stdout)
    assert seen['contentType'] == 'application/json', seen
    assert seen['failure'] == (
        'response header not modelled: x-invented-by-the-control'), seen


_HEADER_PROBE = _dashnode.DashboardNodeHarness(
    _dashnode.DOM + _dashfetch.DOOR + r"""
(async () => {
globalThis.fetch = async (target) => refuse(String(target));
const refused = await fetch('/invented/by-the-control');
const contentType = refused.headers.get('content-type');
let failure = null;
try { refused.headers.get('x-invented-by-the-control'); }
catch (error) { failure = error.message; }
process.stdout.write(JSON.stringify({ contentType, failure }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=0, module=True)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashfetch_')


if __name__ == '__main__':
    raise SystemExit(main())
