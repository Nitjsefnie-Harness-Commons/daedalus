"""The scenario sources `tests/test_dashsection_harness.py` drives.

Each string is one Node child: the shell prelude, a setup block, and a
`report` the case asserts on. They live here rather than beside the
assertions because the cases are what the file is for, and the cases
were closing on the 700-line `tests/` ceiling while their fixtures grew
under them -- a file at its ceiling shares its headroom with every other
branch, and this one is read by a suite a reviewer mutates.

Nothing here asserts. A scenario is a fixture, and a fixture that
asserts is a control hiding where nobody looks for it.
"""
TOKEN = 'tok-abcdefghijklmnop'
SEED = (
    "localStorage.setItem('daedalus-token', '" + TOKEN + "');\n")

IMPORT_API = """const api = await bounded(load('api.js'), 'api import',
  _dashnodeStepTimeoutMs);
phase('dashboard call started');
"""


SELECTORS = r"""
(async () => {
const sub = new El('span');
drive.selector('#s06 [data-sub]', sub);
const first = document.querySelector('#s06 [data-sub]');
const second = document.querySelector('#s06 [data-sub]');
first.textContent = '1 active';
let refusal = null;
try { document.querySelector('#s04 [data-sub]'); }
catch (error) { refusal = error.message; }
report({ same: first === second, isSub: first === sub,
  observed: second.textContent, refusal });
})().catch(leave);
"""


SIBLINGS = r"""
(async () => {
const parent = new El('tbody');
const rows = [new El('tr'), new El('tr'), new El('tr')];
parent.append(...rows);
// A raw `undefined` vanishes from the report rather than failing on it,
// so the past-the-end value is reported both as the claim and as a kind
// a reader can see.
const kind = (value) => (value === null ? 'null' : String(value));
report({
  first: rows[0].nextSibling === rows[1],
  second: rows[1].nextSibling === rows[2],
  end: rows[2].nextSibling === null,
  endKind: kind(rows[2].nextSibling),
  orphan: new El('tr').nextSibling === null,
  orphanKind: kind(new El('tr').nextSibling),
  parentNode: rows[0].parentNode === parent,
  depth: rows[1].children.length,
});
})().catch(leave);
"""


INSERT = r"""
(async () => {
const parent = new El('tbody');
const first = new El('tr');
const last = new El('tr');
parent.append(first, last);
const detail = new El('tr');
const returned = parent.insertBefore(detail, last);
report({ returned: returned === detail,
  index: parent.children.indexOf(detail),
  afterFirst: first.nextSibling === detail,
  afterDetail: detail.nextSibling === last,
  afterLast: last.nextSibling === null,
  identity: detail.parentNode === parent,
  size: parent.children.length });
})().catch(leave);
"""


CLASSES = r"""
(async () => {
const el = new El('span');
el.className = 'meta-v dim';
el.classList.add('armed');
const added = { value: el.className,
                has: el.classList.contains('armed') };
el.className = 'meta-v';
const rewritten = { value: el.className,
                    has: el.classList.contains('armed') };
el.classList.add('armed');
el.classList.remove('armed');
report({ added, rewritten, value: el.className,
  has: el.classList.contains('armed'), length: el.classList.length });
})().catch(leave);
"""


# The armed control in its real shape: `armedAction` arms on the first
# click and re-arms a 2500 ms revert, so a clock that ran the callback as
# it was scheduled disarms the button again inside the same click and the
# handler below can never run at all. The three states are the three
# claims: parked, cancelled, fired.
CLOCK = r"""
(async () => {
const { armedAction } = await bounded(load('sections/_util.js'),
  'util import', _dashnodeStepTimeoutMs);
phase('dashboard call started');
const button = new El('button');
button.textContent = 'delete';
let ran = 0;
button.addEventListener('click', armedAction(() => { ran += 1; }));
button.click();
const armed = { ran, text: button.textContent, live: drive.live(),
                has: button.classList.contains('armed') };
const cancelled = setTimeout(() => { ran += 100; }, 2500);
clearTimeout(cancelled);
const afterClear = { id: cancelled, live: drive.live() };
drive.fire(armed.live[0]);
const reverted = { ran, text: button.textContent,
                   has: button.classList.contains('armed') };
button.click();
const rearmed = drive.live();
button.click();
const confirmed = { ran, live: drive.live() };
report({ armed, afterClear, reverted, rearmed, confirmed });
})().catch(leave);
"""


INNER_HTML = r"""
(async () => {
const host = new El('div');
host.innerHTML = '<div class="dim italic small">loading</div>';
const child = host.firstChild;
const parsed = { tag: child.tag, text: child.textContent,
                 value: child.className, size: host.children.length };
let refusal = null;
try { host.innerHTML = '<span>two</span>'; }
catch (error) { refusal = error.message; }
report({ parsed, refusal, untouched: host.children.length,
  stillThere: host.firstChild === child });
})().catch(leave);
"""


UNPLANNED = r"""
(async () => {
""" + SEED + IMPORT_API + r"""
let refusal = null;
try {
  await bounded(api.extCmd('list-block-rules'), 'an unplanned command',
    _dashnodeStepTimeoutMs);
} catch (error) { refusal = error.message; }
await bounded(settle(), 'after the refusal', _dashnodeStepTimeoutMs);
report({ refusal });
})().catch(leave);
"""


DUPLICATE_ROUTE = r"""
(async () => {
drive.route('/tabs', { json: [] });
let refusal = null;
try { drive.route('/tabs', { json: [] }); }
catch (error) { refusal = error.message; }
report({ refusal, planned: drive.planned() });
})().catch(leave);
"""


ENVELOPE = r"""
(async () => {
""" + SEED + IMPORT_API + r"""
const envelope = { id: 'someone-elses-command', deliveryId: 'd1',
                   resultGeneration: 1, result: 'the wrong result' };
drive.route('/command', { did: 'd1', result: 'the right result' });
drive.route('/result?tab=extension', { envelope });
let outcome = null;
try {
  await bounded(api.extCmd('list-block-rules', {}, { timeout: 700 }),
    'a command no envelope matches', _dashnodeStepTimeoutMs);
} catch (error) { outcome = error.message; }
await bounded(settle(), 'after the give-up', _dashnodeStepTimeoutMs);
report({ outcome });
})().catch(leave);
"""


# The result is only its own on the third poll: the first two carry an
# envelope naming another command, which is what a shared result slot that
# has not been replaced yet looks like. The count is the plan's, so the
# assertion pins three polls rather than however many fitted in a budget.
#
# The 5000 ms budget is deliberate and the sleeps are virtual: the loop's
# own `Date.now() - t0` check reads the host clock plus what the pump has
# spent, so the third check sits at 750 ms against 700 and had 200 ms of
# real host time to spare. A budget in seconds turns that margin from a
# window a loaded runner can close into a wall the test cannot cross, and
# it costs no wall time because nothing here sleeps for real.
LATE_ENVELOPE = r"""
(async () => {
""" + SEED + IMPORT_API + r"""
drive.route('/command', { did: 'd1', result: 'the right result' });
drive.route('/result?tab=extension',
  { wrong: 2, result: 'the right result' });
// The envelopes themselves, read back off the responses, so the case pins
// WHICH envelope the wrong polls carried and not merely that three polls
// happened. Reading them through the wire is the only place they are
// visible; the harness records no envelope of its own.
const seen = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (target, init) => {
  const answered = await realFetch(target, init);
  const key = String(target);
  if (key.startsWith('/result') && key.indexOf('consume') < 0) {
    seen.push(await answered.json());
  }
  return answered;
};
const result = await bounded(api.extCmd('list-block-rules', {},
  { timeout: 5000 }), 'a command the third poll answers',
  _dashnodeStepTimeoutMs);
report({ result, seen });
})().catch(leave);
"""


OWN_ENVELOPE = r"""
(async () => {
""" + SEED + IMPORT_API + r"""
drive.route('/command', { did: 'd1', result: 'the right result' });
drive.route('/result?tab=extension', { result: 'the right result' });
const result = await bounded(api.extCmd('list-block-rules', {},
  { timeout: 700 }), 'a command its own envelope answers',
  _dashnodeStepTimeoutMs);
report({ result });
})().catch(leave);
"""


STORAGE = r"""
(async () => {
const sessions = [{ css: 'a{b:c}', tabId: '17', allFrames: true,
                     ts: 1750000000000 }];
localStorage.setItem('daedalus-dash-css-sessions',
                     JSON.stringify(sessions));
const back = JSON.parse(
  localStorage.getItem('daedalus-dash-css-sessions'));
localStorage.setItem('daedalus-dash-css-sessions', '');
report({ back, emptied: localStorage.getItem('daedalus-dash-css-sessions'),
  absent: localStorage.getItem('daedalus-nothing-here') });
})().catch(leave);
"""


UNDECLARED_MODULE = r"""
(async () => {
let refusal = null;
try { load('api.js'); } catch (error) { refusal = error.message; }
report({ refusal, planned: drive.planned() });
})().catch(leave);
"""


# A fan-out double's contract is its breadth, so this registers several
# listeners and varies what each one does. One listener proves none of it.
# The dispatch is live, as `app.js`'s is, so `late` is reached by the
# dispatch that registered it -- and every `emit` is guarded, so a listener
# that throws out of one is reported as a value rather than taking the
# child with it before `report` runs.
BUS = r"""
(async () => {
const seen = [];
let lateAdded = false;
const stopSecond = bus.on((event) => {
  seen.push('second:' + event.type);
  if (lateAdded) return;
  lateAdded = true;
  bus.on((late) => { seen.push('late:' + late.type); });
});
bus.on(() => { seen.push('third'); throw new Error('listener failed'); });
bus.on((event) => { seen.push('fourth:' + event.type); });
let escaped = null;
const dispatch = (type) => {
  try { bus.emit({ type }); }
  catch (error) { if (escaped === null) escaped = error.message; }
};
dispatch('tabs-synced');
// `seen` is one array the whole run appends to, so each snapshot copies it
// at the moment it was taken rather than aliasing what came later.
const first = { seen: seen.slice(), errors: ERRORS.length };
dispatch('tab-updated');
const second = { seen: seen.slice() };
stopSecond();
dispatch('tab-unregistered');
report({ first, second, third: { seen: seen.slice() }, escaped,
  errors: ERRORS.slice() });
})().catch(leave);
"""


# A 4242 ms timer parked inside a command window is the timer the pump must
# not spend. It is parked from inside the `/command` response, so it sits in
# the window ahead of the poll sleep, and a pump that spends by index
# fires it -- which is a callback no scenario fired and no browser would
# run at that moment.
PUMP_SELECTIVITY = r"""
(async () => {
""" + SEED + IMPORT_API + r"""
let planted = 0;
const realFetch = globalThis.fetch;
globalThis.fetch = async (target, init) => {
  const answered = await realFetch(target, init);
  if (String(target) === '/command') {
    setTimeout(() => { planted += 1; }, 4242);
  }
  return answered;
};
drive.route('/command', { did: 'd1', result: 'the right result' });
drive.route('/result?tab=extension', { result: 'the right result' });
const result = await bounded(api.extCmd('list-block-rules', {},
  { timeout: 700 }), 'a command with a timer parked beside it',
  _dashnodeStepTimeoutMs);
await bounded(settle(), 'after the command', _dashnodeStepTimeoutMs);
report({ result, planted, live: drive.live() });
})().catch(leave);
"""


# The three refusals that are cheap to reach and were unreached: a
# `Headers` bag the transport cannot read a credential from, a result poll
# with no command behind it, and a selector registered twice.
REFUSALS = r"""
(async () => {
let bag = null;
let polled = null;
let twice = null;
try {
  await bounded(fetch('/tabs', { headers: new Headers({ token: 'x' }) }),
    'a headers bag the transport cannot read', _dashnodeStepTimeoutMs);
} catch (error) { bag = error.message; }
drive.route('/result?tab=extension', { result: [] });
try {
  await bounded(fetch('/result?tab=extension'), 'a poll with no command',
    _dashnodeStepTimeoutMs);
} catch (error) { polled = error.message; }
const sub = new El('span');
drive.selector('#s08 [data-sub]', sub);
try { drive.selector('#s08 [data-sub]', sub); }
catch (error) { twice = error.message; }
report({ bag, polled, twice, requests: REQUESTS.length,
  errors: ERRORS.slice() });
})().catch(leave);
"""


# `console.error` is where a section's failed mount and `app.js`'s bus
# report reach a scenario, so the recorder has to hold a line it was given
# and a value whose own `toString` throws.
CONSOLE_ERROR = r"""
(async () => {
console.error('[mount] net-capture failed', new Error('boom'));
let escaped = null;
const unprintable = { toString() { throw new Error('no'); } };
try { console.error('[bus] listener failed', unprintable); }
catch (error) { escaped = error.message; }
report({ escaped });
})().catch(leave);
"""


PHASE_TRACE = r"""
(async () => {
""" + SEED + IMPORT_API + r"""
drive.route('/command', { did: 'd1', result: [] });
drive.route('/result?tab=extension', { result: [] });
await bounded(api.extCmd('list-block-rules'), 'the command',
  _dashnodeStepTimeoutMs);
await bounded(settle(), 'settled', _dashnodeStepTimeoutMs);
report();
})().catch(leave);
"""
