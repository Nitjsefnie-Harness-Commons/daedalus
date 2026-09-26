"""The strict harness every `dashboard/sections/*.js` suite shares.

`_dashnode.DOM` mounts one section into a fresh container. Six of the
shipped sections read surface that double does not carry -- a
document-wide selector, sibling links, a real class set, a clock the
scenario drives, and an observable `innerHTML` -- and every one of those
gaps is silent: an unmodelled member answers `undefined`, the section
misbehaves, and the suite pins the misbehaviour as though it were
behaviour. Each gap is filled here and each fill is controlled by a case
in `tests/test_dashsection_harness.py`.

The house rule is `_dashdom`'s and it is the reason the fills refuse
rather than answer: a surface that is not modelled fails by name, because
"the scaffold did not understand" and "nothing matched" are different
situations that a permissive double renders identical.

`SHELL` is `_dashnode.DOM` followed by the two halves below, which share
one module scope in the child exactly as `_dashshell` composes
`_dashdom.DOM` with its transport. The real `dashboard/api.js` is
imported off disk and runs for real: the token header, the
`URLSearchParams` assembly, the `/command` + `/result` two-leg retry and
the per-tab serialisation are the shipped code, never a re-implementation.
The transport decides only what a planned request answers.

The plan is keyed on the WHOLE request target, which is the answer to
issue #1083: a route-shaped match would serve an origin the scenario
never declared, and a relative target is not the absolute one that was
planned. A target the scenario did not plan is recorded on `unplanned`
and then refused with a throw, because no section wraps its fetch in a
catch that turns a throw into a stream error the way `sse.js` does, so a
thrown refusal is readable and a swallowed one is not. The double never
answers an unrecognised request: every branch either matches a plan or
refuses.

The clock parks every timer for the scenario to fire, and a parked
poll sleep would strand every command inside its first attempt:
`api.js` waits 250 ms between result polls and no scenario can drive
that from outside its own `await`. So a command window opens when a
`/command` is answered, and a pump spends the poll sleep inside it per
turn against a virtual clock. One `settle()` is then all a scenario
needs, and a two-click `armedAction` and a toast that has not faded stay
assertable because nothing outside a window runs.

The transport half lives in `tests/_dashsection_transport.py`, and the two
share one scope in the child, so neither reads a name the other does not
put there first. The file that was carrying both was at 616 of the 700
lines a `tests/` module is allowed, and a file at its ceiling shares its
headroom with every other branch.

Not every refusal in here is controlled, and the docstring should not
read as though it were. What
`tests/test_dashsection_harness.py` holds by mutation: the selector
registry (same element, refusal by name), `nextSibling`, `insertBefore`,
the class set in both directions, the parked clock and its cancellation,
the `innerHTML` parse and its refusal, an unplanned request refused and
recorded, a duplicate plan refused, an envelope that names another
command, the poll retrying until the result is its own, the
`localStorage` round trip, the fan-out bus and its live dispatch, the
pump's selectivity, a `Headers` bag, a poll with no command behind it, a
duplicate selector, and the `console.error` recorder. What is not held
and is a refusal by inspection only: `removeChild` of a non-child,
`remove()` outside the tree, an `insertBefore` reference outside its
parent, an `innerHTML` read, a non-string body and a body that will not
parse. A refusal nothing exercises is still better than an answer, but
it is not a control and should not be counted as one.
"""
import json

from _dashnode import DOM as _DOM
from _dashnode import (DashboardNodeHarness, _BOUNDED_AWAIT,
                       run_dashboard_node)
from _dashsection_transport import TRANSPORT as _TRANSPORT
from _dashshell import dashboard_module as section_path
from _jsread import blank_js_comments

__all__ = ['SHELL', 'build_harness', 'run_scenario', 'section_path']


# The six gaps `_dashnode.DOM` carries for a section, and the bus the
# section's second argument is. Everything here either answers a name the
# shipped sections read or refuses by naming itself.
_ELEMENTS = r"""
import { setImmediate as hostImmediate, setTimeout as realSetTimeout }
  from 'node:timers';

const STORAGE = new Map();
const CLICKS = clicks;
const ERRORS = [];
const SELECTORS = new Map();
const LISTENERS = [];
const PARKED = [];

// Only the pump moves this, and only while a command window is open, so a
// scenario that never runs a command reads the host clock unchanged.
const hostNow = Date.now;
let SPENT = 0;
Date.now = () => hostNow() + SPENT;

function unmodelled(what, value) {
  return new Error('dashboard section harness does not model ' + what
    + ': ' + String(value));
}

function describe(value) {
  try {
    if (typeof value === 'string') return value;
    if (value && typeof value.message === 'string') return value.message;
    return String(value);
  } catch (failure) {
    return '[unprintable value]';
  }
}

const realConsoleError = console.error.bind(console);
console.error = (...args) => {
  ERRORS.push(args.map(describe).join(' '));
  realConsoleError(...args);
};

class DashEl extends El {
  constructor(tag) {
    super(tag);
    this._classes = this._classes || [];
    this._parent = null;
  }
  get className() { return this._classes.join(' '); }
  set className(v) { this._classes = String(v).split(' ').filter(Boolean); }
  // The base constructor assigns its no-op class set; swallowing that one
  // assignment is what keeps it off the instance, where it would shadow
  // the getter below and make every `classList` read answer the stub.
  set classList(_stub) { return undefined; }
  get classList() {
    if (!this._classList) {
      const el = this;
      this._classList = {
        add(name) {
          const key = String(name);
          if (!el._classes.includes(key)) el._classes.push(key);
        },
        remove(name) {
          const key = String(name);
          const at = el._classes.indexOf(key);
          if (at >= 0) el._classes.splice(at, 1);
        },
        contains(name) { return el._classes.includes(String(name)); },
        get value() { return el._classes.join(' '); },
        get length() { return el._classes.length; },
      };
    }
    return this._classList;
  }
  get parentNode() { return this._parent; }
  get nextSibling() {
    const kids = this._parent ? this._parent.children : null;
    if (!kids) return null;
    const at = kids.indexOf(this);
    if (at < 0 || at + 1 >= kids.length) return null;
    return kids[at + 1];
  }
  appendChild(child) {
    const done = super.appendChild(child);
    child._parent = this;
    return done;
  }
  append(...items) {
    for (const item of items) {
      this.appendChild(item instanceof El ? item : textNode(item));
    }
  }
  removeChild(child) {
    if (this.children.indexOf(child) < 0) {
      throw unmodelled('removeChild of a non-child', child.tag);
    }
    const done = super.removeChild(child);
    child._parent = null;
    return done;
  }
  insertBefore(node, reference) {
    if (!(node instanceof El)) throw unmodelled('an insertBefore node', node);
    const kids = this.children;
    let at = kids.length;
    if (reference !== undefined && reference !== null) {
      at = kids.indexOf(reference);
      if (at < 0) {
        throw unmodelled('an insertBefore reference outside the parent',
                         reference.tag);
      }
    }
    kids.splice(at, 0, node);
    node._parent = this;
    return node;
  }
  remove() {
    if (!this._parent) throw unmodelled('a remove outside the tree', this.tag);
    this._parent.removeChild(this);
  }
  get innerHTML() { throw unmodelled('an innerHTML read', this.tag); }
  set innerHTML(v) {
    const parsed = parseFragment(v);
    this.children = [];
    this.text = '';
    if (parsed) this.appendChild(parsed);
  }
}
El = DashEl;

// The three modules that reset a list host assign this one bare div and
// nothing else, so the parser is held to that shape and refuses the rest
// by naming the assignment rather than producing an empty list.
function parseFragment(html) {
  const text = String(html).trim();
  if (text === '') return null;
  const end = text.lastIndexOf('</div>');
  if (!text.startsWith('<div') || end !== text.length - 6) {
    throw unmodelled('an innerHTML assignment of', text);
  }
  const inner = text.slice(5, end);
  const gt = inner.indexOf('>');
  if (gt < 0) throw unmodelled('an innerHTML assignment of', text);
  const body = inner.slice(gt + 1);
  const attrs = inner.slice(0, gt);
  if (body.indexOf('<') >= 0 || body.indexOf('>') >= 0) {
    throw unmodelled('an innerHTML assignment of', text);
  }
  const eq = attrs.indexOf('=');
  const name = eq < 0 ? '' : attrs.slice(0, eq).trim();
  const raw = eq < 0 ? '' : attrs.slice(eq + 1).trim();
  if (name !== 'class' || raw.length < 2 || raw[0] !== '"'
      || raw[raw.length - 1] !== '"' || raw.indexOf('=') >= 0) {
    throw unmodelled('an innerHTML assignment of', text);
  }
  const node = new El('div');
  node.className = raw.slice(1, -1);
  if (body !== '') node.appendChild(textNode(body));
  return node;
}

globalThis.document.body = new El('body');
globalThis.document.querySelector = (selector) => {
  const key = String(selector);
  if (!SELECTORS.has(key)) throw new Error('unmodeled selector ' + key);
  return SELECTORS.get(key);
};
globalThis.localStorage = {
  getItem: (key) => (STORAGE.has(String(key)) ? STORAGE.get(String(key))
    : null),
  setItem: (key, value) => { STORAGE.set(String(key), String(value)); },
  removeItem: (key) => { STORAGE.delete(String(key)); },
  clear: () => { STORAGE.clear(); },
};

function park(kind, callback, delay, ...extra) {
  PARKED.push({ id: PARKED.length + 1, kind, callback, extra,
                delay: Number(delay) || 0 });
  return PARKED.length;
}

function clearParked(id) {
  if (!(id >= 1 && id <= PARKED.length)) return false;
  if (!PARKED[id - 1]) return false;
  PARKED[id - 1] = null;
  return true;
}

globalThis.setTimeout = (callback, delay, ...extra) => (
  park('timeout', callback, delay, ...extra));
globalThis.clearTimeout = (id) => { clearParked(id); };
globalThis.setInterval = (callback, delay, ...extra) => (
  park('interval', callback, delay, ...extra));
globalThis.clearInterval = (id) => { clearParked(id); };

// A parked clock would strand every command inside its first poll:
// `api.js` waits 250 ms between result attempts and a scenario can never
// drive that from outside its own `await`. So a command window opens when
// a `/command` is answered, and the pump spends one timeout parked inside
// it per turn, adding its delay to the virtual clock. The shipped loop
// then spends its 15 s budget the way it spends it -- sixty 250 ms
// attempts -- instead of fifteen thousand attempts that take as long as
// the budget itself. Outside a window nothing runs, which is what makes
// a two-click `armedAction` and a toast that has not faded assertable.
//
// The window spends the poll sleep and nothing else. `api.js` parks no
// other timer, so the delay it polls at identifies it: a slot the window
// did not open for is stepped over, never fired, and stays in
// `drive.live()` for the scenario to fire itself. A window that found no
// spendable slot has nothing to advance, and closes rather than spinning
// on a turn that can never move.
//
// A command timing out in a section suite is the signal to re-measure
// `api.js:142` against this line, not to suspect the harness: a cadence
// that no longer matches leaves nothing spendable and every command hangs
// to its bound, which is loud rather than green.
const POLL_CADENCE_MS = 250;
const PUMP = { open: false, at: 0, delay: 0 };

function spendOne() {
  if (!PUMP.open) return false;
  while (PUMP.at < PARKED.length) {
    const slot = PARKED[PUMP.at];
    PUMP.at += 1;
    if (!slot || slot.kind !== 'timeout') continue;
    if (slot.delay !== PUMP.delay) continue;
    PARKED[PUMP.at - 1] = null;
    SPENT += slot.delay;
    slot.callback(...slot.extra);
    return true;
  }
  return false;
}

function pumpStep() {
  if (spendOne()) { hostImmediate(pumpStep); return; }
  PUMP.open = false;
}

function openPump() {
  PUMP.at = PARKED.length;
  PUMP.delay = POLL_CADENCE_MS;
  PUMP.open = true;
  hostImmediate(pumpStep);
}

// The bus the dashboard hands every section as its second argument, and
// it follows `app.js` on all three points a scenario can observe: `emit`
// catches a listener's failure and reports it through `console.error` so
// one bad listener cannot silence the rest, `on` hands back the
// unsubscribe it stored, and the dispatch iterates LIVE, so a listener
// registered during a dispatch is reached by the dispatch that registered
// it. That last one is a property of JavaScript iteration, not a choice:
// iterating a copy would model a collection the shipped `Set` does not
// have, and a control pinning the copy would be pinning the fake.
//
// The collection here is an Array where `app.js` uses a `Set`, and that
// is a gap rather than a fourth claim. `unsubscribe` behaves the same
// either way -- it drops one registration, the way `Set.delete` does.
// What the Array differs on is a re-registration of a function already
// listening: a `Set` keeps it in place, and a `push` moves it to the end,
// so the two dispatch it at different points. The other difference is
// that the same function registered twice fires twice here and once
// there. No dashboard code reaches either: `bus.on` has three call
// sites, all of them fresh arrow functions registered once at mount. So
// this is recorded rather than restructured, because the difference is
// one nothing can observe.
const bus = {
  on(fn) {
    LISTENERS.push(fn);
    return () => {
      const at = LISTENERS.indexOf(fn);
      if (at >= 0) LISTENERS.splice(at, 1);
    };
  },
  emit(event) {
    for (const fn of LISTENERS) {
      try { fn(event); } catch (failure) { console.error(failure); }
    }
  },
};
"""


SHELL = _DOM + _ELEMENTS + _TRANSPORT


def build_harness(scenario: str, *,
                  sections: tuple[str, ...] = ()
                  ) -> DashboardNodeHarness:
    """Wrap one scenario in the shell and the shipped process boundary.

    The bounded-step count is derived from the assembled source, so a
    scenario cannot disagree with the bound it declares.
    """
    source = 'const MODULES = ' + json.dumps(list(sections)) + ';\n'
    source += SHELL + '\n' + scenario
    steps = len(_BOUNDED_AWAIT.findall(blank_js_comments(source)))
    return DashboardNodeHarness(
        source, bounded_steps=steps, module=True,
        arguments=tuple(section_path(name) for name in sections))


def run_scenario(scenario: str, *,
                 sections: tuple[str, ...] = ()) -> dict:
    """Run one scenario through the shell and return the report it printed."""
    result = run_dashboard_node(build_harness(scenario, sections=sections))
    return json.loads(result.stdout)
