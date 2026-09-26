#!/usr/bin/env python3
"""Every branch of every handler in daedalus_cli/commands_eval.py.

`do_ping`, `do_title` and `do_url` had no test at all before this file, and
the rest had untested render arms. Each test here asserts BOTH halves of a
handler: the exact request it put on the wire — method, path, and the body
field names and values — and the exact output an operator would read,
compared as one string. The rendered halves differ only in spacing and in
which marker precedes a line, so a substring assertion would accept a row
that is otherwise wrong.

`do_result` is the exception to "every", and it lives in
tests/test_cli_result_handlers.py for the file-size gate; the contract it is
held to is the one described here.

A note on markers: the two glyphs below are SPELLED OUT here rather than
read from `daedalus_cli.output`, because an expected value taken from the
module under test pins nothing about it — with `MARK` in the f-strings, a
change to the glyphs left every assertion in this file green. What the
module can render is one of two spellings per glyph, the fancy one or the
ASCII fallback a console that cannot encode it gets, and `_rendered` folds
whichever arrived onto the literal these assertions are written against.

A note on names: `tests/test_client_credentials.py` carries tests called
`test_mcp_title_drops_bridge_token` and `test_mcp_url_drops_bridge_token`,
which read as if they covered these handlers. They do not — they drive the
MCP server's own `title` and `url` tools through `daedalus_mcp`, and the
only CLI command that suite runs is `result --raw`.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_eval  # noqa: E402

run_cli = _cli_dispatch.run_cli
run_cli_exit = _cli_dispatch.run_cli_exit

IN = '←'
OUT = '→'
TOK = 'clitok'


def _rendered(out):
    """`out` with a fallback marker spelling folded onto the pinned glyph.

    Only the two spellings `output._output_markers` documents are folded,
    so a THIRD one — or a changed fancy glyph — reaches the assertions
    below untouched and every whole-string comparison here fails.
    """
    return out.replace('<-', IN).replace('->', OUT)


def _put(body):
    return {'via': 'api', 'method': 'PUT', 'path': '/command', 'body': body}


def _get(path):
    return {'via': 'api', 'method': 'GET', 'path': path, 'body': None}


def _wait(cmd_id, target_tab, delivery, timeout, interval=0.5):
    return {'via': 'wait_for_result', 'id': cmd_id, 'tab': target_tab,
            'delivery': delivery, 'timeout': timeout, 'interval': interval}


def _source(directory, code='document.title'):
    """A file for do_put to read, and its path."""
    path = Path(directory) / 'job.js'
    path.write_text(code, encoding='utf-8')
    return str(path)


@contextlib.contextmanager
def _stdin(text):
    """Stand in for the process's standard input.

    This interpreter's `contextlib` exposes no `redirect_stdin`, so the
    swap is done by hand and undone whatever the handler does.
    """
    original = sys.stdin
    sys.stdin = io.StringIO(text)
    try:
        yield
    finally:
        sys.stdin = original


def _result(**over):
    base = {'id': 'job1', 'result': 'ok', 'error': None, 'ts': 1}
    return dict(base, **over)


# ── do_tabs ──────────────────────────────────────────────────────────

def test_do_tabs_renders_one_row_per_tab_in_age_order(tmp):
    """Ages 120 and 5 arrive in that order and print in the other one.

    The listing sorts by age, so the row an operator reads first is the
    freshest tab. The older tab therefore has to ARRIVE first for the
    assertion to mean anything: a list that already happened to be sorted
    would pass a handler that rendered in the order the bridge returned it,
    which is the whole defect this test exists to catch.
    """
    del tmp
    tabs = [
        {'tabId': 'tab1', 'age': 120, 'url': 'https://two.example.com/',
         'title': 'One'},
        {'tabId': 'tab9', 'age': 5, 'url': 'https://one.example.com/',
         'title': 'Nine'},
    ]
    recorded, out = run_cli(
        ['tabs'], [tabs], module=commands_eval, plan=[_get('/tabs')],
        token=TOK)

    assert recorded.api_calls == [('GET', '/tabs', None)], recorded.api_calls
    # 48 and 49 spaces: the title cell is 50 wide and the gap after the
    # word is that padding plus the two spaces before the url.
    assert out == (
        '  tab9     5s  Nine' + ' ' * 48 + 'https://one.example.com/\n'
        '  tab1   120s  One' + ' ' * 49 + 'https://two.example.com/\n'), \
        repr(out)


def test_do_tabs_says_so_when_no_tab_is_active(tmp):
    """The empty listing is a sentence, not a bare zero rows."""
    del tmp
    _recorded, out = run_cli(
        ['tabs'], [[]], module=commands_eval, plan=[_get('/tabs')], token=TOK)

    assert out == 'No active tabs\n', repr(out)


def test_do_tabs_renders_absent_fields_as_their_placeholders(tmp):
    """A registry row may carry no age, no title and no url.

    All three are read through `.get` with a different default each: 0 to
    sort by, `?` to print, and the empty string for the two text cells. A
    handler that printed None instead would put the word in three places.
    """
    del tmp
    _recorded, out = run_cli(
        ['tabs'], [[{'tabId': 'tab3'}]], module=commands_eval,
        plan=[_get('/tabs')], token=TOK)

    assert out == '  tab3     ?s  ' + ' ' * 52 + '\n', repr(out)


def test_do_tabs_truncates_a_title_at_fifty_columns(tmp):
    """The title cell is fixed width, so a long one is cut, not wrapped."""
    del tmp
    _recorded, out = run_cli(
        ['tabs'],
        [[{'tabId': 'tab4', 'age': 1, 'url': 'https://three.example.com/',
           'title': 'T' * 60}]],
        module=commands_eval, plan=[_get('/tabs')], token=TOK)

    assert out == (
        '  tab4     1s  ' + 'T' * 50
        + '  https://three.example.com/\n'), repr(out)
    assert 'T' * 51 not in out, repr(out)


def test_do_tabs_json_prints_the_list_it_was_given(tmp):
    """`--json` is the machine-readable arm: the registry, indented."""
    del tmp
    tabs = [{'tabId': 'tab9', 'age': 5}]
    _recorded, out = run_cli(
        ['tabs', '--json'], [tabs], module=commands_eval,
        plan=[_get('/tabs')], token=TOK)

    assert out == '[\n  {\n    "tabId": "tab9",\n    "age": 5\n  }\n]\n', \
        repr(out)


def test_do_tabs_json_prints_an_empty_list_when_there_is_none(tmp):
    """`--json` wins over the sentence: the consumer is a program.

    The bridge answering null and the handler printing `No active tabs`
    would leave a JSON consumer parsing prose, so the two arms are pinned
    apart rather than as one row.
    """
    del tmp
    _recorded, out = run_cli(
        ['tabs', '--json'], [None], module=commands_eval,
        plan=[_get('/tabs')], token=TOK)

    assert out == '[]\n', repr(out)


# ── do_put ───────────────────────────────────────────────────────────

def test_do_put_sends_the_file_it_read_and_waits_for_the_result(tmp):
    """The whole round trip: the body, the wait, and both printed halves."""
    path = _source(tmp)
    body = {'token': TOK, 'id': 'job1', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('job1', 'tab0', 'd1', 15)]
    recorded, out = run_cli(
        ['put', 'job1', path], [{'target': 'tab0', 'did': 'd1'},
                                _result(tabId='tab0', world='page:cdp',
                                        exec_ms=3)],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [15], recorded.timeouts
    assert _rendered(out) == (
        f'{OUT} job1 {OUT} tab0  (14 bytes)\n'
        f'{IN} job1  tab=tab0  @channel=page:cdp  3ms\n'
        'ok\n'), repr(out)


def test_do_put_strips_the_code_it_read_from_a_file(tmp):
    """The file's own newline is not part of the code sent to the page."""
    path = _source(tmp, '  document.title\n')
    body = {'token': TOK, 'id': 'job1', 'code': 'document.title',
            'tab': 'tab0'}
    recorded, out = run_cli(
        ['put', 'job1', path, '--no-result'], [{'target': 'tab0'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0',
        token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == f'{OUT} job1 {OUT} tab0  (14 bytes)\n', repr(out)


def test_do_put_reads_the_dash_standard_input(tmp):
    """`-` is stdin, so the code never touches the filesystem."""
    del tmp
    body = {'token': TOK, 'id': 'job1', 'code': 'window.x = 1', 'tab': 'tab0'}
    with _stdin('window.x = 1\n'):
        recorded, out = run_cli(
            ['put', 'job1', '-', '--no-result'], [{'target': 'tab0'}],
            module=commands_eval, plan=[_put(body)], target_tab='tab0',
            token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    # The stdin newline is stripped exactly as a file's is, so the count
    # printed here is the twelve characters and not thirteen.
    assert _rendered(out) == f'{OUT} job1 {OUT} tab0  (12 bytes)\n', repr(out)


def test_do_put_refuses_a_file_that_is_not_there(tmp):
    """The refusal is a sentence naming the path, and nothing is sent.

    Nothing is printed either, and that is asserted rather than assumed:
    `run_cli_exit` hands back what the arm rendered as well as the message
    it exits with, so a handler that printed a row and THEN refused cannot
    pass a test that only reads the code. The message is compared as a whole
    string, because a refusal naming the wrong path would otherwise read as
    the same sentence with a different word in it.
    """
    missing = str(Path(tmp) / 'absent.js')
    code, out = run_cli_exit(['put', 'job1', missing, '--no-result'], [],
                             module=commands_eval, plan=[], token=TOK)

    assert code == f'File not found: {missing}', code
    assert out == '', repr(out)


def test_do_put_broadcast_leaves_the_tab_field_off_the_body(tmp):
    """`-b` means the browser's active tab, which is an absent tab field.

    Sending `tab: ''` instead would name a tab that does not exist rather
    than name none, and the two travel differently on the wire. The row
    the operator reads is the only place the broadcast is visible, since
    the request itself carries no tab at all.
    """
    path = _source(tmp)
    body = {'token': TOK, 'id': 'job1', 'code': 'document.title'}
    recorded, out = run_cli(
        ['put', 'job1', path, '-b', '--no-result'], [{'target': 'bcast'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0',
        token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == f'{OUT} job1 {OUT} bcast  (14 bytes)\n', repr(out)


def test_do_put_waits_the_default_fifteen_seconds_and_not_the_zero(tmp):
    """`-t 0` reads as unset, so the deadline is the documented default.

    Handing the zero to the waiter made the deadline now plus nothing, and
    the CLI reported `Timeout (0s)` for a command the browser then ran.
    """
    path = _source(tmp)
    body = {'token': TOK, 'id': 'job1', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('job1', 'tab0', 'd1', 15)]
    recorded, out = run_cli(
        ['put', 'job1', path, '-t', '0'],
        [{'target': 'tab0', 'did': 'd1'}, _result()],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK)

    assert recorded.timeouts == [15], recorded.timeouts
    assert _rendered(out) == (
        f'{OUT} job1 {OUT} tab0  (14 bytes)\n'
        f'{IN} job1\nok\n'), repr(out)


def test_do_put_carries_an_explicit_timeout_through_unchanged(tmp):
    """The other arm of `args.timeout or 15`: a stated value is the value."""
    path = _source(tmp)
    body = {'token': TOK, 'id': 'job1', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('job1', 'tab0', 'd1', 7)]
    recorded, out = run_cli(
        ['put', 'job1', path, '--timeout', '7'],
        [{'target': 'tab0', 'did': 'd1'}, _result()],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK)

    assert recorded.timeouts == [7], recorded.timeouts
    assert _rendered(out) == (
        f'{OUT} job1 {OUT} tab0  (14 bytes)\n'
        f'{IN} job1\nok\n'), repr(out)


# ── do_exec ──────────────────────────────────────────────────────────

def test_do_exec_sends_the_inline_code_stripped(tmp):
    """Inline code is stripped exactly as a file's is, and waits by default.
    """
    body = {'token': TOK, 'id': 'job5', 'code': '1+1', 'tab': 'tab0'}
    plan = [_put(body), _wait('job5', 'tab0', 'd2', 15)]
    recorded, out = run_cli(
        ['exec', 'job5', '  1+1  '],
        [{'target': 'tab0', 'did': 'd2'},
         _result(id='job5', tabId='tab0', world='page-main', exec_ms=7)],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == (
        f'{OUT} job5 {OUT} tab0  (3 bytes)\n'
        f'{IN} job5  tab=tab0  @channel=page-main  7ms\n'
        'ok\n'), repr(out)


def test_do_exec_broadcast_leaves_the_tab_field_off_the_body(tmp):
    """Same absent-tab contract as do_put, on the inline subcommand."""
    body = {'token': TOK, 'id': 'job5', 'code': '1+1'}
    recorded, out = run_cli(
        ['exec', 'job5', '1+1', '-b', '--no-result'], [{'target': 'bcast'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0',
        token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == f'{OUT} job5 {OUT} bcast  (3 bytes)\n', repr(out)


def test_do_exec_without_a_result_never_waits(tmp):
    """`--no-result` sends and returns: the plan has no second request.

    A handler that waited anyway would block an operator who asked not to,
    and a plan that omitted the wait is what makes that visible. The one
    rendered line is the whole output for this arm, so there is no sibling
    row in the same test to compare it against.
    """
    del tmp
    body = {'token': TOK, 'id': 'job5', 'code': '1+1', 'tab': 'tab0'}
    recorded, out = run_cli(
        ['exec', 'job5', '1+1', '--no-result'], [{'target': 'tab0'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0', token=TOK)

    assert recorded.waits == [], recorded.waits
    assert _rendered(out) == f'{OUT} job5 {OUT} tab0  (3 bytes)\n', repr(out)


# ── do_ping ──────────────────────────────────────────────────────────

def test_do_ping_reports_the_round_trip_in_the_tab_it_named(tmp):
    """The whole handshake: the body, the delivery it waits on, the line.

    The elapsed milliseconds come from the harness's own clock, so the
    rendered line is a whole string rather than a number a regex pulled
    out of it.
    """
    del tmp
    body = {'token': TOK, 'id': '_ping', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('_ping', 'tab0', 'd9', 10, interval=0.3)]
    pong = {'id': '_ping', 'result': 'Hello', 'error': None, 'ts': 1,
            'world': 'page:main'}
    recorded, out = run_cli(
        ['ping'], [{'did': 'd9'}, pong],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK,
        clock=[1000.0, 1000.25])

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == 'Pong @channel=page:main: "Hello"  (250ms)\n', repr(out)


def test_do_ping_leaves_the_tab_field_off_when_none_is_set(tmp):
    """With no tab the payload carries none, and the wait is for the
    broadcast slot — the same absent-not-empty contract as `-b`."""
    del tmp
    body = {'token': TOK, 'id': '_ping', 'code': 'document.title'}
    plan = [_put(body), _wait('_ping', '', 'd9', 10, interval=0.3)]
    pong = {'id': '_ping', 'result': 'Hello', 'error': None, 'ts': 1}
    recorded, out = run_cli(
        ['ping'], [{'did': 'd9'}, pong],
        module=commands_eval, plan=plan, target_tab='', token=TOK,
        clock=[1000.0, 1000.0])

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == 'Pong : "Hello"  (0ms)\n', repr(out)


def test_do_ping_exits_when_no_result_arrives(tmp):
    """The timeout exit names the bound it actually waited.

    This arm prints nothing before exiting, and the second assertion is
    what says so rather than assuming it: `run_cli_exit` returns what the
    arm rendered alongside the message it exits with, so a handler that
    printed the `Pong` row and then exited with this very message would
    fail here instead of shipping.
    """
    del tmp
    body = {'token': TOK, 'id': '_ping', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('_ping', 'tab0', 'd9', 10, interval=0.3)]
    code, out = run_cli_exit(['ping'], [{'did': 'd9'}, None],
                             module=commands_eval, plan=plan,
                             target_tab='tab0', token=TOK)

    assert code == 'Ping timeout (10s)', code
    assert out == '', repr(out)


def test_do_ping_exits_with_the_error_the_page_raised(tmp):
    """An error result is an exit, not a `Pong` line naming nothing.

    As with the timeout arm, the rendered half is empty and that is
    asserted, so the exit message really is the whole of what an operator
    sees on this arm.
    """
    del tmp
    body = {'token': TOK, 'id': '_ping', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('_ping', 'tab0', 'd9', 10, interval=0.3)]
    failed = {'id': '_ping', 'result': None, 'ts': 1,
              'error': 'ReferenceError: x'}
    code, out = run_cli_exit(['ping'], [{'did': 'd9'}, failed],
                             module=commands_eval, plan=plan,
                             target_tab='tab0', token=TOK)

    assert code == 'Ping error: ReferenceError: x', code
    assert out == '', repr(out)


# ── do_navigate / do_reload ──────────────────────────────────────────

def test_do_navigate_sends_a_location_assignment_and_never_waits(tmp):
    """The URL is JSON-encoded into the assignment, and nothing waits.

    A navigation's result arrives long after the operator's patience, so
    the plan has no second request: a handler that waited would be caught
    by the plan rather than by a slow suite.
    """
    del tmp
    body = {'token': TOK, 'id': '_nav',
            'code': 'location.href = "https://example.com/"', 'tab': 'tab0'}
    recorded, out = run_cli(
        ['navigate', 'https://example.com/'], [{'target': 'tab0'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.waits == [], recorded.waits
    assert _rendered(out) == f'{OUT} _nav {OUT} tab0  (38 bytes)\n', repr(out)


def test_do_navigate_json_encodes_a_url_carrying_a_quote(tmp):
    """A quote in the URL is escaped, not pasted: the code must stay a
    JavaScript string literal rather than terminating one.

    The rendered line is pinned whole: its byte count is the ENCODED
    code's, 50 including the backslash, against 38 for the plain URL the
    sibling test navigates to. The escaping is therefore visible in what
    the operator reads and not only in the body.
    """
    del tmp
    body = {'token': TOK, 'id': '_nav',
            'code': 'location.href = "https://example.com/a\\"b?x=1&y=2"',
            'tab': 'tab0'}
    recorded, out = run_cli(
        ['navigate', 'https://example.com/a"b?x=1&y=2'], [{'target': 'tab0'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == f'{OUT} _nav {OUT} tab0  (50 bytes)\n', repr(out)


def test_do_reload_sends_the_reload_expression_to_the_tab(tmp):
    """Reload is a fixed expression, sent to the named tab and not waited on.
    """
    del tmp
    body = {'token': TOK, 'id': '_reload', 'code': 'location.reload()',
            'tab': 'tab0'}
    recorded, out = run_cli(
        ['reload'], [{'target': 'tab0'}], module=commands_eval,
        plan=[_put(body)], target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == (
        f'{OUT} _reload {OUT} tab0  (17 bytes)\n'), repr(out)


def test_do_reload_broadcast_carries_no_tab(tmp):
    """`-b` on reload is the same absent-field contract as on put and exec.
    """
    del tmp
    body = {'token': TOK, 'id': '_reload', 'code': 'location.reload()'}
    recorded, out = run_cli(
        ['reload', '-b'], [{'target': 'bcast'}], module=commands_eval,
        plan=[_put(body)], target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == (
        f'{OUT} _reload {OUT} bcast  (17 bytes)\n'), repr(out)


# ── do_title / do_url ────────────────────────────────────────────────

def test_do_title_asks_the_page_for_its_title_in_the_tab(tmp):
    """The read-only pair sends one expression and does wait for it.

    Unlike navigate and reload, these have a result worth having, so the
    ten-second bound and the default poll interval are both pinned.
    """
    del tmp
    body = {'token': TOK, 'id': '_title', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('_title', 'tab0', 'd3', 10)]
    recorded, out = run_cli(
        ['title'], [{'target': 'tab0', 'did': 'd3'},
                    {'id': '_title', 'result': 'A page', 'error': None,
                     'ts': 1, 'tabId': 'tab0'}],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert _rendered(out) == (
        f'{OUT} _title {OUT} tab0  (14 bytes)\n'
        f'{IN} _title  tab=tab0\n'
        'A page\n'), repr(out)


def test_do_title_leaves_the_tab_field_off_when_none_is_set(tmp):
    """The absent-tab arm of the read-only pair, pinned separately.

    The result carries no `tabId`, so the result line drops the `tab=`
    cell as well as the sent row dropping its own. Both are whole strings
    rather than substrings of the sibling test's.
    """
    del tmp
    body = {'token': TOK, 'id': '_title', 'code': 'document.title'}
    plan = [_put(body), _wait('_title', '', 'd3', 10)]
    recorded, out = run_cli(
        ['title'], [{'target': 'bcast', 'did': 'd3'},
                    {'id': '_title', 'result': 'A page', 'error': None,
                     'ts': 1}],
        module=commands_eval, plan=plan, target_tab='', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == (
        f'{OUT} _title {OUT} bcast  (14 bytes)\n'
        f'{IN} _title\nA page\n'), repr(out)


def test_do_url_asks_the_page_for_its_location_in_the_tab(tmp):
    """`url` is the same shape as `title` over a different expression, and
    the two are pinned apart so neither can stand in for the other."""
    del tmp
    body = {'token': TOK, 'id': '_url', 'code': 'location.href',
            'tab': 'tab0'}
    plan = [_put(body), _wait('_url', 'tab0', 'd4', 10)]
    recorded, out = run_cli(
        ['url'], [{'target': 'tab0', 'did': 'd4'},
                  {'id': '_url', 'result': 'https://example.com/a',
                   'error': None, 'ts': 1, 'tabId': 'tab0'}],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == (
        f'{OUT} _url {OUT} tab0  (13 bytes)\n'
        f'{IN} _url  tab=tab0\n'
        'https://example.com/a\n'), repr(out)


def test_do_url_leaves_the_tab_field_off_when_none_is_set(tmp):
    """The absent-tab arm of `url`, pinned separately from `title`'s."""
    del tmp
    body = {'token': TOK, 'id': '_url', 'code': 'location.href'}
    plan = [_put(body), _wait('_url', '', 'd4', 10)]
    recorded, out = run_cli(
        ['url'], [{'target': 'bcast', 'did': 'd4'},
                  {'id': '_url', 'result': 'https://example.com/a',
                   'error': None, 'ts': 1}],
        module=commands_eval, plan=plan, target_tab='', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert _rendered(out) == (
        f'{OUT} _url {OUT} bcast  (13 bytes)\n'
        f'{IN} _url\nhttps://example.com/a\n'), repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clieval_')


if __name__ == '__main__':
    raise SystemExit(main())
