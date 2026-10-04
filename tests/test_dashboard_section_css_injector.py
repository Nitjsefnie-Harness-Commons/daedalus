#!/usr/bin/env python3
"""The CSS injector panel, run rather than read.

`dashboard/sections/css-injector.js` injects and removes CSS through
`chrome.scripting` and keeps its session list in `localStorage`. The
harness mounts the shipped section over the real `dashboard/api.js` and
`_util.js` in Node, drives the form and the session table, and reads every
request body beside the rendered cells and the store.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import section_runner  # noqa: E402

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


_run = section_runner(scenario, SECTION, plan=shared.COMMAND, setup=SEEDED)


def _store(report):
    raw = report['storage'].get('daedalus-dash-css-sessions')
    return json.loads(raw) if raw else []


def test_the_tab_list_says_nothing_when_there_is_no_token(_tmp):
    """`bindTabSelector` returns before `api.get('/tabs')` when the token
    is empty: no request, no toast, and the MARKUP's own `(active tab)`
    option -- the `placeholder` append sits on the success path and never
    runs here. Nothing is pressed."""
    report = _run(SETTLED + 'sectionReport({ options:'
                  ' container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent),\n'
                  '  toasts: toasts(), requests: REQUESTS.length });\n',
                  setup=TABS_ONLY + NO_TOKEN)
    assert report['options'] == ['(active tab)'], report
    assert report['toasts'] == [], report
    assert report['requests'] == 0, report
    assert report['unplanned'] == [], report


def test_the_tab_list_says_nothing_when_the_bridge_refuses_it(_tmp):
    """This section passes no `errorLabel`, so a 500 leaves the markup's
    own `(active tab)` option and toasts nothing: what the bridge said is
    nowhere on the panel. Nothing is pressed."""
    report = _run(SETTLED + 'sectionReport({ options:'
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
                  + 'sectionReport({ options: sel.options'
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
                  + 'sectionReport({ toasts: toasts() });\n',
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
                  + 'sectionReport({ toasts: toasts() });\n',
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
                  + 'sectionReport({ toasts: toasts() });\n',
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
                  + 'sectionReport({ toasts: toasts() });\n',
                  setup=TABS_ONLY, answers=(INJECTED_PLAIN,))
    assert shared.commands(report)[0]['css'] == '  a{\n  color: red; }  ', \
        report
    assert _store(report)[-1]['css'] == '  a{\n  color: red; }  ', report


def test_a_failed_inject_keeps_the_record_it_reserved(_tmp):
    """`save()` runs BEFORE `extCmd('inject-css')` -- the reserve order the
    ordering case pins -- so the record is in the store when the command
    is refused. The refusal MARKS it `failed` and the row shows the state;
    the success limb, the flag left off, is the exact key set in
    `test_a_selected_tab_and_a_checked_box_reach_the_command`."""
    report = _run('container.find("[data-role=css]").value = "a{color:red}";\n'
                  'button("INJECT").click();\n' + SETTLED
                  + 'sectionReport({ toasts: toasts(),\n'
                    '  rows: rowTexts(container.all()[0]) });\n',
                  setup=SEEDED, answers=(INJECT_REFUSED,))
    assert report['toasts'] == [{'type': 'err',
                                 'text': 'cannot access the tab'}], report
    # Newest first; the seeded two behind it, unmarked.
    assert [row[3] for row in report['rows'][1:]] == [
        '[failed] a{color:red}', 'a{--seed:1}', 'b{--seed:2}'], report
    # The store keeps insertion order: the reserved record is LAST there.
    stored = _store(report)
    assert [entry['css'] for entry in stored] == [
        'b{--seed:2}', 'a{--seed:1}', 'a{color:red}'], report
    assert stored[-1]['failed'] is True, report


def test_a_concurrent_store_write_survives_the_inject_refusal(_tmp):
    """The marking write re-reads the store instead of writing back the
    pre-command array, one inject per limb: a record another window added
    during the flight must survive the marking -- it is a live rule's
    only path to an exact match -- and a record another window removed
    must not be resurrected by a stale write-back."""
    report = _run(
        'const KEY = "daedalus-dash-css-sessions";\n'
        'const realFetch = globalThis.fetch;\n'
        'let leg = 0;\n'
        'globalThis.fetch = async (target, init) => {\n'
        '  const r = await realFetch(target, init);\n'
        '  if (String(target).endsWith("/command")) {\n'
        '    leg += 1;\n'
        '    const store = JSON.parse(localStorage.getItem(KEY));\n'
        '    if (leg === 1) {\n'
        '      store.push({ css: "a{--concurrent}", tabId: "",\n'
        '        allFrames: false, ts: 1750000000099 });\n'
        '    } else {\n'
        '      const at = store.findIndex(\n'
        '        (e) => e.css === "a{--second}");\n'
        '      store.splice(at, 1);\n'
        '    }\n'
        '    localStorage.setItem(KEY, JSON.stringify(store));\n'
        '  }\n'
        '  return r;\n'
        '};\n'
        'container.find("[data-role=css]").value = "a{--first}";\n'
        'button("INJECT").click();\n' + SETTLED
        + 'const mid = JSON.parse(localStorage.getItem(KEY));\n'
        'container.find("[data-role=css]").value = "a{--second}";\n'
        'button("INJECT").click();\n' + SETTLED
        + 'sectionReport({ mid, toasts: toasts(),\n'
        '  rows: rowTexts(container.all()[0]),\n'
        '  left: JSON.parse(localStorage.getItem(\n'
        '    "daedalus-dash-css-sessions")) });\n',
        setup=SEEDED, answers=(INJECT_REFUSED,))
    assert shared.types(report) == ['inject-css', 'inject-css'], report
    # Found limb: the concurrent record survives; only the reservation
    # is marked.
    assert [e['css'] for e in report['mid']] == [
        'b{--seed:2}', 'a{--seed:1}', 'a{--first}',
        'a{--concurrent}'], report
    assert report['mid'][2]['failed'] is True, report
    assert 'failed' not in report['mid'][3], report
    # Not-found limb: the concurrent removal stands; no stale write-back.
    assert [e['css'] for e in report['left']] == [
        'b{--seed:2}', 'a{--seed:1}', 'a{--first}',
        'a{--concurrent}'], report
    assert report['left'][2]['failed'] is True, report
    assert [row[3] for row in report['rows'][1:]] == [
        'a{--concurrent}', '[failed] a{--first}', 'a{--seed:1}',
        'b{--seed:2}'], report
    assert [t['text'] for t in report['toasts']] == [
        'cannot access the tab', 'cannot access the tab'], report


def test_a_failed_remove_leaves_the_session_store_alone(_tmp):
    """The toolbar REMOVE is the plain command: its failure toasts and the
    store is not touched, which is the asymmetry the session row's own
    remove breaks below."""
    report = _run('container.find("[data-role=css]").value = "a{color:red}";\n'
                  'button("REMOVE").click();\n' + SETTLED
                  + 'sectionReport({ toasts: toasts() });\n',
                  setup=SEEDED, answers=(REMOVE_NO_CSS,))
    sent = shared.commands(report)[0]
    assert sent['type'] == 'remove-css', report
    assert sent['css'] == 'a{color:red}', report
    assert report['toasts'] == [{'type': 'err', 'text': 'no such css'}], \
        report
    assert len(_store(report)) == 2, report


def test_a_session_rows_remove_aims_at_what_the_row_recorded(_tmp):
    """The row rebuilds its own fields from the stored entry, not the
    form; a row with no tab and no frames flag carries neither key."""
    # The second click runs on the REBUILT tree, not a detached button.
    report = _run('container.all().filter(\n'
                  '  (el) => el.tag === "button" &&\n'
                  '    el.textContent === "remove")[0].click();\n' + SETTLED
                  + 'button("remove", container).click();\n' + SETTLED
                  + 'sectionReport({ toasts: toasts() });\n',
                  setup=SEEDED, answers=(REMOVED,))
    first, second = shared.commands(report)
    assert first['type'] == 'remove-css', report
    assert first['css'] == 'a{--seed:1}', report
    assert first['tabId'] == 11, report
    assert first.get('allFrames') is True, report
    assert 'tabId' not in second, report
    assert 'allFrames' not in second, report


def test_a_row_remove_that_failed_keeps_the_local_session(_tmp):
    """A refused `remove-css` leaves the record alone: removeCSS needs an
    exact match and the record is the only place the string is kept. A
    handler that always deletes fails here; one that never deletes fails
    in the success case beside it."""
    report = _run('const del = button("remove", container);\n'
                  'del.click();\n' + SETTLED
                  + 'const rows = rowTexts(container.all()[0]);\n'
                  + 'sectionReport({ toasts: toasts(), rows,\n'
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
                  'sectionReport({ toasts: toasts(), rows });\n',
                  setup=SEEDED, answers=(REMOVED_13,))
    assert report['toasts'] == [{'type': 'ok', 'text': 'removed'}], report
    assert [row[3] for row in report['rows'][1:]] == ['b{--seed:2}'], report
    assert len(_store(report)) == 1, report


# A `setItem` that refuses the sessions key, installed AFTER `SEEDED`
# seeded it. A key guard is unobservable here: `save()` is the only
# `setItem` and STORE_KEY the only key it writes.
QUOTA = ("const realSet = localStorage.setItem;\n"
         "localStorage.setItem = (key, value) => {\n"
         "  if (String(key) === 'daedalus-dash-css-sessions') {\n"
         "    const refused = new Error('quota');\n"
         "    refused.name = 'QuotaExceededError';\n"
         "    throw refused;\n"
         "  }\n"
         "  return realSet(key, value);\n"
         "};\n")


def test_a_store_that_refuses_the_write_never_reaches_the_command(_tmp):
    """The half the failed-command case cannot show: nothing is sent at
    all, because `save()` runs first and its `setItem` throws. Both halves
    are asserted -- no command on the wire, and the store byte-for-byte as
    it was -- since a handler that caught the refusal and injected anyway
    would pass the first by luck alone."""
    report = _run('container.find("[data-role=css]").value = "a{color:red}";\n'
                  'button("INJECT").click();\n' + SETTLED
                  + 'sectionReport({ toasts: toasts(),\n'
                    '  rows: rowTexts(container.all()[0]) });\n',
                  setup=SEEDED + QUOTA)
    assert shared.types(report) == [], report
    assert report['toasts'] == [
        {'type': 'err', 'text': 'session not recorded: quota'}], report
    # No half-written record: the two seeded entries, in the order they were
    # seeded, and a table showing exactly those two.
    assert [entry['css'] for entry in _store(report)] == [
        'b{--seed:2}', 'a{--seed:1}'], report
    assert [row[3] for row in report['rows'][1:]] == [
        'a{--seed:1}', 'b{--seed:2}'], report


def test_the_record_is_in_the_store_before_the_command_is_sent(_tmp):
    """The ordering is observed, not asserted: both orders settle to the
    same store, and only the store as the command leaves the wire tells
    them apart. Three is two seeds plus the reservation; writing after the
    answer would read two."""
    body = ('const atSend = [];\n'
            'const realFetch = globalThis.fetch;\n'
            'globalThis.fetch = async (target, init) => {\n'
            '  if (String(target).endsWith("/command")) {\n'
            '    atSend.push(JSON.parse(localStorage.getItem(\n'
            '      "daedalus-dash-css-sessions")).length);\n'
            '  }\n'
            '  return realFetch(target, init);\n'
            '};\n'
            'container.find("[data-role=css]").value = "a{color:red}";\n'
            'button("INJECT").click();\n' + SETTLED
            + 'sectionReport({ atSend, stored: JSON.parse(\n'
              '  localStorage.getItem(\n'
              '  "daedalus-dash-css-sessions")).length });\n')
    report = _run(body, setup=SEEDED, answers=(INJECTED,))
    assert report['atSend'] == [3], report
    assert report['stored'] == 3, report


def test_a_row_remove_the_store_refuses_says_so_and_keeps_the_row(_tmp):
    """`remove-css` has already answered, so a `setItem` that throws here
    costs a record and not a rule: caught and toasted, and the table is
    not re-rendered onto a store it cannot write."""
    report = _run('button("remove", container).click();\n' + SETTLED
                  + 'sectionReport({ toasts: toasts(),\n'
                    '  rows: rowTexts(container.all()[0]) });\n',
                  setup=SEEDED + QUOTA, answers=(REMOVED_13,))
    # The removal itself succeeded, and the operator is told the record is
    # the thing that did not happen.
    assert shared.types(report) == ['remove-css'], report
    assert report['toasts'][-1] == {
        'type': 'err',
        'text': 'session not removed: quota'}, report
    assert [entry['css'] for entry in _store(report)] == [
        'b{--seed:2}', 'a{--seed:1}'], report
    assert [row[3] for row in report['rows'][1:]] == [
        'a{--seed:1}', 'b{--seed:2}'], report


def test_a_full_session_list_refuses_the_next_injection(_tmp):
    """The store holds twenty records and the twenty-first injection is
    refused before anything is sent: a cap applied-but-unrecorded would be
    a rule the operator can only take off by retyping. Twenty successful
    injections are the other half -- a panel that always refuses fails on
    the first, one that never refuses on the twenty-first."""
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
            'sectionReport({ rows: rowTexts(container.all()[0]),'
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
    report = _run('sectionReport({ rows: rowTexts(container.all()[0]) });\n',
                  setup=plan)
    preview = report['rows'][1][3]
    assert len(collapsed) > 100, report
    assert preview == collapsed[:99] + '…', report
    assert preview.index('…') == 99, report
    assert '  ' not in preview and '\n' not in preview, report


def test_a_row_renders_the_tab_the_frames_and_a_local_time(_tmp):
    """Three of the five cells, read off the stored entry: `tabId || '—'`,
    `allFrames ? 'all' : 'top'`, and a local `HH:MM:SS`. The time is
    zone-dependent, so its SHAPE is pinned, not a literal."""
    report = _run('sectionReport({ rows: rowTexts(container.all()[0]) });\n',
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
                  + 'sectionReport({ css:\n'
                    '  container.find("[data-role=css]").value,\n'
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
    report = _run('sectionReport({ sessions:'
                  ' container.find("[data-role=sessions]").textContent });\n',
                  setup=plan)
    assert report['sessions'] == 'none.', report
    assert report['unplanned'] == [], report


def test_a_bus_tab_event_repopulates_the_select_and_keeps_the_choice(_tmp):
    """`bindTabSelector` registers inside `mount`; the event must reach a
    real `/tabs` fetch, which is what separates a registered listener from
    a missing one. A selection survives only while its tab is offered."""
    report = _run(SETTLED
                  + 'const before = REQUESTS.length;\n'
                    'container.find("[data-role=tab]").value = "11";\n'
                    'bus.emit({ type: "tab-updated" });\n' + SETTLED
                  + 'sectionReport({ before, after: REQUESTS.length,\n'
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
                  + 'sectionReport({ before, after: REQUESTS.length });\n',
                  setup=SEEDED)
    assert report['after'] - report['before'] == 0, report


# Nineteen: one more fits under `STORE_MAX`, a second would not.
NINETEEN = ("localStorage.setItem('daedalus-dash-css-sessions',\n"
            "  JSON.stringify(Array.from({ length: 19 }, (_, i) =>\n"
            "    ({ css: 'a{--n:' + i + '}', tabId: '', allFrames: false,\n"
            "       ts: 1750000000000 + i }))));\n")


def _store_after(js, *, setup, answers=()):
    return _run(js + 'sectionReport({ toasts: toasts(),\n'
                '  rows: rowTexts(container.all()[0]),\n'
                '  left: JSON.parse(localStorage.getItem(\n'
                '    "daedalus-dash-css-sessions")) });\n',
                setup=setup, answers=answers)


def test_two_overlapping_injections_cannot_both_pass_the_cap_check(_tmp):
    """The overlap IS the claim -- a sequential pair would pin nothing.
    `click()` does not await the handler, so the first INJECT hangs at its
    `await` while the second runs the cap check over the written record
    and is refused."""
    report = _store_after(
        'const css = container.find("[data-role=css]");\n'
        'css.value = "a{--first}";\n'
        'button("INJECT").click();\n'
        'css.value = "a{--second}";\n'
        'button("INJECT").click();\n'
        'await bounded((async () => {\n'
        '  for (let i = 0; i < 8; i += 1) await settle();\n'
        '})(), "both injections settled", _dashnodeStepTimeoutMs);\n',
        setup=TABS_ONLY + NINETEEN, answers=(INJECTED_9,))
    assert shared.types(report) == ['inject-css'], report
    assert len(report['left']) == 20, report
    assert {'type': 'warn',
            'text': 'session list full (20) — remove one first'} \
        in report['toasts'], report


def test_a_row_remove_finds_its_record_after_the_store_moves(_tmp):
    """Spliced out by the CONTENTS of the record the row was built from,
    not the position it was rendered at -- and a record already gone by
    click time is spliced not at all. The store is re-read at click time
    and the stream fans out to every window, so the scenario has a second
    window remove the record between render and click. A missing
    `findIndex` is -1, a valid `splice` index meaning LAST, so an
    unguarded splice takes the record it did not come for; dropping the
    `at < 0` guard and restoring the index both die here."""
    report = _store_after(
        'const KEY = "daedalus-dash-css-sessions";\n'
        'localStorage.setItem(KEY, JSON.stringify([\n'
        '  { css: "b{--seed:2}", tabId: "", allFrames: false,\n'
        '    ts: 1750000000000 }]));\n'
        'button("remove", container).click();\n' + SETTLED,
        setup=SEEDED, answers=(REMOVED,))
    # The record the row names is gone; the other is still here.
    assert [e['css'] for e in report['left']] == ['b{--seed:2}'], report
    assert [row[3] for row in report['rows'][1:]] == ['b{--seed:2}'], report


def test_a_store_holding_a_valid_non_array_reads_as_an_empty_list(_tmp):
    """`{}` is valid JSON, so it passes the `catch` the corrupt-store guard
    is built on: the mount's row map calls `.slice` first and the panel
    dies at the table; the cap check would have waved the same `undefined`
    through."""
    report = _store_after(
        'container.find("[data-role=css]").value = "a{color:red}";\n'
        'button("INJECT").click();\n' + SETTLED,
        setup=TABS_ONLY
        + "localStorage.setItem('daedalus-dash-css-sessions', '{}');\n",
        answers=(INJECTED,))
    assert shared.types(report) == ['inject-css'], report
    # One record, and it is the one this click made: the `{}` read as no
    # records rather than as a value with no `length`.
    one = ['a{color:red}']
    assert [e['css'] for e in report['left']] == one, report
    assert [row[3] for row in report['rows'][1:]] == one, report


def test_remove_drops_a_failed_record_and_refuses_a_live_one(_tmp):
    """One refused injection reserves a record the row's own remove takes
    away WITHOUT the worker: the panel holds no confirmation the rule is
    live, and asking the worker anyway is what stuck the record before.
    The next click is the seeded LIVE record, which still goes to the
    worker first and stays put when the worker refuses -- the
    refuse-before-splice order of issue 1179. The flag and the row's
    failed state are pinned by
    `test_a_failed_inject_keeps_the_record_it_reserved`."""
    report = _store_after(
        'container.find("[data-role=css]").value = "a{color:red}";\n'
        'button("INJECT").click();\n' + SETTLED
        + 'button("remove", container).click();\n' + SETTLED
        + 'button("remove", container).click();\n' + SETTLED,
        setup=SEEDED, answers=(INJECT_REFUSED, REMOVE_REFUSED))
    # Exactly one remove-css on the wire: the failed record's remove sent
    # nothing, and the live record's remove is that one.
    assert shared.types(report) == ['inject-css', 'remove-css'], report
    assert [t['text'] for t in report['toasts']] == [
        'cannot access the tab', 'removed', 'removeCSS rejected it'], report
    # The failed record is gone -- dropped locally -- while the live one
    # the worker refused is still on offer and still in the store.
    assert [row[3] for row in report['rows'][1:]] == [
        'a{--seed:1}', 'b{--seed:2}'], report
    assert [e['css'] for e in report['left']] == [
        'b{--seed:2}', 'a{--seed:1}'], report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashcss_')


if __name__ == '__main__':
    raise SystemExit(main())
