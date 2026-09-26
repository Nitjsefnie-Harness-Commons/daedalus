#!/usr/bin/env python3
"""Every branch of the net-capture trio in daedalus_cli/commands_content.py.

All three go through `ext_cmd`, so the single recorded call each makes --
its id, its wire type, its fields and the timeout the handler chose --
is what is asserted, beside the lines the operator reads. The two dumps
and the three table printers are pinned as whole strings, because the
rows differ only in their spacing and a substring check would accept one
that is otherwise wrong.

`--chrome-tab 0` is the value that separates `is not None` from a
truthiness test, so the tab is pinned with the zero Chrome really can
number a tab rather than with a large number.

The url column is truncated to 120 characters, and both dumps carry the
whole buffer regardless, so each half of that pair is pinned separately.
No marker glyphs are folded here: every line these handlers print is
plain ASCII.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

run_cli = _cli_dispatch.run_cli

TOK = 'clitok'
SHORT = 'https://a.example.com/one'
LONG_URL = 'https://a.example.com/' + 'p' * 120
# The 120 characters the handler keeps, spelled out rather than sliced, so
# the bound this column has is a literal in this file and not a recomputed
# copy of the handler's own expression.
KEPT_URL = 'https://a.example.com/' + 'p' * 98


def _ext(cmd_id, cmd_type, fields, timeout):
    return {'via': 'ext_cmd', 'id': cmd_id, 'type': cmd_type,
            'fields': fields, 'timeout': timeout}


def _first():
    return {'status': 200, 'method': 'GET', 'type': 'XHR', 'url': SHORT}


def _second():
    return {'status': 302, 'method': 'POST', 'type': 'Document',
            'url': 'https://a.example.com/two'}


# ── do_net_capture ───────────────────────────────────────────────────

def test_do_net_capture_sends_the_buffer_limit_the_parser_chose(tmp):
    """The bare arm: the default limit, and the fifteen this waits for.

    `--max` defaults to 1000 rather than to absent, so the field is on
    the wire even when the operator never mentioned it. That is the
    arm a handler that always sent `maxRequests` and one that sent the
    default both satisfy -- it is here to pin the value, not the
    presence, and `--max 5` below pins the value the operator chose.
    """
    del tmp
    recorded, out = run_cli(
        ['net-capture'], [{'tabId': 4}],
        plan=[_ext('_net_cap', 'net-capture', {'maxRequests': 1000}, 15)],
        token=TOK)

    assert recorded.calls == [('_net_cap', 'net-capture',
                               {'maxRequests': 1000})], recorded.calls
    assert recorded.timeouts == [15], recorded.timeouts
    assert out == 'Capturing network on tab 4\n', repr(out)


def test_do_net_capture_sends_the_buffer_limit_it_was_given(tmp):
    """`--max 5` travels as `maxRequests: 5`."""
    del tmp
    recorded, out = run_cli(
        ['net-capture', '--max', '5'], [{'tabId': 4}],
        plan=[_ext('_net_cap', 'net-capture', {'maxRequests': 5}, 15)],
        token=TOK)

    assert recorded.calls == [('_net_cap', 'net-capture',
                               {'maxRequests': 5})], recorded.calls
    assert out == 'Capturing network on tab 4\n', repr(out)


def test_do_net_capture_names_the_tab_chrome_numbered_zero(tmp):
    """`--chrome-tab 0` adds `tabId: 0`; the guard is a presence test."""
    del tmp
    recorded, out = run_cli(
        ['net-capture', '--chrome-tab', '0'], [{'tabId': 0}],
        plan=[_ext('_net_cap', 'net-capture',
                   {'maxRequests': 1000, 'tabId': 0}, 15)],
        token=TOK)

    assert recorded.calls == [('_net_cap', 'net-capture',
                               {'maxRequests': 1000, 'tabId': 0})], \
        recorded.calls
    assert out == 'Capturing network on tab 0\n', repr(out)


def test_do_net_capture_says_so_when_the_tab_is_already_capturing(tmp):
    """The `already` arm names the tab and what the buffer already holds.

    Two requests buffered rather than one, so the count this arm
    reports is distinguishable from the zero a fresh capture starts
    from and from a placeholder.
    """
    del tmp
    recorded, out = run_cli(
        ['net-capture'], [{'already': True, 'tabId': 6, 'buffered': 12}],
        plan=[_ext('_net_cap', 'net-capture', {'maxRequests': 1000}, 15)],
        token=TOK)

    assert recorded.calls == [('_net_cap', 'net-capture',
                               {'maxRequests': 1000})], recorded.calls
    assert out == 'Already capturing on tab 6 (12 requests buffered)\n', \
        repr(out)


def test_do_net_capture_reports_zero_buffered_when_the_extension_says_nothing(
        tmp):
    """A capture already running with an unstated buffer reads zero."""
    del tmp
    _recorded, out = run_cli(
        ['net-capture'], [{'already': True, 'tabId': 6}],
        plan=[_ext('_net_cap', 'net-capture', {'maxRequests': 1000}, 15)],
        token=TOK)

    assert out == 'Already capturing on tab 6 (0 requests buffered)\n', \
        repr(out)


def test_do_net_capture_reads_a_tab_the_already_arm_left_out(tmp):
    """The `already` line has its own read, and it needed its own test.

    It is a separate f-string from the default arm's, so pinning one
    default says nothing about the other, and both of the tests beside
    this one carry a `tabId`.
    """
    del tmp
    _recorded, out = run_cli(
        ['net-capture'], [{'already': True, 'buffered': 12}],
        plan=[_ext('_net_cap', 'net-capture', {'maxRequests': 1000}, 15)],
        token=TOK)

    assert out == 'Already capturing on tab ? (12 requests buffered)\n', \
        repr(out)


def test_do_net_capture_names_a_tab_the_extension_left_out(tmp):
    """The default arm reads `?` for a result with no tab at all.

    The two arms differ only in the `already` key, so the shared
    `get('tabId', '?')` default is what this pins: the already-arm test
    above always carries a tab. `?` rather than the word Python prints for
    an absent lookup, because that is the placeholder every other handler
    in the CLI already reads an absent id as, and an operator reading the
    word `None` where a tab id belongs learns nothing.
    """
    del tmp
    _recorded, out = run_cli(
        ['net-capture'], [{}],
        plan=[_ext('_net_cap', 'net-capture', {'maxRequests': 1000}, 15)],
        token=TOK)

    assert out == 'Capturing network on tab ?\n', repr(out)


# ── do_net_capture_stop ──────────────────────────────────────────────

def test_do_net_capture_stop_prints_a_row_per_request_in_the_order_given(tmp):
    """Two rows, the count that says there were two, and the thirty this
    handler waits for.

    The table does not sort, so the order the extension returned is what
    prints. Two rows rather than one: a one-request fixture would satisfy
    a handler that printed only the last request it saw.
    """
    del tmp
    recorded, out = run_cli(
        ['net-capture-stop'],
        [{'stopped': True, 'tabId': 4, 'requests': [_first(), _second()]}],
        plan=[_ext('_net_stop', 'net-capture-stop', {}, 30)], token=TOK)

    assert recorded.calls == [('_net_stop', 'net-capture-stop', {})], \
        recorded.calls
    assert recorded.timeouts == [30], recorded.timeouts
    assert out == (
        'Captured 2 requests from tab 4\n'
        f'  200 GET XHR          {SHORT}\n'
        '  302 POST Document     '
        'https://a.example.com/two\n'), repr(out)


def test_do_net_capture_stop_prints_only_the_count_when_none_were_captured(
        tmp):
    """A capture that stopped with an empty buffer prints no rows."""
    del tmp
    _recorded, out = run_cli(
        ['net-capture-stop'],
        [{'stopped': True, 'tabId': 4}],
        plan=[_ext('_net_stop', 'net-capture-stop', {}, 30)], token=TOK)

    assert out == 'Captured 0 requests from tab 4\n', repr(out)


def test_do_net_capture_stop_reads_a_tab_the_extension_left_out(tmp):
    """An answer with no `tabId` reads `?`, the placeholder, not `None`.

    Every sibling of this arm supplies a `tabId`, which is why the read
    stayed dark: a handler that had dropped the field entirely would
    have passed all of them. The two defaults are separate lines of the
    handler, so pinning the count here pins nothing about the tab.
    """
    del tmp
    _recorded, out = run_cli(
        ['net-capture-stop'], [{'stopped': True}],
        plan=[_ext('_net_stop', 'net-capture-stop', {}, 30)], token=TOK)

    assert out == 'Captured 0 requests from tab ?\n', repr(out)


def test_do_net_capture_stop_says_so_when_the_tab_was_not_capturing(tmp):
    """The not-stopped arm gives the reason and returns without a table.

    The whole-string comparison is what pins the early return: a handler
    that printed the reason and then went on to summarise an absent
    buffer would differ in the second line, not the first.
    """
    del tmp
    recorded, out = run_cli(
        ['net-capture-stop'],
        [{'stopped': False, 'reason': 'no capture on this tab'}],
        plan=[_ext('_net_stop', 'net-capture-stop', {}, 30)], token=TOK)

    assert recorded.calls == [('_net_stop', 'net-capture-stop', {})], \
        recorded.calls
    assert out == 'Not capturing: no capture on this tab\n', repr(out)


def test_do_net_capture_stop_prints_a_placeholder_for_a_missing_reason(tmp):
    """A refusal carrying no reason reads `?`, not `None`."""
    del tmp
    _recorded, out = run_cli(
        ['net-capture-stop'], [{}],
        plan=[_ext('_net_stop', 'net-capture-stop', {}, 30)], token=TOK)

    assert out == 'Not capturing: ?\n', repr(out)


def test_do_net_capture_stop_carries_the_tab_and_bodies_when_it_was_given(
        tmp):
    """`--chrome-tab 0 --bodies` adds both, and 0 is the tab that counts."""
    del tmp
    recorded, out = run_cli(
        ['net-capture-stop', '--chrome-tab', '0', '--bodies'],
        [{'stopped': True, 'tabId': 0}],
        plan=[_ext('_net_stop', 'net-capture-stop',
                   {'tabId': 0, 'bodies': True}, 30)],
        token=TOK)

    assert recorded.calls == [('_net_stop', 'net-capture-stop',
                               {'tabId': 0, 'bodies': True})], \
        recorded.calls
    assert out == 'Captured 0 requests from tab 0\n', repr(out)


def test_do_net_capture_stop_cuts_a_long_url_at_a_hundred_and_twenty(tmp):
    """The url column stops at 120 characters; the buffer keeps the rest.

    The url here is 142 characters, so the tail is dropped from the
    table. A handler that printed the whole url would differ in a column
    an operator reads to identify a request, and a handler that truncated
    at 100 or 150 would differ in the last character shown.
    """
    del tmp
    result = {'stopped': True, 'tabId': 4,
              'requests': [{'status': 200, 'method': 'GET', 'type': 'Document',
                            'url': LONG_URL}]}
    _recorded, out = run_cli(
        ['net-capture-stop'], [result],
        plan=[_ext('_net_stop', 'net-capture-stop', {}, 30)], token=TOK)

    assert out == (
        'Captured 1 requests from tab 4\n'
        f'  200 GET Document     {KEPT_URL}\n'), repr(out)


def test_do_net_capture_stop_renders_a_row_carrying_nothing_but_a_url(tmp):
    """A request with no status, method or type reads `???`, `?` and blanks.

    The trailing blanks are the handler's own empty strings, and the
    twelve-wide type cell is what pads them: the row ends in spaces, not
    in a value, and a handler that dropped the padding would render the
    url one column to the left.
    """
    del tmp
    result = {'stopped': True, 'tabId': 4,
              'requests': [{'url': SHORT}]}
    _recorded, out = run_cli(
        ['net-capture-stop'], [result],
        plan=[_ext('_net_stop', 'net-capture-stop', {}, 30)], token=TOK)

    assert out == (
        'Captured 1 requests from tab 4\n'
        f'  ??? ?              {SHORT}\n'), repr(out)


def test_do_net_capture_stop_prints_the_whole_result_as_json_when_raw(tmp):
    """`--raw` returns after the JSON: no count line, no table.

    The dump is the whole result, so the untruncated url is here even
    though the table would have cut it -- the two arms read the same
    buffer and neither of them is the other's output.
    """
    del tmp
    result = {'stopped': True, 'tabId': 4,
              'requests': [{'url': LONG_URL, 'status': 200}]}
    _recorded, out = run_cli(
        ['net-capture-stop', '--raw'], [result],
        plan=[_ext('_net_stop', 'net-capture-stop', {}, 30)], token=TOK)

    assert out == (
        '{\n'
        '  "stopped": true,\n'
        '  "tabId": 4,\n'
        '  "requests": [\n'
        '    {\n'
        f'      "url": "{LONG_URL}",\n'
        '      "status": 200\n'
        '    }\n'
        '  ]\n'
        '}\n'), repr(out)


# ── do_net_capture_get ──────────────────────────────────────────────

def test_do_net_capture_get_prints_a_row_per_request_and_no_result_field(
        tmp):
    """The bare arm: no `tabId`, no `filter`, no `bodies`.

    Two rows again, so the per-request claim is a control rather than a
    single-element fixture that any single-row handler would satisfy.
    """
    del tmp
    recorded, out = run_cli(
        ['net-capture-get'],
        [{'tabId': 4, 'requests': [_first(), _second()]}],
        plan=[_ext('_net_get', 'net-capture-get', {}, 30)], token=TOK)

    assert recorded.calls == [('_net_get', 'net-capture-get', {})], \
        recorded.calls
    assert recorded.timeouts == [30], recorded.timeouts
    assert out == (
        '2 requests on tab 4\n'
        f'  200 GET XHR          {SHORT}\n'
        '  302 POST Document     '
        'https://a.example.com/two\n'), repr(out)


def test_do_net_capture_get_prints_only_the_count_when_the_buffer_is_empty(
        tmp):
    """An empty buffer is a zero and the tab, and no rows."""
    del tmp
    _recorded, out = run_cli(
        ['net-capture-get'], [{'tabId': 4}],
        plan=[_ext('_net_get', 'net-capture-get', {}, 30)], token=TOK)

    assert out == '0 requests on tab 4\n', repr(out)


def test_do_net_capture_get_reads_a_tab_the_extension_left_out(tmp):
    """The `?` arm of the listing, for an answer that names no tab.

    Both siblings of this arm supply a `tabId`, so an undefaulted read
    reached the wire in every test of this handler and nothing noticed.
    """
    del tmp
    _recorded, out = run_cli(
        ['net-capture-get'], [{}],
        plan=[_ext('_net_get', 'net-capture-get', {}, 30)], token=TOK)

    assert out == '0 requests on tab ?\n', repr(out)


def test_do_net_capture_get_carries_every_option_it_was_given(tmp):
    """The three options, all present, with the tab pinned at zero."""
    del tmp
    recorded, out = run_cli(
        ['net-capture-get', '--chrome-tab', '0', '--filter', 'seg-',
         '--bodies'],
        [{'tabId': 0, 'requests': []}],
        plan=[_ext('_net_get', 'net-capture-get',
                   {'tabId': 0, 'filter': 'seg-', 'bodies': True}, 30)],
        token=TOK)

    assert recorded.calls == [('_net_get', 'net-capture-get',
                               {'tabId': 0, 'filter': 'seg-',
                                'bodies': True})], recorded.calls
    assert out == '0 requests on tab 0\n', repr(out)


def test_do_net_capture_get_cuts_a_long_url_at_a_hundred_and_twenty(tmp):
    """The same 120-character bound the stop table applies.

    The two tables are separate print statements in the module, so the
    bound is pinned on each: a handler that truncated one and not the
    other would leave every other test in this file green.
    """
    del tmp
    result = {'tabId': 4,
              'requests': [{'status': 500, 'method': 'HEAD',
                            'type': 'Other', 'url': LONG_URL}]}
    _recorded, out = run_cli(
        ['net-capture-get'], [result],
        plan=[_ext('_net_get', 'net-capture-get', {}, 30)], token=TOK)

    assert out == (
        '1 requests on tab 4\n'
        f'  500 HEAD Other        {KEPT_URL}\n'), repr(out)


def test_do_net_capture_get_renders_a_row_carrying_nothing_but_a_url(tmp):
    """A request with no status, method or type reads `???`, `?` and blanks."""
    del tmp
    _recorded, out = run_cli(
        ['net-capture-get'], [{'tabId': 4, 'requests': [{'url': SHORT}]}],
        plan=[_ext('_net_get', 'net-capture-get', {}, 30)], token=TOK)

    assert out == (
        '1 requests on tab 4\n'
        f'  ??? ?              {SHORT}\n'), repr(out)


def test_do_net_capture_get_prints_the_whole_result_as_json_when_raw(tmp):
    """`--raw` returns after the JSON: no count line, no table.

    The dump is the result as the extension sent it, with the keys in
    the order it sent them, so a handler that re-serialised a reordered
    copy of the buffer would differ here and nowhere else.
    """
    del tmp
    result = {'requests': [{'url': SHORT, 'status': 200, 'type': 'XHR'}],
              'tabId': 4}
    _recorded, out = run_cli(
        ['net-capture-get', '--raw'], [result],
        plan=[_ext('_net_get', 'net-capture-get', {}, 30)], token=TOK)

    assert out == (
        '{\n'
        '  "requests": [\n'
        '    {\n'
        f'      "url": "{SHORT}",\n'
        '      "status": 200,\n'
        '      "type": "XHR"\n'
        '    }\n'
        '  ],\n'
        '  "tabId": 4\n'
        '}\n'), repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clicontcap_')


if __name__ == '__main__':
    raise SystemExit(main())
