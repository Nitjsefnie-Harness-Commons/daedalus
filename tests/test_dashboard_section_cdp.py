#!/usr/bin/env python3
"""The CDP panel, run rather than read.

`dashboard/sections/cdp.js` sends one raw protocol method and shows the
answer, so what the pane says at each moment is the only report the
operator gets: a method that was refused, a params box that will not
parse and a bridge that detached all look alike unless the pane
distinguishes them. The harness mounts the shipped section over the real
`dashboard/api.js` and the real `_util.js` in Node, drives the toolbar,
and reads the pane beside the parsed body of every request the section
made.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import run_scenario  # noqa: E402

SECTION = ('sections/cdp.js',)

EXTRA = r"""
const pane = () => {
  const el = container.find('[data-role=result]');
  return [el.className, el.textContent];
};
const sent = () => REQUESTS.filter((r) => r.target === '/command')
  .map((r) => r.body);
const methods = () => container.all()
  .filter((el) => el.tag === 'datalist')[0].children.map((o) => o.value);
const polls = () => REQUESTS.filter((r) => r.target.slice(0, 7) === '/result'
  && r.target.indexOf('consume') < 0).length;
"""

TABS = ("const TABS = [{ tabId: 11, title: 'first tab',\n"
        "  url: 'https://one.example.com/one' },\n"
        "  { tabId: 22, title: '', url: 'https://two.example.com/two' }];\n"
        "drive.route('/tabs', { json: TABS });\n")

# `bindTabSelector` is called with a placeholder and no `errorLabel`, so
# both of its failure paths return without touching the select. These two
# setups are how a scenario reaches them: no token at all, and a `/tabs`
# the bridge answers with a status `api.js` turns into a throw.
NO_TOKEN = "localStorage.removeItem('daedalus-token');\n"
TABS_FAILING = ("const TABS = [{ tabId: 11, title: 'first tab',\n"
                "  url: 'https://one.example.com/one' }];\n"
                "drive.route('/tabs',"
                " { status: 500, json: { error: 'tabs unavailable' } });\n")

# The control values the cases set, as the JavaScript literals they are
# assigned from.
METHOD = '"Page.reload"'
METHOD_PADDED = '"  Page.reload  "'
METHOD_BLANK = '""'
PARAMS_BLANK = '""'
PARAMS_OBJECT = '\'{"depth": 3}\''
PARAMS_BROKEN = '"{oops"'
TAB_CHOSEN = '"22"'

ANSWER_TEXT = "answer('cdp', { result: 'frameNavigated' });\n"
ANSWER_OBJECT = ("answer('cdp', { result: { frameId: 'A1',"
                 " loaderId: 'L1' } });\n")
ANSWER_REFUSED = ("answer('cdp',"
                  " { error: 'detached before the reply' });\n")

SETTLED = ('await bounded(settle(), "after the run",'
           ' _dashnodeStepTimeoutMs);\n')

COMMON_METHODS = [
    'Page.captureScreenshot', 'Page.reload', 'Page.navigate',
    'Runtime.evaluate', 'Runtime.enable', 'DOM.getDocument',
    'DOM.querySelector', 'Network.enable', 'Network.getCookies',
    'Network.setCookie', 'Network.getResponseBody', 'Target.getTargets',
    'Emulation.setDeviceMetricsOverride',
    'Emulation.clearDeviceMetricsOverride', 'Input.dispatchMouseEvent',
    'Input.dispatchKeyEvent',
]


def _set(role, literal):
    """One operator action on one control, as the scenario reads it."""
    return ('container.find("[data-role=' + role + ']").value = '
            + literal + ';\n')


def _press(method=None, params=None, tab=None):
    """Fill the form the way an operator would, then press RUN.

    The defaults resolve at call time, so a case reads as the control
    values it means rather than as whatever the module held at import.
    """
    method = METHOD if method is None else method
    params = PARAMS_BLANK if params is None else params
    chosen = '' if tab is None else _set('tab', tab)
    return (_set('method', method) + _set('params', params) + chosen
            + 'button("RUN").click();\n')


# A result leg that never matches: the loop spends its whole budget and
# gives up, and the number of polls it spent is the budget.
NEVER_ANSWERED = ("drive.route('/result?tab=extension',"
                  " { pending: true });\n")


def scenario(body, *, setup=None, answers=(), plan=shared.COMMAND,
             by_type=True):
    """One child: seed the token, plan every answer, mount, then drive.

    `setup` lands first because the answer table is written in the scope
    `setup` defines.
    """
    table = shared.results(*answers) if by_type else ''
    return ('(async () => {\n' + shared.SEED + shared.PRELUDE + EXTRA
            + shared.open_section(SECTION[0])
            + (TABS if setup is None else setup) + plan + table
            + shared.MOUNT + shared.SETTLE + body
            + '})().catch(leave);\n')


def _run(body, *, setup=None, answers=(), plan=shared.COMMAND,
         by_type=True):
    return run_scenario(
        scenario(body, setup=setup, answers=answers, plan=plan,
                 by_type=by_type),
        sections=SECTION)


def test_the_mount_offers_the_tabs_and_leaves_the_pane_empty(_tmp):
    """The pane starts in the `empty` state and the mount sends nothing
    at all: the one request it makes is the tab list `bindTabSelector`
    issues, and a mount that sent a CDP command would reach
    `extension/worker/cdp.js:30`, which attaches a debugger, before the
    operator asked for anything."""
    report = _run('report({ pane: pane(), sent: sent(),'
                  ' options: container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent) });\n')
    assert report['pane'] == ['pane empty', 'no result yet.'], report
    assert report['sent'] == [], report
    assert report['options'] == ['(active tab)', '11  first tab',
                                 '22  https://two.example.com/two'], report
    assert report['unplanned'] == [], report


def test_the_tab_list_says_nothing_when_there_is_no_token(_tmp):
    """`bindTabSelector` returns before `api.get('/tabs')` when the token
    is empty, and this section passes no `errorLabel`, so the select keeps
    the `(active tab)` option the markup shipped with. A RUN would reach
    `runCommand` and be refused at `api.js:128`; this case does not
    press it, and states instead that the mount left the pane empty."""
    report = _run('report({ options: container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent),\n'
                  '  pane: pane() });\n', setup=TABS + NO_TOKEN)
    assert report['options'] == ['(active tab)'], report
    assert report['pane'] == ['pane empty', 'no result yet.'], report
    assert report['unplanned'] == [], report


def test_the_tab_list_says_nothing_when_the_bridge_refuses_it(_tmp):
    """The `/tabs` catch renders the error only when an `errorLabel` was
    passed, and this section passes none -- so a 500 leaves the select on
    its placeholder with nothing on the pane to say why. The panel is
    still runnable: an omitted tab is the active tab."""
    report = _run(_press() + SETTLED + 'report({ options:'
                  ' container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent),\n'
                  '  pane: pane() });\n',
                  setup=TABS_FAILING, answers=(ANSWER_TEXT,))
    assert report['options'] == ['(active tab)'], report
    assert report['pane'][0] == 'pane flash', report
    assert report['unplanned'] == [], report


def test_the_datalist_carries_the_sixteen_common_methods_in_order(_tmp):
    """`COMMON` is a fixed list the datalist is built from, and the case
    pins the list AND its order: the browser offers the options in DOM
    order, so a member moved or a name respelled changes what the panel
    offers first."""
    report = _run('report({ methods: methods() });\n')
    assert report['methods'] == COMMON_METHODS, report


def test_the_five_result_pane_states_are_distinguishable(_tmp):
    """Five moments, five readings, each pinned by BOTH the class and the
    text. The invalid-JSON and the runtime-error states share `pane err`
    and are told apart only by the prefix, which is why the prefix is the
    assertion and not the engine's message after it."""
    initial = _run('report({ pane: pane() });\n', answers=(ANSWER_TEXT,))
    flying = _run(_press() + 'report({ pane: pane(), sent: sent() });\n',
                  answers=(ANSWER_TEXT,))
    worked = _run(_press() + SETTLED + 'report({ pane: pane() });\n',
                  answers=(ANSWER_TEXT,))
    broken = _run(_press(params=PARAMS_BROKEN) + SETTLED
                  + 'report({ pane: pane() });\n', answers=(ANSWER_TEXT,))
    refused = _run(_press() + SETTLED + 'report({ pane: pane() });\n',
                   answers=(ANSWER_REFUSED,))
    assert initial['pane'] == ['pane empty', 'no result yet.'], initial
    assert flying['pane'] == ['pane', 'running…'], flying
    assert worked['pane'] == ['pane flash', 'frameNavigated'], worked
    assert broken['pane'][0] == 'pane err', broken
    assert broken['pane'][1].startswith('invalid params JSON: '), broken
    assert refused['pane'] == ['pane err', 'detached before the reply'], \
        refused


def test_a_run_in_flight_has_not_reached_the_wire_yet(_tmp):
    """The pane reads `running…` before the command leg is recorded, which
    is what makes it a state rather than a leftover: a pane that said
    `running…` after a reply arrived would be a lie the operator reads
    as a slow bridge."""
    report = _run(_press() + 'report({ pane: pane(), sent: sent() });\n',
                  answers=(ANSWER_TEXT,))
    assert report['pane'] == ['pane', 'running…'], report
    assert report['sent'] == [], report


def test_a_call_sends_the_trimmed_method_and_an_empty_params_object(_tmp):
    """`params` is defaulted to `{}` and the key is always written, so an
    empty box is an empty object rather than an absent member -- the
    difference between "no arguments" and "nothing to send" on the
    bridge. The method is trimmed on the way in."""
    report = _run(_press(METHOD_PADDED) + SETTLED
                  + 'report({ sent: sent(), pane: pane() });\n',
                  answers=(ANSWER_TEXT,))
    first = report['sent'][0]
    assert first['type'] == 'cdp', report
    assert first['method'] == 'Page.reload', report
    assert first['params'] == {}, report
    assert 'params' in first, report
    assert 'tabId' not in first, report
    assert report['pane'][0] == 'pane flash', report
    assert report['unplanned'] == [], report


def test_a_params_box_that_holds_json_reaches_the_command_parsed(_tmp):
    """`JSON.parse(raw)` is the only validation, so what the worker
    receives is the parsed value and not the text the operator typed."""
    report = _run(_press(params=PARAMS_OBJECT) + SETTLED
                  + 'report({ sent: sent(), pane: pane() });\n',
                  answers=(ANSWER_TEXT,))
    assert report['sent'][0]['params'] == {'depth': 3}, report
    assert report['pane'][0] == 'pane flash', report


def test_a_chosen_tab_arrives_as_the_string_the_select_holds(_tmp):
    """`fields.tabId = tabSel.value` with no `Number()` around it, which is
    the difference from `net-capture.js` and `css-injector.js` -- both wrap
    the same control in `Number()`. A test that asserted equality alone
    would pass against both, so the type is what this case states. The
    worker's own guard is about emptiness, not type --
    `extension/worker/cdp.js:13` is `!chromeTabId` -- and it is
    `extension/worker/cdp.js:20-21` that takes a string, so a tab id
    this panel sends as one is a tab id the worker parses."""
    report = _run(_press(tab=TAB_CHOSEN) + SETTLED
                  + 'report({ sent: sent() });\n',
                  answers=(ANSWER_TEXT,))
    first = report['sent'][0]
    assert first['tabId'] == '22', report
    assert isinstance(first['tabId'], str), report


def test_a_blank_method_toasts_method_required_and_sends_nothing(_tmp):
    """The guard returns before the params are read, so a pane already
    holding a result keeps it: a blank method with a stale result on
    screen is the operator's last answer, not a new one."""
    report = _run(_press() + SETTLED + _press(method=METHOD_BLANK) + SETTLED
                  + 'report({ pane: pane(), toasts: toasts(),'
                    ' sent: sent().length });\n', answers=(ANSWER_TEXT,))
    assert report['toasts'] == [{'type': 'warn',
                                 'text': 'method required'}], report
    assert report['pane'] == ['pane flash', 'frameNavigated'], report
    assert report['sent'] == 1, report


def test_params_that_will_not_parse_render_the_invalid_state_and_send_nothing(
        _tmp):
    """`JSON.parse` throws before `extCmd` is called, so no command is
    written and nothing is toasted -- the pane carries the whole report.
    The assertion is the PREFIX and never the rest, because the rest is
    the `e.message` of the engine's `JSON.parse` and this suite does not
    state it."""
    report = _run(_press(params=PARAMS_BROKEN) + SETTLED
                  + 'report({ pane: pane(), sent: sent(),'
                    ' toasts: toasts() });\n', answers=(ANSWER_TEXT,))
    assert report['sent'] == [], report
    assert report['toasts'] == [], report
    assert report['pane'][0] == 'pane err', report
    assert report['pane'][1].startswith('invalid params JSON: '), report
    assert len(report['pane'][1]) > len('invalid params JSON: '), report


def test_a_bridge_failure_renders_the_pane_and_toasts_nothing(_tmp):
    """A bridge failure reaches the result pane and nothing else. The
    `toasts() == []` half is the pin: a module that toasted as well
    would still render this pane. `net-capture.js`'s poll does the same
    to its own status line, so what the two share is the absence of a
    toast, not the presence of the pane."""
    report = _run(_press() + SETTLED + 'report({ pane: pane(),'
                  ' toasts: toasts() });\n', answers=(ANSWER_REFUSED,))
    assert report['pane'] == ['pane err', 'detached before the reply'], \
        report
    assert report['toasts'] == [], report


def test_a_string_answer_reaches_the_pane_unquoted(_tmp):
    """`pretty` returns a string as it is and `JSON.stringify`s anything
    else, so a string answer reaches the pane as the text itself rather
    than as a quoted string the operator would have to read past."""
    report = _run(_press() + SETTLED + 'report({ pane: pane() });\n',
                  answers=(ANSWER_TEXT,))
    assert report['pane'] == ['pane flash', 'frameNavigated'], report
    assert '"' not in report['pane'][1], report


def test_an_object_answer_reaches_the_pane_indented(_tmp):
    """Anything else goes through `JSON.stringify(v, null, 2)`, so the
    pane is the two-space-indented form rather than one long line -- the
    difference between reading an answer and scrolling it."""
    report = _run(_press() + SETTLED + 'report({ pane: pane() });\n',
                  answers=(ANSWER_OBJECT,))
    assert report['pane'] == ['pane flash', '{\n'
                              '  "frameId": "A1",\n'
                              '  "loaderId": "L1"\n'
                              '}'], report


def test_a_call_that_never_answers_gives_up_at_twenty_seconds(_tmp):
    """`extCmd('cdp', fields, { timeout: 20000 })` names the budget
    explicitly, and it is observable only as the number of result polls
    the loop spends -- eighty at 250 ms each. The pane is the only place
    that failure is reported, so the eighty is what says the budget was
    the one the module asked for and not the fifteen-second default."""
    report = _run(_press() + SETTLED
                  + 'report({ polls: polls(), pane: pane(),'
                    ' toasts: toasts() });\n',
                  plan=NEVER_ANSWERED + shared.COMMAND, by_type=False)
    assert report['polls'] == 80, report
    assert report['pane'][0] == 'pane err', report
    assert report['pane'][1].startswith(
        'Timeout (20000ms) waiting for _cdp_1_'), report
    assert report['toasts'] == [], report
    assert report['unplanned'] == [], report


def test_a_bus_tab_event_repopulates_the_select_and_keeps_the_choice(_tmp):
    """`bindTabSelector` is called inside `mount`, which is what
    `app.js` calls with the bus. The event has to reach a real `/tabs`
    fetch -- counting them is what separates a registered listener from
    one that never was."""
    report = _run('container.find("[data-role=tab]").value = "11";\n'
                  'const before = REQUESTS.length;\n'
                  'bus.emit({ type: "tabs-synced" });\n' + SETTLED
                  + 'report({ before, after: REQUESTS.length,\n'
                    '  chosen: container.find("[data-role=tab]").value,\n'
                    '  options: container.find("[data-role=tab]")'
                    '.options.map((o) => o.textContent) });\n')
    assert report['after'] - report['before'] == 1, report
    assert report['chosen'] == '11', report
    assert report['options'] == ['(active tab)', '11  first tab',
                                 '22  https://two.example.com/two'], report
    assert report['unplanned'] == [], report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashcdp_')


if __name__ == '__main__':
    raise SystemExit(main())
