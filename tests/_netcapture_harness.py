"""Shared Node-VM harness for the extension/worker/netcapture.js suite.

Not a suite itself — run_tests.py only loads `test_*.py`.

The chrome surface is the shared stub plus the debugger deltas this module
owns, and the retained `eventTarget` is the shared helper, so
`chrome.debugger.onEvent.listeners[0]` is the module's own handler. The
retained `chrome.tabs.onRemoved` carries BOTH the registry's and this
module's listeners, and every dispatch here iterates the whole array: a
harness that picked one listener would not be driving the registration the
shipped worker performs.

A scenario is a list of `steps`, replayed in order, and each step is one
input the worker observes: a command (through the real `dispatchCommand`),
a CDP event, a tab closing, Chrome detaching us, or a mutation of the array
the last answer returned. The harness answers with every posted
`postResult` payload, every recorded `chrome.*` call, the worker's own
`_netCaptures` / `_cdpSessions` state, and the strict fetch gate's own
verdict. The suites supply the steps and assert all of it; this module
decides nothing about what a handler should do.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)
from _worker_sources import (  # noqa: E402
    STREAM_RESPONSE, chrome_stub, event_target_stub, import_scripts_stub)

TOKEN = 'netcapture-token'
SERVER = 'https://net.example.com'

SYNC = 'POST /sync-tabs'
UNREGISTER = 'POST /unregister'
RESULT = 'POST /result'

_PROGRAM = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, plan] = process.argv.slice(1);
const calls = [];
// chrome_stub's sendCommand bookkeeping pushes to and reassigns both of
// these; the stub's own docstring makes them the caller's to define.
const released = [];
let pendingResolve = null;

function copy(value) {
  return value === undefined
    ? undefined
    : JSON.parse(JSON.stringify(value));
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

function record(api, args) {
  calls.push({ api, args: copy(args) });
}

// A plan can name a surface that must fail, so each handler's catch arm is
// reachable. The call is recorded before the failure, so a reader can see
// the call a refused arm still made.
function maybeReject(table, key) {
  const reject = (table || {})[key];
  if (reject === undefined) return;
  if (typeof reject === 'string') throw new Error(reject);
  throw reject;
}

const DEFAULT_ACTIVE_TABS = [
  { id: 7, windowId: 3, url: 'https://a.example.com' },
];
"""
_PROGRAM += event_target_stub()
_PROGRAM += chrome_stub(f"'{TOKEN}'", f"'{SERVER}'", 'handleDebuggerCommand')
_PROGRAM += r"""
// Scenario deltas over the shared stub: the surfaces it does not model.
chrome.tabs.query = function(query, callback) {
  // Boot's registerAllTabs reads a callback; answering it off the record
  // leaves only the handlers' own promise-form queries in the call log.
  if (typeof callback === 'function') {
    callback([]);
    return undefined;
  }
  record('tabs.query', [query]);
  maybeReject(plan.chromeReject, 'tabs.query');
  const tabs = plan.activeTabs || DEFAULT_ACTIVE_TABS;
  return Promise.resolve(tabs.map((tab) => ({ ...tab })));
};
chrome.debugger.attach = async (target, version) => {
  record('debugger.attach', [target, version]);
  maybeReject(plan.chromeReject, 'debugger.attach');
};
chrome.debugger.detach = async (target) => {
  record('debugger.detach', [target]);
  maybeReject(plan.chromeReject, 'debugger.detach');
};

// The answer to a protocol call is the plan's, never the argument's: the
// body map is keyed on the requestId and its values are the scenario's
// text, so a cross-wired entry cannot be satisfied by an echo. A method
// the plan did not model is a loud failure rather than a quiet `{}` that
// every assertion would read as a success.
const bodyAnswers = plan.bodies || {};
async function handleDebuggerCommand(_target, method, params) {
  record('debugger.sendCommand', [{ tabId: _target.tabId }, method, params]);
  maybeReject(plan.sendCommandReject, method);
  if (method === 'Network.enable') return {};
  // A second modelled method, so a scenario that opens a kept CDP session
  // and then a capture can tell the two protocol calls apart instead of
  // driving the same method twice with one value.
  if (method === 'Runtime.enable') return { modelled: 'Runtime.enable' };
  if (method === 'Network.getResponseBody') {
    const answer = bodyAnswers[params.requestId];
    if (answer === undefined) {
      throw new Error('unmodelled response body: ' + params.requestId);
    }
    if (answer.throw) throw new Error(answer.throw);
    return copy(answer);
  }
  throw new Error('unmodelled debugger method: ' + method);
}

const BRIDGE_URL = 'https://net.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
"""
_PROGRAM += STREAM_RESPONSE
_PROGRAM += STRICT_FETCH
_PROGRAM += r"""
const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  crypto: { randomUUID: () => 'net-1' },
  AbortController,
  TextDecoder,
  URL,
  performance,
  atob,
  btoa,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
"""
_PROGRAM += import_scripts_stub('context')
_PROGRAM += r"""
function evaluate(expression) {
  // vm-load-exempt: runs a realm expression composed above, not a file
  return vm.runInContext(expression, context);
}

function dispatchTo(target, argument) {
  for (const listener of target.listeners) listener(argument);
}

// The slice/live distinction is observable without reading an identity: a
// scenario mutates the array the last answer returned, and a later capture
// on the same tab shows whether the buffer the worker still holds moved.
function mutateReturnedRequests(how) {
  const last = resultPosts[resultPosts.length - 1];
  const returned = last.result.requests;
  if (how === 'clear') {
    returned.length = 0;
  } else if (how === 'overwrite') {
    returned[0] = { requestId: how, url: how };
  } else {
    returned.push({ requestId: how, url: how });
  }
  return returned.length;
}

// What the worker POSTED, snapshotted at post time: a scenario step may go
// on to mutate the object the parsed body produced, and the answer a test
// reads must be the one the bridge was handed.
const answerSnapshots = [];

async function runStep(step) {
  if (step.command !== undefined) {
    context.nextCommand = step.command;
    await vm.runInContext('dispatchCommand(nextCommand)', context);
    answerSnapshots.push(copy(resultPosts[resultPosts.length - 1]));
    return;
  }
  if (step.netEvent !== undefined) {
    for (const listener of chrome.debugger.onEvent.listeners) {
      listener(step.netEvent.source, step.netEvent.method,
               step.netEvent.params);
    }
    return;
  }
  if (step.tabRemoved !== undefined) {
    dispatchTo(chrome.tabs.onRemoved, step.tabRemoved);
    return;
  }
  if (step.debuggerDetached !== undefined) {
    dispatchTo(chrome.debugger.onDetach, step.debuggerDetached);
    return;
  }
  if (step.mutateReturnedRequests !== undefined) {
    mutateReturnedRequests(step.mutateReturnedRequests);
    return;
  }
  throw new Error('unmodelled step: ' + Object.keys(step).join(','));
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);
  // The clock the fallback timestamp arm reads is world state, not a value
  // the handler is handed: the handler still computes `Date.now()` itself.
  if (plan.frozenNow !== undefined) {
    evaluate('Date.now = function () { return '
      + Number(plan.frozenNow) + '; };');
  }
  for (const step of plan.steps || []) {
    await runStep(step);
  }
  return {
    calls,
    posted: answerSnapshots,
    // A probe is a realm expression the scenario names, run last: the pure
    // limit reader has no command to reach it through, and a thrown refusal
    // is as much of its answer as a returned value.
    probes: (plan.probes || []).map((expression) => {
      try {
        return { value: copy(evaluate(expression)) };
      } catch (error) {
        return { error: String((error && error.message) || error) };
      }
    }),
    state: {
      captures: evaluate(
        'Object.keys(_netCaptures).sort().map(function (key) {'
        + ' return { tabId: key, maxRequests: _netCaptures[key].maxRequests,'
        + ' requestIds: _netCaptures[key].requests.map('
        + '   function (entry) { return entry.requestId; }) };'
        + ' })'),
      cdpSessions: evaluate('Object.keys(_cdpSessions).sort()'),
    },
    gate: {
      contractFaults: gateContractFaults,
      records: nonStreamFetches,
      refused: refusedFetches,
      badOrigins,
      streamAnswered: streamFetches.map((fetch) => fetch.answered),
    },
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def steps_plan(steps, *, plan, posts=None):
    """The gate declaration a step list implies, beside the steps themselves.

    `planned` is derived from the steps, not from what the worker did: every
    command in this module's three handlers posts exactly one answer, the
    boot syncs once, and a tab closing also drives the registry's own
    unregister. Deriving it here keeps an UNDECLARED request — one the
    worker made and the scenario never planned — a gate refusal, which is
    what the gate is for.
    """
    commands = sum(1 for step in steps if 'command' in step)
    return dict(plan,
                steps=steps,
                planned=[SYNC]
                + [RESULT] * (commands if posts is None else posts)
                + [UNREGISTER] * sum(1 for step in steps
                                     if 'tabRemoved' in step),
                planned_stream=[503])


def extension_root():
    """The extension tree a run loads, and how a mutation proof redirects it.

    `DAEDALUS_NETCAPTURE_EXTENSION` names another tree holding the real
    worker, so a mutated COPY of extension/worker/netcapture.js can be
    exercised without the checkout's own file being edited. Read per run,
    not at import: a module-level snapshot cannot be right for a value
    chosen per call.
    """
    return Path(os.environ.get('DAEDALUS_NETCAPTURE_EXTENSION')
                or EXTENSION_ROOT)


def run_capture(steps, *, root=None, **plan):
    """Run the worker VM over `steps`; return the full observable outcome.

    The strict fetch gate's verdict is asserted here, on every run, so a
    bridge request the scenario never declared cannot pass unnoticed. `root`
    points the load at another extension tree, which is how a mutation
    proof exercises a mutated copy of the real module.
    """
    node = require_node()
    background = Path(root or extension_root()) / 'background.js'
    payload = steps_plan(steps, plan=plan)
    outcome = run_gate(node, _PROGRAM, [str(background)], cwd=ROOT,
                       plan=payload)
    assert_gate_clean(
        contract_faults=outcome['gate']['contractFaults'],
        records=outcome['gate']['records'],
        refused=outcome['gate']['refused'],
        bad_origins=outcome['gate']['badOrigins'],
        stream_answered=outcome['gate']['streamAnswered'],
        planned=payload['planned'],
        planned_stream=payload['planned_stream'])
    return outcome


def apis(outcome, *names):
    """Ordered [api, args] pairs for the named chrome surfaces."""
    return [[call['api'], call['args']] for call in outcome['calls']
            if call['api'] in names]


def posted(outcome):
    """Every posted postResult payload, in order."""
    return [json.dumps(entry, sort_keys=True) for entry in outcome['posted']]


# ─── the shapes a scenario is written in ───

ATTACH = 'debugger.attach'
DETACH = 'debugger.detach'
SEND = 'debugger.sendCommand'
QUERY = 'tabs.query'
ENABLE = 'Network.enable'
BODY = 'Network.getResponseBody'
# The message a limit refusal raises, pinned whole: it names both ends of
# the bound, so a reworded or truncated one fails on the equality.
BOUND = 'maxRequests must be an integer from 1 to 20000'
# The clock the fallback timestamp arm reads. It is world state, not a value
# a handler is handed, so nothing here or in the suite asserts how long
# anything took.
FROZEN = 1750000000123

_ids = iter(range(1, 100000))


def attach_call(tab):
    return [ATTACH, [{'tabId': tab}, '1.3']]


def detach_call(tab):
    return [DETACH, [{'tabId': tab}]]


def send_call(tab, method, params=None):
    return [SEND, [{'tabId': tab}, method,
                   {} if params is None else params]]


def cmd(kind, **fields):
    return {'id': f'c-{kind}-{next(_ids)}', 'type': kind, **fields}


def start(tab=5, **fields):
    return {'command': cmd('net-capture', tabId=tab, **fields)}


def stop(tab=5, **fields):
    return {'command': cmd('net-capture-stop', tabId=tab, **fields)}


def read(tab=5, **fields):
    return {'command': cmd('net-capture-get', tabId=tab, **fields)}


def keep(tab=5):
    """A kept CDP session, opened the way the worker opens one."""
    return {'command': cmd('cdp', tabId=tab, method='Runtime.enable',
                           keep_session=True)}


def event(tab, method, **params):
    return {'netEvent': {'source': {'tabId': tab}, 'method': method,
                         'params': params}}


def request(tab, request_id, url, post=None, **params):
    sent = {'url': url, 'method': 'GET', 'headers': {}}
    if post is not None:
        sent['postData'] = post
    return event(tab, 'Network.requestWillBeSent', requestId=request_id,
                 request=sent, **params)


def response(tab, request_id, **fields):
    return event(tab, 'Network.responseReceived', requestId=request_id,
                 response=fields)


def finished(tab, request_id, **params):
    return event(tab, 'Network.loadingFinished', requestId=request_id,
                 **params)


def answers(outcome):
    return [post['result'] for post in outcome['posted']]


def errors(outcome):
    return [post['error'] for post in outcome['posted']]


def entries(outcome, index=-1):
    return answers(outcome)[index]['requests']


def ids(outcome, index=-1):
    """The requestId of each entry, in buffer order."""
    return [entry['requestId'] for entry in entries(outcome, index)]


def sent_bodies(outcome):
    """The requestId of every response body this run asked for, in order."""
    return [call['args'][2]['requestId'] for call in outcome['calls']
            if call['api'] == SEND and call['args'][1] == BODY]


def buffered(steps, tab=5, **plan):
    """The entries one capture holds once `steps` has run."""
    plan.setdefault('frozenNow', FROZEN)
    return entries(run_capture(steps + [read(tab)], **plan))
