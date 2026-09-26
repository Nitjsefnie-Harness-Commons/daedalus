"""Discover and load the extension service worker's classic scripts."""
import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_PATH = ROOT / 'extension' / 'background.js'

_JS_IDENTIFIER = r'[A-Za-z_$][\w$]*'


def directive_entries(source, directive):
    """Names one `/* directive ... */` comment lists, in source order."""
    names = []
    pattern = re.compile(rf'/\*\s*{directive}\b([^*]*)\*/')
    for match in pattern.finditer(source):
        for item in match.group(1).split(','):
            name = item.strip().partition(':')[0]
            if name:
                assert re.fullmatch(_JS_IDENTIFIER, name), (
                    f'unreadable {directive} directive entry {name!r}')
                names.append(name)
    return names


def imported_worker_paths(background_path=BACKGROUND_PATH):
    background_path = Path(background_path)
    source = background_path.read_text(encoding='utf-8')
    calls = re.findall(
        r'^importScripts\((.*?)\);\s*$', source,
        flags=re.MULTILINE | re.DOTALL)
    assert len(calls) == 1, (
        f'expected one importScripts call in {background_path}, found '
        f'{len(calls)}')
    try:
        names = ast.literal_eval('[' + calls[0] + ']')
    except (SyntaxError, ValueError) as error:
        raise AssertionError(
            f'importScripts arguments are not string literals: {error}') \
            from error
    assert all(isinstance(name, str) for name in names), (
        'importScripts arguments must all be string literals')
    return tuple(background_path.parent / name for name in names)


def worker_source_paths(background_path=BACKGROUND_PATH):
    background_path = Path(background_path)
    return (background_path, *imported_worker_paths(background_path))


def import_scripts_stub(context_name, trace_map_name=None):
    """Build the classic-script loader and its honest-worker path trace.

    Node's vm is not a security boundary: a host function installed in its
    context exposes host-realm intrinsics, so deliberately hostile worker
    source can forge the array this trace appends through. The trace proves
    which modules an honest split asks to load, not resistance to its author.
    """
    trace_registration = ''
    if trace_map_name is not None:
        trace_registration = (
            f'{trace_map_name}.set('
            f'{context_name}, loadedWorkerSourcePaths);')
    return r"""
const loadedWorkerSourcePaths = [];
__TRACE_REGISTRATION__
__CONTEXT__.importScripts = (...sourceNames) => {
  for (const sourceName of sourceNames) {
    const sourcePath = require('path').resolve(
      require('path').dirname(backgroundPath), sourceName);
    loadedWorkerSourcePaths.push(sourcePath);
    vm.runInContext(
      fs.readFileSync(sourcePath, 'utf8'), __CONTEXT__,
      { filename: sourcePath });
  }
};
""".replace('__CONTEXT__', context_name).replace(
        '__TRACE_REGISTRATION__', trace_registration)


# The stream answer factory the eval-relay, CDP, overlap and boundary
# harnesses share. The cross-file duplicate check cannot see JavaScript inside
# a Python string, so a copied factory would not be caught; one copy here is
# what keeps the four from drifting.
STREAM_RESPONSE = r"""
function streamResponse(answer) {
  if (answer === 'hang') {
    return {
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: () => new Promise(() => {}),
          cancel: () => Promise.resolve(),
        }),
      },
    };
  }
  return response(answer, { error: 'disabled' });
}
"""
RELAY_CONTEXT = r"""
const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  crypto: { randomUUID: () => 'relay-1' },
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
""" + import_scripts_stub('context') + r"""
"""


def chrome_stub(token, server, send_command):
    """The chrome surface the CDP and starvation harnesses stand against.

    The stub text closes over two free variables — `released` (an array the
    caller owns) and `pendingResolve` (a `let` the caller owns) — because the
    shared `Runtime.releaseObject` bookkeeping pushes to and reassigns them.
    A caller must define both with exactly these names before the stub runs,
    and its own `send_command` closes over the same two.
    """
    template = r"""
const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': __TOKEN__,
        'daedalus-server': __SERVER__,
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_query, callback) {
      const tabs = [{ id: 7, url: '', title: 'Page' }];
      if (callback) {
        callback(tabs);
        return undefined;
      }
      return Promise.resolve(tabs);
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {},
    detach: async () => {},
    sendCommand: async (_target, method, params) => {
      if (method === 'Runtime.releaseObject') {
        released.push(params.objectId);
        if (params.objectId === 'pending-original' && pendingResolve) {
          const resolve = pendingResolve;
          pendingResolve = null;
          setImmediate(() => resolve({
            result: { objectId: 'pending-late' },
          }));
        }
        return {};
      }
      return __SEND_COMMAND__(_target, method, params);
    },
  },
  scripting: { executeScript: async () => [{ result: false }] },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: { onAlarm: eventTarget(), create() {} },
};
"""
    return (template
            .replace('__TOKEN__', token)
            .replace('__SERVER__', server)
            .replace('__SEND_COMMAND__', send_command))


# The page globals a harness must supply before it can run the shipped
# content script or the shipped page script, because both use them at load:
# `crypto`, which the content script mints its replay token with, and the
# document, which both relay through.
#
# Every member below is one `content.js` or `page.js` actually reads, and
# nothing more: a step nothing reads is inert data that reads as coverage.
# `getAttribute` is deliberately NOT here — no shipped script reads it. The
# only thing that does is the hotfix harness's own document double, which
# models the guard reading the token back, and that harness keeps its own.
#
# The attribute store is real rather than a set of no-ops. A documentElement
# that swallowed the write would let a control pass against a page that never
# planted anything, which is the shape of a false green this suite family has
# produced before. One copy, because a second would drift from the first and
# the drift would be invisible.
CONTENT_SCRIPT_PAGE = r"""
function contentScriptPage() {
  const attributes = new Map();
  return {
    crypto: { randomUUID: () => 'content-doc-token' },
    document: {
      documentElement: {
        setAttribute(name, value) { attributes.set(name, String(value)); },
        removeAttribute(name) { attributes.delete(name); },
      },
      addEventListener() {},
      removeEventListener() {},
      head: { appendChild() {} },
      createElement: () => ({
        remove() {}, set onload(_listener) {}, set onerror(_listener) {},
      }),
    },
  };
}
"""


def event_target_stub():
    """The event-target stand-in, which RETAINS every listener added to it.

    `eventTarget()` and `eventTarget(retained)` yield
    `{ addListener, listeners }`. `eventTarget(retained, true)` adds
    `dispatch(...args)`, which calls every retained listener in order, over
    a snapshot taken at the call, containing each listener's exception so
    one that throws neither silences the rest nor fails the caller. It is
    opt-in: a harness that never fires an event must not have a listener
    run behind its back. A contained exception is not reported, so a
    harness that needs to see one wraps its own listener.
    `tests/test_worker_sources.py` holds the controls.
    """
    return r"""
function eventTarget(retained = [], dispatches = false) {
  const target = {
    addListener(listener) { retained.push(listener); },
    listeners: retained,
  };
  if (dispatches) {
    target.dispatch = (...args) => {
      for (const listener of [...retained]) {
        try {
          listener(...args);
        } catch (_) { /* a real event target isolates this too */ }
      }
    };
  }
  return target;
}
"""


def resolve_target_stub():
    """The injection-target resolver the MAIN-world and hotfix doubles share.

    Chrome resolves an injection target to a document: a bare `tabId` lands
    on whatever holds the tab at injection time, a `documentIds` target
    lands on the document it names, and a document that is gone is refused
    rather than answered from a stale binding. A target shape the double
    does not model is refused by name, because a stub that quietly accepted
    what it does not model is how a false green is manufactured.

    The two doubles keep their document stores in different shapes — one an
    array, one an object keyed by id — so the shared half is the CONTROL and
    the store access is the caller's: `find(id)` answers the document with
    that id and `current()` answers the live one. Both are functions, not
    values, because the live document changes under a navigation and the
    answer is read at call time.
    """
    return r"""
function resolveTarget(target, find, current) {
  if (!target || target.tabId === undefined) {
    throw new Error('unmodelled injection target ' + JSON.stringify(target));
  }
  const shape = Object.keys(target).sort().join(',');
  if (shape === 'tabId') return current();
  if (shape === 'documentIds,tabId') {
    const named = target.documentIds;
    if (!Array.isArray(named) || named.length !== 1) {
      throw new Error('unmodelled documentIds ' + JSON.stringify(named));
    }
    const doc = find(named[0]);
    if (!doc || !doc.live) {
      throw new Error('Cannot access contents of the page');
    }
    return doc;
  }
  throw new Error('unmodelled injection target ' + shape);
}
"""
