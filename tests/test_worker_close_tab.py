#!/usr/bin/env python3
"""The worker's close-tab shape validation and per-tab result contract.

The faked removal is delivered to the worker's real onRemoved listeners,
which is what makes the requests a closed tab produces observable here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)
from _worker_chrome_fake import INERT_WORKER_APIS  # noqa: E402
from _worker_sources import RELAY_CONTEXT, event_target_stub  # noqa: E402

TOKEN = 'close-token'
SERVER = 'https://bridge.example.com'
SYNC = 'POST /sync-tabs'
UNREGISTER = 'POST /unregister'
RESULT = 'POST /result'
# The boot's stream answer, and the reason it never reconnects here.
BOOT_STREAM = (503,)
# The tab the attachment controls capture; no other scenario here uses it.
CAPTURED_TAB = 9


def _planned_requests(unregistered=0, results=1):
    """The routes a scenario's own removals put on the bridge.

    Each closed tab unregisters through the worker's own onRemoved listener,
    and the listener's deferred full sync is coalesced across the burst, so
    a run that closed N tabs shows N unregisters and ONE extra sync however
    many timers it armed. A scenario that closed nothing pays neither. One
    result post per dispatched command.
    """
    requests = [SYNC] + [UNREGISTER] * unregistered
    if unregistered:
        requests.append(SYNC)
    return requests + [RESULT] * results


_CLOSE_TAB_HARNESS = (r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, plan] = process.argv.slice(1);
const removeCalls = [];
// Attachment calls and the closures that make them releasable, in order, so
// a control can say which came first and not only that something happened.
const attachmentLog = [];
const messageListeners = [];
const storageStore = {
  'daedalus-token': '__TOKEN__',
  'daedalus-server': '__SERVER__',
};

function copy(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

// The window a removal reports. A constant of this harness's own rather than
// one derived from the tab id the scenario supplied, so the stand-in can
// never answer with the caller's own value; no control reads it yet.
const REMOVED_WINDOW_ID = 42;
// The only target that fires: nothing else this suite dispatches, and an
// opt-in target is the only way a listener runs here at all.
const onRemovedTarget = eventTarget([], true);

// The timer the removal listener defers its registry sync behind. A timer
// armed WHILE an event is being dispatched is collected and run once the
// commands settle, so a burst of removals coalesces into the one sync the
// worker's own guard admits. Every other timer stays inert, so the
// stream's reconnect cannot loop.
const deferredTimers = [];
let dispatchingEvent = false;
function setTimeoutStandIn(callback, _delay) {
  if (!dispatchingEvent) return 1;
  deferredTimers.push(callback);
  return deferredTimers.length;
}

""" + event_target_stub() + r"""
const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const key of [].concat(keys)) {
          if (key in storageStore) out[key] = copy(storageStore[key]);
        }
        return out;
      },
      set: async (entries) => {
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
    onRemoved: onRemovedTarget,
    query(_query, callback) {
      if (plan.failQuery && _query.active) {
        throw new Error('planned chrome.tabs.query rejection');
      }
      if (callback) {
        callback([]);
        return undefined;
      }
      return Promise.resolve([]);
    },
    get: async () => {
      throw new Error('unmodelled chrome.tabs.get');
    },
    remove: async (tabId) => {
      removeCalls.push(tabId);
      const rejects = plan.reject || {};
      const message = rejects[String(tabId)];
      if (message !== undefined) throw new Error(message);
      // Chrome's own signature is (tabId, removeInfo), and a removal that
      // refused above never reaches this line.
      dispatchingEvent = true;
      try {
        onRemovedTarget.dispatch(tabId, {
          windowId: REMOVED_WINDOW_ID, isWindowClosing: false,
        });
      } finally {
        dispatchingEvent = false;
      }
    },
    sendMessage: async () => {
      throw new Error('unmodelled chrome.tabs.sendMessage');
    },
    create() { throw new Error('unmodelled chrome.tabs.create'); },
  },
""" + INERT_WORKER_APIS + r"""
};

// A local attach/detach over the shared inert fixture, which refuses to
// attach at all, so no capture could be live and a close had nothing to
// release. Overridden HERE, not in tests/_worker_chrome_fake.py: 8 suites
// import that fixture and none wants a different meaning of attach.
// `afterCloses` is read off removeCalls, so the log and the closure census
// cannot disagree about what a close was.
chrome.debugger.attach = async (target, version) => {
  attachmentLog.push({ api: 'attach', tabId: target.tabId, version,
                       afterCloses: removeCalls.length });
  return { attached: true };
};
chrome.debugger.detach = (target) => {
  attachmentLog.push({ api: 'detach', tabId: target.tabId,
                       afterCloses: removeCalls.length });
};

// The shared gate's in-scope contract. The gate answers only what the
// scenario declared and records every request it sees.
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
// The shared context's timer is inert, which would strand the removal
// listener's deferred sync; the stand-in above runs only what an event
// dispatch armed, so nothing else the worker defers comes with it.
context.setTimeout = setTimeoutStandIn;

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);
  const outcomes = [];
  for (const command of plan.commands || []) {
    context.nextCommand = command;
    try {
      await vm.runInContext('dispatchCommand(nextCommand)', context);
      outcomes.push({ settled: 'resolved' });
    } catch (error) {
      outcomes.push({ settled: 'rejected', message: error.message });
    }
  }
  for (const deferred of deferredTimers.splice(0)) deferred();
  return {
    removes: removeCalls,
    attachmentLog,
    posted: resultPosts.map((p) => ({
      id: p.id, tabId: p.tabId, result: p.result, error: p.error,
    })),
    outcomes,
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
""").replace('__TOKEN__', TOKEN).replace('__SERVER__', SERVER)


def _run_close_tab(command, reject=None, fail_query=False, unregistered=0,
                   results=None, planned_stream=BOOT_STREAM):
    commands = list(command) if isinstance(command, (list, tuple)) else [
        command]
    # One result post per dispatched command, so the count is derived rather
    # than hand-written into each scenario; only a command that posts
    # nothing says so.
    posts = len(commands) if results is None else results
    plan = {
        'commands': commands,
        'planned': _planned_requests(unregistered, posts),
    }
    if reject is not None:
        plan['reject'] = reject
    if fail_query:
        plan['failQuery'] = True
    outcome = run_gate(require_node(), _CLOSE_TAB_HARNESS,
                       [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT,
                       plan=plan)
    assert_gate_clean(
        contract_faults=outcome['contractFaults'],
        records=outcome['records'], refused=outcome['refused'],
        bad_origins=outcome['badOrigins'],
        stream_answered=outcome['streamAnswered'],
        planned=plan['planned'], planned_stream=list(planned_stream))
    return {
        'removes': outcome['removes'],
        'posted': outcome['posted'],
        'outcomes': outcome['outcomes'],
        'attachments': outcome['attachmentLog'],
        'unregisters': [
            record['body']['tabId'] for record in outcome['records']
            if record['request'] == UNREGISTER],
    }


def _command(**fields):
    command = {'id': 'close-1', 'type': 'close-tab', '_did': 'did-close'}
    command.update(fields)
    return command


# The answer fields a control is about, projected so an assertion never
# depends on a field nothing here can tell a real value from a typed one.
_ANSWER_FIELDS = ('capturing', 'already', 'tabId', 'closed', 'errors')


def _answer(post):
    return {key: post['result'][key] for key in _ANSWER_FIELDS
            if key in post['result']}


def _capture(identifier):
    return {'id': identifier, 'type': 'net-capture', 'tabId': CAPTURED_TAB,
            '_did': 'did-' + identifier}


# One tab, captured through the public surface, closed, captured again. The
# second capture answers `already` only while the first is live, so the pair
# separates "the close released it" from "nothing was ever held".
def _capture_then_close():
    return _run_close_tab([
        _capture('cap-1'),
        _capture('cap-2'),
        _command(tabIds=[CAPTURED_TAB]),
        _capture('cap-3'),
    ], unregistered=1)


# The other limb of netcapture's `held`: a kept CDP session, opened through
# the public surface the same way, so the close has a session to release and
# no capture behind it.
def _keep_session(identifier):
    return {'id': identifier, 'type': 'cdp', 'tabId': CAPTURED_TAB,
            'method': 'Runtime.enable', 'keep_session': True,
            '_did': 'did-' + identifier}


def _session_then_close():
    return _run_close_tab([
        _keep_session('cdp-1'),
        _command(tabIds=[CAPTURED_TAB]),
    ], unregistered=1)


def _assert_wrong_shape(value):
    outcome = _run_close_tab(_command(tabIds=value))
    assert outcome == {
        'removes': [],
        'posted': [{
            'id': 'close-1',
            'tabId': 'extension',
            'result': None,
            'error': 'tabIds must be an array',
        }],
        'outcomes': [{'settled': 'resolved'}],
        'attachments': [],
        'unregisters': [],
    }, outcome


def test_string_tab_ids_is_rejected_without_removing_tabs(tmp):
    del tmp
    _assert_wrong_shape('7')


def test_zero_tab_ids_is_rejected_as_present_wrong_shape(tmp):
    del tmp
    _assert_wrong_shape(0)


def test_false_tab_ids_is_rejected_as_present_wrong_shape(tmp):
    del tmp
    _assert_wrong_shape(False)


def test_true_tab_ids_is_rejected_as_present_wrong_shape(tmp):
    del tmp
    _assert_wrong_shape(True)


def test_nonzero_tab_ids_is_rejected_as_present_wrong_shape(tmp):
    del tmp
    _assert_wrong_shape(7)


def test_object_tab_ids_is_rejected_without_removing_tabs(tmp):
    del tmp
    _assert_wrong_shape({})


def test_wrong_shape_tab_ids_is_rejected_even_with_tab_id(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabId=5, tabIds='x'))
    assert outcome['removes'] == [], outcome
    assert outcome['outcomes'] == [{'settled': 'resolved'}], outcome
    assert outcome['posted'] == [{
        'id': 'close-1',
        'tabId': 'extension',
        'result': None,
        'error': 'tabIds must be an array',
    }], outcome
    assert outcome['unregisters'] == [], outcome
    assert outcome['attachments'] == [], outcome


def test_settlement_recorder_captures_rejected_eval_dispatch(tmp):
    del tmp
    outcome = _run_close_tab(
        _command(type='eval', code='1'), fail_query=True, results=0)
    assert outcome == {
        'removes': [],
        'posted': [],
        'outcomes': [{
            'settled': 'rejected',
            'message': 'planned chrome.tabs.query rejection',
        }],
        'attachments': [],
        'unregisters': [],
    }, outcome


def test_mixed_type_tab_ids_are_parsed_and_closed_in_order(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabIds=[1, '2']), unregistered=2)
    assert outcome == {
        'removes': [1, 2],
        'posted': [{
            'id': 'close-1',
            'tabId': 'extension',
            'result': {'closed': [1, 2], 'errors': []},
            'error': None,
        }],
        'outcomes': [{'settled': 'resolved'}],
        'attachments': [],
        'unregisters': ['1', '2'],
    }, outcome


def test_empty_tab_ids_answers_empty_close_result(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabIds=[]))
    assert outcome == {
        'removes': [],
        'posted': [{
            'id': 'close-1',
            'tabId': 'extension',
            'result': {'closed': [], 'errors': []},
            'error': None,
        }],
        'outcomes': [{'settled': 'resolved'}],
        'attachments': [],
        'unregisters': [],
    }, outcome


def test_tab_id_alone_is_closed(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabId=5), unregistered=1)
    assert outcome == {
        'removes': [5],
        'posted': [{
            'id': 'close-1',
            'tabId': 'extension',
            'result': {'closed': [5], 'errors': []},
            'error': None,
        }],
        'outcomes': [{'settled': 'resolved'}],
        'attachments': [],
        'unregisters': ['5'],
    }, outcome


def test_null_tab_ids_falls_back_to_tab_id(tmp):
    del tmp
    outcome = _run_close_tab(
        _command(tabId=5, tabIds=None), unregistered=1)
    assert outcome == {
        'removes': [5],
        'posted': [{
            'id': 'close-1',
            'tabId': 'extension',
            'result': {'closed': [5], 'errors': []},
            'error': None,
        }],
        'outcomes': [{'settled': 'resolved'}],
        'attachments': [],
        'unregisters': ['5'],
    }, outcome


def test_missing_tab_ids_and_tab_id_answers_missing_error(tmp):
    del tmp
    outcome = _run_close_tab(_command())
    assert outcome == {
        'removes': [],
        'posted': [{
            'id': 'close-1',
            'tabId': 'extension',
            'result': None,
            'error': 'Missing tabId or tabIds',
        }],
        'outcomes': [{'settled': 'resolved'}],
        'attachments': [],
        'unregisters': [],
    }, outcome


def test_one_remove_error_is_reported_while_other_tabs_close(tmp):
    del tmp
    outcome = _run_close_tab(
        _command(tabIds=[1, '2', 3]), reject={'2': 'cannot close 2'},
        unregistered=2)
    assert outcome == {
        'removes': [1, 2, 3],
        'posted': [{
            'id': 'close-1',
            'tabId': 'extension',
            'result': {
                'closed': [1, 3],
                'errors': [{'id': 2, 'error': 'cannot close 2'}],
            },
            'error': None,
        }],
        'outcomes': [{'settled': 'resolved'}],
        'attachments': [],
        'unregisters': ['1', '3'],
    }, outcome


def test_closing_a_captured_tab_releases_its_debugger_attachment(tmp):
    del tmp
    outcome = _capture_then_close()
    assert outcome['attachments'] == [
        {'api': 'attach', 'tabId': 9, 'version': '1.3', 'afterCloses': 0},
        {'api': 'detach', 'tabId': 9, 'afterCloses': 1},
        {'api': 'attach', 'tabId': 9, 'version': '1.3', 'afterCloses': 1},
    ], outcome
    assert outcome['removes'] == [9], outcome


def test_a_capture_does_not_outlive_the_tab_it_was_taken_on(tmp):
    del tmp
    outcome = _capture_then_close()
    # The fields the claim is about. `buffered` is left out: nothing here
    # can tell a real zero from a typed one, so asserting it pins a constant.
    assert [_answer(post) for post in outcome['posted']] == [
        {'capturing': True, 'tabId': 9},
        {'already': True, 'tabId': 9},
        {'closed': [9], 'errors': []},
        {'capturing': True, 'tabId': 9},
    ], outcome


def test_closing_a_tab_with_a_kept_cdp_session_releases_its_attachment(tmp):
    del tmp
    outcome = _session_then_close()
    assert outcome['attachments'] == [
        {'api': 'attach', 'tabId': 9, 'version': '1.3', 'afterCloses': 0},
        {'api': 'detach', 'tabId': 9, 'afterCloses': 1},
    ], outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='closetab_')


if __name__ == '__main__':
    raise SystemExit(main())
