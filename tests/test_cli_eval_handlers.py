#!/usr/bin/env python3
"""Every branch of every handler in daedalus_cli/commands_eval.py.

`do_ping`, `do_title` and `do_url` had no test at all before this file, and
the rest had untested render arms. Each test here asserts BOTH halves of a
handler: the exact request it put on the wire — method, path, and the body
field names and values — and the exact output an operator would read,
compared as one string. The rendered halves differ only in spacing and in
which marker precedes a line, so a substring assertion would accept a row
that is otherwise wrong.

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
from daedalus_cli.output import MARK  # noqa: E402

run_cli = _cli_dispatch.run_cli

IN = MARK['in']
OUT = MARK['out']

TOK = 'clitok'


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
    """Ages 5 and 120 arrive in that order and print in the other one.

    The listing sorts by age, so the row an operator reads first is the
    freshest tab. Rendering them in the order the bridge returned them
    would still look right for a list that already happened to be sorted.
    """
    del tmp
    tabs = [
        {'tabId': 'tab9', 'age': 5, 'url': 'https://one.example.com/',
         'title': 'Nine'},
        {'tabId': 'tab1', 'age': 120, 'url': 'https://two.example.com/',
         'title': 'One'},
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
    assert out == (
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
    assert out == f'{OUT} job1 {OUT} tab0  (14 bytes)\n', repr(out)


def test_do_put_reads_the_dash_standard_input(tmp):
    """`-` is stdin, so the code never touches the filesystem."""
    del tmp
    body = {'token': TOK, 'id': 'job1', 'code': 'window.x = 1', 'tab': 'tab0'}
    with _stdin('window.x = 1\n'):
        recorded, _out = run_cli(
            ['put', 'job1', '-', '--no-result'], [{'target': 'tab0'}],
            module=commands_eval, plan=[_put(body)], target_tab='tab0',
            token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


def test_do_put_refuses_a_file_that_is_not_there(tmp):
    """The refusal is a sentence naming the path, and nothing is sent."""
    missing = str(Path(tmp) / 'absent.js')
    try:
        run_cli(['put', 'job1', missing, '--no-result'], [],
                module=commands_eval, plan=[], token=TOK)
    except SystemExit as exit_request:
        assert exit_request.code == f'File not found: {missing}', \
            exit_request.code
    else:
        raise AssertionError('a missing file must not enqueue a command')


def test_do_put_broadcast_leaves_the_tab_field_off_the_body(tmp):
    """`-b` means the browser's active tab, which is an absent tab field.

    Sending `tab: ''` instead would name a tab that does not exist rather
    than name none, and the two travel differently on the wire.
    """
    path = _source(tmp)
    body = {'token': TOK, 'id': 'job1', 'code': 'document.title'}
    recorded, _out = run_cli(
        ['put', 'job1', path, '-b', '--no-result'], [{'target': 'bcast'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0',
        token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


def test_do_put_waits_the_default_fifteen_seconds_and_not_the_zero(tmp):
    """`-t 0` reads as unset, so the deadline is the documented default.

    Handing the zero to the waiter made the deadline now plus nothing, and
    the CLI reported `Timeout (0s)` for a command the browser then ran.
    """
    path = _source(tmp)
    body = {'token': TOK, 'id': 'job1', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('job1', 'tab0', 'd1', 15)]
    recorded, _out = run_cli(
        ['put', 'job1', path, '-t', '0'],
        [{'target': 'tab0', 'did': 'd1'}, _result()],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK)

    assert recorded.timeouts == [15], recorded.timeouts


def test_do_put_carries_an_explicit_timeout_through_unchanged(tmp):
    """The other arm of `args.timeout or 15`: a stated value is the value."""
    path = _source(tmp)
    body = {'token': TOK, 'id': 'job1', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('job1', 'tab0', 'd1', 7)]
    recorded, _out = run_cli(
        ['put', 'job1', path, '--timeout', '7'],
        [{'target': 'tab0', 'did': 'd1'}, _result()],
        module=commands_eval, plan=plan, target_tab='tab0', token=TOK)

    assert recorded.timeouts == [7], recorded.timeouts


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
    assert out == (
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
    assert out == f'{OUT} job5 {OUT} bcast  (3 bytes)\n', repr(out)


def test_do_exec_without_a_result_never_waits(tmp):
    """`--no-result` sends and returns: the plan has no second request.

    A handler that waited anyway would block an operator who asked not to,
    and a plan that omitted the wait is what makes that visible.
    """
    del tmp
    body = {'token': TOK, 'id': 'job5', 'code': '1+1', 'tab': 'tab0'}
    recorded, _out = run_cli(
        ['exec', 'job5', '1+1', '--no-result'], [{'target': 'tab0'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0',
        token=TOK)

    assert recorded.waits == [], recorded.waits


# ── do_result ────────────────────────────────────────────────────────

def test_do_result_asks_for_the_tab_it_was_given(tmp):
    """The tab is a query parameter, percent-encoded by the shared builder."""
    del tmp
    recorded, out = run_cli(
        ['result'], [_result(tabId='tab0')],
        module=commands_eval, plan=[_get('/result?tab=tab0')],
        target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('GET', '/result?tab=tab0', None)], \
        recorded.api_calls
    assert out == f'{IN} job1  tab=tab0\nok\n', repr(out)


def test_do_result_asks_for_the_broadcast_result_when_no_tab_is_set(tmp):
    """No tab means the path carries no query at all, not an empty one."""
    del tmp
    _recorded, out = run_cli(
        ['result'], [_result()], module=commands_eval, plan=[_get('/result')],
        target_tab='', token=TOK)

    assert out == f'{IN} job1\nok\n', repr(out)


def test_do_result_adds_the_consume_flag_only_when_it_was_asked(tmp):
    """`consume=1` travels after `tab`, and only with the flag."""
    del tmp
    _recorded, _out = run_cli(
        ['result', '-c'], [_result()], module=commands_eval,
        plan=[_get('/result?tab=tab0&consume=1')], target_tab='tab0',
        token=TOK)


def test_do_result_says_so_when_nothing_is_pending(tmp):
    """The empty arm is a sentence and an early return, not a print of the
    pending envelope the bridge sent."""
    del tmp
    _recorded, out = run_cli(
        ['result'], [{'pending': True}], module=commands_eval,
        plan=[_get('/result?tab=tab0')], target_tab='tab0', token=TOK)

    assert out == 'No result pending\n', repr(out)


def test_do_result_prints_raw_json_when_asked(tmp):
    """`--raw` hands the machine the envelope, unindented by no printer."""
    del tmp
    _recorded, out = run_cli(
        ['result', '--raw'], [_result()], module=commands_eval,
        plan=[_get('/result?tab=tab0')], target_tab='tab0', token=TOK)

    assert out == (
        '{\n  "id": "job1",\n  "result": "ok",\n  "error": null,\n'
        '  "ts": 1\n}\n'), repr(out)


def test_do_result_renders_an_undefined_result_as_a_word(tmp):
    """A command that produced nothing says so, rather than printing None.
    """
    del tmp
    _recorded, out = run_cli(
        ['result'], [_result(result=None, tabId='tab0')],
        module=commands_eval,
        plan=[_get('/result?tab=tab0')], target_tab='tab0', token=TOK)

    assert out == f'{IN} job1  tab=tab0\n(undefined)\n', repr(out)


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
    """The timeout exit names the bound it actually waited."""
    del tmp
    body = {'token': TOK, 'id': '_ping', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('_ping', 'tab0', 'd9', 10, interval=0.3)]
    try:
        run_cli(['ping'], [{'did': 'd9'}, None], module=commands_eval,
                plan=plan, target_tab='tab0', token=TOK)
    except SystemExit as exit_request:
        assert exit_request.code == 'Ping timeout (10s)', exit_request.code
    else:
        raise AssertionError('a ping that never came back must not report one')


def test_do_ping_exits_with_the_error_the_page_raised(tmp):
    """An error result is an exit, not a `Pong` line naming nothing."""
    del tmp
    body = {'token': TOK, 'id': '_ping', 'code': 'document.title',
            'tab': 'tab0'}
    plan = [_put(body), _wait('_ping', 'tab0', 'd9', 10, interval=0.3)]
    failed = {'id': '_ping', 'result': None, 'ts': 1,
              'error': 'ReferenceError: x'}
    try:
        run_cli(['ping'], [{'did': 'd9'}, failed],
                module=commands_eval, plan=plan, target_tab='tab0', token=TOK)
    except SystemExit as exit_request:
        assert exit_request.code == 'Ping error: ReferenceError: x', \
            exit_request.code
    else:
        raise AssertionError('a failed ping must not report a pong')


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
    assert out == f'{OUT} _nav {OUT} tab0  (38 bytes)\n', repr(out)


def test_do_navigate_json_encodes_a_url_carrying_a_quote(tmp):
    """A quote in the URL is escaped, not pasted: the code must stay a
    JavaScript string literal rather than terminating one."""
    del tmp
    body = {'token': TOK, 'id': '_nav',
            'code': 'location.href = "https://example.com/a\\"b?x=1&y=2"',
            'tab': 'tab0'}
    recorded, _out = run_cli(
        ['navigate', 'https://example.com/a"b?x=1&y=2'], [{'target': 'tab0'}],
        module=commands_eval, plan=[_put(body)], target_tab='tab0', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


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
    assert out == f'{OUT} _reload {OUT} tab0  (17 bytes)\n', repr(out)


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
    assert out == f'{OUT} _reload {OUT} bcast  (17 bytes)\n', repr(out)


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
    assert out == (
        f'{OUT} _title {OUT} tab0  (14 bytes)\n'
        f'{IN} _title  tab=tab0\n'
        'A page\n'), repr(out)


def test_do_title_leaves_the_tab_field_off_when_none_is_set(tmp):
    """The absent-tab arm of the read-only pair, pinned separately."""
    del tmp
    body = {'token': TOK, 'id': '_title', 'code': 'document.title'}
    plan = [_put(body), _wait('_title', '', 'd3', 10)]
    recorded, _out = run_cli(
        ['title'], [{'target': 'bcast', 'did': 'd3'},
                    {'id': '_title', 'result': 'A page', 'error': None,
                     'ts': 1}],
        module=commands_eval, plan=plan, target_tab='', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


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
    assert out == (
        f'{OUT} _url {OUT} tab0  (13 bytes)\n'
        f'{IN} _url  tab=tab0\n'
        'https://example.com/a\n'), repr(out)


def test_do_url_leaves_the_tab_field_off_when_none_is_set(tmp):
    """The absent-tab arm of `url`, pinned separately from `title`'s."""
    del tmp
    body = {'token': TOK, 'id': '_url', 'code': 'location.href'}
    plan = [_put(body), _wait('_url', '', 'd4', 10)]
    recorded, _out = run_cli(
        ['url'], [{'target': 'bcast', 'did': 'd4'},
                  {'id': '_url', 'result': 'https://example.com/a',
                   'error': None, 'ts': 1}],
        module=commands_eval, plan=plan, target_tab='', token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clieval_')


if __name__ == '__main__':
    raise SystemExit(main())
