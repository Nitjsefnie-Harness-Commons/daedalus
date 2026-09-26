#!/usr/bin/env python3
"""The CSS injector panel, run rather than read.

`dashboard/sections/css-injector.js` injects and removes CSS through
`chrome.scripting` and keeps its own session list in `localStorage`, so a
command that carries the wrong tab, drops the all-frames flag, or sends
whitespace the bridge will reject is an operator looking at a page that
did not change. The harness mounts the shipped section over the real
`dashboard/api.js` and the real `_util.js` in Node, drives the form and
the session table, and reads the parsed body of every request beside the
cells the section rendered and the store it wrote.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import run_scenario  # noqa: E402

SECTION = ('sections/css-injector.js',)

TABS = ("const TABS = [{ tabId: 11, title: 'first tab',\n"
        "  url: 'https://one.example.com/one' },\n"
        "  { tabId: 22, title: '', url: 'https://two.example.com/two' }];\n")

# The store the mount reads, and the plan for the one GET it makes.
SEEDED = TABS + (
    "const SEEDED = [{ css: 'b{--seed:2}', tabId: '', allFrames: false,\n"
    "  ts: 1750000000000 },\n"
    "  { css: 'a{--seed:1}', tabId: '11', allFrames: true,\n"
    "  ts: 1750000000060 }];\n"
    "localStorage.setItem('daedalus-dash-css-sessions',\n"
    "  JSON.stringify(SEEDED));\n"
    "drive.route('/tabs', { json: TABS });\n")

TABS_ONLY = TABS + "drive.route('/tabs', { json: TABS });\n"

# `bindTabSelector` is called with a placeholder and no `errorLabel`, so
# both of its failure paths return without touching the select. These two
# setups are how a scenario reaches them: no token at all, and a `/tabs`
# the bridge answers with a status, which `api.js:58` turns into a throw.
NO_TOKEN = "localStorage.removeItem('daedalus-token');\n"
TABS_FAILING = ("const TABS = [{ tabId: 11, title: 'first tab',\n"
                "  url: 'https://one.example.com/one' }];\n"
                "drive.route('/tabs',"
                " { status: 500, json: { error: 'tabs unavailable' } });\n")

INJECTED = "answer('inject-css', { result: { injected: 13, tabId: 5 } });\n"
INJECTED_11 = ("answer('inject-css',"
               " { result: { injected: 13, tabId: 11 } });\n")
INJECTED_PLAIN = "answer('inject-css', { result: {} });\n"
INJECTED_9 = "answer('inject-css', { result: { injected: 9, tabId: 0 } });\n"
INJECT_REFUSED = "answer('inject-css', { error: 'cannot access the tab' });\n"
REMOVED = "answer('remove-css', { result: {} });\n"
REMOVED_13 = "answer('remove-css', { result: { removed: 13 } });\n"
REMOVE_REFUSED = "answer('remove-css', { error: 'removeCSS rejected it' });\n"
REMOVE_NO_CSS = "answer('remove-css', { error: 'no such css' });\n"

SETTLED = ('await bounded(settle(), "after the click",'
           ' _dashnodeStepTimeoutMs);\n')


def scenario(body, *, setup=SEEDED, answers=(), plan=shared.COMMAND):
    """One child: seed the token, plan every answer, mount, then drive.

    `setup` lands first because the answer table is written in the scope
    `setup` defines.
    """
    return ('(async () => {\n' + shared.SEED + shared.PRELUDE
            + shared.open_section(SECTION[0]) + setup + plan
            + shared.results(*answers)
            + shared.MOUNT + body + '})().catch(leave);\n')


def _run(body, *, setup=SEEDED, answers=(), plan=shared.COMMAND):
    return run_scenario(
        scenario(body, setup=setup, answers=answers, plan=plan),
        sections=SECTION)


def _store(report):
    raw = report['storage'].get('daedalus-dash-css-sessions')
    return json.loads(raw) if raw else []


def test_the_tab_list_says_nothing_when_there_is_no_token(_tmp):
    """`bindTabSelector` returns before `api.get('/tabs')` when the token
    is empty, and the zero-request half is the claim no other tab-selector
    case in this suite carries: a select the mount never populated has
    nothing to say even with a command behind it. `(active tab)` is the
    option `css-injector.js:18` ships in the MARKUP, not the one the
    `placeholder` argument appends -- that append is on the success path,
    after the try/catch, so it never runs here. `runCommand` still
    refuses a command at `api.js:128`; this case does not press anything
    to say so."""
    report = _run(SETTLED + 'report({ options:'
                  ' container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent),\n'
                  '  toasts: toasts(), requests: REQUESTS.length });\n',
                  setup=TABS_ONLY + NO_TOKEN)
    assert report['options'] == ['(active tab)'], report
    assert report['toasts'] == [], report
    # Not one request: a guard that fell through would have asked for the
    # list and been answered with the two tabs the plan declares.
    assert report['requests'] == 0, report
    assert report['unplanned'] == [], report


def test_the_tab_list_says_nothing_when_the_bridge_refuses_it(_tmp):
    """`_util.js:63` renders the error only when an `errorLabel` was
    passed and this section passes none, so a 500 leaves the markup's own
    `(active tab)` option and toasts nothing. What the bridge said is
    nowhere on the panel, which is the contract the missing `errorLabel`
    buys. Nothing is pressed, so that silence is the whole claim: the
    case above makes the same disclosure about the no-token path, and
    neither shows an INJECT still reaching the bridge under an
    unpopulated select."""
    report = _run(SETTLED + 'report({ options:'
                  ' container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent),\n'
                  '  toasts: toasts() });\n', setup=TABS_FAILING)
    assert report['options'] == ['(active tab)'], report
    assert report['toasts'] == [], report
    assert report['unplanned'] == [], report


def test_the_tab_list_labels_a_tab_with_its_id_and_two_spaces(_tmp):
    """The option label is the id, TWO spaces, and the title falling back
    to the url. A tab whose title is empty is offered by its url, so a
    label built from the title alone would offer a nameless option."""
    report = _run('const sel = container.find("[data-role=tab]");\n'
                  + SETTLED
                  + 'report({ options: sel.options'
                    '.map((o) => o.textContent),\n'
                  '  values: sel.options.map((o) => o.value),\n'
                  '  chosen: sel.value });\n',
                  setup=TABS_ONLY)
    assert report['options'] == ['(active tab)', '11  first tab',
                                 '22  https://two.example.com/two'], report
    assert report['values'] == ['', '11', '22'], report
    assert report['chosen'] == '', report
    assert report['unplanned'] == [], report


def test_injecting_sends_the_css_and_neither_tab_nor_frames_by_default(_tmp):
    """`buildFields` adds `tabId` and `allFrames` only when the control
    holds something, so the way to say "the active tab, top frame only" is
    the absence of both keys rather than a defaulted pair."""
    report = _run('container.find("[data-role=css]").value = "a{color:red}";\n'
                  'button("INJECT").click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=TABS_ONLY, answers=(INJECTED,))
    sent = shared.commands(report)[0]
    assert sent['type'] == 'inject-css', report
    assert sent['css'] == 'a{color:red}', report
    assert 'tabId' not in sent, report
    assert 'allFrames' not in sent, report
    assert report['toasts'] == [
        {'type': 'ok', 'text': 'injected 13 chars → tab 5'}], report


def test_a_selected_tab_and_a_checked_box_reach_the_command(_tmp):
    """The counterpart of the pair above: a chosen tab arrives as a
    NUMBER and a checked box as `true`, and the session record keeps both
    so the row's own remove can aim at the same target later."""
    report = _run('container.find("[data-role=tab]").value = "11";\n'
                  'container.find("[data-role=all]").checked = true;\n'
                  'container.find("[data-role=css]").value = "a{color:red}";\n'
                  'button("INJECT").click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=TABS_ONLY, answers=(INJECTED_11,))
    sent = shared.commands(report)[0]
    assert sent['tabId'] == 11, report
    assert sent.get('allFrames') is True, report
    stored = _store(report)[-1]
    assert stored['tabId'] == 11, report
    assert stored['allFrames'] is True, report
    assert sorted(stored) == ['allFrames', 'css', 'tabId', 'ts'], report


def test_a_textarea_holding_only_whitespace_is_refused(_tmp):
    """The emptiness guard is on the TRIMMED value while the field itself
    is sent untrimmed, so a textarea of spaces is empty and a value with
    meaningful leading whitespace is not."""
    report = _run('container.find("[data-role=css]").value = "  \\n\\t ";\n'
                  'button("INJECT").click();\n' + SETTLED
                  + 'button("REMOVE").click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=SEEDED, answers=(INJECTED_PLAIN, REMOVED))
    assert shared.commands(report) == [], report
    assert report['toasts'] == [
        {'type': 'warn', 'text': 'css is empty'},
        {'type': 'warn', 'text': 'css is empty'}], report
    assert len(_store(report)) == 2, report


def test_the_css_reaches_the_command_with_its_own_whitespace(_tmp):
    """`f.css = cssEl.value || ''` does not trim, so a rule the operator
    indented reaches the bridge exactly as they typed it."""
    report = _run('container.find("[data-role=css]").value ='
                  ' "  a{\\n  color: red; }  ";\n'
                  'button("INJECT").click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=TABS_ONLY, answers=(INJECTED_PLAIN,))
    assert shared.commands(report)[0]['css'] == '  a{\n  color: red; }  ', \
        report
    assert _store(report)[-1]['css'] == '  a{\n  color: red; }  ', report


def test_a_failed_inject_appends_no_session_and_re_renders_nothing(_tmp):
    """The `catch` on INJECT covers the push as well as the command, so a
    refused injection leaves the table with the rows it already had."""
    report = _run('const before = rowTexts(container.all()[0]).length;\n'
                  'container.find("[data-role=css]").value = "a{color:red}";\n'
                  'button("INJECT").click();\n' + SETTLED
                  + 'report({ toasts: toasts(), before,\n'
                    '  after: rowTexts(container.all()[0]).length });\n',
                  setup=SEEDED, answers=(INJECT_REFUSED,))
    assert report['toasts'] == [{'type': 'err',
                                 'text': 'cannot access the tab'}], report
    assert report['after'] == report['before'], report
    assert len(_store(report)) == 2, report


def test_a_failed_remove_leaves_the_session_store_alone(_tmp):
    """The toolbar REMOVE is the plain command: its failure toasts and the
    store is not touched, which is the asymmetry the session row's own
    remove breaks below."""
    report = _run('container.find("[data-role=css]").value = "a{color:red}";\n'
                  'button("REMOVE").click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=SEEDED, answers=(REMOVE_NO_CSS,))
    sent = shared.commands(report)[0]
    assert sent['type'] == 'remove-css', report
    assert sent['css'] == 'a{color:red}', report
    assert report['toasts'] == [{'type': 'err', 'text': 'no such css'}], \
        report
    assert len(_store(report)) == 2, report


def test_a_session_rows_remove_aims_at_what_the_row_recorded(_tmp):
    """The row rebuilds its own fields from the stored entry rather than
    from the form, so a rule injected into one tab on all frames is removed
    from that tab on those frames even though the form says something else
    by now. A row with no tab and no frames flag carries neither key."""
    # The second click goes through the tree as it is after the first
    # one rebuilt it, so the case proves the rebuilt table is interactive
    # rather than that a detached button still answers.
    report = _run('container.all().filter(\n'
                  '  (el) => el.tag === "button" &&\n'
                  '    el.textContent === "remove")[0].click();\n' + SETTLED
                  + 'button("remove", container).click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=SEEDED, answers=(REMOVED,))
    first, second = shared.commands(report)
    assert first['type'] == 'remove-css', report
    assert first['css'] == 'a{--seed:1}', report
    assert first['tabId'] == 11, report
    assert first.get('allFrames') is True, report
    assert 'tabId' not in second, report
    assert 'allFrames' not in second, report


def test_a_row_remove_that_failed_keeps_the_local_session(_tmp):
    """A refused `remove-css` leaves the record alone, and the workflow is
    why: `chrome.scripting.removeCSS` needs an exact match, and that
    record is the only place the string is kept. Drop it and a rule still
    applied to the page can only come off by retyping the rule by hand,
    with the operator working from a preview that is gone.

    The success case beside it is the other half: a handler that never
    deletes fails there, and one that always deletes fails here.
    """
    report = _run('const del = button("remove", container);\n'
                  'del.click();\n' + SETTLED
                  + 'const rows = rowTexts(container.all()[0]);\n'
                  + 'report({ toasts: toasts(), rows,\n'
                    '  live: drive.live().length });\n',
                  setup=SEEDED, answers=(REMOVE_REFUSED,))
    assert shared.types(report) == ['remove-css'], report
    assert report['toasts'] == [{'type': 'err',
                                 'text': 'removeCSS rejected it'}], report
    # The row is still on offer, in the order the store holds it.
    assert [row[3] for row in report['rows'][1:]] == [
        'a{--seed:1}', 'b{--seed:2}'], report
    assert [entry['css'] for entry in _store(report)] == [
        'b{--seed:2}', 'a{--seed:1}'], report
    # The toast's 2600 ms fade is still parked, so the operator can still
    # read what the refused remove said.
    assert report['live'] == 1, report


def test_a_row_remove_that_succeeded_deletes_it_and_says_so(_tmp):
    """The half the failure case needs: the same row, the same splice, and
    a toast naming the removal rather than an error."""
    report = _run('const del = button("remove", container);\n'
                  'del.click();\n' + SETTLED
                  + 'const rows = rowTexts(container.all()[0]);\n'
                  'report({ toasts: toasts(), rows });\n',
                  setup=SEEDED, answers=(REMOVED_13,))
    assert report['toasts'] == [{'type': 'ok', 'text': 'removed'}], report
    assert [row[3] for row in report['rows'][1:]] == ['b{--seed:2}'], report
    assert len(_store(report)) == 1, report


def test_a_full_session_list_refuses_the_next_injection(_tmp):
    """The store holds twenty records and the twenty-first injection is
    refused before anything is sent. The workflow is why: the panel's own
    hint calls this list the way back to an exact `removeCSS` match, so a
    twenty-first rule that is applied and then not recorded is one the
    operator can only take off by retyping.

    Twenty successful injections are the other half of this case: a panel
    that always refuses fails on the first of them, and a panel that never
    refuses fails on the twenty-first. The cap itself is pinned by what
    the refusal proves, not by an eviction being observed.
    """
    body = ('const css = container.find("[data-role=css]");\n'
            'const inject = async () => {\n'
            '  for (let i = 0; i < 21; i += 1) {\n'
            '    css.value = "a{--i:" + i + "}";\n'
            '    button("INJECT").click();\n'
            '    await settle();\n'
            '  }\n'
            '};\n'
            'await bounded(inject(), "twenty-one injections",'
            ' _dashnodeStepTimeoutMs);\n'
            'report({ rows: rowTexts(container.all()[0]),'
            ' toasts: toasts() });\n')
    report = _run(body, setup=TABS_ONLY, answers=(INJECTED_9,))
    # Twenty commands, and the twenty-first injection never reached the
    # wire, so the rule it named is not sitting on a page unrecorded.
    assert shared.types(report).count('inject-css') == 20, report
    assert len(report['toasts']) == 21, report
    assert report['toasts'][-1] == {
        'type': 'warn',
        'text': 'session list full (20) — remove one first'}, report
    previews = [row[3] for row in report['rows'][1:]]
    assert len(previews) == 20, report
    assert previews[0] == 'a{--i:19}' and previews[-1] == 'a{--i:0}', report
    stored = [entry['css'] for entry in _store(report)]
    assert len(stored) == 20, report
    assert stored[0] == 'a{--i:0}' and stored[-1] == 'a{--i:19}', report
    assert 'a{--i:20}' not in stored, report


def test_the_preview_collapses_whitespace_before_it_caps(_tmp):
    """The cell runs `truncate(s.css.replace(/\\s+/g, ' '), 100)`, so the
    collapse happens FIRST and the cap counts the collapsed string. A cap
    applied to the raw value would cut inside a run of newlines and show
    a different hundred characters entirely."""
    parts = ['a{color:red}', 'b{color:blue}', 'c{color:green}']
    parts += ['s' + str(i) + '{e:' + str(i) + '}' for i in range(20)]
    raw = '\n\n\t'.join(parts[:3]) + '\n' + '   '.join(parts[3:])
    collapsed = ' '.join(parts)
    plan = (TABS
            + "localStorage.setItem('daedalus-dash-css-sessions',\n"
            "  JSON.stringify([{ css: " + json.dumps(raw) + ",\n"
            "    tabId: '', allFrames: false,\n"
            "    ts: 1750000000000 }]));\n"
            + "drive.route('/tabs', { json: TABS });\n")
    report = _run('report({ rows: rowTexts(container.all()[0]) });\n',
                  setup=plan)
    preview = report['rows'][1][3]
    assert len(collapsed) > 100, report
    assert preview == collapsed[:99] + '…', report
    assert preview.index('…') == 99, report
    assert '  ' not in preview and '\n' not in preview, report


def test_a_row_renders_the_tab_the_frames_and_a_local_time(_tmp):
    """Three of the five cells this row renders -- the time, the tab and
    the frames flag, the fourth being the preview the case above pins.
    They are read off the stored entry: `tabId || '—'`,
    `allFrames ? 'all' : 'top'`, and a time computed inline as local
    `HH:MM:SS`. The time is host-timezone dependent, so the case pins
    its SHAPE and its relation to the entry's own `ts` rather than a
    literal a runner in another zone would refuse."""
    report = _run('report({ rows: rowTexts(container.all()[0]) });\n',
                  setup=SEEDED)
    rows = report['rows'][1:]
    assert [row[1] for row in rows] == ['11', '—'], report
    assert [row[2] for row in rows] == ['all', 'top'], report
    assert [row[3] for row in rows] == ['a{--seed:1}', 'b{--seed:2}'], report
    stamped = [row[0] for row in rows]
    assert [len(t) for t in stamped] == [8, 8], report
    assert [t[2] for t in stamped] == [':', ':'], report
    assert all(t.replace(':', '').isdigit() for t in stamped), report


def test_the_load_button_refills_the_form_from_the_row(_tmp):
    """The row's `load` is how an operator re-injects the same rule after
    the page navigated, so it has to put the css, the tab and the frames
    flag back on the form rather than only showing the row."""
    report = _run(SETTLED + 'button("load", container).click();\n' + SETTLED
                  + 'report({ css: container.find("[data-role=css]").value,\n'
                    '  tab: container.find("[data-role=tab]").value,\n'
                    '  all: container.find("[data-role=all]").checked,\n'
                    '  toasts: toasts() });\n',
                  setup=SEEDED)
    assert report['css'] == 'a{--seed:1}', report
    assert report['tab'] == '11', report
    assert report['all'] is True, report
    assert report['toasts'] == [{'type': 'info', 'text': 'loaded'}], report


def test_a_store_that_will_not_parse_renders_the_empty_state(_tmp):
    """The one catch in the module that renders nothing: unreadable JSON
    reads as an empty list, so a corrupted store shows `none.` rather
    than an error pane or a crash."""
    plan = (TABS + "drive.route('/tabs', { json: TABS });\n"
            + "localStorage.setItem('daedalus-dash-css-sessions',"
            " '{not json');\n")
    report = _run('report({ sessions:'
                  ' container.find("[data-role=sessions]").textContent });\n',
                  setup=plan)
    assert report['sessions'] == 'none.', report
    assert report['unplanned'] == [], report


def test_a_bus_tab_event_repopulates_the_select_and_keeps_the_choice(_tmp):
    """`bindTabSelector` registers its listener inside `mount`, which is
    what `app.js` calls with the bus. The event has to reach a real
    `/tabs` fetch -- counting them is what separates a listener that was
    registered from one that was not, since the listing it repopulates is
    the listing the mount already had. A selection survives a refresh only
    while its tab is still offered."""
    report = _run(SETTLED
                  + 'const before = REQUESTS.length;\n'
                    'container.find("[data-role=tab]").value = "11";\n'
                    'bus.emit({ type: "tab-updated" });\n' + SETTLED
                  + 'report({ before, after: REQUESTS.length,\n'
                    '  chosen: container.find("[data-role=tab]").value,\n'
                    '  options: container.find("[data-role=tab]").options'
                    '.map((o) => o.textContent) });\n',
                  setup=SEEDED)
    # One fetch at the mount, one for the event.
    assert report['after'] - report['before'] == 1, report
    assert report['chosen'] == '11', report
    assert report['options'] == ['(active tab)', '11  first tab',
                                 '22  https://two.example.com/two'], report
    assert report['unplanned'] == [], report


def test_an_internal_bus_event_repopulates_nothing(_tmp):
    """`ev.__internal` is filtered out before the three lifecycle types are
    read, so an event carrying it repopulates nothing -- the filter is the
    difference between a synthetic event and a real one."""
    report = _run(SETTLED
                  + 'const before = REQUESTS.length;\n'
                    'bus.emit({ type: "tab-updated", __internal: true });\n'
                  + SETTLED
                  + 'report({ before, after: REQUESTS.length });\n',
                  setup=SEEDED)
    assert report['after'] - report['before'] == 0, report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashcss_')


if __name__ == '__main__':
    raise SystemExit(main())
