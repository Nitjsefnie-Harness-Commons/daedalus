#!/usr/bin/env python3
"""What the section harness guarantees, proven by breaking it.

The shell is a guard, so a green run on the tree it was written against
proves nothing. Each case drives the real `dashboard/api.js` or the shell
itself through a Node child and reads the report it printed.

None of this asserts how a section behaves. It asserts that a scenario
can see what it drove: a selector that answers the same element twice, a
sibling walk that ends, an insertion that agrees with it, a class set that
agrees with `className` in both directions, a timer that stays parked
until it is fired, a refusal recorded as well as thrown, and an envelope
the fake cannot pass off as somebody else's.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashsection  # noqa: E402
import _util  # noqa: E402
from _dashnode import run_dashboard_node  # noqa: E402
from _dashsection import build_harness, run_scenario, section_path  # noqa

_TOKEN = 'tok-abcdefghijklmnop'
_SEED = (
    "localStorage.setItem('daedalus-token', '" + _TOKEN + "');\n")

_IMPORT_API = """const api = await bounded(load('api.js'), 'api import',
  _dashnodeStepTimeoutMs);
phase('dashboard call started');
"""


_SELECTORS = r"""
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


_SIBLINGS = r"""
(async () => {
const parent = new El('tbody');
const rows = [new El('tr'), new El('tr'), new El('tr')];
parent.append(...rows);
report({
  first: rows[0].nextSibling === rows[1],
  second: rows[1].nextSibling === rows[2],
  end: rows[2].nextSibling,
  orphan: new El('tr').nextSibling,
  parentNode: rows[0].parentNode === parent,
  depth: rows[1].children.length,
});
})().catch(leave);
"""


_INSERT = r"""
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
  afterLast: last.nextSibling,
  identity: detail.parentNode === parent,
  size: parent.children.length });
})().catch(leave);
"""


_CLASSES = r"""
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
_CLOCK = r"""
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


_INNER_HTML = r"""
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


_UNPLANNED = r"""
(async () => {
""" + _SEED + _IMPORT_API + r"""
let refusal = null;
try {
  await bounded(api.extCmd('list-block-rules'), 'an unplanned command',
    _dashnodeStepTimeoutMs);
} catch (error) { refusal = error.message; }
await bounded(settle(), 'after the refusal', _dashnodeStepTimeoutMs);
report({ refusal });
})().catch(leave);
"""


_DUPLICATE_ROUTE = r"""
(async () => {
drive.route('/tabs', { json: [] });
let refusal = null;
try { drive.route('/tabs', { json: [] }); }
catch (error) { refusal = error.message; }
report({ refusal, planned: drive.planned() });
})().catch(leave);
"""


_ENVELOPE = r"""
(async () => {
""" + _SEED + _IMPORT_API + r"""
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


_OWN_ENVELOPE = r"""
(async () => {
""" + _SEED + _IMPORT_API + r"""
drive.route('/command', { did: 'd1', result: 'the right result' });
drive.route('/result?tab=extension', { result: 'the right result' });
const result = await bounded(api.extCmd('list-block-rules', {},
  { timeout: 700 }), 'a command its own envelope answers',
  _dashnodeStepTimeoutMs);
report({ result });
})().catch(leave);
"""


_STORAGE = r"""
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


_UNDECLARED_MODULE = r"""
(async () => {
let refusal = null;
try { load('api.js'); } catch (error) { refusal = error.message; }
report({ refusal, planned: drive.planned() });
})().catch(leave);
"""


_PHASE_TRACE = r"""
(async () => {
""" + _SEED + _IMPORT_API + r"""
drive.route('/command', { did: 'd1', result: [] });
drive.route('/result?tab=extension', { result: [] });
await bounded(api.extCmd('list-block-rules'), 'the command', _dashnodeStepTimeoutMs);
await bounded(settle(), 'settled', _dashnodeStepTimeoutMs);
report();
})().catch(leave);
"""


def _phases(scenario, sections=()):
    """The diagnostic checkpoint labels one scenario run recorded."""
    result = run_dashboard_node(build_harness(scenario, sections=sections))
    return re.findall(r'^\[phase\] (.+)$', result.stderr, re.MULTILINE)


def test_a_registered_selector_answers_the_same_element_twice(_tmp):
    """`block-rules`, `cookies`, `fetch-timings` and `net-capture` all
    write `document.querySelector('#sNN [data-sub]').textContent` with no
    null guard, and a double that mints a fresh element per call throws
    that write away. Returning a throwaway leaves `observed` empty."""
    report = run_scenario(_SELECTORS)
    assert report['same'] is True, report
    assert report['isSub'] is True, report
    assert report['observed'] == '1 active', report
    assert report['refusal'] is not None, report
    assert 'unmodeled selector' in report['refusal'], report
    assert '#s04 [data-sub]' in report['refusal'], report


def test_an_unregistered_selector_is_refused_by_name(_tmp):
    """The refusal names the selector, because a silent `null` turns every
    happy-path assertion in a section suite into an error-pane assertion
    that reads as the section's behaviour."""
    report = run_scenario(_SELECTORS)
    assert report['refusal'] == 'unmodeled selector #s04 [data-sub]', report


def test_next_sibling_walks_and_ends(_tmp):
    """`net-capture` toggles a detail row through `tr.nextSibling`, so a
    walk that answers the first child forever, or `undefined` past the
    end rather than `null`, is a row that never collapses."""
    report = run_scenario(_SIBLINGS)
    assert report['first'] is True, report
    assert report['second'] is True, report
    assert report['end'] is None, report
    assert report['orphan'] is None, report
    assert report['parentNode'] is True, report
    assert report['depth'] == 0, report


def test_insert_before_agrees_with_the_sibling_walk(_tmp):
    """`net-capture` inserts with `tr.parentNode.insertBefore(detail,
    tr.nextSibling)`, so the insertion has to land at the reference's own
    index in the parent that owns the row, and the walk has to agree
    afterwards. Appending instead leaves `index` at 2."""
    report = run_scenario(_INSERT)
    assert report['returned'] is True, report
    assert report['index'] == 1, report
    assert report['afterFirst'] is True, report
    assert report['afterDetail'] is True, report
    assert report['afterLast'] is None, report
    assert report['identity'] is True, report
    assert report['size'] == 3, report


def test_the_class_set_and_class_name_agree_in_both_directions(_tmp):
    """`armedAction` adds and removes `.armed` while `_util.h()` writes
    `className` as a whole string, so a set that does not follow the
    string leaves `rewritten.has` true and a control that only ever adds
    passes against a set that never removes."""
    report = run_scenario(_CLASSES)
    assert report['added'] == {'value': 'meta-v dim armed', 'has': True}, report
    assert report['rewritten'] == {'value': 'meta-v', 'has': False}, report
    assert report['value'] == 'meta-v', report
    assert report['has'] is False, report
    assert report['length'] == 1, report


def test_a_parked_timer_runs_only_when_fired(_tmp):
    """The armed control in the shape the section uses. A clock that ran
    the callback as it was scheduled disarms the button inside the first
    click, so `confirmed.ran` would stay 0; a clock that never fires it
    leaves `reverted.text` at the confirm label."""
    report = run_scenario(_CLOCK, sections=('sections/_util.js',))
    assert report['armed']['ran'] == 0, report
    assert report['armed']['text'] == 'sure?', report
    assert report['armed']['has'] is True, report
    assert len(report['armed']['live']) == 1, report
    # clearTimeout cancelled the second timer, so the id it returned is
    # gone rather than waiting to be fired into the assertions below.
    assert report['afterClear']['id'] not in report['armed']['live'], report
    assert report['afterClear']['live'] == report['armed']['live'], report
    assert report['reverted'] == {'ran': 0, 'text': 'delete', 'has': False}, \
        report
    assert len(report['rearmed']) == 1, report
    assert report['confirmed']['ran'] == 1, report
    assert report['confirmed']['live'] == [], report


def test_inner_html_yields_a_child_and_refuses_what_it_cannot_parse(_tmp):
    """The three list hosts reset to one bare `<div class="...">` and read
    it back, so the parse has to produce an observable child. A shape it
    does not model is refused by name and leaves the element alone, rather
    than emptying a host the section is about to render into."""
    report = run_scenario(_INNER_HTML)
    assert report['parsed'] == {'tag': 'div', 'text': 'loading',
                                'value': 'dim italic small', 'size': 1}, report
    assert report['refusal'] is not None, report
    assert 'does not model an innerHTML assignment of' in report['refusal'], \
        report
    assert '<span>two</span>' in report['refusal'], report
    assert report['untouched'] == 1, report
    assert report['stillThere'] is True, report


def test_an_unplanned_request_is_refused_and_recorded(_tmp):
    """Both halves, per the #1083 wording: the target the scenario never
    declared is on the record AND the request is refused. A double that
    answers 200 for everything unrecognised leaves `unplanned` empty and
    `refusal` null while the section reads the refusal as a result."""
    report = run_scenario(_UNPLANNED, sections=('api.js',))
    assert report['unplanned'] == [{'n': 1, 'target': '/command'}], report
    assert report['requests'][0]['target'] == '/command', report
    assert report['requests'][0]['method'] == 'PUT', report
    assert report['requests'][0]['authorization'] == 'Bearer ' + _TOKEN, report
    assert report['refusal'] == 'unexpected request /command', report


def test_a_duplicate_route_is_refused(_tmp):
    """A second plan for a target is a scenario bug, and answering it
    silently lets the second plan decide the answer to the first."""
    report = run_scenario(_DUPLICATE_ROUTE)
    assert report['refusal'] == 'route already planned: /tabs', report
    assert report['planned'] == ['/tabs'], report


def test_an_envelope_naming_another_command_is_not_a_match(_tmp):
    """The fake has to be able to tell a wrong result from a right one, and
    the shipped loop is what decides: it skips an envelope whose `id` is
    not the command's and keeps polling to its own budget. Repairing the
    envelope in the double hands the section a result it never sent."""
    report = run_scenario(_ENVELOPE, sections=('api.js',))
    assert report['outcome'].startswith('Timeout (700ms) waiting for '), report
    polls = [r for r in report['requests'] if 'consume' not in r['target']]
    assert len(polls) == 4, report
    assert report['unplanned'] == [], report


def test_the_envelope_the_transport_anchors_is_the_commands_own(_tmp):
    """The other half of the same claim: a default envelope takes its `id`
    and `deliveryId` from the command the transport received, so a
    matching command completes. An envelope anchored on a constant matches
    nothing, and the pair of cases is what stops either one passing alone."""
    report = run_scenario(_OWN_ENVELOPE, sections=('api.js',))
    assert report['result'] == 'the right result', report
    assert report['unplanned'] == [], report
    consumed = [r['target'] for r in report['requests'] if 'consume' in r['target']]
    assert consumed == ['/result?tab=extension&consume=1&expected=1'], report


def test_the_session_store_round_trips(_tmp):
    """`css-injector` keeps its session list in `localStorage` as JSON. A
    `setItem` that does nothing leaves the store permanently empty, which
    reads as a module that never records a session."""
    report = run_scenario(_STORAGE)
    assert report['back'] == [{'css': 'a{b:c}', 'tabId': '17',
                               'allFrames': True, 'ts': 1750000000000}], report
    assert report['emptied'] == '', report
    assert report['absent'] is None, report
    assert report['storage']['daedalus-dash-css-sessions'] == '', report


def test_loading_an_undeclared_module_is_refused(_tmp):
    """`build_harness` passes one path per declared section, so a name it
    was not given has no `process.argv` slot to read. Importing something
    else instead would load a module the scenario never declared."""
    report = run_scenario(_UNDECLARED_MODULE)
    assert report['refusal'] == 'no module argument for api.js', report
    assert report['planned'] == [], report


def test_a_scenario_records_the_six_phase_checkpoints(_tmp):
    """`test_dashboard_harness.py` pins this trace for every shipped
    harness. `load` emits the two import checkpoints and `report` the two
    that close the run, so a scenario cannot emit them out of order or
    leave one out."""
    assert _phases(_PHASE_TRACE, ('api.js',)) == [
        'dashboard harness started',
        'dashboard module import started',
        'dashboard module imported',
        'dashboard call started',
        'dashboard call settled',
        'dashboard harness finished',
    ]


def test_a_scenario_cannot_disagree_with_its_declared_bound(_tmp):
    """`build_harness` counts the bounds in the assembled source, so the
    count a scenario's own `await bounded(` earns is the count the child
    is timed against."""
    harness = build_harness(
        "await bounded(work, 'a step', _dashnodeStepTimeoutMs);\n"
        "await bounded(more, 'another step', _dashnodeStepTimeoutMs);\n",
        sections=())
    assert harness.bounded_steps == 2, harness.bounded_steps
    assert harness.module is True, harness
    assert harness.arguments == (), harness


def test_a_module_name_that_escapes_the_dashboard_is_refused(_tmp):
    """`section_path` mirrors `_dashshell.dashboard_module`: the name is
    resolved under `dashboard/`, and a name that climbs out of it is
    refused rather than resolved."""
    assert section_path('sections/net-capture.js').name == 'net-capture.js'
    assert section_path('api.js').name == 'api.js'
    for name in ('../server.py', '/etc/passwd', ''):
        try:
            section_path(name)
        except ValueError:
            continue
        raise AssertionError(f'escaping module name accepted: {name!r}')


def test_the_public_surface_is_the_set_the_suite_imports(_tmp):
    """`__all__` is a promise, and a name in it that does not exist is a
    section suite's `ImportError` rather than a test failure here."""
    assert _dashsection.__all__ == ['SHELL', 'build_harness', 'run_scenario',
                                    'section_path'], _dashsection.__all__
    for name in _dashsection.__all__[1:]:
        assert callable(getattr(_dashsection, name)), name
    assert isinstance(_dashsection.SHELL, str), type(_dashsection.SHELL)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashsect_')


if __name__ == '__main__':
    raise SystemExit(main())
