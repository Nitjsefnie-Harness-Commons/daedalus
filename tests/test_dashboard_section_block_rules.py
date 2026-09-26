#!/usr/bin/env python3
"""The block-rules panel, run rather than read.

`dashboard/sections/block-rules.js` mints declarativeNetRequest session
rules from a form and takes them away again from a row or in bulk, so the
fields a command carries decide what the bridge will build, and a field
that goes missing is a rule scoped to every tab the operator meant to
scope to one. The harness mounts the shipped section over the real
`dashboard/api.js` and the real `_util.js` in Node, drives its buttons and
its inputs, and reads the parsed body of every request the section made
beside the cells it rendered.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import run_scenario  # noqa: E402

SECTION = ('sections/block-rules.js',)

ANSWER_RULES = "answer('list-block-rules', { result: RULES });\n"
ANSWER_UNBLOCK = "answer('unblock-requests', { result: {} });\n"
ANSWER_BLOCK = "answer('block-requests', { result: { ruleId: 42 } });\n"

RULES = (
    "const RULES = [{ id: 7, condition: { urlFilter: '*/ads/*' } },\n"
    "  { id: 8, condition: { urlFilter: '*/pixel/*', tabIds: [3, 4] } },\n"
    "  { id: 9 }];\n"
)

REFUSED = ("drive.route('/command', { status: 503, error: 'bridge down' });\n"
           "drive.route('/result?tab=extension', { result: null });\n")


def scenario(body, *, setup='', plan=shared.PLAN):
    """One child: seed the token, plan every answer, mount, then drive."""
    return ('(async () => {\n' + shared.SEED + shared.ANSWERS + shared.PRELUDE
            + shared.open_section(SECTION[0]) + plan + setup
            + 'const sub = new El("span");\n'
            + 'drive.selector("#s06 [data-sub]", sub);\n'
            + shared.MOUNT + shared.SETTLE + body
            + '})().catch(leave);\n')


def _run(body, *, setup='', plan=shared.PLAN):
    return run_scenario(scenario(body, setup=setup, plan=plan),
                        sections=SECTION)


def test_the_mount_lists_rules_and_carries_no_field_of_its_own(_tmp):
    """`extCmd` is called with no fields argument, so the body is the
    command's own four keys and nothing else. A `tabId` defaulted onto it
    would scope the list itself to one tab."""
    report = _run('report({ sub: sub.textContent,\n'
                  '  head: headers(container.find("[data-role=list]")),\n'
                  '  rows: rowTexts(container.find("[data-role=list]")) });\n',
                  setup=RULES + ANSWER_RULES)
    assert shared.types(report) == ['list-block-rules'], report
    body = shared.commands(report)[0]
    assert sorted(body) == ['id', 'tab', 'token', 'type'], report
    assert body['tab'] == 'extension', report
    assert report['sub'] == '3 active', report
    assert report['head'] == ['id', 'pattern', 'tabs', ''], report
    # A rule with no condition and a rule whose condition names two tabs.
    assert [row[:3] for row in report['rows'][1:]] == [
        ['7', '*/ads/*', 'all'],
        ['8', '*/pixel/*', '3,4'],
        ['9', '', 'all'],
    ], report
    assert report['unplanned'] == [], report


def test_the_counter_is_written_before_the_empty_check(_tmp):
    """The `data-sub` write sits above the `arr.length === 0` return, so a
    panel with nothing on it reports `0 active` rather than leaving the
    section's own count at whatever the last non-empty listing said."""
    report = _run('report({ sub: sub.textContent,\n'
                  '  list: container.find("[data-role=list]")'
                  '.textContent });\n',
                  setup="answer('list-block-rules', { result: [] });\n")
    assert report['sub'] == '0 active', report
    assert report['list'] == 'no active block rules.', report


def test_the_refresh_button_re_sends_the_same_bare_command(_tmp):
    """`refresh` is wired straight to `load`, so the operator's own click
    sends the same four-key body the mount did -- a second field added on
    the way in would be one the bridge could not read."""
    report = _run('button("refresh").click();\n'
                  'await bounded(settle(), "after the refresh",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'report({ sub: sub.textContent });\n',
                  setup=RULES + ANSWER_RULES)
    assert shared.types(report) == ['list-block-rules', 'list-block-rules'], \
        report
    assert sorted(shared.commands(report)[1]) == [
        'id', 'tab', 'token', 'type'], report
    assert report['sub'] == '3 active', report


def test_a_result_that_is_not_an_array_renders_the_empty_state(_tmp):
    """`Array.isArray(rules) ? rules : []` is the defence, so a bridge that
    answers with an object renders the empty state instead of throwing
    inside the `arr.length` read."""
    report = _run('report({ sub: sub.textContent,\n'
                  '  list: container.find("[data-role=list]")'
                  '.textContent });\n',
                  setup="answer('list-block-rules',"
                        " { result: { not: 'an array' } });\n")
    assert report['sub'] == '0 active', report
    assert report['list'] == 'no active block rules.', report


def test_adding_a_rule_sends_the_trimmed_pattern_and_the_typed_tab_id(
        _tmp):
    """The tab id is read as a string and guarded on that string, so a
    typed `0` is a tab id and is sent -- the falsy number is not a reason
    to leave the field out. The pattern is trimmed on the way out."""
    report = _run('container.find("[data-role=pat]").value = "  */ads/*  ";\n'
                  'container.find("[data-role=tid]").value = "0";\n'
                  'button("ADD").click();\n'
                  'await bounded(settle(), "after the add",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'report({ toasts: toasts(), sub: sub.textContent });\n',
                  setup=RULES + ANSWER_RULES + ANSWER_BLOCK)
    assert shared.types(report) == ['list-block-rules', 'block-requests',
                                    'list-block-rules'], report
    added = shared.commands(report)[1]
    assert added['pattern'] == '*/ads/*', report
    assert added.get('tabId') == 0, report
    assert added['type'] == 'block-requests', report
    # The rule id the bridge answered with is what the toast names.
    assert report['toasts'] == [{'type': 'ok', 'text': 'added rule 42'}], \
        report
    assert report['sub'] == '3 active', report
    assert report['unplanned'] == [], report


def test_adding_with_the_tab_id_input_blank_omits_the_field(_tmp):
    """An empty tab id means "all tabs", and the way to say that is the
    ABSENCE of the field. A `tabId: 0` default would aim the rule at one
    tab instead, so the case asserts the key is not in the parsed body
    rather than that it holds a value."""
    report = _run('container.find("[data-role=pat]").value = "*/ads/*";\n'
                  'container.find("[data-role=tid]").value = "";\n'
                  'button("ADD").click();\n'
                  'await bounded(settle(), "after the add",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'report({ toasts: toasts() });\n',
                  setup=RULES + ANSWER_RULES
                  + "answer('block-requests', { result: {} });\n")
    added = shared.commands(report)[1]
    assert added['pattern'] == '*/ads/*', report
    assert 'tabId' not in added, report
    assert report['toasts'] == [{'type': 'ok', 'text': 'added rule'
                                 ' undefined'}], report


def test_an_empty_pattern_toasts_and_sends_nothing(_tmp):
    """The guard is on the trimmed pattern, so a field holding only spaces
    is empty: the panel warns and the bridge is never asked, which is the
    only way to tell this apart from a rule created with no pattern."""
    report = _run('container.find("[data-role=pat]").value = "   ";\n'
                  'container.find("[data-role=tid]").value = "5";\n'
                  'button("ADD").click();\n'
                  'await bounded(settle(), "after the refused add",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'report({ toasts: toasts(), sub: sub.textContent });\n',
                  setup=RULES + ANSWER_RULES
                  + "answer('block-requests', { result: {} });\n")
    assert shared.types(report) == ['list-block-rules'], report
    assert report['toasts'] == [{'type': 'warn',
                                 'text': 'pattern required'}], report
    # The mount's own three legs and nothing after: no command was sent.
    assert len(report['requests']) == 3, report
    assert report['sub'] == '3 active', report


def test_a_failed_bridge_renders_the_error_pane(_tmp):
    """One `catch` wraps the whole of `load()`, so a refused command
    clears the host and renders the message the bridge sent -- and the
    rows the panel was showing are gone with it. The counter never moved,
    because its write sits below the command, inside the same try."""
    report = _run('const list = container.find("[data-role=list]");\n'
                  'report({ sub: sub.textContent,\n'
                  + '  list: list.textContent,\n'
                  + '  pane: list.all().some((el) => '
                    'hasClass(el, "pane err")) });\n',
                  setup="answer('list-block-rules', { result: [] });\n",
                  plan=REFUSED)
    assert report['pane'] is True, report
    assert report['list'] == 'HTTP 503: bridge down', report
    assert report['sub'] == '', report
    # A refused PUT never opens a command window, so no poll was made.
    assert shared.legs(report)['poll'] == [], report
    assert report['unplanned'] == [], report


def test_a_failed_add_toasts_and_does_not_reload(_tmp):
    """The three mutation paths toast on failure and do NOT call `load()`
    again, so a bridge that rejected the rule leaves the listing exactly
    as it was -- a reload would either erase the rules or blank the host
    behind a second command nothing asked for."""
    report = _run('const list = container.find("[data-role=list]");\n'
                  'const before = list.textContent;\n'
                  'container.find("[data-role=pat]").value = "*/ads/*";\n'
                  'button("ADD").click();\n'
                  'await bounded(settle(), "after the failed add",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'report({ toasts: toasts(), before,\n'
                  '  after: list.textContent, sub: sub.textContent });\n',
                  setup=RULES + ANSWER_RULES
                  + "answer('block-requests', { error: 'rule rejected' });\n")
    assert shared.types(report) == ['list-block-rules',
                                    'block-requests'], report
    assert report['toasts'] == [{'type': 'err',
                                 'text': 'rule rejected'}], report
    assert report['after'] == report['before'], report
    assert report['sub'] == '3 active', report


def test_a_rows_remove_names_the_rule_it_is_on(_tmp):
    """`ruleId` is the row's own `r.id`, so the button on the second row
    takes away the second rule and not the first."""
    report = _run('const list = container.find("[data-role=list]");\n'
                  'const rows = list.all().filter((el) => el.tag === "tr");\n'
                  'const second = rows[2];\n'
                  + 'const del = second.all().find(\n'
                  + '  (el) => el.tag === "button");\n'
                  'del.click();\n'
                  'await bounded(settle(), "after the row remove",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'report({ toasts: toasts(), sub: sub.textContent });\n',
                  setup=RULES + ANSWER_RULES + ANSWER_UNBLOCK)
    assert shared.types(report) == ['list-block-rules', 'unblock-requests',
                                    'list-block-rules'], report
    removed = shared.commands(report)[1]
    assert removed['type'] == 'unblock-requests', report
    assert removed['ruleId'] == 8, report
    assert report['toasts'] == [{'type': 'ok', 'text': 'removed'}], report
    assert report['sub'] == '3 active', report


def test_remove_all_arms_before_it_sends_and_omits_the_rule_id(_tmp):
    """`armedAction`'s first click only arms, and the bulk command carries
    NO `ruleId` -- omitting it is what selects "every rule" server-side,
    so a defaulted id would remove one rule behind a button that says
    all. The first click is the anti-vacuity half: a handler that ran on
    the first click would send a command these assertions do not expect.
    """
    report = _run('const clear = button("remove all");\n'
                  'clear.click();\n'
                  'await bounded(settle(), "after the arming click",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'const armed = { text: clear.textContent,\n'
                  '  has: clear.classList.contains("armed"),\n'
                  '  sent: REQUESTS.length };\n'
                  'clear.click();\n'
                  'await bounded(settle(), "after the confirmed click",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'report({ armed, toasts: toasts() });\n',
                  setup=RULES + ANSWER_RULES + ANSWER_UNBLOCK)
    assert report['armed']['text'] == 'confirm remove all', report
    assert report['armed']['has'] is True, report
    # Nothing beyond the mount's own three legs: an un-armed first click
    # would have sent the bulk command here and taken every rule away.
    assert report['armed']['sent'] == 3, report
    assert shared.types(report) == ['list-block-rules', 'unblock-requests',
                                    'list-block-rules'], report
    bulk = shared.commands(report)[1]
    assert bulk['type'] == 'unblock-requests', report
    assert 'ruleId' not in bulk, report
    assert report['toasts'] == [{'type': 'ok', 'text': 'all removed'}], report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashblk_')


if __name__ == '__main__':
    raise SystemExit(main())
