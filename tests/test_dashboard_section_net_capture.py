#!/usr/bin/env python3
"""The net-capture panel, run rather than read.

`dashboard/sections/net-capture.js` starts a CDP capture, polls it and
stops it, so what rides on each of the three commands decides what the
worker does, and where a refusal is shown decides whether the operator
sees it at all. The harness mounts the shipped section over the real
`dashboard/api.js` and the real `_util.js` in Node, drives the toolbar,
expands a row, and reads the parsed body of every request beside the
status line and the table the section rendered.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import run_scenario  # noqa: E402

SECTION = ('sections/net-capture.js',)

EXTRA = r"""
const status = () => container.find('[data-role=status]');
const list = () => container.find('[data-role=list]');
const said = () => ({ text: status().textContent,
  classes: status().children.map((c) => c.className).filter(Boolean) });
const body = () => list().all().filter((el) => el.tag === 'tbody')[0];
const cells = (root) => root.all().filter((el) => el.tag === 'tr')
  .map((tr) => tr.children.map((td) => [td.className, td.textContent]));
const rows = () => (body() ? cells(body()).slice(0) : []);
const detail = () => (body() ? body().children.filter(
  (tr) => tr.dataset.detail) : []);
const detailText = () => {
  const found = detail();
  return found.length === 0 ? null
    : found[0].children[0].children[0].textContent;
};
const sent = () => REQUESTS.filter((r) => r.target === '/command')
  .map((r) => r.body);
const legs = () => REQUESTS.filter((r) => r.target.slice(0, 7) === '/result'
  && r.target.indexOf('consume') < 0).length;
"""

# One captured request per status cell -- and the third is 400 exactly,
# because `>= 400` is the boundary the comparison states and a fixture of
# 404 alone would not tell `>=` from `>`. The first carries every member
# the detail pane reads -- so `pretty` has all eleven of its keys to print,
# and a three-row table is what makes the detail's POSITION observable: a
# one-row table inserts and appends to the same place.
LONG_URL = 'https://one.example.com/' + 'a' * 120 + '.js'
REQS = ("const LONG = 'https://one.example.com/' + 'a'.repeat(120)"
        " + '.js';\n"
        "const REQS = [{ status: 200, method: 'GET', type: 'Script',\n"
        "  url: LONG, encodedLength: 2048, statusText: 'OK',\n"
        "  mimeType: 'text/javascript', headers: { accept: 'text/html' },\n"
        "  responseHeaders: { 'content-type': 'text/javascript' },\n"
        "  initiator: 'parser', ts: 1750000000000,\n"
        "  body: 'var a = 1;' },\n"
        "  { status: 301, method: 'GET', type: 'Other',\n"
        "    url: 'https://one.example.com/b' },\n"
        "  { status: 400, method: 'POST', type: 'XHR',\n"
        "    url: 'https://one.example.com/c', encodedLength: 512 }];\n")

# The two boundary cases count this marker in the rendered pane, so it is
# a letter no other part of the pane carries -- `example` has an `x`.
BODY_2000 = ("const D = { status: 200, method: 'GET',\n"
             "  url: 'https://one.example.com/a.js',"
             " body: 'q'.repeat(2000) };\n")
BODY_2001 = BODY_2000.replace("2000) };", "2001) };")
NO_BODY = ("const D = { status: 200, method: 'GET',\n"
           "  url: 'https://one.example.com/a.js' };\n")

EXPECTED_DETAIL = (
    '{\n'
    '  "url": "' + LONG_URL + '",\n'
    '  "method": "GET",\n'
    '  "status": 200,\n'
    '  "statusText": "OK",\n'
    '  "mimeType": "text/javascript",\n'
    '  "requestHeaders": {\n'
    '    "accept": "text/html"\n'
    '  },\n'
    '  "responseHeaders": {\n'
    '    "content-type": "text/javascript"\n'
    '  },\n'
    '  "initiator": "parser",\n'
    '  "ts": 1750000000000,\n'
    '  "bodyBase64": false,\n'
    '  "body": "var a = 1;"\n'
    '}')


TABS = ("const TABS = [{ tabId: 11, title: 'first tab',\n"
        "  url: 'https://one.example.com/one' },\n"
        "  { tabId: 22, title: '', url: 'https://two.example.com/two' }];\n"
        "drive.route('/tabs', { json: TABS });\n")

# `bindTabSelector` is called with a placeholder and no `errorLabel`, so
# both of its failure paths return without touching the select. These two
# setups are how a scenario reaches them: no token at all, and a `/tabs`
# answered with a status, which `api.js:58` turns into a throw.
NO_TOKEN = "localStorage.removeItem('daedalus-token');\n"
TABS_FAILING = ("const TABS = [{ tabId: 11, title: 'first tab',\n"
                "  url: 'https://one.example.com/one' }];\n"
                "drive.route('/tabs',"
                " { status: 500, json: { error: 'tabs unavailable' } });\n")

# The mount's tab list and the three captured requests most cases poll for.
DEFAULT = TABS + REQS


def answer(kind, expr):
    return "answer('" + kind + "', { result: " + expr + " });\n"


ANSWER_START = answer('net-capture', '{ tabId: 11 }')
ANSWER_ALREADY = answer('net-capture',
                        '{ already: true, tabId: 22, buffered: 40 }')
ANSWER_POLL = answer('net-capture-get',
                     '{ count: 3, tabId: 11, requests: REQS }')
ANSWER_POLL_BARE = answer('net-capture-get', '{ tabId: 11, requests: REQS }')
ANSWER_POLL_NONE = answer('net-capture-get',
                          '{ count: 0, tabId: 11, requests: [] }')
ANSWER_ONE = answer('net-capture-get',
                    '{ count: 1, tabId: 11, requests: [D] }')
ANSWER_STOPPED = answer('net-capture-stop',
                        '{ stopped: true, tabId: 11, count: 3,'
                        ' requests: REQS }')
ANSWER_NOT_CAPTURING = answer('net-capture-stop',
                              '{ stopped: false,'
                              " reason: 'no capture running' }")
ANSWER_NOT_CAPTURING_BARE = answer('net-capture-stop', '{ stopped: false }')

REFUSED = "answer('%s', { error: '%s' });\n"


def refusal(kind, message):
    return REFUSED % (kind, message)


# A result leg that never matches: the loop spends its whole budget and
# gives up, and the number of polls it spent is the budget.
NEVER_ANSWERED = ("drive.route('/result?tab=extension',"
                  " { pending: true });\n")

REFUSE_POLL = refusal('net-capture-get', 'nothing running')
REFUSE_START = refusal('net-capture', 'debugger refused')
REFUSE_STOP = refusal('net-capture-stop', 'detach failed')


SETTLED = ('await bounded(settle(), "after the click",'
           ' _dashnodeStepTimeoutMs);\n')


def scenario(body, *, setup=DEFAULT, answers=(), plan=shared.COMMAND,
             by_type=True):
    """One child: seed the token, plan every answer, mount, then drive.

    `setup` lands first because the answer table is written in the scope
    `setup` defines. `by_type=False` is for the give-up cases, which plan
    the result leg themselves and would otherwise collide with the
    answer table's route for the same target.
    """
    table = shared.results(*answers) if by_type else ''
    return ('(async () => {\n' + shared.SEED + shared.PRELUDE + EXTRA
            + shared.open_section(SECTION[0]) + setup + plan + table
            + 'const sub = new El("span");\n'
            + 'drive.selector("#s07 [data-sub]", sub);\n'
            + shared.MOUNT + shared.SETTLE + body
            + '})().catch(leave);\n')


def _run(body, *, setup=None, answers=(), plan=shared.COMMAND,
         by_type=True):
    return run_scenario(
        scenario(body, setup=DEFAULT if setup is None else setup,
                 answers=answers, plan=plan, by_type=by_type),
        sections=SECTION)


def _click(label):
    return 'button("' + label + '").click();\n' + SETTLED


# The control values the input-driven cases set, as the JavaScript
# literals they are assigned from.
MAX_NONE = '""'
MAX_LETTERS = '"lots"'
MAX_ONE = '"1"'
CHOSEN_TAB = '"11"'
FILTER_PADDED = '"  \\\\.m3u8$  "'
BODIES_ON = 'true'


def _set(role, literal):
    """One operator action on one control, as the scenario reads it."""
    member = '.checked = ' if role == 'bodies' else '.value = '
    return ('container.find("[data-role=' + role + ']")' + member
            + literal + ';\n')


def test_the_mount_lists_the_tabs_and_says_it_is_not_capturing(_tmp):
    """`bindTabSelector` is called with a placeholder and no `errorLabel`,
    so a mount that got the tab list offers every tab and a mount that
    did not is still offering the placeholder. Nothing is sent: the panel
    has no automatic command the way the timings panel has."""
    report = _run('report({ options: container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent),\n'
                  '  status: said(), sent: sent().length });\n')
    assert report['options'] == ['(active tab)', '11  first tab',
                                 '22  https://two.example.com/two'], report
    assert report['status'] == {'text': 'not capturing.', 'classes': []}, \
        report
    assert report['sent'] == 0, report
    assert report['unplanned'] == [], report


def test_the_tab_list_says_nothing_when_there_is_no_token(_tmp):
    """`bindTabSelector` returns before `api.get('/tabs')` when the token
    is empty, and this section passes no `errorLabel`, so the select keeps
    the `(active tab)` option the markup shipped with and says nothing
    about why. The panel is otherwise live -- `runCommand` still refuses
    a command at `api.js:128` -- and this case does not press anything to
    say so."""
    report = _run('report({ options: container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent),\n'
                  '  status: said() });\n', setup=TABS + NO_TOKEN)
    assert report['options'] == ['(active tab)'], report
    assert report['status'] == {'text': 'not capturing.', 'classes': []}, \
        report
    assert report['unplanned'] == [], report


def test_the_tab_list_says_nothing_when_the_bridge_refuses_it(_tmp):
    """`_util.js:63` renders the error only when an `errorLabel` was
    passed, and this section passes none -- so a 500 leaves the select on
    its placeholder. What the bridge said is nowhere on the panel, which
    is the contract the missing `errorLabel` buys."""
    report = _run('report({ options: container.find("[data-role=tab]")'
                  '.options.map((o) => o.textContent),\n'
                  '  toasts: toasts() });\n', setup=TABS_FAILING)
    assert report['options'] == ['(active tab)'], report
    assert report['toasts'] == [], report
    assert report['unplanned'] == [], report


def test_start_sends_the_max_and_neither_tab_nor_filter(_tmp):
    """`fields(true)` adds the max from the control and adds a tab only
    when the select holds one, so the way to say "the active tab, default
    depth, no filter" is the absence of the other two keys."""
    report = _run(_click('START') + 'report({ sent: sent(),'
                  ' status: said() });\n',
                  answers=(ANSWER_START,))
    first = report['sent'][0]
    assert first['type'] == 'net-capture', report
    assert first['maxRequests'] == 1000, report
    assert 'tabId' not in first, report
    assert 'filter' not in first, report
    assert 'bodies' not in first, report
    assert report['status'] == {'text': 'capturing on tab 11',
                                'classes': ['cyan']}, report


def test_an_empty_or_unreadable_max_input_becomes_one_thousand(_tmp):
    """`Number(maxInput.value) || 1000` is the whole of the guard, so an
    empty box and a box holding letters both fall back to a thousand --
    and the `min` and `max` the control carries are never enforced here."""
    empty = _run(_set('max', MAX_NONE) + _click('START')
                 + 'report({ sent: sent() });\n', answers=(ANSWER_START,))
    assert empty['sent'][0]['maxRequests'] == 1000, empty
    letters = _run(_set('max', MAX_LETTERS) + _click('START')
                   + 'report({ sent: sent() });\n', answers=(ANSWER_START,))
    assert letters['sent'][0]['maxRequests'] == 1000, letters
    small = _run(_set('max', MAX_ONE) + _click('START')
                 + 'report({ sent: sent() });\n', answers=(ANSWER_START,))
    assert small['sent'][0]['maxRequests'] == 1, small


def test_a_chosen_tab_arrives_as_a_number_and_the_filter_is_trimmed(_tmp):
    """`Number(tabSel.value)` is the difference from `cdp.js`, which sends
    the same control's value as the string it holds, and the filter is
    trimmed here -- so a pattern typed with a trailing space reaches the
    worker without one."""
    report = _run(_set('tab', CHOSEN_TAB) + _set('filter', FILTER_PADDED)
                  + _click('START') + 'report({ sent: sent() });\n',
                  answers=(ANSWER_START,))
    first = report['sent'][0]
    assert first['tabId'] == 11, report
    assert isinstance(first['tabId'], int), report
    assert first['filter'] == '\\.m3u8$', report


def test_the_bodies_box_rides_on_start_poll_and_stop(_tmp):
    """`bodies` is read by `fields()` on all three paths rather than only on
    the stop, which `extension/worker/netcapture.js:130` and `:175` are
    the only two handlers that read at all. The start body is the odd one:
    `handleNetCapture` never reads the key, so this case pins that
    `fields()` sends it rather than that the worker wants it -- and a poll
    that dropped it would ask for a buffer the get handler filled without
    them, having attached nothing on the way in."""
    report = _run(_set('bodies', BODIES_ON)
                  + _click('START') + _click('poll') + _click('STOP')
                  + 'report({ sent: sent() });\n',
                  answers=(ANSWER_START, ANSWER_POLL, ANSWER_STOPPED))
    assert [body['type'] for body in report['sent']] == [
        'net-capture', 'net-capture-get', 'net-capture-stop'], report
    for body in report['sent']:
        assert body.get('bodies') is True, report


def test_the_max_is_on_the_start_command_and_on_nothing_else(_tmp):
    """`fields(true)` is what the start passes and `fields(false)` is what
    the other two pass, and `extension/worker/netcapture.js:80` reads
    `cmd.maxRequests` only in the start handler -- so the key on a poll is
    a member no handler would read, and a poll that could set the capture
    depth would be the operator's first sign of it."""
    report = _run(_click('START') + _click('poll') + _click('STOP')
                  + 'report({ sent: sent() });\n',
                  answers=(ANSWER_START, ANSWER_POLL, ANSWER_STOPPED))
    first, second, third = report['sent']
    assert first['maxRequests'] == 1000, report
    assert 'maxRequests' not in second, report
    assert 'maxRequests' not in third, report


def test_start_says_already_capturing_with_the_buffered_depth(_tmp):
    """The `already` branch is a different pair of appends, and the depth
    it reports is the only record of what the previous capture already
    holds -- an operator who started twice and got `capturing` would read
    a buffer that is not empty as one that is."""
    report = _run(_click('START') + 'report({ status: said() });\n',
                  answers=(ANSWER_ALREADY,))
    assert report['status'] == {
        'text': 'already capturing on tab 22 · 40 buffered',
        'classes': ['amber']}, report


def test_poll_says_the_count_and_renders_the_rows(_tmp):
    """The count is stringified straight off the answer, so a worker that
    sent a number renders that number and one that sent nothing renders
    `undefined` -- which is the other half of the pair below.

    The url cell is the 140-character cap: this row's url is longer than
    that, so the cell is the first 139 characters and an ellipsis
    (`truncate` is `slice(0, n - 1) + '…'`), and a cap of 100 would show
    40 fewer."""
    report = _run(_click('poll') + 'report({ status: said(),'
                  ' sub: sub.textContent, rows: rows() });\n',
                  answers=(ANSWER_POLL,))
    assert report['status'] == {'text': '3 request(s) on tab 11',
                                'classes': ['cyan']}, report
    assert report['sub'] == '3 req', report
    assert len(LONG_URL) == 147, LONG_URL
    assert report['rows'][0] == [
        ['mono green', '200'], ['mono', 'GET'], ['dim small', 'Script'],
        ['url', LONG_URL[:139] + '…'], ['num', '2.0 KB']], report
    assert report['unplanned'] == [], report


def test_a_poll_answer_without_a_count_says_undefined(_tmp):
    """`String(r.count)` has no fallback, so a count the worker did not
    send reaches the operator as the word `undefined` -- beside a table
    that is not empty, which is the confusing part of it."""
    report = _run(_click('poll') + 'report({ status: said(),'
                  ' sub: sub.textContent });\n',
                  answers=(ANSWER_POLL_BARE,))
    assert report['status']['text'] == 'undefined request(s) on tab 11', \
        report
    assert report['sub'] == '3 req', report


def test_stop_when_it_was_not_capturing_says_the_reason_and_renders_nothing(
        _tmp):
    """`if (!r.stopped)` returns above the `render(...)` call, so the
    refusal leaves the previous table standing rather than replacing it
    with an empty one -- the operator keeps the rows the last poll gave
    them."""
    report = _run(_click('poll') + _click('STOP')
                  + 'report({ status: said(), sub: sub.textContent,'
                    ' rows: rows().length });\n',
                  answers=(ANSWER_POLL, ANSWER_NOT_CAPTURING))
    assert report['status'] == {
        'text': 'no capture running', 'classes': ['dim']}, report
    assert report['rows'] == 3, report
    assert report['sub'] == '3 req', report


def test_a_stop_that_had_no_reason_names_the_default(_tmp):
    """`String(r.reason || 'not capturing')` is the only fallback on the
    stop line, so a refusal with no reason of its own reads as the
    sentence rather than as `undefined`."""
    report = _run(_click('STOP') + 'report({ status: said(),'
                  ' rows: rows().length });\n',
                  answers=(ANSWER_NOT_CAPTURING_BARE,))
    assert report['status'] == {'text': 'not capturing', 'classes': ['dim']}, \
        report
    assert report['rows'] == 0, report


def test_stop_says_the_tab_and_what_it_captured(_tmp):
    """The stop line carries two spaces on each side of both `=`, which is
    what separates the tab id from the count at a glance."""
    report = _run(_click('STOP') + 'report({ status: said(),'
                  ' sub: sub.textContent, rows: rows().length });\n',
                  answers=(ANSWER_STOPPED,))
    assert report['status'] == {
        'text': 'stopped  tab=11  captured=3', 'classes': ['green']}, report
    assert report['sub'] == '3 req', report
    assert report['rows'] == 3, report


def test_a_start_that_never_answers_gives_up_at_fifteen_seconds(_tmp):
    """`extCmd('net-capture', ...)` is called with no `opts`, so the
    fifteen-second default is its budget. The budget is observable only as
    the number of result polls the loop spends -- sixty at 250 ms each --
    and that count is what a section calling with a different budget
    would change."""
    report = _run(_click('START')
                  + 'report({ polls: legs(), status: said(),'
                    '  toasts: toasts() });\n',
                  plan=NEVER_ANSWERED + shared.COMMAND, by_type=False)
    assert report['polls'] == 60, report
    assert report['status'] == {'text': 'not capturing.', 'classes': []}, \
        report
    assert report['toasts'][0]['type'] == 'err', report
    assert report['toasts'][0]['text'].startswith(
        'Timeout (15000ms) waiting for _net-capture_1_'), report


def test_a_poll_that_never_answers_gives_up_at_thirty_seconds(_tmp):
    """The other two commands pass `{ timeout: 30000 }` explicitly, so
    each spends a hundred and twenty polls before it gives up where the
    start above spends sixty. A panel that had dropped the option would
    read identically right up to that count and no further."""
    report = _run(_click('poll')
                  + 'report({ polls: legs(), status: said() });\n',
                  plan=NEVER_ANSWERED + shared.COMMAND, by_type=False)
    assert report['polls'] == 120, report
    assert report['status']['classes'] == ['red'], report
    assert report['status']['text'].startswith(
        'Timeout (30000ms) waiting for _net-capture-get_1_'), report


def test_a_stop_that_never_answers_gives_up_at_thirty_seconds(_tmp):
    """The stop shares the poll's budget, so the third of the three is the
    second's count with a different command id in the message -- and the
    stop reports a give-up by toast, where the poll renders it inline."""
    report = _run(_click('STOP')
                  + 'report({ polls: legs(), status: said(),'
                    '  toasts: toasts() });\n',
                  plan=NEVER_ANSWERED + shared.COMMAND, by_type=False)
    assert report['polls'] == 120, report
    assert report['status'] == {'text': 'not capturing.', 'classes': []}, \
        report
    assert report['toasts'][0]['text'].startswith(
        'Timeout (30000ms) waiting for _net-capture-stop_1_'), report


def test_a_failed_poll_is_rendered_inline_in_red(_tmp):
    """The poll is the one of the three that renders its own failure, so
    a refusal there is visible without the operator looking for a toast
    that is not there. `assert report['toasts'] == []` is the half that
    would fail against a module that toasted as well."""
    report = _run(_click('poll') + 'report({ status: said(),'
                  ' toasts: toasts() });\n',
                  answers=(REFUSE_POLL,))
    assert report['status'] == {'text': 'nothing running',
                                'classes': ['red']}, report
    assert report['toasts'] == [], report


def test_a_failed_start_toasts_and_leaves_the_status_line_alone(_tmp):
    """The start catches into `toast` and never touches `statusEl`, so
    the pre-click sentence survives a refusal. That is the discriminator
    against the poll above: a module that rendered both the same way
    would pass one of the two and fail the other."""
    report = _run(_click('START') + 'report({ status: said(),'
                  ' toasts: toasts() });\n',
                  answers=(REFUSE_START,))
    assert report['status'] == {'text': 'not capturing.', 'classes': []}, \
        report
    assert report['toasts'] == [{'type': 'err',
                                 'text': 'debugger refused'}], report


def test_a_failed_stop_toasts_and_leaves_the_status_line_alone(_tmp):
    """The stop's catch is the start's catch: same toast, same untouched
    line. Pinned as its own case because the stop also has the
    `!r.stopped` branch above it, and the two are easy to confuse."""
    report = _run(_click('poll') + _click('STOP')
                  + 'report({ status: said(), toasts: toasts(),'
                    ' rows: rows().length });\n',
                  answers=(ANSWER_POLL, REFUSE_STOP))
    assert report['status'] == {'text': '3 request(s) on tab 11',
                                'classes': ['cyan']}, report
    assert report['toasts'] == [{'type': 'err', 'text': 'detach failed'}], \
        report
    assert report['rows'] == 3, report


def test_the_status_cell_is_three_way_with_a_hyphen_for_nothing(_tmp):
    """`status >= 400` then `status >= 300` then neither, and the text is
    `String(r.status || '-')` -- a HYPHEN, where `fetch-timings` renders
    the same field as an empty string. The two modules genuinely differ
    and both are the contract. The 400 is the boundary itself: a `> 400`
    comparison would read the third row amber, and the 301 is what makes
    the middle branch reachable at all."""
    report = _run(_click('poll') + 'report({ rows: rows() });\n',
                  answers=(ANSWER_POLL,))
    assert [row[0] for row in report['rows']] == [
        ['mono green', '200'], ['mono amber', '301'], ['mono red', '400']], \
        report
    bare = _run(_click('poll') + 'report({ rows: rows() });\n',
                setup=TABS + "const BARE = [{}];\n",
                answers=(answer('net-capture-get',
                                '{ count: 1, tabId: 11,'
                                ' requests: BARE }'),))
    assert bare['rows'][0][0] == ['mono green', '-'], bare
    assert bare['rows'][0][4] == ['num', '0 B'], bare


def test_a_poll_with_no_rows_says_so_and_still_counts(_tmp):
    """`render` writes the section's own count before the empty check, so
    a poll that found nothing reports `0 req` beside the empty line
    rather than leaving the previous poll's count standing."""
    report = _run(_click('poll') + 'report({ sub: sub.textContent,'
                  ' list: list().textContent,'
                  ' rows: rows().length });\n',
                  answers=(ANSWER_POLL_NONE,))
    assert report['sub'] == '0 req', report
    assert report['list'] == 'no requests captured.', report
    assert report['rows'] == 0, report


def test_clicking_a_row_inserts_the_detail_just_below_it(_tmp):
    """`tr.parentNode.insertBefore(detail, tr.nextSibling)` puts the pane
    between the row that was clicked and the row after it, so the detail
    is at index 1 of a three-row table -- not at the end, which is where
    an append would leave it. A one-row table cannot tell the two apart,
    which is why the poll here is the three-row one.

    The pane itself is `td[colspan=5] > pre.pane` over `pretty` of an
    object literal, so the key order is the order it prints and
    `requestHeaders` is the request's `headers` under a different name
    from the response's."""
    report = _run(_click('poll') + 'body().children[0].click();\n'
                  + 'report({ open: detail().length,\n'
                    '  at: body().children.indexOf(detail()[0]),\n'
                    '  order: body().children.map((tr) =>\n'
                    '    tr.dataset.detail ? \'detail\' : tr.children[0]'
                    '.textContent),\n'
                    '  colspan: detail()[0].children[0]'
                    '.getAttribute(\'colspan\'),\n'
                    '  cursor: body().children[0].style.cursor,\n'
                    '  text: detailText() });\n',
                  answers=(ANSWER_POLL,))
    assert report['open'] == 1, report
    assert report['at'] == 1, report
    assert report['order'] == ['200', 'detail', '301', '400'], report
    assert report['colspan'] == '5', report
    assert report['cursor'] == 'pointer', report
    assert report['text'] == EXPECTED_DETAIL, report


def test_clicking_the_same_row_again_closes_the_detail(_tmp):
    """The toggle reads `tr.nextSibling` rather than a stored reference,
    and the pane it opens is what that walk finds, so a second click on
    the same row collapses it and the three rows the poll rendered are
    what is left. This case clicks the FIRST row, where an append would
    put the pane past the last one and the walk would never see it; on
    the last row the two positions coincide, which is why the case is
    not the one that states the index."""
    report = _run(_click('poll')
                  + 'const row = body().children[0];\n'
                    'row.click();\n'
                    'const open = detail().length;\n'
                    'row.click();\n'
                    'report({ open, closed: detail().length,\n'
                    '  rows: rows().length, requests: sent().length });\n',
                  answers=(ANSWER_POLL,))
    assert report['open'] == 1, report
    assert report['closed'] == 0, report
    assert report['rows'] == 3, report
    assert report['requests'] == 1, report


def test_a_body_of_exactly_two_thousand_characters_is_not_truncated(_tmp):
    """The comparison is `> 2000`, so a body of exactly the cap is shown
    whole. A cap applied with `>=` would print a truncation note on a
    body that was not cut."""
    report = _run(_click('poll') + 'body().children[0].click();\n'
                  'report({ text: detailText() });\n',
                  setup=TABS + BODY_2000, answers=(ANSWER_ONE,))
    assert 'truncated' not in report['text'], report
    assert report['text'].count('q') == 2000, report


def test_a_body_of_two_thousand_and_one_characters_is_truncated(_tmp):
    """One character over the cap prints the first 2000 and a note naming
    how many were dropped, so the operator can see that the pane is not
    showing the whole body."""
    report = _run(_click('poll') + 'body().children[0].click();\n'
                  'report({ text: detailText() });\n',
                  setup=TABS + BODY_2001, answers=(ANSWER_ONE,))
    assert '…(truncated 1 chars)' in report['text'], report
    assert report['text'].count('q') == 2000, report


def test_a_request_with_no_body_says_which_flag_fetches_one(_tmp):
    """A falsy body renders the sentence naming the control, and
    `bodyBase64` is coerced to `false` so the key is present rather than
    dropped by `JSON.stringify` -- an absent key would read as a member
    the worker never sent."""
    report = _run(_click('poll') + 'body().children[0].click();\n'
                  'report({ text: detailText() });\n',
                  setup=TABS + NO_BODY, answers=(ANSWER_ONE,))
    assert '"bodyBase64": false' in report['text'], report
    assert '"body": "(no body — use bodies=on)"' in report['text'], report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashnet_')


if __name__ == '__main__':
    raise SystemExit(main())
