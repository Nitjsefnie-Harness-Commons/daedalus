#!/usr/bin/env python3
"""The worker's origin-allowlisted segment-job mint and its allowlist.

A page reaches `POST /segment-job` only through the service worker, and the
worker mints only for an origin an operator has allowed. These run the
shipped worker in a Node VM against a fake browser, with the shared bridge
gate in place: every scenario declares the exact requests it makes, the gate
answers only those, and a request outside the plan is refused by status and
recorded — so an invented fetch, a foreign origin or a request the scenario
never planned fails the scenario instead of answering 200 to nobody.
"""
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)
from _worker_chrome_fake import INERT_WORKER_APIS  # noqa: E402
from _worker_sources import RELAY_CONTEXT  # noqa: E402

ORIGINS_KEY = 'daedalus-segment-origins'
ALLOWED = 'https://allowed.example.com'
OTHER = 'https://other.example.com'
SERVER = 'https://bridge.example.com'
TOKEN = 'mint-token'
INVALID_ORIGIN = 'Missing or invalid origin (http(s) origin required)'
SEGMENT = 'POST /segment-job'
SYNC = 'POST /sync-tabs'
RESULT = 'POST /result'
# Every scenario's recording, from a run of the shipped worker: boot opens the
# stream and syncs the tab list, then the scenario's own work posts its
# result. A scenario with no stored bridge URL never gets boot past its
# config, and declares no request at all. The boot stream fetch is answered
# 503 and is declared and asserted like the accounted routes.
BOOT_STREAM = (503,)
MINT_PLAN = [SYNC, SEGMENT]
REFUSED_PLAN = [SYNC]

_MINT_HARNESS = (r"""
const fs = require('fs');
const vm = require('vm');

// run_node_program pushes the payload as an object literal, not text.
const [backgroundPath, plan] = process.argv.slice(1);
const messageListeners = [];
let storageBroken = false;
let releaseWrite = null;
const storageStore = Object.assign({
  'daedalus-token': '__TOKEN__',
  'daedalus-server': '__SERVER__',
}, plan.store || {});

function copy(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => {
      if (data === null) throw new SyntaxError('not JSON');
      return data;
    },
    text: async () => JSON.stringify(data),
  };
}

function eventTarget(listeners = null) {
  return {
    addListener(listener) {
      if (listeners) listeners.push(listener);
    },
  };
}

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        if (storageBroken) throw new Error('storage unavailable');
        const out = {};
        for (const key of [].concat(keys)) {
          if (key in storageStore) out[key] = copy(storageStore[key]);
        }
        return out;
      },
      set: async (entries) => {
        // The first write parks until the harness releases it, so a read
        // dispatched behind it observes either the lock or its absence.
        if (plan.holdFirstWrite && releaseWrite === null) {
          await new Promise((resolve) => { releaseWrite = resolve; });
        }
        for (const key of Object.keys(entries)) {
          storageStore[key] = copy(entries[key]);
        }
      },
      remove: async (keys) => {
        for (const key of [].concat(keys)) delete storageStore[key];
      },
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_query, callback) {
      if (callback) {
        callback([]);
        return undefined;
      }
      return Promise.resolve([]);
    },
    get: async (tabId) => ({ id: tabId, url: '', title: '' }),
    sendMessage: async () => {},
    create(_details, callback) { callback({ id: 101 }); },
  },
""" + INERT_WORKER_APIS + r"""
};

// The shared gate's in-scope contract. The gate answers only what the
// scenario declared and records every request it sees. No route is
// special-cased: the control plane the old fake waved through at boot is
// declared in the plan like any other.
const BRIDGE_URL = '__SERVER__';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
function streamResponse(answer) {
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + RELAY_CONTEXT + r"""

// One message, one answer, as content.js relays for a page. The sender is
// the plan's, because what the worker trusts is exactly what Chrome puts
// there — a test that supplied its own tab URL would be testing itself.
//
// Chrome keeps the reply channel open past the listener's return only
// when the listener returned `true`; otherwise the channel closes as it
// returns, a later sendResponse is dropped, and the sender gets
// undefined. A branch that forgets `return true` is therefore dead in the
// browser, and this harness answers exactly as the browser would.
function send(message) {
  return new Promise((resolve, reject) => {
    try {
      let open = true;
      let kept = false;
      for (const listener of messageListeners) {
        const returned = listener(message, plan.sender, (answer) => {
          if (open) resolve(answer);
        });
        if (returned === true) kept = true;
      }
      if (!kept) {
        open = false;
        setImmediate(() => resolve(undefined));
      }
    } catch (error) {
      reject(error);
    }
  });
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);
  storageBroken = Boolean(plan.storageThrows);
  const answers = [];
  for (const message of plan.messages || []) {
    answers.push(await send(message));
  }
  for (const command of plan.commands || []) {
    context.nextCommand = command;
    await vm.runInContext('dispatchCommand(nextCommand)', context);
  }
  context.concurrentCommands = plan.concurrent || [];
  await vm.runInContext(
    'Promise.all(concurrentCommands.map(dispatchCommand))', context);
  context.heldSequence = plan.heldSequence || [];
  if (context.heldSequence.length > 0) {
    const pending = vm.runInContext(
      'heldSequence.map(dispatchCommand)', context);
    for (let attempt = 0; releaseWrite === null; attempt++) {
      if (attempt === 1000) throw new Error('no write was ever held');
      await new Promise((resolve) => setImmediate(resolve));
    }
    releaseWrite();
    await Promise.all(pending);
  }
  const stored = storageStore['__ORIGINS_KEY__'];
  return {
    answers,
    posted: resultPosts.map((item) => ({
      id: item.id, result: item.result, error: item.error,
    })),
    stored: stored === undefined ? null : stored,
    records: nonStreamFetches,
    refused: refusedFetches,
    badOrigins,
    streamAnswered: streamFetches.map((f) => f.answered),
    contractFaults: gateContractFaults,
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""").replace('__TOKEN__', TOKEN).replace('__SERVER__', SERVER).replace(
    '__ORIGINS_KEY__', ORIGINS_KEY)


def _run_mint(plan, planned_stream=BOOT_STREAM):
    """Drive the worker under Node with one plan and read back.

    The scenario's declared requests are checked against what the gate
    recorded, so a request outside the plan is refused by status and fails
    here, whatever the worker does with the refusal.
    """
    outcome = run_gate(require_node(), _MINT_HARNESS,
                       [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT,
                       plan=plan)
    assert_gate_clean(
        contract_faults=outcome['contractFaults'],
        records=outcome['records'], refused=outcome['refused'],
        bad_origins=outcome['badOrigins'],
        stream_answered=outcome['streamAnswered'],
        planned=plan['planned'],
        planned_stream=list(planned_stream))
    return outcome


def _mint_requests(outcome, route):
    return [record for record in outcome['records']
            if record['request'] == route]


def _sender(origin: Optional[str] = ALLOWED):
    sender = {'tab': {'id': 7}, 'url': ALLOWED + '/play'}
    if origin is not None:
        sender['origin'] = origin
    return sender


def _mint_plan(allowlist, sender, message, answer=None, planned=None,
               **extra):
    """A mint scenario's plan: boot's sync plus the mint post.

    `answer` is the bridge answer the scenario planned for the mint post —
    a status and body, or a throw for a bridge it models as unreachable.
    `planned` names the requests for a scenario that never reaches the
    bridge at all (its default), or names none when no bridge URL is
    configured and boot itself makes no request.
    """
    plan = {
        'store': {} if allowlist is None else {ORIGINS_KEY: allowlist},
        'sender': sender,
        'messages': [message],
        'planned': list(REFUSED_PLAN if planned is None else planned),
    }
    if answer is not None:
        plan['answers'] = {SEGMENT: answer}
    plan.update(extra)
    return plan


def _command(command_type, **fields):
    command = {'id': 'x', 'type': command_type, '_did': 'did-x'}
    command.update(fields)
    return command


def _refused(outcome, message):
    """A refusal is exactly `{error}`, and the plan proved it never reached
    the bridge: a mint post outside the scenario's plan is refused by the
    gate, which the whole-list check would have failed on."""
    assert len(outcome['answers']) == 1, outcome
    answer = outcome['answers'][0]
    assert isinstance(answer, dict), f'reply channel dropped: {answer!r}'
    assert list(answer) == ['error'], answer
    assert answer['error'] == message, answer


def test_a_listed_origin_gets_a_sig_through_one_bridge_post(tmp):
    """The round trip: allowlisted sender, one POST, answer exactly {sig}."""
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        answer={'status': 200, 'body': {'ok': True, 'sig': 'SIG1'}},
        planned=MINT_PLAN))
    mints = _mint_requests(outcome, SEGMENT)
    assert len(mints) == 1, outcome
    request = mints[0]
    assert request['auth'] == 'Bearer ' + TOKEN, request
    assert request['body'] == {'token': TOKEN, 'job': 'job_1'}, request
    assert outcome['answers'] == [{'sig': 'SIG1'}], outcome
    assert list(outcome['answers'][0]) == ['sig'], outcome


def test_an_unlisted_origin_is_refused_without_a_fetch(tmp):
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(OTHER), {'type': 'segmentJob', 'job': 'job_1'}))
    _refused(outcome, 'origin not allowed')


def test_an_empty_or_absent_allowlist_refuses_every_origin(tmp):
    del tmp
    for allowlist in ([], None):
        outcome = _run_mint(_mint_plan(
            allowlist, _sender(), {'type': 'segmentJob', 'job': 'job_1'}))
        _refused(outcome, 'origin not allowed')


def test_an_unlisted_origin_learns_nothing_else(tmp):
    """The allowlist is checked before the job and the bridge config.

    An unlisted page with a bad job or an unconfigured bridge hears
    `origin not allowed`, never `Missing job` or `bridge not configured`.
    """
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(OTHER), {'type': 'segmentJob'}))
    _refused(outcome, 'origin not allowed')
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(OTHER), {'type': 'segmentJob', 'job': ''}))
    _refused(outcome, 'origin not allowed')
    plan = _mint_plan(
        [ALLOWED], _sender(OTHER), {'type': 'segmentJob', 'job': 'job_1'},
        planned=[])
    plan['store']['daedalus-server'] = ''
    _refused(_run_mint(plan, planned_stream=()), 'origin not allowed')


def test_a_sender_without_an_origin_is_refused(tmp):
    """No fallback to the sender's tab or URL: absent origin fails closed."""
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(None), {'type': 'segmentJob', 'job': 'job_1'}))
    _refused(outcome, 'origin not allowed')


def test_a_stored_entry_is_compared_verbatim_never_canonicalised(tmp):
    """The store is canonical by construction; the reader trusts it as is.

    The mint canonicalises the sender's origin, so a non-canonical spelling
    can only be on the stored side. An entry with a path or an uppercase
    host is not what the sender's origin will ever equal, and the reader
    must not rescue it by canonicalising what it read.
    """
    del tmp
    for spelling in ('https://Allowed.example.com', ALLOWED + '/',
                     ALLOWED + '/play'):
        outcome = _run_mint(_mint_plan(
            [spelling], _sender(), {'type': 'segmentJob', 'job': 'job_1'}))
        _refused(outcome, 'origin not allowed')


def test_a_missing_empty_or_non_string_job_is_refused(tmp):
    del tmp
    for message in ({'type': 'segmentJob'},
                    {'type': 'segmentJob', 'job': ''},
                    {'type': 'segmentJob', 'job': 42},
                    {'type': 'segmentJob', 'job': ['job_1']}):
        outcome = _run_mint(_mint_plan([ALLOWED], _sender(), message))
        _refused(outcome, 'Missing job')


def test_an_unconfigured_bridge_is_reported_without_a_fetch(tmp):
    """No server URL: say so rather than fetch a relative path.

    The plan names no request: without a bridge URL the worker is never
    configured, so boot opens no stream and syncs no tabs — a claim the
    gate now checks, which the old fake's blanket 200 could not.
    """
    del tmp
    plan = _mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        planned=[])
    plan['store']['daedalus-server'] = ''
    _refused(_run_mint(plan, planned_stream=()), 'bridge not configured')


def test_a_bridge_refusal_is_reported_with_its_status(tmp):
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        answer={'status': 403, 'body': {'error': 'forbidden'}},
        planned=MINT_PLAN))
    assert len(_mint_requests(outcome, SEGMENT)) == 1, outcome
    assert outcome['answers'] == [{'error': 'segment-job refused (403)'}], (
        outcome)


def test_a_bridge_answer_without_a_sig_is_an_error(tmp):
    del tmp
    for body in ({'ok': True}, {'ok': True, 'sig': ''},
                 {'ok': True, 'sig': 7}, None):
        outcome = _run_mint(_mint_plan(
            [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
            answer={'status': 200, 'body': body}, planned=MINT_PLAN))
        assert len(_mint_requests(outcome, SEGMENT)) == 1, outcome
        assert outcome['answers'] == [
            {'error': 'segment-job answered no sig'}], (body, outcome)


def test_an_unreachable_bridge_is_reported_with_its_reason(tmp):
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        answer={'throw': 'Failed to fetch'}, planned=MINT_PLAN))
    assert outcome['answers'] == [
        {'error': 'segment-job unreachable: Failed to fetch'}], outcome


def test_a_storage_failure_answers_an_error_not_silence(tmp):
    """A throwing store still answers: a page waiting forever is the bug."""
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        storageThrows=True))
    _refused(outcome, 'storage unavailable')


def _commands(commands, store=None, concurrent=None):
    """Dispatch commands, declaring boot's sync plus one result per command.

    The count is the recording: every dispatched command posts its result,
    so a scenario that dispatches a third command the plan does not name is
    refused by the gate rather than waved through.
    """
    dispatched = len(commands) + len(concurrent or [])
    plan = {'store': store or {}, 'commands': commands,
            'planned': [SYNC] + [RESULT] * dispatched}
    if concurrent:
        plan['concurrent'] = concurrent
    return _run_mint(plan)


def test_allow_canonicalises_and_reports_the_addition(tmp):
    del tmp
    outcome = _commands([_command(
        'allow-segment-origin',
        origin='https://Allowed.example.com/some/path?q=1')])
    assert outcome['posted'] == [{
        'id': 'x', 'error': None,
        'result': {'origin': ALLOWED, 'origins': [ALLOWED], 'added': True},
    }], outcome
    assert outcome['stored'] == [ALLOWED], outcome


def test_allowing_a_listed_origin_again_changes_nothing(tmp):
    del tmp
    outcome = _commands(
        [_command('allow-segment-origin', origin=ALLOWED + '/')],
        store={ORIGINS_KEY: [ALLOWED]})
    assert outcome['posted'] == [{
        'id': 'x', 'error': None,
        'result': {'origin': ALLOWED, 'origins': [ALLOWED], 'added': False},
    }], outcome
    assert outcome['stored'] == [ALLOWED], outcome


def test_allow_refuses_anything_but_an_http_origin(tmp):
    """Each refusal names the exact error and leaves the store as it was."""
    del tmp
    cases = [{'origin': value} for value in (
        'ftp://x', 'allowed.example.com', 'chrome-extension://abc', '', 42)]
    cases.append({})
    for fields in cases:
        outcome = _commands(
            [_command('allow-segment-origin', **fields)],
            store={ORIGINS_KEY: [ALLOWED]})
        assert outcome['posted'] == [{
            'id': 'x', 'result': None, 'error': INVALID_ORIGIN,
        }], (fields, outcome)
        assert outcome['stored'] == [ALLOWED], (fields, outcome)
        # The same refusal on an empty store creates nothing.
        outcome = _commands([_command('allow-segment-origin', **fields)])
        assert outcome['posted'][0]['error'] == INVALID_ORIGIN, (
            fields, outcome)
        assert outcome['stored'] is None, (fields, outcome)


def test_revoke_removes_a_listed_origin_and_reports_found(tmp):
    del tmp
    outcome = _commands(
        [_command('revoke-segment-origin', origin=ALLOWED + '/x')],
        store={ORIGINS_KEY: [ALLOWED, OTHER]})
    assert outcome['posted'] == [{
        'id': 'x', 'error': None,
        'result': {'origin': ALLOWED, 'origins': [OTHER], 'found': True},
    }], outcome
    assert outcome['stored'] == [OTHER], outcome


def test_revoking_an_absent_origin_reports_not_found(tmp):
    del tmp
    outcome = _commands(
        [_command('revoke-segment-origin', origin=OTHER)],
        store={ORIGINS_KEY: [ALLOWED]})
    assert outcome['posted'] == [{
        'id': 'x', 'error': None,
        'result': {'origin': OTHER, 'origins': [ALLOWED], 'found': False},
    }], outcome
    assert outcome['stored'] == [ALLOWED], outcome


def test_revoke_refuses_an_invalid_origin(tmp):
    del tmp
    for fields in ({'origin': 'ftp://x'}, {'origin': ''}, {}):
        outcome = _commands(
            [_command('revoke-segment-origin', **fields)],
            store={ORIGINS_KEY: [ALLOWED]})
        assert outcome['posted'] == [{
            'id': 'x', 'result': None, 'error': INVALID_ORIGIN,
        }], (fields, outcome)
        assert outcome['stored'] == [ALLOWED], (fields, outcome)


def test_list_reports_the_stored_origins(tmp):
    del tmp
    outcome = _commands(
        [_command('list-segment-origins')],
        store={ORIGINS_KEY: [ALLOWED, OTHER]})
    assert outcome['posted'] == [{
        'id': 'x', 'error': None, 'result': {'origins': [ALLOWED, OTHER]},
    }], outcome
    outcome = _commands([_command('list-segment-origins')])
    assert outcome['posted'] == [{
        'id': 'x', 'error': None, 'result': {'origins': []},
    }], outcome


def test_origins_added_out_of_order_are_stored_sorted(tmp):
    del tmp
    outcome = _commands([
        _command('allow-segment-origin', origin=OTHER, id='first'),
        _command('allow-segment-origin', origin=ALLOWED, id='second'),
    ])
    assert outcome['stored'] == [ALLOWED, OTHER], outcome
    assert outcome['posted'][1]['result']['origins'] == [ALLOWED, OTHER], (
        outcome)


def test_two_allows_in_flight_at_once_both_land(tmp):
    """Two read-modify-write cycles dispatched together must serialise.

    Without the lock both read the empty store, both answer `added`, and
    only the later write survives.
    """
    del tmp
    outcome = _commands([], concurrent=[
        _command('allow-segment-origin', origin=ALLOWED, id='a'),
        _command('allow-segment-origin', origin=OTHER, id='b'),
    ])
    assert outcome['stored'] == [ALLOWED, OTHER], outcome
    by_id = {item['id']: item for item in outcome['posted']}
    assert sorted(by_id) == ['a', 'b'], outcome
    for item in by_id.values():
        assert item['error'] is None, item
        assert item['result']['added'] is True, item


def test_a_list_behind_an_in_flight_allow_sees_the_write(tmp):
    """A list dispatched while an allow's write is pending waits for it.

    The stream dispatches frames without awaiting, so an operator's
    allow-then-list arrives back to back. The fake parks the allow's
    storage write until both commands are in flight; a list outside the
    lock reads the store before that write and answers the old array.
    """
    del tmp
    outcome = _run_mint({
        'store': {},
        'holdFirstWrite': True,
        'planned': [SYNC, RESULT, RESULT],
        'heldSequence': [
            _command('allow-segment-origin', origin=ALLOWED, id='allow'),
            _command('list-segment-origins', id='list'),
        ],
    })
    by_id = {item['id']: item for item in outcome['posted']}
    assert by_id['allow']['result']['added'] is True, outcome
    assert by_id['list'] == {
        'id': 'list', 'error': None, 'result': {'origins': [ALLOWED]},
    }, outcome
    assert outcome['stored'] == [ALLOWED], outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='segmentmint_')


if __name__ == '__main__':
    raise SystemExit(main())
