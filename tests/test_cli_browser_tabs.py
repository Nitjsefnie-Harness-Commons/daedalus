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


def _ext(cmd_id, cmd_type, fields, timeout=10):
    return {'via': 'ext_cmd', 'id': cmd_id, 'type': cmd_type,
            'fields': fields, 'timeout': timeout}


# ── do_open_tab ──────────────────────────────────────────────────────

def test_do_open_tab_sends_the_url_and_neither_option(tmp):
    """The bare arm: `url` alone, and a ten-second wait."""
    del tmp
    plan = [_ext('_open_tab', 'open-tab', {'url': 'https://example.com/'})]
    recorded, out = run_cli(
        ['open-tab', 'https://example.com/'],
        [{'tabId': 42, 'url': 'https://example.com/'}],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.calls == [('_open_tab', 'open-tab',
                               {'url': 'https://example.com/'})], \
        recorded.calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert _rendered(out) == f'Opened tab 42 {OUT} https://example.com/\n', \
        repr(out)


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
        plan=[_ext('_open_tab', 'open-tab', fields)], token=TOK)

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
        plan=[_ext('_open_tab', 'open-tab', fields)], token=TOK)

    assert recorded.calls == [('_open_tab', 'open-tab', fields)], \
        recorded.calls
    assert _rendered(out) == f'Opened tab 42 {OUT} https://example.com/\n', \
        repr(out)


def test_do_open_tab_prints_a_placeholder_for_a_missing_tab_id(tmp):
    """A result with no `tabId` reads `?` rather than `None`."""
    del tmp
    plan = [_ext('_open_tab', 'open-tab', {'url': 'https://example.com/'})]
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
    plan = [_ext('_open_tabs', 'open-tabs', fields, timeout=30)]
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
    """One opened and one refused: three lines, in that order.

    The count line is the last thing printed and names both halves, so a
    partial open is readable without counting the rows above it.
    """
    del tmp
    fields = {'urls': ['https://a.example.com/', 'https://b.example.com/']}
    plan = [_ext('_open_tabs', 'open-tabs', fields, timeout=30)]
    _recorded, out = run_cli(
        ['open-tabs', 'https://a.example.com/', 'https://b.example.com/'],
        [{'opened': [{'tabId': 42, 'url': 'https://a.example.com/'}],
          'errors': [{'url': 'https://b.example.com/',
                      'error': 'blocked by policy'}]}],
        module=commands_browser, plan=plan, token=TOK)

    assert _rendered(out) == (
        f'Opened tab 42 {OUT} https://a.example.com/\n'
        'FAILED https://b.example.com/: blocked by policy\n'
        '1 opened, 1 failed\n'), repr(out)


def test_do_open_tabs_prints_only_a_count_when_nothing_opened(tmp):
    del tmp
    plan = [_ext('_open_tabs', 'open-tabs',
                 {'urls': ['https://a.example.com/']}, timeout=30)]
    _recorded, out = run_cli(
        ['open-tabs', 'https://a.example.com/'], [{}],
        module=commands_browser, plan=plan, token=TOK)

    assert _rendered(out) == '0 opened, 0 failed\n', repr(out)


def test_do_open_tabs_sends_active_false_only_with_background(tmp):
    """`--background` is the field `active: False`; `pinned` stays off."""
    del tmp
    fields = {'urls': ['https://a.example.com/'], 'active': False}
    recorded, out = run_cli(
        ['open-tabs', 'https://a.example.com/', '--background'],
        [{'opened': [{'tabId': 42, 'url': 'https://a.example.com/'}]}],
        module=commands_browser,
        plan=[_ext('_open_tabs', 'open-tabs', fields, timeout=30)],
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
        plan=[_ext('_open_tabs', 'open-tabs', fields, timeout=30)],
        token=TOK)

    assert recorded.calls == [('_open_tabs', 'open-tabs', fields)], \
        recorded.calls
    assert _rendered(out) == (
        f'Opened tab 42 {OUT} https://a.example.com/\n'
        '1 opened, 0 failed\n'), repr(out)


def test_do_open_tabs_renders_a_refusal_carrying_no_url_or_error(tmp):
    """Two independent placeholders in one refusal line.

    The url is what an operator would retype, so its absence has to be
    visible rather than printed as None.
    """
    del tmp
    plan = [_ext('_open_tabs', 'open-tabs',
                 {'urls': ['https://a.example.com/']}, timeout=30)]
    _recorded, out = run_cli(
        ['open-tabs', 'https://a.example.com/'], [{'errors': [{}]}],
        module=commands_browser, plan=plan, token=TOK)

    assert _rendered(out) == 'FAILED ?: \n0 opened, 1 failed\n', repr(out)


# ── do_focus_tab ─────────────────────────────────────────────────────

def test_do_focus_tab_names_the_tab_and_the_window_it_focused(tmp):
    del tmp
    plan = [_ext('_focus', 'focus-tab', {'tabId': 0})]
    recorded, out = run_cli(
        ['focus-tab', '0'], [{'tabId': 0, 'windowId': 7}],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.calls == [('_focus', 'focus-tab', {'tabId': 0})], \
        recorded.calls
    assert out == 'Focused tab 0 window=7\n', repr(out)


def test_do_focus_tab_prints_placeholders_for_both_absent_fields(tmp):
    del tmp
    plan = [_ext('_focus', 'focus-tab', {'tabId': 0})]
    _recorded, out = run_cli(
        ['focus-tab', '0'], [{}],
        module=commands_browser, plan=plan, token=TOK)

    assert out == 'Focused tab ? window=?\n', repr(out)


# ── do_ext_navigate ──────────────────────────────────────────────────

def test_do_ext_navigate_sends_the_url_and_no_tab_id(tmp):
    """No `tabId`: the browser's active tab runs it."""
    del tmp
    plan = [_ext('_nav', 'navigate', {'url': 'https://example.com/'})]
    recorded, out = run_cli(
        ['ext-navigate', 'https://example.com/'],
        [{'tabId': 42}],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.calls == [('_nav', 'navigate',
                               {'url': 'https://example.com/'})], \
        recorded.calls
    assert _rendered(out) == (
        f'Navigated tab 42 {OUT} https://example.com/\n'), repr(out)


def test_do_ext_navigate_carries_the_chrome_tab_only_when_it_was_given(tmp):
    """`--chrome-tab 0` adds `tabId` — `is not None`, so zero is named.

    A truthiness test would drop tab 0 and navigate the active tab
    instead, which is the one tab an operator never means.
    """
    del tmp
    fields = {'url': 'https://example.com/', 'tabId': 0}
    recorded, out = run_cli(
        ['ext-navigate', 'https://example.com/', '--chrome-tab', '0'],
        [{'tabId': 0}],
        module=commands_browser, plan=[_ext('_nav', 'navigate', fields)],
        token=TOK)

    assert recorded.calls == [('_nav', 'navigate', fields)], \
        recorded.calls
    assert _rendered(out) == f'Navigated tab 0 {OUT} https://example.com/\n', \
        repr(out)


# ── do_ext_reload ────────────────────────────────────────────────────

def test_do_ext_reload_sends_no_fields_at_all_on_the_bare_arm(tmp):
    """Both fields are opt-in, so the bare body is an empty object.

    This is the assertion that makes `tabId` and `bypassCache` opt-in: a
    handler that always sent them would pass the two tests below.
    """
    del tmp
    recorded, out = run_cli(
        ['ext-reload'], [{'tabId': 42}],
        module=commands_browser, plan=[_ext('_reload', 'reload', {})],
        token=TOK)

    assert recorded.calls == [('_reload', 'reload', {})], recorded.calls
    assert out == 'Reloaded tab 42\n', repr(out)


def test_do_ext_reload_carries_the_chrome_tab_only_when_it_was_given(tmp):
    """`--chrome-tab` adds `tabId`; the rendered line is unchanged."""
    del tmp
    recorded, out = run_cli(
        ['ext-reload', '--chrome-tab', '0'], [{'tabId': 0}],
        module=commands_browser,
        plan=[_ext('_reload', 'reload', {'tabId': 0})], token=TOK)

    assert recorded.calls == [('_reload', 'reload', {'tabId': 0})], \
        recorded.calls
    assert out == 'Reloaded tab 0\n', repr(out)


def test_do_ext_reload_names_the_cache_bypass_in_its_output_too(tmp):
    """`--bypass-cache` changes the body AND the line an operator reads.

    The two are asserted together because a handler that sent the field
    without the suffix, or printed the suffix without sending the field,
    would each pass half of a split assertion.
    """
    del tmp
    fields = {'bypassCache': True}
    recorded, out = run_cli(
        ['ext-reload', '--bypass-cache'], [{'tabId': 42}],
        module=commands_browser, plan=[_ext('_reload', 'reload', fields)],
        token=TOK)

    assert recorded.calls == [('_reload', 'reload', fields)], \
        recorded.calls
    assert out == 'Reloaded tab 42 (bypass cache)\n', repr(out)


def test_do_ext_reload_carries_both_fields_when_both_were_given(tmp):
    del tmp
    fields = {'tabId': 7, 'bypassCache': True}
    recorded, out = run_cli(
        ['ext-reload', '--chrome-tab', '7', '--bypass-cache'],
        [{'tabId': 7}],
        module=commands_browser, plan=[_ext('_reload', 'reload', fields)],
        token=TOK)

    assert recorded.calls == [('_reload', 'reload', fields)], \
        recorded.calls
    assert out == 'Reloaded tab 7 (bypass cache)\n', repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clibrowtab_')


if __name__ == '__main__':
    raise SystemExit(main())
