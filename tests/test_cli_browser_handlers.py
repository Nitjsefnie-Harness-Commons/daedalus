#!/usr/bin/env python3
"""Every branch of the four cookie handlers in commands_browser.py.

`do_cookies`, `do_set_cookie`, `do_remove_cookie` and `do_clear_cookies`
build their own `/command` body and pair `api` with `wait_for_result`, so
each test here asserts the exact body that reached the wire — field names
and values, in the shape `transport.py` sends — and the exact output an
operator reads, compared as one string.

Every field a handler adds under a flag is pinned BOTH ways: once present
and once absent. An arm that is only ever taken alongside another never
runs in a suite that only ever passes every flag, and the untested arm is a
silent lockout — the flag stops working and nothing goes red.

These are driven through `tests/_cli_dispatch.py`, the shared harness, not
through the older private `run_cli` copy in tests/test_fetch_timings_count.py:
that one is permissive, records no plan, and would pass a handler that sent
a body nobody declared.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_browser  # noqa: E402

run_cli = _cli_dispatch.run_cli
run_cli_exit = _cli_dispatch.run_cli_exit

TOK = 'clitok'


def _put(body):
    return {'via': 'api', 'method': 'PUT', 'path': '/command', 'body': body}


def _wait(cmd_id, delivery, timeout, interval=0.5):
    return {'via': 'wait_for_result', 'id': cmd_id, 'tab': 'extension',
            'delivery': delivery, 'timeout': timeout, 'interval': interval}


def _envelope(**over):
    """The result shape the bridge hands back for a cookie command."""
    base = {'id': 'x', 'result': [], 'error': None, 'ts': 1}
    return dict(base, **over)


# ── do_cookies ───────────────────────────────────────────────────────

def test_do_cookies_asks_with_no_filter_and_sends_neither_field(tmp):
    """The bare arm carries neither `domain` nor `url` on the wire.

    Sending both as empty strings would be a different request: the
    extension filters on the field's presence, so an empty one narrows the
    answer to nothing rather than filtering by nothing.
    """
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_cookies', 'd1', 10)]
    recorded, out = run_cli(
        ['cookies'], [{'did': 'd1'},
                      _envelope(id='_cookies', result=[
                          {'domain': 'a.example.com', 'name': 'sid',
                           'value': 'abc'},
                          {'domain': 'b.example.com', 'name': 'pref',
                           'value': 'dark'}])],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == (
        '  a.example.com  sid=abc\n'
        '  b.example.com  pref=dark\n'
        '2 cookies\n'), repr(out)


def test_do_cookies_carries_the_domain_only_when_it_was_given(tmp):
    """`-d` adds `domain`; the sibling `url` filter stays off."""
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension', 'domain': 'example.com'}
    plan = [_put(body), _wait('_cookies', 'd1', 10)]
    recorded, out = run_cli(
        ['cookies', '-d', 'example.com'],
        [{'did': 'd1'}, _envelope(id='_cookies', result=[])],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == '0 cookies\n', repr(out)


def test_do_cookies_carries_the_url_only_when_it_was_given(tmp):
    """`-u` adds `url`, and `domain` is still absent on this arm."""
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/'}
    plan = [_put(body), _wait('_cookies', 'd1', 10)]
    _recorded, out = run_cli(
        ['cookies', '-u', 'https://example.com/'],
        [{'did': 'd1'}, _envelope(id='_cookies', result=[])],
        module=commands_browser, plan=plan, token=TOK)

    assert out == '0 cookies\n', repr(out)


def test_do_cookies_carries_both_filters_when_both_were_given(tmp):
    """The two filters are independent; asking for both sends both."""
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension', 'domain': 'example.com',
            'url': 'https://example.com/'}
    plan = [_put(body), _wait('_cookies', 'd1', 10)]
    recorded, out = run_cli(
        ['cookies', '-d', 'example.com', '-u', 'https://example.com/'],
        [{'did': 'd1'}, _envelope(id='_cookies', result=[])],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == '0 cookies\n', repr(out)


def test_do_cookies_prints_the_whole_cookie_list_as_json_when_raw(tmp):
    """`--raw` is the machine arm: the list, indented, then the count.

    The count is printed either way, so a consumer reading stdout has the
    header it needs without a second round trip.
    """
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_cookies', 'd1', 10)]
    _recorded, out = run_cli(
        ['cookies', '--raw'],
        [{'did': 'd1'},
         _envelope(id='_cookies', result=[
             {'domain': 'a.example.com', 'name': 'sid', 'value': 'abc'}])],
        module=commands_browser, plan=plan, token=TOK)

    assert out == (
        '[\n'
        '  {\n'
        '    "domain": "a.example.com",\n'
        '    "name": "sid",\n'
        '    "value": "abc"\n'
        '  }\n'
        ']\n'
        '1 cookies\n'), repr(out)


def test_do_cookies_renders_a_cookie_carrying_none_of_its_three(tmp):
    """A cookie row may carry no domain, no name and no value.

    Each cell has its own empty-string default, so the row is four spaces,
    an equals sign and nothing else — not the word `None` in three places.
    """
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_cookies', 'd1', 10)]
    _recorded, out = run_cli(
        ['cookies'], [{'did': 'd1'}, _envelope(id='_cookies', result=[{}])],
        module=commands_browser, plan=plan, token=TOK)

    assert out == '    =\n1 cookies\n', repr(out)


def test_do_cookies_exits_when_no_result_arrives(tmp):
    """The timeout exit names the bound it actually waited."""
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_cookies', 'd1', 10)]
    code, out = run_cli_exit(['cookies'], [{'did': 'd1'}, None],
                             module=commands_browser, plan=plan, token=TOK)

    assert code == 'Timeout (10s)', code
    assert out == '', repr(out)


def test_do_cookies_waits_the_bound_it_was_given(tmp):
    """`-t 7` is the wait and the message; the parser default of 0 is not.

    `args.timeout or 10` reads a zero as unset, so a caller who never asked
    gets the documented ten and a caller who asked gets exactly what they
    asked for.
    """
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_cookies', 'd1', 7)]
    code, out = run_cli_exit(['cookies', '-t', '7'], [{'did': 'd1'}, None],
                             module=commands_browser, plan=plan, token=TOK)

    assert code == 'Timeout (7s)', code
    assert out == '', repr(out)


def test_do_cookies_exits_with_the_error_the_extension_reported(tmp):
    """An error result is an exit, not a `0 cookies` line."""
    del tmp
    body = {'id': '_cookies', 'type': 'cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_cookies', 'd1', 10)]
    code, out = run_cli_exit(
        ['cookies'], [{'did': 'd1'},
                      {'id': '_cookies', 'error': 'no such tab', 'ts': 1}],
        module=commands_browser, plan=plan, token=TOK)

    assert code == 'Cookie error: no such tab', code
    assert out == '', repr(out)


# ── do_set_cookie ────────────────────────────────────────────────────

def test_do_set_cookie_sends_the_three_required_fields_and_nothing_else(tmp):
    """The bare arm: url, name and value, with every option off the body.

    The options are pinned by their absence here, because the sibling test
    that turns all six on cannot tell a handler that always sent them.
    """
    del tmp
    body = {'id': '_set_cookie', 'type': 'set-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid',
            'value': 'abc123'}
    plan = [_put(body), _wait('_set_cookie', 'd2', 10)]
    recorded, out = run_cli(
        ['set-cookie', 'https://example.com/', 'sid', 'abc123'],
        [{'did': 'd2'}, _envelope(id='_set_cookie', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == 'Set: sid=abc123 on https://example.com/\n', repr(out)


def test_do_set_cookie_sends_every_option_it_was_given(tmp):
    """All six optional fields, spelled the way the wire spells them.

    `httpOnly`, `secure` and `bypassCache`-style camel casing is the
    extension's, not the CLI's: `--http-only` and `--same-site` become
    `httpOnly` and `sameSite`, and `--expires` becomes `expirationDate`
    after the parser's float conversion.
    """
    del tmp
    body = {'id': '_set_cookie', 'type': 'set-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid',
            'value': 'abc123', 'domain': 'example.com', 'path': '/app',
            'httpOnly': True, 'secure': True, 'sameSite': 'lax',
            'expirationDate': 1800000000.0}
    plan = [_put(body), _wait('_set_cookie', 'd2', 10)]
    recorded, out = run_cli(
        ['set-cookie', 'https://example.com/', 'sid', 'abc123',
         '-d', 'example.com', '--path', '/app', '--http-only', '--secure',
         '--same-site', 'lax', '--expires', '1800000000'],
        [{'did': 'd2'}, _envelope(id='_set_cookie', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == 'Set: sid=abc123 on https://example.com/\n', repr(out)


def test_do_set_cookie_sends_an_expiry_of_zero_because_it_is_not_unset(tmp):
    """`--expires 0` is a real expiry, and the field is `is not None`.

    Every other option on this handler is guarded by truthiness, so this
    is the one that is not: `args.expires is not None` is what lets a zero
    reach the wire. `transport.expiration_timestamp` documents the type as
    "`float()`'s whole domain, inf and nan included", so `0` is
    operator-reachable and a truthiness guard would silently drop it —
    turning a cookie the caller asked to expire into a session cookie,
    with the `Set:` line reporting success either way.

    The expected body carries `expirationDate` and no other optional
    field, so this arm is the one that says the value is sent on its own
    rather than riding along with the six-option test above.
    """
    del tmp
    body = {'id': '_set_cookie', 'type': 'set-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid',
            'value': 'abc123', 'expirationDate': 0.0}
    plan = [_put(body), _wait('_set_cookie', 'd2', 10)]
    recorded, out = run_cli(
        ['set-cookie', 'https://example.com/', 'sid', 'abc123',
         '--expires', '0'],
        [{'did': 'd2'}, _envelope(id='_set_cookie', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == 'Set: sid=abc123 on https://example.com/\n', repr(out)


def test_do_set_cookie_sends_the_secure_flag_on_its_own(tmp):
    """`--secure` alone: `httpOnly` and `sameSite` are still absent."""
    del tmp
    body = {'id': '_set_cookie', 'type': 'set-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid',
            'value': 'abc123', 'secure': True}
    plan = [_put(body), _wait('_set_cookie', 'd2', 10)]
    recorded, _out = run_cli(
        ['set-cookie', 'https://example.com/', 'sid', 'abc123', '--secure'],
        [{'did': 'd2'}, _envelope(id='_set_cookie', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


def test_do_set_cookie_truncates_the_value_it_echoes_at_sixty_columns(tmp):
    """The confirmation line cuts the value; the body carries all of it.

    Sixty characters, with no ellipsis: the point of the cut is a fixed
    width, and a marker after it would make the width a floor instead.
    """
    del tmp
    long_value = 'x' * 70
    body = {'id': '_set_cookie', 'type': 'set-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid',
            'value': long_value}
    plan = [_put(body), _wait('_set_cookie', 'd2', 10)]
    recorded, out = run_cli(
        ['set-cookie', 'https://example.com/', 'sid', long_value],
        [{'did': 'd2'}, _envelope(id='_set_cookie', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == (
        'Set: sid=' + 'x' * 60 + ' on https://example.com/\n'), repr(out)


def test_do_set_cookie_exits_when_no_result_arrives(tmp):
    """The timeout exit is the fixed ten — this handler takes no `-t`."""
    del tmp
    body = {'id': '_set_cookie', 'type': 'set-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid',
            'value': 'abc123'}
    plan = [_put(body), _wait('_set_cookie', 'd2', 10)]
    code, out = run_cli_exit(
        ['set-cookie', 'https://example.com/', 'sid', 'abc123'],
        [{'did': 'd2'}, None],
        module=commands_browser, plan=plan, token=TOK)

    assert code == 'Timeout (10s)', code
    assert out == '', repr(out)


def test_do_set_cookie_exits_with_the_error_the_extension_reported(tmp):
    """A refused cookie is an exit naming the refusal."""
    del tmp
    body = {'id': '_set_cookie', 'type': 'set-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid',
            'value': 'abc123'}
    plan = [_put(body), _wait('_set_cookie', 'd2', 10)]
    code, out = run_cli_exit(
        ['set-cookie', 'https://example.com/', 'sid', 'abc123'],
        [{'did': 'd2'},
         {'id': '_set_cookie', 'error': 'Invalid cookie', 'ts': 1}],
        module=commands_browser, plan=plan, token=TOK)

    assert code == 'Error: Invalid cookie', code
    assert out == '', repr(out)


# ── do_remove_cookie ─────────────────────────────────────────────────

def test_do_remove_cookie_names_the_url_and_the_cookie_it_removes(tmp):
    """The bare arm: two fields, one PUT, one wait, one printed line."""
    del tmp
    body = {'id': '_rm_cookie', 'type': 'remove-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid'}
    plan = [_put(body), _wait('_rm_cookie', 'd3', 10)]
    recorded, out = run_cli(
        ['remove-cookie', 'https://example.com/', 'sid'],
        [{'did': 'd3'}, _envelope(id='_rm_cookie', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == 'Removed: sid from https://example.com/\n', repr(out)


def test_do_remove_cookie_exits_when_no_result_arrives(tmp):
    """The timeout exit, and nothing rendered before it."""
    del tmp
    body = {'id': '_rm_cookie', 'type': 'remove-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid'}
    plan = [_put(body), _wait('_rm_cookie', 'd3', 10)]
    code, out = run_cli_exit(
        ['remove-cookie', 'https://example.com/', 'sid'],
        [{'did': 'd3'}, None],
        module=commands_browser, plan=plan, token=TOK)

    assert code == 'Timeout (10s)', code
    assert out == '', repr(out)


def test_do_remove_cookie_exits_with_the_error_the_extension_reported(tmp):
    """A refused removal is an exit, not a `Removed:` line."""
    del tmp
    body = {'id': '_rm_cookie', 'type': 'remove-cookie', 'token': TOK,
            'tab': 'extension', 'url': 'https://example.com/', 'name': 'sid'}
    plan = [_put(body), _wait('_rm_cookie', 'd3', 10)]
    code, out = run_cli_exit(
        ['remove-cookie', 'https://example.com/', 'sid'],
        [{'did': 'd3'},
         {'id': '_rm_cookie', 'error': 'No such cookie', 'ts': 1}],
        module=commands_browser, plan=plan, token=TOK)

    assert code == 'Error: No such cookie', code
    assert out == '', repr(out)


# ── do_clear_cookies ─────────────────────────────────────────────────

def test_do_clear_cookies_sends_neither_filter_when_none_was_given(tmp):
    """The bare arm: no `domain`, no `url` — a whole-store clear."""
    del tmp
    body = {'id': '_clear_cookies', 'type': 'clear-cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_clear_cookies', 'd4', 10)]
    recorded, out = run_cli(
        ['clear-cookies'],
        [{'did': 'd4'}, _envelope(id='_clear_cookies', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [10], recorded.timeouts
    # A result carrying no `removed` counts zero rather than refusing.
    assert out == 'Cleared 0 cookies\n', repr(out)


def test_do_clear_cookies_carries_both_filters_when_both_were_given(tmp):
    """`-d` and `-u` are independent, exactly as on `cookies`."""
    del tmp
    body = {'id': '_clear_cookies', 'type': 'clear-cookies', 'token': TOK,
            'tab': 'extension', 'domain': 'example.com',
            'url': 'https://example.com/'}
    plan = [_put(body), _wait('_clear_cookies', 'd4', 10)]
    recorded, out = run_cli(
        ['clear-cookies', '-d', 'example.com', '-u', 'https://example.com/'],
        [{'did': 'd4'},
         _envelope(id='_clear_cookies', result={'removed': 2})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == 'Cleared 2 cookies\n', repr(out)


def test_do_clear_cookies_reports_the_cookies_it_could_not_remove(tmp):
    """A refused removal is its own line, below the count.

    Folding these into the number would report a domain as cleared while
    the cookies are still there, so the two are printed separately and the
    names are joined in the order the extension listed them.
    """
    del tmp
    body = {'id': '_clear_cookies', 'type': 'clear-cookies', 'token': TOK,
            'tab': 'extension', 'domain': 'example.com'}
    plan = [_put(body), _wait('_clear_cookies', 'd4', 10)]
    answer = {'removed': 1,
              'failed': ['a.example.com', 'b.example.com']}
    _recorded, out = run_cli(
        ['clear-cookies', '-d', 'example.com'],
        [{'did': 'd4'}, _envelope(id='_clear_cookies', result=answer)],
        module=commands_browser, plan=plan, token=TOK)

    assert out == (
        'Cleared 1 cookies\n'
        'Could not remove 2: a.example.com, b.example.com\n'), repr(out)


def test_do_clear_cookies_prints_no_second_line_when_nothing_failed(tmp):
    """An empty `failed` list prints nothing at all.

    `result.get('failed') or []` treats an absent key and an empty list the
    same, and this is the arm that keeps the second line out of the output
    for the common all-succeeded case.
    """
    del tmp
    body = {'id': '_clear_cookies', 'type': 'clear-cookies', 'token': TOK,
            'tab': 'extension', 'domain': 'example.com'}
    plan = [_put(body), _wait('_clear_cookies', 'd4', 10)]
    _recorded, out = run_cli(
        ['clear-cookies', '-d', 'example.com'],
        [{'did': 'd4'},
         _envelope(id='_clear_cookies', result={'removed': 3, 'failed': []})],
        module=commands_browser, plan=plan, token=TOK)

    assert out == 'Cleared 3 cookies\n', repr(out)


def test_do_clear_cookies_exits_when_no_result_arrives(tmp):
    """The timeout exit names the bound it waited — ten by default."""
    del tmp
    body = {'id': '_clear_cookies', 'type': 'clear-cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_clear_cookies', 'd4', 10)]
    code, out = run_cli_exit(['clear-cookies'], [{'did': 'd4'}, None],
                             module=commands_browser, plan=plan, token=TOK)

    assert code == 'Timeout (10s)', code
    assert out == '', repr(out)


def test_do_clear_cookies_waits_the_bound_it_was_given(tmp):
    """`-t 4` is the wait and the message it exits with."""
    del tmp
    body = {'id': '_clear_cookies', 'type': 'clear-cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_clear_cookies', 'd4', 4)]
    code, out = run_cli_exit(['clear-cookies', '-t', '4'],
                             [{'did': 'd4'}, None],
                             module=commands_browser, plan=plan, token=TOK)

    assert code == 'Timeout (4s)', code
    assert out == '', repr(out)


def test_do_clear_cookies_exits_with_the_error_the_extension_reported(tmp):
    """A refused clear is an exit, not a `Cleared 0 cookies` line."""
    del tmp
    body = {'id': '_clear_cookies', 'type': 'clear-cookies', 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_clear_cookies', 'd4', 10)]
    code, out = run_cli_exit(
        ['clear-cookies'], [{'did': 'd4'},
                            {'id': '_clear_cookies',
                             'error': 'Extension asleep', 'ts': 1}],
        module=commands_browser, plan=plan, token=TOK)

    assert code == 'Error: Extension asleep', code
    assert out == '', repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clibrowsec_')


if __name__ == '__main__':
    raise SystemExit(main())
