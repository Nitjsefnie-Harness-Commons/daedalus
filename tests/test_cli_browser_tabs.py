#!/usr/bin/env python3
"""Every branch of the five tab handlers in commands_browser.py.

Each test pins the wire, the PUT and wait of `open-tab` and `open-tabs` or
the one `ext_cmd` call of the other three (command id, wire type, the fields
as the extension receives them, the timeout chosen), beside the exact output
an operator reads, compared as one string.

A field sent only under a flag is pinned both ways: only the bare arm sees a
handler that always sends `active` or `pinned`. The marker glyphs are spelled
out, not read from `daedalus_cli.output`: an expected value taken from the
module under test pins nothing about it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
from _cli_handler_wire import _api_put, _ext_cmd, _wait_result  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_browser  # noqa: E402

run_cli = _cli_dispatch.run_cli
run_cli_exit = _cli_dispatch.run_cli_exit

OUT = '→'
TOK = 'clitok'


def _tab_plan(command, fields, waited):
    """The send and the wait of one tab-opening command, answered as `d1`."""
    cmd_id = '_' + command.replace('-', '_')
    return [_api_put({'id': cmd_id, 'type': command, 'token': TOK,
                      'tab': 'extension', **fields}),
            _wait_result(cmd_id, 'd1', waited)]


def _rendered(out):
    """`out` with the ASCII marker fallback folded onto the glyph."""
    return out.replace('->', OUT)


# ── do_open_tab ──────────────────────────────────────────────────────

def test_do_open_tab_sends_each_option_as_its_own_field_and_waits_as_told(
        tmp):
    """`--background` is `active: False` (not `background`), `--pinned` is
    `pinned`; `-t` is the wait, `-t 0` keeps the default 10, and a result
    with no `tabId` reads `?`. Each arm carries nothing of the others.
    """
    del tmp
    cases = ((['--background'], {'active': False}, 10, 42),
             (['--pinned'], {'pinned': True}, 10, 42),
             (['-t', '7'], {}, 7, 42),
             (['-t', '0'], {}, 10, '?'))
    for flags, extra, waited, tab in cases:
        _recorded, out = run_cli(
            ['open-tab', 'https://example.com/', *flags],
            [{'did': 'd1'}, {'result': {} if tab == '?' else {'tabId': tab}}],
            module=commands_browser, token=TOK, plan=_tab_plan(
                'open-tab', {'url': 'https://example.com/', **extra}, waited))

        assert _rendered(out) == (
            f'Opened tab {tab} {OUT} https://example.com/\n'), repr(out)


def test_a_tab_opening_command_calls_no_answer_unconfirmed(tmp):
    """No answer in time is an unknown outcome, not a plain failure; an
    extension error is a plain one."""
    del tmp
    url = 'https://example.com/'
    for argv, fields, waited, answer, expected in (
            (['open-tab', url, '-t', '7'], {'url': url}, 7, None,
             'Timeout (7s), outcome unknown: the tab may have opened. '
             'Run `tabs` to check before retrying.'),
            (['open-tabs', url], {'urls': [url]}, 30, None,
             'Timeout (30s), outcome unknown: the tabs may have opened. '
             'Run `tabs` to check before retrying.'),
            (['open-tab', url, '-t', '7'], {'url': url}, 7,
             {'error': 'tabs.create refused'}, 'Error: tabs.create refused')):
        code, out = run_cli_exit(
            argv, [{'did': 'd1'}, answer], module=commands_browser, token=TOK,
            plan=_tab_plan(argv[0], fields, waited))
        assert (code, out) == (expected, ''), (code, out)


# ── do_open_tabs ─────────────────────────────────────────────────────

def test_do_open_tabs_sends_every_url_in_order_and_waits_thirty(tmp):
    """The urls travel as a list in the order they were typed.

    Thirty seconds, not the ten the other four tab handlers use: opening
    many tabs is the one round trip that legitimately outlives it.
    """
    del tmp
    fields = {'urls': ['https://a.example.com/', 'https://b.example.com/']}
    _recorded, out = run_cli(
        ['open-tabs', 'https://a.example.com/', 'https://b.example.com/'],
        [{'did': 'd1'}, {'result': {'opened': [
            {'tabId': 42, 'url': 'https://a.example.com/'},
            {'tabId': 43, 'url': 'https://b.example.com/'}], 'errors': []}}],
        module=commands_browser, token=TOK,
        plan=_tab_plan('open-tabs', fields, 30))

    assert _rendered(out) == (
        f'Opened tab 42 {OUT} https://a.example.com/\n'
        f'Opened tab 43 {OUT} https://b.example.com/\n'
        '2 opened, 0 failed\n'), repr(out)


def test_do_open_tabs_reports_each_line_and_sends_each_flag_as_its_field(tmp):
    """`--background` adds `active: False`, `--pinned` adds `pinned`."""
    del tmp
    one = ['https://a.example.com/']
    cases = (
        (['https://a.example.com/', 'https://b.example.com/'], [], {},
         {'opened': [{'tabId': 42, 'url': 'https://a.example.com/'}],
          'errors': [{'url': 'https://b.example.com/',
                      'error': 'blocked by policy'}]},
         f'Opened tab 42 {OUT} https://a.example.com/\n'
         'FAILED https://b.example.com/: blocked by policy\n'
         '1 opened, 1 failed\n'),
        (one, ['--background'], {'active': False}, {}, '0 opened, 0 failed\n'),
        (one, ['--pinned'], {'pinned': True}, {'errors': [{}]},
         'FAILED ?: \n0 opened, 1 failed\n'),
    )
    for urls, flags, extra, result, expected in cases:
        _recorded, out = run_cli(
            ['open-tabs', *urls, *flags], [{'did': 'd1'}, {'result': result}],
            module=commands_browser, token=TOK,
            plan=_tab_plan('open-tabs', {'urls': urls, **extra}, 30))
        assert _rendered(out) == expected, repr(out)


# ── do_focus_tab ─────────────────────────────────────────────────────

def test_do_focus_tab_names_the_tab_and_window_or_a_placeholder_for_each(tmp):
    del tmp
    for result, expected in (({'tabId': 0, 'windowId': 7},
                              'Focused tab 0 window=7\n'),
                             ({}, 'Focused tab ? window=?\n')):
        recorded, out = run_cli(
            ['focus-tab', '0'], [result], module=commands_browser, token=TOK,
            plan=[_ext_cmd('_focus', 'focus-tab', {'tabId': 0})])

        assert recorded.calls == [('_focus', 'focus-tab', {'tabId': 0})]
        assert out == expected, repr(out)


# ── do_ext_navigate ──────────────────────────────────────────────────

def test_do_ext_navigate_carries_the_chrome_tab_only_when_it_was_given(tmp):
    """The active tab is implicit; --chrome-tab names even tab zero."""
    del tmp
    cases = (([], {'url': 'https://example.com/'}, 42),
             (['--chrome-tab', '0'],
              {'url': 'https://example.com/', 'tabId': 0}, 0))
    for flags, fields, tab in cases:
        recorded, out = run_cli(
            ['ext-navigate', 'https://example.com/', *flags],
            [{'tabId': tab}], module=commands_browser,
            plan=[_ext_cmd('_nav', 'navigate', fields)], token=TOK)
        assert recorded.calls == [('_nav', 'navigate', fields)], \
            recorded.calls
        assert _rendered(out) == (
            f'Navigated tab {tab} {OUT} https://example.com/\n'), repr(out)


# ── do_ext_reload ────────────────────────────────────────────────────

def test_do_ext_reload_names_the_cache_bypass_in_its_output_too(tmp):
    del tmp
    cases = (
        ([], {}, 42, 'Reloaded tab 42\n'),
        (['--chrome-tab', '0'], {'tabId': 0}, 0, 'Reloaded tab 0\n'),
        (['--bypass-cache'], {'bypassCache': True}, 42,
         'Reloaded tab 42 (bypass cache)\n'),
        (['--chrome-tab', '7', '--bypass-cache'],
         {'tabId': 7, 'bypassCache': True}, 7,
         'Reloaded tab 7 (bypass cache)\n'),
    )
    for flags, fields, tab, expected in cases:
        recorded, out = run_cli(
            ['ext-reload', *flags], [{'tabId': tab}],
            module=commands_browser,
            plan=[_ext_cmd('_reload', 'reload', fields)], token=TOK)
        assert recorded.calls == [('_reload', 'reload', fields)], \
            recorded.calls
        assert out == expected, repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clibrowtab_')


if __name__ == '__main__':
    raise SystemExit(main())
