#!/usr/bin/env python3
"""One debugger attachment per tab, whoever asked for it.

Chrome allows one debugger on a tab. A second `chrome.debugger.attach` while
one is live is refused with `Another debugger is already attached`, and that
refusal reaches the CALLER as a failed command — so before this claim map
existed, two `cdp` commands dispatched in the same turn both attached and one
of the two commands the caller issued came back an error. Nothing was
misrouted; a call simply failed.

The double here refuses a second attach exactly as Chrome does, per tab, and
holds a tab as attached until its detach PROMISE settles rather than until
the call is made. That second half is what makes the detach/attach window
observable: a claim arriving between the detach call and its settlement is
the one case where an attach issued now is refused by the very detach that
is about to make room for it.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _noderun import run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402

# Chrome's own words, so a control that reads the failure reads what the
# browser says rather than a spelling only this suite would produce.
REFUSAL = 'Another debugger is already attached'

_ATTACHMENT_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');

const backgroundPath = process.argv[1];
const spec = JSON.parse(process.argv[process.argv.length - 1]);

const attachCalls = [];
const detachCalls = [];
const order = [];
const live = new Set();
const settling = new Set();
const posted = [];
let attachFailures = spec.attachFailures || 0;
let enableFailures = spec.enableFailures || 0;
let dispatchInDetach = spec.dispatchInDetach || null;
let inFlight = [];
let commandSeq = 0;

function turn() {
  return new Promise((resolve) => setImmediate(resolve));
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

function eventTarget(listeners) {
  return { addListener(listener) { listeners.push(listener); } };
}

const tabRemoved = [];
const detachEvents = [];

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'attach-token',
        'daedalus-server': 'https://bridge.example.com',
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget([]),
  },
  tabs: {
    onUpdated: eventTarget([]),
    onCreated: eventTarget([]),
    onRemoved: eventTarget(tabRemoved),
    query: async () => [{ id: 7, url: 'https://page.example.com/',
                          title: 'Page' }],
    get: async (id) => ({ id, url: 'https://page.example.com/', title: 'P' }),
  },
  debugger: {
    onEvent: eventTarget([]),
    onDetach: eventTarget(detachEvents),
    attach(target) {
      const tabId = target.tabId;
      attachCalls.push(tabId);
      order.push('attach:' + tabId);
      if (attachFailures > 0) {
        attachFailures -= 1;
        return Promise.reject(new Error('debugger refused the attach'));
      }
      // A tab stays held until the DETACH settles, not until the call is
      // made: that is the window a claim has to chain through.
      if (live.has(tabId) || settling.has(tabId)) {
        return Promise.reject(new Error(
          'Another debugger is already attached'));
      }
      live.add(tabId);
      return turn();
    },
    detach(target) {
      const tabId = target.tabId;
      detachCalls.push(tabId);
      order.push('detach:' + tabId);
      live.delete(tabId);
      settling.add(tabId);
      // The new command is dispatched from inside the detach call, so it
      // arrives in the same turn as the release that issued it.
      if (dispatchInDetach) {
        const command = dispatchInDetach;
        dispatchInDetach = null;
        dispatch(command);
      }
      return turn().then(() => { settling.delete(tabId); });
    },
    sendCommand(_target, method) {
      if (method === 'Network.enable' && enableFailures > 0) {
        enableFailures -= 1;
        return Promise.reject(new Error('Network.enable failed'));
      }
      return Promise.resolve({ result: { value: method } });
    },
  },
  scripting: {
    executeScript: async () => { throw new Error('unmodelled executeScript'); },
  },
  cookies: { getAll: async () => [], remove: async () => null },
  declarativeNetRequest: {
    getSessionRules: async () => [],
    updateSessionRules: async () => {},
  },
  runtime: {
    lastError: null,
    onMessage: eventTarget([]),
    onConnect: eventTarget([]),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: { onAlarm: eventTarget([]), create() {} },
};

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.endsWith('/result') && init && init.method === 'POST') {
      posted.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    // Park the boot stream: a worker-side reconnect would arm a timer a
    // control could mistake for the claim it is here to observe.
    if (url.includes('/stream')) return new Promise(() => {});
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'attach-' + (++commandSeq) },
  AbortController,
  TextDecoder,
  TextEncoder,
  URL,
  performance,
  btoa,
  atob,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
""" + import_scripts_stub('context') + r"""

function dispatch(command) {
  const full = Object.assign(
    { _did: 'did-x' }, command);
  context.stepCommand = full;
  inFlight.push(
    vm.runInContext('dispatchCommand(stepCommand)', context)
      .then(() => undefined));
  return full;
}

function claims() {
  // `null` rather than a throw when the map is absent: a control must fail
  // on the behaviour it is about, and an exception raised here would report
  // the missing readout as the defect instead.
  const observed = vm.runInContext(
    'typeof _cdpClaims === "undefined" ? null'
    + ' : JSON.stringify(Array.from(_cdpClaims.entries())'
    + '.map(([tabId, entry]) => [tabId, entry.refs, entry.keep]))',
    context);
  return observed === null ? null : JSON.parse(observed);
}

async function settle(times) {
  for (let turn_ = 0; turn_ < (times || 6); turn_++) await new Promise(
    (resolve) => setImmediate(resolve));
}

(async () => {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);

  for (const action of spec.actions || []) {
    if (action.dispatch) dispatch(action.dispatch);
    if (action.detach) {
      for (const listener of detachEvents) listener({ tabId: action.detach });
    }
    if (action.tabRemoved) {
      for (const listener of tabRemoved) listener(action.tabRemoved);
    }
    if (action.settle) await settle(action.settle);
    if (action.drain) {
      const waiting = inFlight;
      inFlight = [];
      await Promise.all(waiting);
      await settle(8);
    }
  }
  await settle(10);

  process.stdout.write(JSON.stringify({
    attachCalls,
    detachCalls,
    order,
    live: Array.from(live).sort(),
    claims: claims(),
    posted: posted.map((item) => ({
      id: item.id,
      result: item.result === undefined ? null : item.result,
      error: item.error,
    })),
  }), () => process.exit(0));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n',
                       () => process.exit(1));
});
""")


def run_attachment_case(case):
    """Run one attachment case and read what the browser was asked to do.

    Nothing about the property is decided here: this builds a chrome that
    refuses a second attach per tab the way Chrome does, dispatches the
    commands the case names, and reports the attach/detach calls in the
    order they happened beside what each command was answered with.
    """
    node = shutil.which('node')
    assert node, 'node is required to execute the debugger attachment harness'
    result = run_node_program(
        node, _ATTACHMENT_HARNESS, [str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, payload=json.dumps(case))
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _cdp(command_id, tab_id=7, **extra):
    return dict({'id': command_id, 'type': 'cdp',
                 'method': 'Runtime.enable'}, **extra)


def _by_id(outcome):
    return {row['id']: row for row in outcome['posted']}


def _ran(outcome, command_id):
    """The CDP method a command's own answer carries.

    The fake answers `sendCommand` with the method it was given, so a
    command's result says which command ran rather than merely that one did.
    """
    return _by_id(outcome)[command_id]['result']['result']['value']


def test_two_commands_dispatched_together_share_one_attachment(tmp):
    """C1: the issue's own reproduction, unchanged and unproxied.

    Two transient cdp commands for one tab, dispatched together and neither
    awaited, is what a caller does when it pipelines. Chrome refuses the
    second attach, so before the claim map the loser of that race came back
    `Another debugger is already attached` — a command the caller issued and
    the browser ran, answered with an error.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'dispatch': _cdp('first', tabId=7, method='Runtime.enable')},
        {'dispatch': _cdp('second', tabId=7, method='Page.enable')},
        {'drain': True},
    ]})
    # The browser was asked to attach once, not twice.
    assert outcome['attachCalls'] == [7], outcome
    # And BOTH commands were answered, each with the result of its own
    # command — a refusal would have taken one of them out of the answers.
    posted = _by_id(outcome)
    assert sorted(posted) == ['first', 'second'], outcome
    assert all(row['error'] is None for row in posted.values()), outcome
    assert [_ran(outcome, key) for key in sorted(posted)] == [
        'Runtime.enable', 'Page.enable'], outcome
    # The transient attachment was given back once both commands were done.
    assert outcome['detachCalls'] == [7], outcome
    assert outcome['claims'] == [], outcome


def test_a_failing_attach_fails_every_joiner_and_leaves_no_claim(tmp):
    """C2: a refused attach is one failure, and it is not sticky.

    The joiners share the first caller's `ready`, so one refusal reaches all
    of them rather than each retrying an attachment Chrome has already
    refused, and the record is dropped so the next command is free to try
    again instead of inheriting a dead promise forever.
    """
    del tmp
    outcome = run_attachment_case({
        'attachFailures': 1,
        'actions': [
            {'dispatch': _cdp('first', tabId=7)},
            {'dispatch': _cdp('second', tabId=7)},
            {'drain': True},
        ]})
    # One attempt served both commands; the second never attached.
    assert outcome['attachCalls'] == [7], outcome
    posted = _by_id(outcome)
    assert len(posted) == 2, outcome
    assert all('debugger refused the attach' in (row['error'] or '')
               for row in posted.values()), outcome
    # The same error, not two: neither command re-attempted.
    assert len({row['error'] for row in posted.values()}) == 1, outcome
    # No claim left behind, and nothing was detached that was never
    # attached.
    assert outcome['claims'] == [], outcome
    assert outcome['detachCalls'] == [], outcome

    # The anti-vacuity half: a later command is not poisoned by the failure,
    # because a claim map that kept the dead `ready` would refuse it too.
    later = run_attachment_case({
        'attachFailures': 1,
        'actions': [
            {'dispatch': _cdp('first', tabId=7)},
            {'dispatch': _cdp('second', tabId=7)},
            {'drain': True},
            {'dispatch': _cdp('third', tabId=7)},
            {'drain': True},
        ]})
    assert later['attachCalls'] == [7, 7], later
    third = [row for key, row in _by_id(later).items() if key.startswith('third')]
    assert len(third) == 1 and third[0]['error'] is None, later


def test_a_kept_session_survives_a_transient_command_on_the_same_tab(tmp):
    """C3: `keep_session` is what the caller asked to keep.

    The transient command joins the kept session's attachment and gives back
    only its own share. If release dropped the record on the way out, the
    kept session would find itself detached and the next command on that tab
    would be the one that discovered it.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'dispatch': _cdp('kept', tabId=7, keep_session=True)},
        {'drain': True},
        {'dispatch': _cdp('transient', tabId=7)},
        {'drain': True},
    ]})
    assert outcome['attachCalls'] == [7], outcome
    # The transient release must not have taken the session's attachment.
    assert outcome['detachCalls'] == [], outcome
    assert outcome['live'] == [7], outcome
    assert outcome['claims'] == [[7, 1, True]], outcome
    posted = _by_id(outcome)
    assert all(row['error'] is None for row in posted.values()), outcome


def test_a_running_capture_holds_the_attachment_against_a_cdp_command(tmp):
    """C4: a capture's attachment is the capture's, not the command's.

    A cdp command issued against a capturing tab must neither attach over
    the capture nor detach it on the way out; the capture is a long-lived
    observer and both would end it.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'dispatch': {'id': 'capture', 'type': 'net-capture', 'tabId': 7}},
        {'drain': True},
        {'dispatch': _cdp('over-capture', tabId=7)},
        {'drain': True},
    ]})
    posted = _by_id(outcome)
    assert posted['capture']['error'] is None, outcome
    assert posted['over-capture']['error'] is None, outcome
    # The capture attached once; the cdp command attached nothing and, more
    # importantly, gave nothing back.
    assert outcome['attachCalls'] == [7], outcome
    assert outcome['detachCalls'] == [], outcome
    assert outcome['live'] == [7], outcome


def test_a_capture_owns_the_attachment_its_stop_gives_back(tmp):
    """C4 again, at the other end: the capture's claim is the capture's.

    A capture that held no claim would be answered by the next command with
    a refusal, and one that never gave its claim back would leave the tab
    attached for the life of the worker with no capture to account for it.
    So the two halves are the same claim, and the stop is the half that
    returns it.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'dispatch': {'id': 'capture', 'type': 'net-capture', 'tabId': 7}},
        {'drain': True},
        {'dispatch': {'id': 'stop', 'type': 'net-capture-stop', 'tabId': 7}},
        {'drain': True},
        {'dispatch': _cdp('after-stop', tabId=7)},
        {'drain': True},
    ]})
    posted = _by_id(outcome)
    assert posted['stop']['error'] is None, outcome
    assert posted['after-stop']['error'] is None, outcome
    # The stop gave the attachment back, and the command after it had to
    # attach for itself and give that back in turn.
    assert outcome['order'] == [
        'attach:7', 'detach:7', 'attach:7', 'detach:7'], outcome
    assert outcome['attachCalls'] == [7, 7], outcome
    assert outcome['detachCalls'] == [7, 7], outcome
    assert outcome['live'] == [], outcome


def test_two_tabs_hold_two_independent_attachments(tmp):
    """C5: the claim is per tab, so one tab's attachment is not another's.

    A claim map keyed by anything but the tab id would make a second tab's
    command join the first tab's record, attach nothing, and then send its
    command to a tab with no debugger on it.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'dispatch': _cdp('kept', tabId=7, keep_session=True)},
        {'drain': True},
        {'dispatch': _cdp('other-tab', tabId=8)},
        {'drain': True},
    ]})
    assert outcome['attachCalls'] == [7, 8], outcome
    posted = _by_id(outcome)
    assert all(row['error'] is None for row in posted.values()), outcome
    assert _ran(outcome, 'other-tab') == 'Runtime.enable', outcome
    # The kept session on tab 7 is untouched by tab 8's release.
    assert outcome['detachCalls'] == [8], outcome
    assert outcome['live'] == [7], outcome


def test_a_claim_arriving_mid_detach_waits_for_that_detach(tmp):
    """C6: the detach/attach window is chained, not raced.

    The first command releases and Chrome is still holding the tab when the
    next command claims it. An attach issued into that window is refused by
    the detach that is already in flight, so the claim chains behind it: the
    order is attach, detach, attach, and the second command is answered.
    """
    del tmp
    outcome = run_attachment_case({
        'dispatchInDetach': _cdp('racing', tabId=7),
        'actions': [
            {'dispatch': _cdp('leaving', tabId=7)},
            {'drain': True},
        ]})
    # attach, detach, attach, detach: the second claim waited for the first
    # detach to settle, and the racing command is transient, so it gives its
    # own attachment back in turn.
    assert outcome['order'] == ['attach:7', 'detach:7', 'attach:7', 'detach:7'], \
        outcome
    assert outcome['attachCalls'] == [7, 7], outcome
    assert outcome['detachCalls'] == [7, 7], outcome
    posted = _by_id(outcome)
    assert len(posted) == 2, outcome
    assert all(row['error'] is None for row in posted.values()), outcome
    assert all(REFUSAL not in (row['error'] or '')
               for row in posted.values()), outcome
    assert outcome['claims'] == [], outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='attachclaim_')


if __name__ == '__main__':
    raise SystemExit(main())
