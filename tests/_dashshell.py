"""The dashboard shell harness for `dashboard/app.js`.

`app.js` is the dashboard's entry point and nothing ran it. The shared
`DOM` in `_dashnode` is sized for one section mounted into a fresh
container; the entry point reaches for surface that scaffold does not
have -- document-wide selectors, a class set, an `IntersectionObserver`,
a parked clock and a live stream.

`SHELL` supplies that surface and `build_harness` reuses `_dashnode`'s
process boundary, so a suite writes a scenario and reads the report it
printed:

    report = _dashshell.run_scenario(scenario, modules=('app.js',))

`SHELL` is `_dashdom.DOM` followed by the transport, driver and report
half below. The two halves share one module scope in the child, so this
half reads `PARKED`, `OBSERVERS`, `STORAGE` and `CLICKS` from the DOM
half. The split is the size baseline's own remedy: a file over the
700-line test ceiling is relocated, never given an entry.

Nothing here decides how the dashboard should behave; it only makes a
behaviour assertable. A surface that is not modelled fails by name
instead of answering, because `app.js` behaves completely differently
between "nothing matched" and "the scaffold did not understand".

A scenario shares the shell's scope, so it wraps itself in an IIFE.
"""
import json
from pathlib import Path, PurePosixPath

from _dashdom import DOM as _DOM
from _dashnode import (DashboardNodeHarness, _BOUNDED_AWAIT,
                       run_dashboard_node)
from _jsread import blank_js_comments
from _repo import ROOT

__all__ = ['SHELL', 'UNPLANNED_STATUS', 'build_harness',
           'dashboard_module', 'run_scenario']


# The status a request the scenario never planned is answered with. The
# double records the refusal and keeps serving, because a throwing double
# is swallowed by sse.js's catch and reads as an ordinary stream error.
UNPLANNED_STATUS = 599


_TRANSPORT = r"""
// The strict transport, the driver and the report. Every response member
// and every header a scenario reads is answered by name; a request the
// scenario never planned is recorded, not thrown at.
import { setTimeout as realSetTimeout } from 'node:timers';
import { pathToFileURL } from 'node:url';

const ERRORS = [];
const ROUTES = new Map();
const REQUESTS = [];
const REFUSALS = [];
const SCRIPTS = [];

function serverPrefix(target) {
  const marks = [target.indexOf('?'), target.indexOf('#')]
    .filter((at) => at >= 0);
  const stop = marks.length ? Math.min.apply(null, marks) : target.length;
  const scheme = target.indexOf('://');
  const slash = target.indexOf('/', scheme < 0 ? 0 : scheme + 3);
  if (slash < 0 || slash > stop) return '';
  return target.slice(0, slash);
}

function queryValue(target, name) {
  const at = target.indexOf('?');
  if (at < 0) return null;
  for (const pair of target.slice(at + 1).split('&')) {
    const eq = pair.indexOf('=');
    const key = eq < 0 ? pair : pair.slice(0, eq);
    if (key === name) return eq < 0 ? '' : pair.slice(eq + 1);
  }
  return null;
}

// Only a plain header object is read. A Headers instance carries the
// credential just as really, and recording it as absent would assert the
// opposite of the truth, so the bag itself is refused by name.
function readAuthorization(headers) {
  if (headers === undefined || headers === null) return null;
  if (typeof headers !== 'object' || typeof headers.get === 'function'
      || typeof headers.forEach === 'function') {
    throw new Error('dashboard shell reads Authorization only from a plain '
      + 'header object, not from '
      + Object.prototype.toString.call(headers));
  }
  const auth = headers.Authorization;
  return auth === undefined ? null : String(auth);
}

function contentTypeHeader(type) {
  return {
    get(name) {
      if (String(name).toLowerCase() !== 'content-type') {
        throw new Error('response header not modelled: ' + String(name));
      }
      return type;
    },
  };
}

const RESPONSE_MEMBERS = new Set([
  'ok', 'status', 'statusText', 'headers', 'body', 'json', 'text', 'blob',
  'then',
]);

// Same rule as the observer: a member the double does not model fails by
// name, because "blob is not a function" reads as a broken scaffold
// rather than as one thing a scenario has to know it cannot have.
const responseGuard = {
  get(target, prop) {
    if (typeof prop === 'symbol' || RESPONSE_MEMBERS.has(prop)) {
      return Reflect.get(target, prop);
    }
    throw new Error('response member not modelled: ' + String(prop));
  },
};

function guardResponse(plain) { return new Proxy(plain, responseGuard); }

function unmodelledBlob() {
  return Promise.reject(new Error(
    'response blob not modelled: api.js objectUrl() needs one'));
}

function abortError() {
  const error = new Error('the request was aborted');
  error.name = 'AbortError';
  return error;
}

function settleWith(script, result) {
  if (script.waiting) {
    const waiting = script.waiting;
    script.waiting = null;
    waiting.resolve(result);
    return;
  }
  script.queued.push(result);
}

// Every settlement is recorded, so a frame split across two reads, a
// stream reaching its end and a teardown's abort stay assertable.
function newReaderScript() {
  const script = {
    settlements: [], queued: [], waiting: null, closed: false, why: '',
  };
  const refuse = (what) => {
    throw new Error('reader already settled (' + script.why + '): ' + what);
  };
  script.push = (text) => {
    if (script.closed) refuse('push');
    script.settlements.push({ kind: 'chunk', text: String(text) });
    settleWith(script, {
      done: false, value: new TextEncoder().encode(String(text)),
    });
  };
  script.end = () => {
    if (script.closed) refuse('end');
    script.closed = true;
    script.why = 'done';
    script.settlements.push({ kind: 'done' });
    settleWith(script, { done: true, value: undefined });
  };
  script.fail = (message) => {
    if (script.closed) refuse('fail');
    script.closed = true;
    script.why = 'error';
    script.settlements.push({ kind: 'error', message: String(message) });
    if (script.waiting) {
      const waiting = script.waiting;
      script.waiting = null;
      waiting.reject(new Error(String(message)));
    }
  };
  script.read = (signal) => {
    if (script.closed) {
      return Promise.resolve({ done: true, value: undefined });
    }
    if (script.queued.length) return Promise.resolve(script.queued.shift());
    if (script.waiting) {
      throw new Error('the reader double has two concurrent reads');
    }
    return new Promise((resolve, reject) => {
      const waiting = { resolve, reject };
      script.waiting = waiting;
      if (signal && typeof signal.addEventListener === 'function') {
        signal.addEventListener('abort', () => {
          if (script.waiting !== waiting) return;
          script.waiting = null;
          script.closed = true;
          script.why = 'abort';
          script.settlements.push({ kind: 'abort' });
          reject(abortError());
        }, { once: true });
      }
    });
  };
  return script;
}

function jsonResponse(data, status) {
  const code = status === undefined ? 200 : status;
  return guardResponse({
    ok: code >= 200 && code < 300,
    status: code,
    statusText: '',
    headers: contentTypeHeader('application/json'),
    json: async () => data,
    text: async () => JSON.stringify(data),
    blob: unmodelledBlob,
  });
}

function streamResponse(signal) {
  const script = newReaderScript();
  SCRIPTS.push(script);
  return guardResponse({
    ok: true,
    status: 200,
    headers: contentTypeHeader('text/event-stream'),
    body: { getReader: () => ({ read: () => script.read(signal) }) },
    blob: unmodelledBlob,
  });
}

function refusedResponse() {
  return Object.assign(jsonResponse({}, UNPLANNED_STATUS), {
    statusText: 'unplanned request',
    headers: contentTypeHeader('text/plain'),
    body: null,
  });
}

// The plan is keyed on the WHOLE request target: a route-shaped match
// would serve an origin the scenario never declared, and a relative
// target is not the absolute one that was planned.
globalThis.fetch = async (target, init) => {
  const options = init === undefined ? {} : init;
  const whole = String(target);
  const auth = readAuthorization(options.headers);
  REQUESTS.push({
    n: REQUESTS.length + 1,
    target: whole,
    method: options.method ? String(options.method) : 'GET',
    authorization: auth,
    tab: queryValue(whole, 'tab'),
    server: serverPrefix(whole),
    hasSignal: Boolean(options.signal),
    planned: ROUTES.has(whole),
  });
  const spec = ROUTES.get(whole);
  if (!spec) {
    REFUSALS.push({ n: REQUESTS.length, target: whole });
    return refusedResponse();
  }
  if (spec.stream) return streamResponse(options.signal);
  return jsonResponse(spec.json, spec.status);
};

const drive = {
  route(target, spec) {
    const key = String(target);
    if (ROUTES.has(key)) throw new Error('route already planned: ' + key);
    ROUTES.set(key, spec || {});
    return key;
  },
  planned() { return Array.from(ROUTES.keys()); },
  live() {
    return PARKED.filter(Boolean).map((slot) => (
      { id: slot.id, kind: slot.kind, delay: slot.delay }));
  },
  fire(id) {
    const slot = PARKED[id - 1];
    if (!slot) throw new Error('no parked timer with id ' + id);
    slot.callback(...slot.extra);
    if (slot.kind === 'timeout') PARKED[id - 1] = null;
    return id;
  },
  ids(predicate) {
    return PARKED.filter((slot) => slot && (!predicate || predicate(slot)))
      .map((slot) => slot.id);
  },
  lastScript() {
    if (!SCRIPTS.length) throw new Error('no stream has been requested');
    return SCRIPTS[SCRIPTS.length - 1];
  },
  observers() { return OBSERVERS.slice(); },
};

// The recorder is read by every control that asserts a logged line, so
// describing a value must not be able to throw. `String(symbol)` is the
// one coercion that does not throw -- `ToString` on a symbol does, so a
// template literal or `+` on the same symbol does too -- while a value
// with a `toString` of its own that throws is ordinary, and the throw
// would escape the `console.error` that called it.
function describe(value) {
  try {
    if (typeof value === 'string') return value;
    if (value && typeof value.message === 'string') return value.message;
    return String(value);
  } catch (e) {
    return '[unprintable value]';
  }
}

const realError = console.error.bind(console);
console.error = (...args) => {
  ERRORS.push(args.map(describe).join(' '));
  realError(...args);
};

const settle = () => new Promise((resolve) => setImmediate(resolve));
const pause = (ms) => new Promise((resolve) => realSetTimeout(resolve, ms));

function load(name) {
  const at = MODULES.indexOf(name);
  if (at < 0) throw new Error('no module argument for ' + name);
  return import(pathToFileURL(process.argv[at + 1]).href);
}

function report(extra) {
  process.stdout.write(JSON.stringify(Object.assign({
    requests: REQUESTS,
    unplanned: REFUSALS,
    settlements: SCRIPTS.map((script) => script.settlements),
    timers: drive.live(),
    observers: OBSERVERS.map((io) => io.observed.length),
    clicks: CLICKS,
    errors: ERRORS,
    storage: Object.fromEntries(STORAGE),
  }, extra || {})));
}
"""


SHELL = _DOM + _TRANSPORT


def dashboard_module(name: str) -> Path:
    """The path `build_harness` passes to the child for a dashboard module."""
    parts = PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or not parts.parts:
        raise ValueError(f'dashboard module name escapes dashboard: {name}')
    return ROOT / 'dashboard' / Path(*parts.parts)


def build_harness(scenario: str, *,
                  modules: tuple[str, ...] = ('app.js',)
                  ) -> DashboardNodeHarness:
    """Wrap one scenario in the shell and the shipped process boundary.

    The bounded-step count is derived from the assembled source, so a
    scenario cannot disagree with the bound it declares. A scenario
    still writes every awaited step out as `await bounded(...)`, because
    a named bound says what was being waited for.
    """
    source = 'const MODULES = ' + json.dumps(list(modules)) + ';\n'
    source += 'const UNPLANNED_STATUS = ' + str(UNPLANNED_STATUS) + ';\n'
    source += SHELL + '\n' + scenario
    steps = len(_BOUNDED_AWAIT.findall(blank_js_comments(source)))
    return DashboardNodeHarness(
        source, bounded_steps=steps, module=True,
        arguments=tuple(dashboard_module(name) for name in modules))


def run_scenario(scenario: str, *,
                 modules: tuple[str, ...] = ('app.js',)) -> dict:
    """Run one scenario through the shell and return the report it printed."""
    result = run_dashboard_node(build_harness(scenario, modules=modules))
    return json.loads(result.stdout)
