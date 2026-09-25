"""The Node VM harness the hotfix replay scope controls run in.

A tab id names a tab, not a document. Chrome gives every document in a tab
its own identity, and a message from a content script carries that identity
in `sender.documentId`; a document that navigates, or that a prerender
swaps in or activates, leaves the request aimed at something the tab no
longer holds. So the double here models DOCUMENTS: an injection target is
resolved the way Chrome resolves it, a bare `tabId` lands on whatever holds
the tab at injection time, a `documentIds` target lands on the document it
names and is refused once that document is gone, and a CDP expression is
evaluated in the tab's LIVE document. A control therefore reads which
document received which fix.

A target shape the double does not model is refused, never ignored — a stub
that quietly accepted what it does not model is how a false green is
manufactured. The two shapes it does model are the two production uses:
`{tabId}` and `{tabId, documentIds}`.

The CDP expression is evaluated through `node:repl`, the same V8 Chrome's
REPL mode is: a top-level `await` settles and a top-level `var` or function
declaration lands in the document's global. An evaluation that could not
tell a statement-list guard from an IIFE wrapper cannot hold the control
that exists for exactly that difference.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _noderun import run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402

_HOTFIX_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');
const repl = require('repl');
const { PassThrough } = require('stream');

const backgroundPath = process.argv[1];
// The case rides last on the command line, as JSON text.
const spec = JSON.parse(process.argv[process.argv.length - 1]);

const HOTFIX_KEY = 'daedalus-hotfixes';
const TAB_ID = 7;
const posted = [];
const bgConsole = [];
const messageListeners = [];
const injections = [];
const submitted = [];
const timers = [];
const storageStore = {
  'daedalus-token': 'hotfix-token',
  'daedalus-server': 'https://bridge.example.com',
};
let sequence = 0;

function eventTarget(listeners = null) {
  return {
    addListener(listener) { if (listeners) listeners.push(listener); },
  };
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
function schedule(callback, delay) {
  const timer = { callback, delay, cleared: false };
  timers.push(timer);
  return timers.length;
}
function clearScheduled(id) {
  const timer = timers[id - 1];
  if (timer) timer.cleared = true;
}

// ─── the tab's documents ───

const documents = [];
let currentDocument = null;
let documentSeq = 0;
let navigated = false;

// A document's `location`, with the components a real Location carries. The
// CDP channel reads `origin`, `pathname` and `search` off it, so a double
// carrying only `href` would model a page the channel cannot read.
const LOCATION_FIELDS = ['href', 'origin', 'protocol', 'host', 'hostname',
                         'port', 'pathname', 'search', 'hash'];
function fillLocation(target, url) {
  const parsed = new URL(url);
  for (const field of LOCATION_FIELDS) target[field] = parsed[field];
  return target;
}

// One document is one REPL context: the same global the MAIN-world
// injection compiles into and the same global a CDP expression runs in, so
// a fix that reached the wrong document is visible in the wrong array.
function openDocument(url) {
  const doc = {
    id: 'doc-' + (++documentSeq), url, live: true, hits: [], pending: null,
  };
  const output = new PassThrough();
  const server = repl.start({
    useGlobal: false, input: new PassThrough(), output,
    terminal: false, prompt: '',
  });
  const page = server.context;
  // Chrome's `location` is [LegacyUnforgeable]: evaluated source in the
  // document cannot replace it, so the CDP channel's own check reads a value
  // the page does not control. A writable plain property would let the double
  // model an assignment Chrome refuses, and C6 would then establish the
  // same-evaluation half of that ruling without the unforgeable half. The
  // href stays writable because a same-document fragment change is a real
  // navigation a page performs on itself.
  doc.location = fillLocation({}, url);
  Object.defineProperty(page, 'location', {
    value: doc.location, writable: false,
  });
  page.daedalusHits = doc.hits;
  page.performance = performance;
  // An evaluation that throws never reaches the REPL's callback; it is
  // written to the output stream instead, so that is what settles it.
  output.on('data', (chunk) => {
    const text = chunk.toString();
    if (!text.startsWith('Uncaught') || !doc.pending) return;
    // Node's REPL prints `Uncaught:` and the message on the next line when
    // the message is long enough to be inspected rather than inlined.
    const lines = text.split('\n');
    const description = lines[0].replace(/^Uncaught:?\s*/, '') || lines[1];
    const pending = doc.pending;
    pending({ exception: String(description).trim() });
  });
  doc.server = server;
  doc.page = page;
  documents.push(doc);
  return doc;
}

// A navigation retires every document the tab held and installs a new one.
function navigate(url) {
  for (const doc of documents) doc.live = false;
  currentDocument = openDocument(url);
  return currentDocument;
}

function navigateAt(point) {
  if (spec.navigateAt !== point || navigated) return;
  navigated = true;
  navigate(spec.navigateTo);
}

// A fragment change is not a navigation: the document is the same one, the
// tab still holds it, and only its url moved. Modelled apart from
// `navigate` so a control can put the two document identities in conflict
// without retiring the document.
function relocateAt(point) {
  if (spec.relocateAt !== point) return;
  // The location OBJECT is unforgeable, so a fragment change writes the
  // document's own object in place rather than rebinding the global.
  fillLocation(currentDocument.location, spec.relocateTo);
  currentDocument.url = spec.relocateTo;
}

// ─── the fake browser ───

// Chrome resolves an injection target to a document: a bare tabId resolves
// to whatever holds the tab at injection time, a documentIds target
// resolves to the document it names and is refused once it is gone.
function resolveTarget(target) {
  if (!target || target.tabId === undefined) {
    throw new Error('unmodelled injection target ' + JSON.stringify(target));
  }
  const shape = Object.keys(target).sort().join(',');
  if (shape === 'tabId') return currentDocument;
  if (shape === 'documentIds,tabId') {
    const named = target.documentIds;
    if (!Array.isArray(named) || named.length !== 1) {
      throw new Error('unmodelled documentIds ' + JSON.stringify(named));
    }
    const doc = documents.find((candidate) => candidate.id === named[0]);
    if (!doc || !doc.live) {
      throw new Error('Cannot access contents of the page');
    }
    return doc;
  }
  throw new Error('unmodelled injection target ' + shape);
}

async function executeScript(injection) {
  navigateAt('first-script-call');
  if (injection.world !== 'MAIN') {
    throw new Error('unmodelled injection world ' + injection.world);
  }
  const doc = resolveTarget(injection.target);
  const isProbe = injection.func.name === '_canUseMainWorldEval';
  injections.push({
    documentId: doc.id, world: injection.world, probe: isProbe,
  });
  if (isProbe) {
    if (spec.probe === 'throw') {
      throw new Error(spec.probeError || 'the document refused the probe');
    }
    navigateAt('after-probe');
    return [{ documentId: doc.id, result: spec.probe !== false }];
  }
  doc.page.__args = injection.args || [];
  const source = '(' + injection.func.toString() + ')(...__args)';
  // vm-load-exempt: runs the function the extension injected
  const result = await vm.runInContext(source, doc.page);
  delete doc.page.__args;
  return [{ documentId: doc.id, result }];
}

function evaluateIn(doc, expression) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (answer) => {
      if (settled) return;
      settled = true;
      doc.pending = null;
      resolve(answer);
    };
    doc.pending = (answer) => settle(answer);
    doc.server.eval(expression, doc.page, 'hotfix-cdp.js', (error, value) => {
      settle(error
        ? { exception: String((error && error.message) || error) }
        : { value });
    });
  });
}

// CDP is tab-bound: the expression runs in whatever document the tab holds
// now, which is the whole reason the submitted source carries its own check.
async function sendCommand(_target, method, params) {
  if (method !== 'Runtime.evaluate') return {};
  submitted.push({
    replMode: params.replMode === true,
    awaitPromise: params.awaitPromise === true,
  });
  navigateAt('before-cdp-evaluate');
  relocateAt('before-cdp-evaluate');
  const answer = await evaluateIn(currentDocument, params.expression);
  if (answer.exception) {
    return {
      result: { objectId: 'cdp-result' },
      exceptionDetails: {
        text: 'Uncaught',
        exception: {
          objectId: 'cdp-exception', description: answer.exception,
        },
      },
    };
  }
  return { result: { value: answer.value === undefined ? null
                                             : answer.value } };
}

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const key of [].concat(keys)) {
          if (key in storageStore) {
            out[key] = JSON.parse(JSON.stringify(storageStore[key]));
          }
        }
        return out;
      },
      set: async (entries) => {
        for (const key of Object.keys(entries)) {
          storageStore[key] = JSON.parse(JSON.stringify(entries[key]));
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
    query: async () => [{ id: TAB_ID, url: currentDocument.url,
                           title: 'Page' }],
    get: async (id) => ({ id, url: currentDocument.url, title: 'Page' }),
    sendMessage: async () => {},
  },
  scripting: { executeScript },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {
      if (spec.attach === 'fail') throw new Error('debugger refused');
    },
    detach: async () => {},
    sendCommand,
  },
  cookies: { getAll: async () => [], remove: async () => null },
  declarativeNetRequest: {
    getSessionRules: async () => [],
    updateSessionRules: async () => {},
  },
  runtime: {
    lastError: null,
    onMessage: eventTarget(messageListeners),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: { onAlarm: eventTarget(), create() {} },
};

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.endsWith('/result') && init && init.method === 'POST') {
      posted.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    // Park the boot stream: a worker-side reconnect timer would be an
    // arming a control could mistake for a settlement bound.
    if (url.includes('/stream')) return new Promise(() => {});
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'hotfix-' + (++sequence) },
  AbortController,
  TextDecoder,
  TextEncoder,
  URL,
  performance,
  btoa,
  atob,
  setTimeout: schedule,
  clearTimeout: clearScheduled,
  setInterval: () => 1,
  clearInterval() {},
  console: {
    log: (...a) => bgConsole.push({ level: 'log', text: a.join(' ') }),
    warn: (...a) => bgConsole.push({ level: 'warn', text: a.join(' ') }),
    error: (...a) => bgConsole.push({ level: 'error', text: a.join(' ') }),
  },
});
""" + import_scripts_stub('context') + r"""

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 2000; attempt++) {
    if (predicate()) return true;
    await delay();
  }
  return predicate();
}

(async () => {
  (spec.documents || []).forEach((url, index) => {
    const doc = openDocument(url);
    if (index === (spec.current === undefined
                   ? spec.documents.length - 1 : spec.current)) {
      currentDocument = doc;
    }
  });
  const asker = documents[spec.asker === undefined ? 0 : spec.asker];
  storageStore[HOTFIX_KEY] = {
    version: '0.18.0', fixes: (spec.fixes || []).map((fix) =>
      Object.assign({ permanent: true }, fix)),
  };

  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);

  for (const command of spec.store || []) {
    context.storeCommand = Object.assign(
      { type: 'store-hotfix', _did: 'did-' + command.fixId }, command);
    await vm.runInContext('dispatchCommand(storeCommand)', context);
  }

  // The real message listener, with the sender Chrome builds: the tab, the
  // document the content script lives in, and that document's URL. The
  // console mark is taken here, so the report is the replay's own and not
  // the worker's boot lines.
  if (spec.ask !== false) {
    const marked = bgConsole.length;
    const senderUrl = spec.senderUrl === undefined ? asker.url
      : spec.senderUrl;
    const sender = {
      tab: { id: TAB_ID },
      documentId: asker.id,
      url: senderUrl,
      origin: '',
    };
    try { sender.origin = new URL(senderUrl).origin; } catch (_) {}
    // What Chrome left off the sender. A content-script message carries all
    // three, so a case that omits one is a shape the real browser does not
    // produce — the point is to pin what the worker does with a request it
    // cannot bind rather than to model a page.
    for (const field of spec.senderOmits || []) delete sender[field];
    for (const listener of messageListeners) {
      listener({ type: 'replayHotfixes' }, sender, () => {});
    }
    // The listener does not await the replay, and the replay's last act is
    // its report, so the report is the signal that it finished.
    await waitFor(() => bgConsole.length > marked);
    for (let turn = 0; turn < 5; turn++) await delay();
    bgConsole.splice(0, marked);
  }

  const globals = {};
  for (const name of spec.globals || []) {
    for (const doc of documents) {
      globals[doc.id + '.' + name] = doc.page[name] === undefined
        ? null : doc.page[name];
    }
  }
  process.stdout.write(JSON.stringify({
    asker: { id: asker.id, url: asker.url },
    current: currentDocument ? currentDocument.id : null,
    documentUrls: Object.fromEntries(
      documents.map((doc) => [doc.id, doc.url])),
    delivered: Object.fromEntries(
      documents.map((doc) => [doc.id, doc.hits])),
    globals,
    injections,
    submitted,
    replay: bgConsole,
    posted: posted.map((item) => ({
      id: item.id,
      result: item.result === undefined ? null : item.result,
      error: item.error,
    })),
    stored: (storageStore[HOTFIX_KEY] || { fixes: [] }).fixes.map((fix) => ({
      id: fix.id, match: fix.match === undefined ? null : fix.match,
    })),
    armings: timers.filter((timer) => !timer.cleared).map((t) => t.delay),
  }), () => process.exit(0));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n',
                       () => process.exit(1));
});
""")


def run_hotfix_case(case):
    """Run one hotfix replay case and read the answer the worker produced.

    The case names the tab's documents, which one the content script asked
    from, and where a navigation lands relative to the replay. Nothing about
    the property is decided here; this only builds the browser the shipped
    worker runs against and reports what each document received.
    """
    node = shutil.which('node')
    assert node, 'node is required to execute the hotfix replay harness'
    result = run_node_program(
        node, _HOTFIX_HARNESS, [str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, payload=json.dumps(case))
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)
