#!/usr/bin/env python3
"""Every branch of the five tab handlers in commands_browser.py.

`do_open_tab`, `do_open_tabs`, `do_focus_tab`, `do_ext_navigate` and
`do_ext_reload` all go through `ext_cmd`, so each test asserts the single
recorded call — command id, wire type, the fields exactly as the extension
receives them, and the timeout the handler chose — alongside the exact
output an operator reads, compared as one string.

A field a handler sends only under a flag is pinned BOTH ways. The bare
arm's body is the one that says the flag is opt-in: a handler that always
sent `active` or `pinned` would satisfy every test here that asks for
them, and only the bare arm would notice.

The marker glyphs are SPELLED OUT rather than read from
`daedalus_cli.output`, for the reason the sibling suites give: an expected
value taken from the module under test pins nothing about it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
from _cli_handler_wire import _ext_cmd  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_browser  # noqa: E402

run_cli = _cli_dispatch.run_cli

OUT = '→'
TOK = 'clitok'


def _rendered(out):
    """`out` with a fallback marker spelling folded onto the pinned glyph.

    Only the two spellings `output._output_markers` documents are folded,
    so a third one — or a changed fancy glyph — reaches the assertions
    untouched and every whole-string comparison here fails.
    """
    return out.replace('->', OUT)


# ── do_open_tab ──────────────────────────────────────────────────────

def test_do_open_tab_sends_active_false_only_with_background(tmp):
    """`--background` is the field `active: False` — not `background`.

    The wire field is the negation, so a handler that sent `background`
    would open a foreground tab and still print the line an operator
    believes they asked for. `pinned` is absent on this arm.
    """
    del tmp
    fields = {'url': 'https://example.com/', 'active': False}
    recorded, out = run_cli(
        ['open-tab', 'https://example.com/', '--background'],
        [{'tabId': 42, 'url': 'https://example.com/'}],
        module=commands_browser,
        plan=[_ext_cmd('_open_tab', 'open-tab', fields)], token=TOK)

    assert recorded.calls == [('_open_tab', 'open-tab', fields)], \
        recorded.calls
    assert _rendered(out) == f'Opened tab 42 {OUT} https://example.com/\n', \
        repr(out)


def test_do_open_tab_sends_pinned_only_with_the_flag(tmp):
    """`--pinned` adds `pinned` and nothing else."""
    del tmp
    fields = {'url': 'https://example.com/', 'pinned': True}
    recorded, out = run_cli(
        ['open-tab', 'https://example.com/', '--pinned'],
        [{'tabId': 42, 'url': 'https://example.com/'}],
        module=commands_browser,
        plan=[_ext_cmd('_open_tab', 'open-tab', fields)], token=TOK)

    assert recorded.calls == [('_open_tab', 'open-tab', fields)], \
        recorded.calls
    assert _rendered(out) == f'Opened tab 42 {OUT} https://example.com/\n', \
        repr(out)


def test_do_open_tab_prints_a_placeholder_for_a_missing_tab_id(tmp):
    """A result with no `tabId` reads `?` rather than `None`."""
    del tmp
    plan = [_ext_cmd('_open_tab', 'open-tab', {'url': 'https://example.com/'})]
    _recorded, out = run_cli(
        ['open-tab', 'https://example.com/'], [{}],
        module=commands_browser, plan=plan, token=TOK)

    assert _rendered(out) == f'Opened tab ? {OUT} https://example.com/\n', \
        repr(out)


# ── do_open_tabs ─────────────────────────────────────────────────────

def test_do_open_tabs_sends_every_url_in_order_and_waits_thirty(tmp):
    """The urls travel as a list in the order they were typed.

    Thirty seconds, not the ten the other four tab handlers use: opening
    many tabs is the one round trip that legitimately outlives it.
    """
    del tmp
    fields = {'urls': ['https://a.example.com/', 'https://b.example.com/']}
    plan = [_ext_cmd('_open_tabs', 'open-tabs', fields, timeout=30)]
    recorded, out = run_cli(
        ['open-tabs', 'https://a.example.com/', 'https://b.example.com/'],
        [{'opened': [{'tabId': 42, 'url': 'https://a.example.com/'},
                     {'tabId': 43, 'url': 'https://b.example.com/'}],
          'errors': []}],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.calls == [('_open_tabs', 'open-tabs', fields)], \
        recorded.calls
    assert recorded.timeouts == [30], recorded.timeouts
    assert _rendered(out) == (
        f'Opened tab 42 {OUT} https://a.example.com/\n'
        f'Opened tab 43 {OUT} https://b.example.com/\n'
        '2 opened, 0 failed\n'), repr(out)


def test_do_open_tabs_reports_a_partial_failure_as_both_kinds_of_line(tmp):
    del tmp
    cases = (
        (['https://a.example.com/', 'https://b.example.com/'],
         {'opened': [{'tabId': 42, 'url': 'https://a.example.com/'}],
          'errors': [{'url': 'https://b.example.com/',
                      'error': 'blocked by policy'}]},
         f'Opened tab 42 {OUT} https://a.example.com/\n'
         'FAILED https://b.example.com/: blocked by policy\n'
         '1 opened, 1 failed\n'),
        (['https://a.example.com/'], {}, '0 opened, 0 failed\n'),
        (['https://a.example.com/'], {'errors': [{}]},
         'FAILED ?: \n0 opened, 1 failed\n'),
    )
    for urls, result, expected in cases:
        plan = [_ext_cmd('_open_tabs', 'open-tabs',
                         {'urls': urls}, timeout=30)]
        _recorded, out = run_cli(
            ['open-tabs', *urls], [result],
            module=commands_browser, plan=plan, token=TOK)
        assert _rendered(out) == expected, repr(out)


def test_do_open_tabs_sends_active_false_only_with_background(tmp):
    """`--background` is the field `active: False`; `pinned` stays off."""
    del tmp
    fields = {'urls': ['https://a.example.com/'], 'active': False}
    recorded, out = run_cli(
        ['open-tabs', 'https://a.example.com/', '--background'],
        [{'opened': [{'tabId': 42, 'url': 'https://a.example.com/'}]}],
        module=commands_browser,
        plan=[_ext_cmd('_open_tabs', 'open-tabs', fields, timeout=30)],
        token=TOK)

    assert recorded.calls == [('_open_tabs', 'open-tabs', fields)], \
        recorded.calls
    assert _rendered(out) == (
        f'Opened tab 42 {OUT} https://a.example.com/\n'
        '1 opened, 0 failed\n'), repr(out)


def test_do_open_tabs_sends_pinned_only_with_the_flag(tmp):
    """`--pinned` adds `pinned` and leaves `active` off."""
    del tmp
    fields = {'urls': ['https://a.example.com/'], 'pinned': True}
    recorded, out = run_cli(
        ['open-tabs', 'https://a.example.com/', '--pinned'],
        [{'opened': [{'tabId': 42, 'url': 'https://a.example.com/'}]}],
        module=commands_browser,
        plan=[_ext_cmd('_open_tabs', 'open-tabs', fields, timeout=30)],
        token=TOK)

    assert recorded.calls == [('_open_tabs', 'open-tabs', fields)], \
        recorded.calls
    assert _rendered(out) == (
        f'Opened tab 42 {OUT} https://a.example.com/\n'
        '1 opened, 0 failed\n'), repr(out)


# ── do_focus_tab ─────────────────────────────────────────────────────

def test_do_focus_tab_names_the_tab_and_the_window_it_focused(tmp):
    del tmp
    plan = [_ext_cmd('_focus', 'focus-tab', {'tabId': 0})]
    recorded, out = run_cli(
        ['focus-tab', '0'], [{'tabId': 0, 'windowId': 7}],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.calls == [('_focus', 'focus-tab', {'tabId': 0})], \
        recorded.calls
    assert out == 'Focused tab 0 window=7\n', repr(out)


def test_do_focus_tab_prints_placeholders_for_both_absent_fields(tmp):
    del tmp
    plan = [_ext_cmd('_focus', 'focus-tab', {'tabId': 0})]
    _recorded, out = run_cli(
        ['focus-tab', '0'], [{}],
        module=commands_browser, plan=plan, token=TOK)

    assert out == 'Focused tab ? window=?\n', repr(out)


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
