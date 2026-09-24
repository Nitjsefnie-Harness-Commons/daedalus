#!/usr/bin/env python3
"""The shared bridge-fetch gate's own properties, driven without the worker.

The gate answers a request only while the scenario declares it, refuses
everything else with a status the worker's own error handling can hear, and
records every request it sees. Each test below pins one of those properties
against the shared module directly — no worker in the room — so a migration
of any harness rests on this floor.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)

BRIDGE = 'https://bridge.example.com'
ELSEWHERE = 'https://elsewhere.example.com'
SYNC = 'POST /sync-tabs'
OTHER = 'POST /other'
RESULT = 'POST /result'
TABS = 'POST /tabs'
NO_ORIGIN = '(no origin)'

# The probe's outcome, whole. The list is compared by equality so the count
# is pinned: five requests were seen even though the plan declares three,
# because the probe deliberately over- and mis-addresses requests.
EXPECTED_STATUSES = [200, 599, 599, 599, 599, 200, 200]
EXPECTED_NON_STREAM = [SYNC, SYNC, TABS, OTHER, RESULT]
EXPECTED_REFUSED = [SYNC, TABS]
EXPECTED_BAD_ORIGINS = [ELSEWHERE, NO_ORIGIN]

_ORACLE_HARNESS = r"""
const [plan] = process.argv.slice(1);
const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function streamResponse(answer) {
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + r"""

async function run() {
  const statuses = [];
  for (const step of plan.probe) {
    const init = { method: step.method };
    if (step.body !== undefined) init.body = JSON.stringify(step.body);
    const answer = await bridgeFetch(step.url, init);
    statuses.push(answer.status);
  }
  return {
    statuses,
    nonStream: nonStreamFetches.map((i) => i.request),
    refused: refusedFetches,
    badOrigins,
    bodies: nonStreamFetches.map(
      (i) => ({ request: i.request, body: i.body })),
    resultPosts,
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def _probe_plan():
    return {
        'planned': [SYNC, OTHER, RESULT],
        'hosts': [BRIDGE],
        'probe': [
            {'method': 'POST', 'url': BRIDGE + '/sync-tabs',
             'body': {'tabs': []}},
            {'method': 'POST', 'url': BRIDGE + '/sync-tabs',
             'body': {'tabs': []}},
            {'method': 'POST', 'url': BRIDGE + '/tabs', 'body': {}},
            {'method': 'POST', 'url': ELSEWHERE + '/other', 'body': {}},
            {'method': 'POST', 'url': '/relative', 'body': {}},
            {'method': 'POST', 'url': BRIDGE + '/other', 'body': {}},
            {'method': 'POST', 'url': BRIDGE + '/result',
             'body': {'id': 'r1', 'tabId': 'extension', 'result': 1,
                      '_did': 'did-1'}},
        ],
    }


def _probe():
    return run_gate(require_node(), _ORACLE_HARNESS, [], cwd=ROOT,
                    plan=_probe_plan())


def test_a_declared_route_answers_200_the_first_time(tmp):
    del tmp
    outcome = _probe()
    assert outcome['statuses'][0] == 200, outcome
    assert outcome['nonStream'][0] == SYNC, outcome


def test_a_request_beyond_the_declared_count_is_refused_by_status(tmp):
    """A refusal is a status the worker can hear, not a thrown error.

    The probe runs to completion and returns statuses, so no request threw;
    the second answer is the 599 refusal.
    """
    del tmp
    outcome = _probe()
    assert outcome['statuses'][1] == 599, outcome
    assert outcome['refused'] == EXPECTED_REFUSED, outcome


def test_an_undeclared_route_is_refused(tmp):
    del tmp
    outcome = _probe()
    assert outcome['statuses'][2] == 599, outcome
    assert TABS in outcome['refused'], outcome


def test_a_route_at_an_unpermitted_origin_is_refused(tmp):
    del tmp
    outcome = _probe()
    assert outcome['statuses'][3] == 599, outcome
    assert outcome['badOrigins'] == EXPECTED_BAD_ORIGINS, outcome


def test_a_relative_url_is_refused(tmp):
    del tmp
    outcome = _probe()
    assert outcome['statuses'][4] == 599, outcome
    assert outcome['badOrigins'] == EXPECTED_BAD_ORIGINS, outcome


def test_an_unpermitted_origin_spends_no_route_allowance(tmp):
    """A refused origin debits no route, so the declared one still answers."""
    del tmp
    outcome = _probe()
    assert outcome['statuses'][5] == 200, outcome
    assert outcome['refused'] == EXPECTED_REFUSED, outcome


def test_every_request_is_recorded_in_the_whole_list(tmp):
    """Whole-list equality pins the count: five seen, three declared."""
    del tmp
    outcome = _probe()
    assert outcome['nonStream'] == EXPECTED_NON_STREAM, outcome
    assert outcome['refused'] == EXPECTED_REFUSED, outcome


def test_a_recorded_request_carries_its_parsed_body(tmp):
    del tmp
    outcome = _probe()
    assert outcome['bodies'][0] == {
        'request': SYNC, 'body': {'tabs': []}}, outcome


def test_a_recorded_result_carries_its_full_payload(tmp):
    del tmp
    outcome = _probe()
    posted = outcome['resultPosts'][0]
    assert posted['id'] == 'r1', outcome
    assert posted['tabId'] == 'extension', outcome
    assert posted['result'] == 1, outcome
    assert posted['did'] == 'did-1', outcome


def test_a_scenario_matching_its_plan_passes_the_whole_list_check(tmp):
    plan = {
        'planned': [SYNC, OTHER, RESULT],
        'hosts': [BRIDGE],
        'probe': [
            {'method': 'POST', 'url': BRIDGE + '/sync-tabs', 'body': {}},
            {'method': 'POST', 'url': BRIDGE + '/other', 'body': {}},
            {'method': 'POST', 'url': BRIDGE + '/result', 'body': {'id': 'x'}},
        ],
    }
    outcome = run_gate(require_node(), _ORACLE_HARNESS, [], cwd=ROOT,
                       plan=plan)
    assert outcome['statuses'] == [200, 200, 200], outcome
    assert_gate_clean(outcome['nonStream'], outcome['refused'],
                      outcome['badOrigins'], plan['planned'])


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='bridgefakeoracle_')


if __name__ == '__main__':
    raise SystemExit(main())
