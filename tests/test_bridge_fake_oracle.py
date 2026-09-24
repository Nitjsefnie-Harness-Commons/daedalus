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
EXPECTED_STATUSES = [200, 599, 599, 599, 599, 200, 200, 599, 599]
EXPECTED_NON_STREAM = [SYNC, SYNC, TABS, OTHER, RESULT]
EXPECTED_REFUSED = [SYNC, TABS]
EXPECTED_BAD_ORIGINS = [ELSEWHERE, NO_ORIGIN, ELSEWHERE, NO_ORIGIN]

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
  const answered = [];
  for (const step of plan.probe) {
    const init = { method: step.method };
    if (step.body !== undefined) init.body = JSON.stringify(step.body);
    if (step.headers !== undefined) init.headers = step.headers;
    // A step that expects the gate to throw says so, so the probe runs to
    // completion and the throw is evidence rather than a dead child.
    if (step.expectThrow) {
      let thrown = null;
      try { await bridgeFetch(step.url, init); }
      catch (error) { thrown = error.message; }
      statuses.push(thrown === null ? 'no throw' : 'throw: ' + thrown);
      answered.push(null);
      continue;
    }
    const answer = await bridgeFetch(step.url, init);
    statuses.push(answer.status);
    answered.push(await answer.json().catch(() => null));
  }
  return {
    statuses,
    answered,
    nonStream: nonStreamFetches.map((i) => i.request),
    refused: refusedFetches,
    badOrigins,
    records: nonStreamFetches,
    streamAnswered: streamFetches.map((f) => f.answered),
    contractFaults: gateContractFaults,
    bodies: nonStreamFetches.map(
      (i) => ({ request: i.request, body: i.body })),
    auths: nonStreamFetches.map((i) => i.auth),
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
            {'method': 'GET', 'url': ELSEWHERE + '/stream?tab=x'},
            {'method': 'GET', 'url': '/stream?tab=x'},
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


def test_a_foreign_origin_stream_url_is_refused_and_recorded(tmp):
    """The stream URL is the one request derived from runtime config, so the
    origin gate must run before the stream branch: a foreign-origin stream
    is refused, not silently answered, and is recorded in badOrigins."""
    del tmp
    outcome = _probe()
    assert outcome['statuses'][7] == 599, outcome
    assert outcome['badOrigins'] == EXPECTED_BAD_ORIGINS, outcome


def test_a_relative_stream_url_is_refused_and_recorded(tmp):
    del tmp
    outcome = _probe()
    assert outcome['statuses'][8] == 599, outcome
    assert outcome['badOrigins'] == EXPECTED_BAD_ORIGINS, outcome


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
    assert_gate_clean(
        contract_faults=outcome['contractFaults'],
        records=outcome['records'], refused=outcome['refused'],
        bad_origins=outcome['badOrigins'],
        stream_answered=outcome['streamAnswered'],
        planned=plan['planned'], planned_stream=[])


def test_a_recorded_request_carries_its_authorization_header(tmp):
    """The token travels in a header, so a scenario reads it off the record
    instead of wrapping its own fetch."""
    del tmp
    plan = {
        'planned': [SYNC],
        'probe': [
            {'method': 'POST', 'url': BRIDGE + '/sync-tabs', 'body': {},
             'headers': {'Authorization': 'Bearer probe-token'}},
            {'method': 'POST', 'url': BRIDGE + '/sync-tabs', 'body': {}},
        ],
    }
    outcome = run_gate(require_node(), _ORACLE_HARNESS, [], cwd=ROOT,
                       plan=plan)
    assert outcome['auths'] == ['Bearer probe-token', None], outcome


def test_a_planned_answer_sets_the_status_and_body_the_scenario_declared(
        tmp):
    """A scenario that exercises a bridge's own error answer declares that
    answer; the gate then hands the worker the status and body it planned."""
    del tmp
    plan = {
        'planned': [OTHER],
        'answers': {OTHER: {'status': 403, 'body': {'error': 'forbidden'}}},
        'probe': [
            {'method': 'POST', 'url': BRIDGE + '/other', 'body': {}},
        ],
    }
    outcome = run_gate(require_node(), _ORACLE_HARNESS, [], cwd=ROOT,
                       plan=plan)
    assert outcome['statuses'] == [403], outcome
    assert outcome['answered'] == [{'error': 'forbidden'}], outcome
    assert outcome['records'][0]['status'] == 403, outcome


def test_a_planned_throw_answers_by_throwing_and_is_still_recorded(tmp):
    """An unreachable bridge is a thrown fetch, and a scenario that models
    one declares the throw. The record carries it, so a swallowed throw
    still shows the request happened."""
    del tmp
    plan = {
        'planned': [OTHER],
        'answers': {OTHER: {'throw': 'Failed to fetch'}},
        'probe': [
            {'method': 'POST', 'url': BRIDGE + '/other', 'body': {},
             'expectThrow': True},
        ],
    }
    outcome = run_gate(require_node(), _ORACLE_HARNESS, [], cwd=ROOT,
                       plan=plan)
    assert outcome['statuses'] == ['throw: Failed to fetch'], outcome
    assert outcome['records'] == [
        {'request': OTHER, 'refused': False, 'body': {}, 'auth': None,
         'status': 'throw'}], outcome
    assert outcome['refused'] == [], outcome


def test_a_refused_request_is_refused_even_where_an_answer_was_planned(tmp):
    """A planned answer never smuggles an undeclared request through: past
    the declared count the gate refuses by status, plan or no plan."""
    del tmp
    plan = {
        'planned': [SYNC],
        'answers': {TABS: {'status': 200, 'body': {'ok': True}}},
        'probe': [
            {'method': 'POST', 'url': BRIDGE + '/tabs', 'body': {}},
        ],
    }
    outcome = run_gate(require_node(), _ORACLE_HARNESS, [], cwd=ROOT,
                       plan=plan)
    assert outcome['statuses'] == [599], outcome
    assert outcome['refused'] == [TABS], outcome
    assert outcome['records'][0]['status'] == 599, outcome


def test_a_wrong_typed_plan_answers_table_is_a_contract_fault(tmp):
    """A mis-spelled answers table would otherwise be ignored and every
    declared request answered 200, so the self-check names it."""
    del tmp
    plan = {
        'planned': [SYNC],
        'answers': [OTHER],
        'probe': [
            {'method': 'POST', 'url': BRIDGE + '/sync-tabs', 'body': {}},
        ],
    }
    outcome = run_gate(require_node(), _ORACLE_HARNESS, [], cwd=ROOT,
                       plan=plan)
    assert outcome['contractFaults'] == ['plan.answers'], outcome


# ---- assert_gate_clean's own controls -------------------------------------
# Each drives the helper directly with a record that carries one specific
# defect and requires it to be rejected. A weakening of the helper that lets
# its defect through makes the matching control fail, so these are the
# controls the four re-mutations must each turn red.

def _record(request, status=200):
    return {'request': request, 'status': status}


def _must_reject(**overrides):
    kwargs = {
        'contract_faults': [],
        'records': [_record(SYNC)],
        'refused': [],
        'bad_origins': [],
        'stream_answered': [],
        'planned': [SYNC],
        'planned_stream': [],
    }
    kwargs.update(overrides)
    try:
        assert_gate_clean(**kwargs)
    except AssertionError:
        return
    raise AssertionError(
        f'assert_gate_clean accepted a wrong record: {overrides}')


def test_assert_gate_clean_accepts_a_matching_record(tmp):
    del tmp
    assert_gate_clean(
        contract_faults=[], records=[_record(SYNC)], refused=[],
        bad_origins=[], stream_answered=[], planned=[SYNC],
        planned_stream=[])


def test_assert_gate_clean_rejects_a_missing_entry_of_a_declared_route(tmp):
    """Declared twice, recorded once: sets match, counts do not. Catches a
    membership (`set == set`) weakening, the docstring's rejected option."""
    del tmp
    _must_reject(records=[_record(SYNC)], planned=[SYNC, SYNC])


def test_assert_gate_clean_rejects_a_different_route_at_the_same_count(tmp):
    """Two declared, two recorded, different routes. Catches a count-only
    (`len == len`) weakening, which this fixture satisfies."""
    del tmp
    _must_reject(records=[_record(SYNC), _record(TABS)],
                 planned=[SYNC, OTHER])


def test_assert_gate_clean_rejects_an_extra_undeclared_route(tmp):
    del tmp
    _must_reject(records=[_record(SYNC), _record(TABS)], planned=[SYNC])


def test_assert_gate_clean_rejects_a_bad_origin(tmp):
    """Catches deleting the `bad_origins == []` assertion."""
    del tmp
    _must_reject(bad_origins=[ELSEWHERE])


def test_assert_gate_clean_rejects_a_refused_request(tmp):
    """Catches deleting the `refused == []` assertion."""
    del tmp
    _must_reject(refused=[SYNC])


def test_assert_gate_clean_rejects_a_wrong_stream_list(tmp):
    """Catches deleting or weakening the whole-list stream comparison."""
    del tmp
    _must_reject(stream_answered=[503], planned_stream=[])
    _must_reject(stream_answered=[503, 503], planned_stream=[503])
    _must_reject(stream_answered=[200], planned_stream=[503])


def test_assert_gate_clean_rejects_an_unanswered_request(tmp):
    """A request the gate recorded but never answered (a missing `response`)
    leaves status null, and must be rejected."""
    del tmp
    _must_reject(records=[{'request': SYNC, 'status': None}])


def test_assert_gate_clean_rejects_a_contract_fault(tmp):
    del tmp
    _must_reject(contract_faults=['streamResponse'])


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='bridgefakeoracle_')


if __name__ == '__main__':
    raise SystemExit(main())
