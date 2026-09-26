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

A case asks for a fault by naming it — `attach`, `cdpRefused`,
`injectedError`, `storageReadFails`, `recordVersion` — and each is refused
shaped rather than ignored, so a double that answered a shape it does not
model would read as a program that behaved.
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
const contentPath = process.argv[2];
// The case rides last on the command line, as JSON text.
const spec = JSON.parse(process.argv[process.argv.length - 1]);

const HOTFIX_KEY = 'daedalus-hotfixes';
// The version a seeded record carries. No shipped `VERSION` can equal it, so
// "the key is gone" and "a record is still there" are told apart by the
// version `list-hotfixes` answers — stated, not left to two constants that
// happen to differ.
const RECORD_VERSION = '0.00.0-fixture';
const DOC_TOKEN_ATTRIBUTE = 'data-daedalus-doc';
const TAB_ID = 7;
const posted = [];
const bgConsole = [];
const messageListeners = [];
const injections = [];
const submitted = [];
const attachCalls = [];
const detachCalls = [];
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
// CDP channel reads `protocol`, `host`, `pathname` and `search` off it, so
// a double carrying only `href` would model a page the channel cannot
// read, and one carrying `origin` instead of `host` would model a page
// whose authority is not the one the channel compares.
const LOCATION_FIELDS = ['href', 'origin', 'protocol', 'host', 'hostname',
                         'port', 'pathname', 'search', 'hash'];
function fillLocation(target, url) {
  const parsed = new URL(url);
  for (const field of LOCATION_FIELDS) target[field] = parsed[field];
  return target;
}

// The token text a document mints. Every document runs the SHIPPED content
// script against its own DOM, so this is what that script draws from
// `crypto.randomUUID` — the harness supplies the uuid, the script decides
// what to do with it, and the request carries whatever it planted.
function docTokenFor(index) {
  return 'doc-token-' + index;
}

// The content-script frame for one document: the real extension/content.js,
// run against that document's OWN documentElement, in its own realm. The
// token in the replay request is therefore the one the shipped producer
// minted and planted, and a producer that stopped planting, or minted one
// value for every document, is visible here rather than hidden behind a
// fixture that plants the token itself.
function openContentFrame(doc) {
  const messages = [];
  const answers = [];
  const listeners = [];
  let frameLastError = null;
  doc.frame = { get lastError() { return frameLastError; } };
  const context = vm.createContext({
    window: { addEventListener() {}, postMessage() {} },
    document: {
      documentElement: doc.documentElement,
      addEventListener(type, listener) {
        if (type === 'DOMContentLoaded') doc.onReady = listener;
      },
      removeEventListener() {},
    },
    // `cryptoFrame: 'http'` is a plain-http page, where randomUUID is
    // [SecureContext] and therefore absent. The shipped mint has a fallback
    // for exactly that, and no other double in the tree exercises it.
    crypto: spec.cryptoFrame === 'http' ? {} : {
      randomUUID: () => docTokenFor(documents.indexOf(doc)),
    },
    chrome: {
      runtime: {
        get lastError() { return frameLastError; },
        sendMessage(message, callback) {
          messages.push(message);
          // The port is held open until the worker answers, exactly as
          // Chrome holds it while a listener returned true.
          if (callback) answers.push(callback);
        },
        onMessage: {
          addListener(listener) { listeners.push(listener); },
        },
        connect: () => ({
          postMessage() {}, disconnect() {},
          onDisconnect: { addListener() {} },
        }),
      },
    },
    location: { hostname: new URL(doc.url).hostname },
    setInterval: () => 1,
    clearInterval() {},
    setTimeout: () => 1,
    clearTimeout() {},
    console: { log() {}, warn() {}, error() {} },
  });
  vm.runInContext(
    fs.readFileSync(contentPath, 'utf8'), context,
    { filename: contentPath });
  // A document that found no documentElement at document_start defers its
  // whole replay to DOMContentLoaded, so the harness fires that too rather
  // than leaving the case with no request at all.
  if (doc.onReady) doc.onReady();
  doc.messages = messages;
  doc.answers = answers;
  doc.frameListeners = listeners;
  return doc;
}

// One document is one REPL context: the same global the MAIN-world
// injection compiles into and the same global a CDP expression runs in, so
// a fix that reached the wrong document is visible in the wrong array.
function openDocument(url) {
  const doc = {
    id: 'doc-' + (++documentSeq), url, live: true, hits: [], pending: null,
  };
  const attributes = new Map();
  doc.documentElement = {
    setAttribute(name, value) { attributes.set(name, String(value)); },
    getAttribute(name) {
      return attributes.has(name) ? attributes.get(name) : null;
    },
    removeAttribute(name) { attributes.delete(name); },
  };
  const output = new PassThrough();
  const server = repl.start({
    useGlobal: false, input: new PassThrough(), output,
    terminal: false, prompt: '',
  });
  const page = server.context;
  // Chrome's `location` is [LegacyUnforgeable]: evaluated source in the
  // document cannot replace the binding, so the CDP channel's own check
  // reads a value the page does not control. A writable plain property
  // would let the double model an assignment Chrome refuses, and C6 would
  // then establish the same-evaluation half of that ruling without the
  // unforgeable half. The fields on the object stay ordinary writable data
  // properties — that is Chrome's shape, and it is what a same-document
  // fragment change writes.
  doc.location = fillLocation({}, url);
  Object.defineProperty(page, 'location', {
    value: doc.location, writable: false,
  });
  page.daedalusHits = doc.hits;
  page.document = { documentElement: doc.documentElement };
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
  // Opened after the document joins the tab: the frame's uuid double draws on
  // the document's own index, and Chrome injects into a document that is
  // already open.
  openContentFrame(doc);
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
  // The other answer shape a MAIN-world injection can come back in: a frame
  // carrying `error` where every other frame carries `result`. Nothing ran
  // in the document it names, so the code that would have run there was
  // never compiled into it.
  if (spec.injectedError !== undefined) {
    return [{ documentId: doc.id, error: spec.injectedError }];
  }
  doc.page.__args = injection.args || [];
  const source = '(' + injection.func.toString() + ')(...__args)';
  // vm-load-exempt: runs the function the extension injected
  const result = await vm.runInContext(source, doc.page);
  delete doc.page.__args;
  // `answerDocumentId` models the browser answering about a DIFFERENT
  // document than the target named — Chrome resolved the named document to
  // the one that replaced it. Without it the injected result is always
  // reported for the document the request already named, and a worker that
  // never compared the two is indistinguishable from one that does.
  const answered = spec.answerDocumentId === undefined
    ? doc.id : spec.answerDocumentId;
  return [{ documentId: answered, result }];
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
  // An unmodelled method is refused, not answered: `_cdpError({})` is null, so
  // a `{}` here reads as "no error, the fix ran" and a control that planted
  // a different method would be told the page said nothing wrong.
  if (method !== 'Runtime.evaluate') {
    throw new Error('unmodelled CDP method ' + method);
  }
  submitted.push({
    replMode: params.replMode === true,
    awaitPromise: params.awaitPromise === true,
  });
  // The call reached the debugger and the debugger refused it. The
  // submission is recorded first, because it WAS made — that is what tells a
  // refused command apart from one the worker never issued.
  if (spec.cdpRefused) throw new Error(spec.cdpRefused);
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
        // A read Chrome refuses for one NAMED key. The name is matched so a
        // fault planted on the hotfix key leaves the boot read of the token
        // alone, and an unnamed key is not a fault this double models.
        if (spec.storageReadFails
            && [].concat(keys).includes(spec.storageReadFails)) {
          throw new Error('storage read refused for ' + spec.storageReadFails);
        }
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
    // Declared, never called on this path; a call is a shape this double
    // does not model, so it fails rather than reporting a send that nobody
    // can observe.
    sendMessage: async () => {
      throw new Error('unmodelled tabs.sendMessage');
    },
  },
  scripting: { executeScript },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async (target) => {
      attachCalls.push(target.tabId);
      if (spec.attach === 'fail') throw new Error('debugger refused');
    },
    detach: async (target) => { detachCalls.push(target.tabId); },
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
  const mintedAtLoad = Object.fromEntries(documents.map((doc) => [doc.id,
    doc.documentElement.getAttribute(DOC_TOKEN_ATTRIBUTE)]));
  // Every document minted and planted its OWN token, because every document
  // runs the shipped content script. `copiedToken` is the one state the
  // browser does not produce and the guard must still decide on: a live
  // document holding the asker's token, as two documents would after a
  // prerender's content script and the visible one's were handed the same
  // value. It exists so a guard that compares VALUES is distinguishable from
  // one that only asks whether an attribute is there.
  if (spec.copiedToken !== false) {
    for (const index of spec.copiedToken || []) {
      documents[index].documentElement.setAttribute(
        DOC_TOKEN_ATTRIBUTE,
        asker.documentElement.getAttribute(DOC_TOKEN_ATTRIBUTE));
    }
  }
  storageStore[HOTFIX_KEY] = {
    // `RECORD_VERSION` is a value the worker cannot stamp; a case overrides
    // it when the version is what it is reading.
    version: spec.recordVersion === undefined ? RECORD_VERSION
                                              : spec.recordVersion,
    fixes: (spec.fixes || []).map((fix) =>
      Object.assign({ permanent: true }, fix)),
  };

  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);

  // `commands` is the general spelling; `store` is the older key, the same
  // loop with `store-hotfix` defaulted. Naming both is a case this double
  // cannot resolve, and dropping one without a word is the failure mode the
  // module's own record default would paper over.
  //
  // The error carries a NAME as well as a message. A control telling this
  // refusal from any other way a case can fail cannot anchor on prose: a
  // reword is a false red, and so is anchoring on the key names the message
  // happens to carry. Node prints `name: message` into the stack.
  if (spec.commands !== undefined && spec.store !== undefined) {
    const refused = new Error('the case names both `commands` and `store`; '
                              + 'one spelling of the list is required');
    refused.name = 'CaseShapeRefused';
    throw refused;
  }
  const commands = spec.commands === undefined ? (spec.store || [])
                                                 : spec.commands;
  for (const command of commands) {
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
    // The message is the one the SHIPPED content script sent: whatever token
    // it minted, in whatever shape. A case that strips `docToken` models a
    // request the shipped producer never makes, which is what the worker's
    // fail-closed arm is for.
    const message = Object.assign({}, asker.messages.find(
      (entry) => entry.type === 'replayHotfixes') || {});
    if (spec.docTokenOmitted) delete message.docToken;
    // Whether the WORKER answered, which Chrome reports back to the content
    // script as the difference between a normal callback and one carrying
    // `lastError`. A worker that holds the channel open and never answers
    // still gets its callback, but with the error set — so the two are
    // distinguished here rather than collapsed into "the callback ran".
    let answered = false;
    const sendResponse = () => { answered = true; };
    asker.answered = false;
    Object.defineProperty(asker, 'answered', {
      get() { return answered; }, configurable: true,
    });
    for (const listener of messageListeners) {
      listener(message, sender, sendResponse);
    }
    // The listener does not await the replay, and the replay's last act is
    // its report, so the report is the signal that it finished.
    await waitFor(() => bgConsole.length > marked);
    for (let turn = 0; turn < 5; turn++) await delay();
    bgConsole.splice(0, marked);
    // The worker answers once the replay is done, which is what runs the
    // content script's cleanup. A worker that never answers leaves the
    // planted token in the page, and the report reads the attribute AFTER
    // the answer so the cleanup is observable either way.
    for (const answer of asker.answers) {
      if (!answered) {
        // Chrome delivers the port-closed error the same way, and the
        // content script's cleanup runs either way. Modelling it is what
        // makes "the worker answered" observable at all.
        for (const doc of documents) {
          doc.frame.lastError = { message: 'The message port closed' };
        }
      }
      try { answer(); } catch (_) {}
    }
    for (let turn = 0; turn < 3; turn++) await delay();
  }

  const globals = {};
  for (const name of spec.globals || []) {
    for (const doc of documents) {
      globals[doc.id + '.' + name] = doc.page[name] === undefined
        ? null : doc.page[name];
    }
  }
  process.stdout.write(JSON.stringify({
    attachCalls,
    detachCalls,
    // False means the worker held the channel open and never answered, so
    // Chrome delivered the content script's callback with `lastError` set.
    answered: spec.ask === false ? null : asker.answered,
    asker: { id: asker.id, url: asker.url },
    current: currentDocument ? currentDocument.id : null,
    documentUrls: Object.fromEntries(
      documents.map((doc) => [doc.id, doc.url])),
    // What each document's own content script planted AT LOAD, read before
    // the worker answered anything. Two documents planting two values is the
    // property the guard leans on; a producer that planted one value for
    // every document, or planted nothing, is visible here. Read at report
    // time it would already show the cleanup.
    minted: mintedAtLoad,
    // The same attributes AFTER the worker answered, which is when the
    // shipped content script takes its token back out of the DOM.
    afterAnswer: Object.fromEntries(documents.map((doc) => [doc.id,
      doc.documentElement.getAttribute(DOC_TOKEN_ATTRIBUTE)])),
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
    record: (storageStore[HOTFIX_KEY] || {}).fixes || [],
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
        node, _HOTFIX_HARNESS,
        [str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js')],
        cwd=ROOT, payload=json.dumps(case))
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)
