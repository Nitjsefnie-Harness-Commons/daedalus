#!/usr/bin/env python3
"""Every branch of do_cdp, do_close_tab, do_fetch_timings and
do_ext_self_reload in commands_browser.py.

`do_cdp` and `do_close_tab` build their own `/command` body, so each of
their tests asserts the exact body that reached the wire. The other two go
through `ext_cmd`, so their single recorded call — id, wire type, fields
and the timeout the handler chose — is what is asserted.

`do_fetch_timings` is driven here rather than through the private
`run_cli` copy in tests/test_fetch_timings_count.py: that file rebinds
`commands_browser.ext_cmd` directly and records nothing but a tuple, so a
handler that sent an undeclared field, or waited for the wrong delivery,
would pass it. The shared harness plans the request instead.

A note on markers: the glyphs below are SPELLED OUT rather than read from
`daedalus_cli.output`, because an expected value taken from the module
under test pins nothing about it. `_rendered` folds the ASCII fallback a
console that cannot encode a glyph gets onto the pinned spelling.
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

IN = '←'
TOK = 'clitok'


def _rendered(out):
    """`out` with a fallback marker spelling folded onto the pinned glyph.

    Only the two spellings `output._output_markers` documents are folded,
    so a third one — or a changed fancy glyph — reaches the assertions
    untouched and every whole-string comparison here fails.
    """
    return out.replace('<-', IN)


def _put(body):
    return {'via': 'api', 'method': 'PUT', 'path': '/command', 'body': body}


def _wait(cmd_id, delivery, timeout, interval=0.5):
    return {'via': 'wait_for_result', 'id': cmd_id, 'tab': 'extension',
            'delivery': delivery, 'timeout': timeout, 'interval': interval}


def _ext(cmd_id, cmd_type, fields, timeout=10):
    return {'via': 'ext_cmd', 'id': cmd_id, 'type': cmd_type,
            'fields': fields, 'timeout': timeout}


def _envelope(**over):
    base = {'id': 'x', 'result': {}, 'error': None, 'ts': 1}
    return dict(base, **over)


# ── do_cdp ───────────────────────────────────────────────────────────

def test_do_cdp_sends_empty_params_and_neither_option_when_none_given(tmp):
    """The bare arm: `params` is `{}` — a real empty object, not absent.

    `tabId` and `keep_session` are absent rather than null, so this test is
    the only thing that pins the two flags as opt-in: a handler that always
    sent them would still satisfy the sibling test that asks for both.
    """
    del tmp
    body = {'id': '_cdp', 'type': 'cdp', 'method': 'Page.captureScreenshot',
            'params': {}, 'token': TOK, 'tab': 'extension'}
    plan = [_put(body), _wait('_cdp', 'd1', 30)]
    recorded, out = run_cli(
        ['cdp', 'Page.captureScreenshot'],
        [{'did': 'd1'},
         _envelope(id='_cdp', result={'format': 'png'})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [30], recorded.timeouts
    assert _rendered(out) == f'{IN} _cdp\n{{\n  "format": "png"\n}}\n', \
        repr(out)


def test_do_cdp_sends_the_params_it_was_given(tmp):
    """`-p` travels as the value the parser decoded, object or not."""
    del tmp
    body = {'id': '_cdp', 'type': 'cdp', 'method': 'Page.navigate',
            'params': {'url': 'https://example.com/'}, 'token': TOK,
            'tab': 'extension'}
    plan = [_put(body), _wait('_cdp', 'd1', 30)]
    recorded, _out = run_cli(
        ['cdp', 'Page.navigate', '-p', '{"url": "https://example.com/"}'],
        [{'did': 'd1'}, _envelope(id='_cdp', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


def test_do_cdp_carries_the_chrome_tab_only_when_it_was_given(tmp):
    """`--chrome-tab` adds `tabId`, as the integer Chrome numbers tabs with.

    `is not None` rather than truthiness is what lets a tab numbered 0 be
    named, so the arm is pinned with the zero and not with a big number.
    """
    del tmp
    body = {'id': '_cdp', 'type': 'cdp', 'method': 'Page.enable', 'params': {},
            'token': TOK, 'tab': 'extension', 'tabId': 0}
    plan = [_put(body), _wait('_cdp', 'd1', 30)]
    recorded, _out = run_cli(
        ['cdp', 'Page.enable', '--chrome-tab', '0'],
        [{'did': 'd1'}, _envelope(id='_cdp', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


def test_do_cdp_carries_the_keep_session_flag_only_when_it_was_given(tmp):
    """`--keep-session` adds `keep_session` and nothing else."""
    del tmp
    body = {'id': '_cdp', 'type': 'cdp', 'method': 'Profiler.enable',
            'params': {}, 'token': TOK, 'tab': 'extension',
            'keep_session': True}
    plan = [_put(body), _wait('_cdp', 'd1', 30)]
    recorded, _out = run_cli(
        ['cdp', 'Profiler.enable', '--keep-session'],
        [{'did': 'd1'}, _envelope(id='_cdp', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls


def test_do_cdp_renders_the_header_cells_the_result_carries(tmp):
    """A result with a tab, a channel and a duration prints all three cells."""
    del tmp
    body = {'id': '_cdp', 'type': 'cdp', 'method': 'Runtime.evaluate',
            'params': {}, 'token': TOK, 'tab': 'extension'}
    plan = [_put(body), _wait('_cdp', 'd1', 30)]
    _recorded, out = run_cli(
        ['cdp', 'Runtime.evaluate'],
        [{'did': 'd1'},
         _envelope(id='_cdp', result='captured', tabId='tab9',
                   world='page:main', exec_ms=5)],
        module=commands_browser, plan=plan, token=TOK)

    assert _rendered(out) == (
        f'{IN} _cdp  tab=tab9  @channel=page:main  5ms\ncaptured\n'), repr(out)


def test_do_cdp_prints_the_whole_envelope_as_json_when_raw(tmp):
    """`--raw` hands the machine the envelope and prints no header line."""
    del tmp
    body = {'id': '_cdp', 'type': 'cdp', 'method': 'Page.enable', 'params': {},
            'token': TOK, 'tab': 'extension'}
    plan = [_put(body), _wait('_cdp', 'd1', 30)]
    _recorded, out = run_cli(
        ['cdp', 'Page.enable', '--raw'],
        [{'did': 'd1'},
         _envelope(id='_cdp', result={'format': 'png'})],
        module=commands_browser, plan=plan, token=TOK)

    assert out == (
        '{\n'
        '  "id": "_cdp",\n'
        '  "result": {\n'
        '    "format": "png"\n'
        '  },\n'
        '  "error": null,\n'
        '  "ts": 1\n'
        '}\n'), repr(out)


def test_do_cdp_exits_when_no_result_arrives(tmp):
    """The timeout exit names the fixed thirty this handler waits for."""
    del tmp
    body = {'id': '_cdp', 'type': 'cdp', 'method': 'Page.enable', 'params': {},
            'token': TOK, 'tab': 'extension'}
    plan = [_put(body), _wait('_cdp', 'd1', 30)]
    code, out = run_cli_exit(['cdp', 'Page.enable'], [{'did': 'd1'}, None],
                             module=commands_browser, plan=plan, token=TOK)

    assert code == 'Timeout (30s)', code
    assert out == '', repr(out)


# ── do_close_tab ─────────────────────────────────────────────────────

def test_do_close_tab_sends_one_id_as_tab_id(tmp):
    """A single tab travels as `tabId` — a scalar, not a one-item list.

    The two spellings are not interchangeable on the extension's side, so
    the arity split is pinned by the request rather than by the output,
    which is identical for both.
    """
    del tmp
    body = {'id': '_close_tab', 'type': 'close-tab', 'token': TOK,
            'tab': 'extension', 'tabId': 101}
    plan = [_put(body), _wait('_close_tab', 'd2', 10)]
    recorded, out = run_cli(
        ['close-tab', '101'],
        [{'did': 'd2'},
         _envelope(id='_close_tab', result={'closed': [101]})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == 'Closed 1 tab(s): [101]\n', repr(out)


def test_do_close_tab_sends_several_ids_as_a_list(tmp):
    """Two or more travel as `tabIds`, and `tabId` is absent."""
    del tmp
    body = {'id': '_close_tab', 'type': 'close-tab', 'token': TOK,
            'tab': 'extension', 'tabIds': [101, 102, 103]}
    plan = [_put(body), _wait('_close_tab', 'd2', 10)]
    recorded, out = run_cli(
        ['close-tab', '101', '102', '103'],
        [{'did': 'd2'},
         _envelope(id='_close_tab', result={'closed': [101, 102, 103]})],
        module=commands_browser, plan=plan, token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == 'Closed 3 tab(s): [101, 102, 103]\n', repr(out)


def test_do_close_tab_reports_each_tab_the_extension_could_not_close(tmp):
    """A refusal is one line per tab, id then error, in the order given."""
    del tmp
    body = {'id': '_close_tab', 'type': 'close-tab', 'token': TOK,
            'tab': 'extension', 'tabIds': [101, 102]}
    plan = [_put(body), _wait('_close_tab', 'd2', 10)]
    answer = {'errors': [{'id': 102, 'error': 'no such tab'}]}
    _recorded, out = run_cli(
        ['close-tab', '101', '102'],
        [{'did': 'd2'}, _envelope(id='_close_tab', result=answer)],
        module=commands_browser, plan=plan, token=TOK)

    assert out == 'Failed tab 102: no such tab\n', repr(out)


def test_do_close_tab_reports_a_partial_close_as_both_lines(tmp):
    """Some closed and some refused: the count line and the failure lines.

    The order is the closed count first and then each refusal, which is
    what makes a partial close readable rather than a bare number.
    """
    del tmp
    body = {'id': '_close_tab', 'type': 'close-tab', 'token': TOK,
            'tab': 'extension', 'tabIds': [101, 102]}
    plan = [_put(body), _wait('_close_tab', 'd2', 10)]
    answer = {'closed': [101], 'errors': [{'id': 102, 'error': 'busy'}]}
    _recorded, out = run_cli(
        ['close-tab', '101', '102'],
        [{'did': 'd2'}, _envelope(id='_close_tab', result=answer)],
        module=commands_browser, plan=plan, token=TOK)

    assert out == (
        'Closed 1 tab(s): [101]\n'
        'Failed tab 102: busy\n'), repr(out)


def test_do_close_tab_says_so_when_no_tab_was_affected(tmp):
    """An empty result is a sentence, not silence.

    The arm is reachable whenever the extension matched nothing, and a
    handler that printed neither list and said nothing would leave an
    operator unable to tell it from a crash.
    """
    del tmp
    body = {'id': '_close_tab', 'type': 'close-tab', 'token': TOK,
            'tab': 'extension', 'tabId': 101}
    plan = [_put(body), _wait('_close_tab', 'd2', 10)]
    _recorded, out = run_cli(
        ['close-tab', '101'],
        [{'did': 'd2'}, _envelope(id='_close_tab', result={})],
        module=commands_browser, plan=plan, token=TOK)

    assert out == 'No tabs affected\n', repr(out)


def test_do_close_tab_renders_a_failure_carrying_neither_field(tmp):
    """A refusal with no id and no error reads `?` and an empty reason.

    Two independent defaults in one row: the id is what an operator needs
    to retry, so its absence must be visible rather than printed as None.
    """
    del tmp
    body = {'id': '_close_tab', 'type': 'close-tab', 'token': TOK,
            'tab': 'extension', 'tabId': 101}
    plan = [_put(body), _wait('_close_tab', 'd2', 10)]
    _recorded, out = run_cli(
        ['close-tab', '101'],
        [{'did': 'd2'},
         _envelope(id='_close_tab', result={'errors': [{}]})],
        module=commands_browser, plan=plan, token=TOK)

    assert out == 'Failed tab ?: \n', repr(out)


def test_do_close_tab_exits_when_no_result_arrives(tmp):
    """The timeout exit, with nothing rendered before it."""
    del tmp
    body = {'id': '_close_tab', 'type': 'close-tab', 'token': TOK,
            'tab': 'extension', 'tabId': 101}
    plan = [_put(body), _wait('_close_tab', 'd2', 10)]
    code, out = run_cli_exit(['close-tab', '101'], [{'did': 'd2'}, None],
                             module=commands_browser, plan=plan, token=TOK)

    assert code == 'Timeout (10s)', code
    assert out == '', repr(out)


def test_do_close_tab_exits_with_the_error_the_extension_reported(tmp):
    """A refused whole command is an exit, not an empty result sentence."""
    del tmp
    body = {'id': '_close_tab', 'type': 'close-tab', 'token': TOK,
            'tab': 'extension', 'tabId': 101}
    plan = [_put(body), _wait('_close_tab', 'd2', 10)]
    code, out = run_cli_exit(
        ['close-tab', '101'],
        [{'did': 'd2'},
         {'id': '_close_tab', 'error': 'Extension asleep', 'ts': 1}],
        module=commands_browser, plan=plan, token=TOK)

    assert code == 'Error: Extension asleep', code
    assert out == '', repr(out)


# ── do_fetch_timings ─────────────────────────────────────────────────

FIRST = {'method': 'GET', 'status': 200, 'bodySize': 1048576,
         'ms_bodyDecode': 1, 'ms_fetch': 11, 'ms_encode': 2, 'ms_total': 14,
         'url': 'https://cdn.example.com/first.ts'}
SECOND = {'method': 'POST', 'status': 206, 'bodySize': 2097152,
          'ms_bodyDecode': 3, 'ms_fetch': 33, 'ms_encode': 4,
          'ms_total': 40, 'url': 'https://cdn.example.com/second.ts'}
ABORTED = {'method': 'GET', 'error': 'net::ERR_ABORTED', 'ms_total': 5,
           'url': 'https://cdn.example.com/bad.ts'}

# The column header the handler prints: six-wide method and status, a
# ten-wide size, three eight-wide timers, and the url after two spaces.
COLUMNS = 'method status       size   decode    fetch   encode    total  url'
# 49 spaces: the three that pad the six-wide `ERR` cell, a separator, the
# empty ten-wide size, three separators and three empty eight-wide cells,
# a separator, and the seven that pad the eight-wide total.
ERR_GAP = ' ' * 49


def _timings(*entries, count=None, native=False):
    return {'timings': list(entries), 'hasNativeToBase64': native,
            'count': len(entries) if count is None else count}


def test_do_fetch_timings_asks_without_a_reset_and_renders_the_table(tmp):
    """The bare arm: no `reset` field, the table, and the statistics.

    The size column is mebibytes to two decimals, and a 1 MiB body reads
    `1.00M` — the divisor is 1024 twice, not 1000, and the column is ten
    wide including the `M`.
    """
    del tmp
    recorded, out = run_cli(
        ['fetch-timings'], [_timings(FIRST, SECOND)],
        module=commands_browser,
        plan=[_ext('_fetch_timings', 'fetch-timings', {})], token=TOK)

    assert recorded.calls == [('_fetch_timings', 'fetch-timings', {})], \
        recorded.calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == (
        'Fetch timings: 2 entries  nativeToBase64=False\n'
        f'{COLUMNS}\n'
        'GET       200      1.00M        1       11        2       14  '
        'https://cdn.example.com/first.ts\n'
        'POST      206      2.00M        3       33        4       40  '
        'https://cdn.example.com/second.ts\n'
        '\n2 successful: total median=27ms mean=27ms '
        'fetch median=22ms encode median=3ms\n'), repr(out)


def test_do_fetch_timings_carries_the_reset_flag_only_when_it_was_given(tmp):
    """`--reset` adds `reset`, and the bare arm above shows it is absent."""
    del tmp
    recorded, out = run_cli(
        ['fetch-timings', '--reset'], [_timings(FIRST)],
        module=commands_browser,
        plan=[_ext('_fetch_timings', 'fetch-timings', {'reset': True})],
        token=TOK)

    assert recorded.calls == [('_fetch_timings', 'fetch-timings',
                               {'reset': True})], recorded.calls
    assert out == (
        'Fetch timings: 1 entries  nativeToBase64=False\n'
        f'{COLUMNS}\n'
        'GET       200      1.00M        1       11        2       14  '
        'https://cdn.example.com/first.ts\n'
        '\n1 successful: total median=14ms mean=14ms '
        'fetch median=11ms encode median=2ms\n'), repr(out)


def test_do_fetch_timings_prints_the_buffer_as_json_when_raw(tmp):
    """`--raw` returns after the JSON: no table, no statistics.

    The `hasNativeToBase64` flag is part of that first line and is pinned
    true here, so the two spellings of the header are held apart.
    """
    del tmp
    _recorded, out = run_cli(
        ['fetch-timings', '--raw'],
        [_timings(FIRST, count=7, native=True)],
        module=commands_browser,
        plan=[_ext('_fetch_timings', 'fetch-timings', {})], token=TOK)

    assert out == (
        'Fetch timings: 7 entries  nativeToBase64=True\n'
        '[\n'
        '  {\n'
        '    "method": "GET",\n'
        '    "status": 200,\n'
        '    "bodySize": 1048576,\n'
        '    "ms_bodyDecode": 1,\n'
        '    "ms_fetch": 11,\n'
        '    "ms_encode": 2,\n'
        '    "ms_total": 14,\n'
        '    "url": "https://cdn.example.com/first.ts"\n'
        '  }\n'
        ']\n'), repr(out)


def test_do_fetch_timings_says_so_when_the_buffer_is_empty(tmp):
    """No entries is the word `(empty)` and an early return.

    Without the return the statistics block would follow, and computing a
    median over nothing is the crash this arm exists to avoid.
    """
    del tmp
    _recorded, out = run_cli(
        ['fetch-timings'], [_timings()],
        module=commands_browser,
        plan=[_ext('_fetch_timings', 'fetch-timings', {})], token=TOK)

    assert out == (
        'Fetch timings: 0 entries  nativeToBase64=False\n(empty)\n'), repr(out)


def test_do_fetch_timings_renders_a_failed_entry_in_its_own_columns(tmp):
    """An entry carrying `error` has no status, size or timer to print.

    The five blank cells are the handler's own empty strings, and the
    reason is appended in parentheses after the url rather than occupying
    a column of its own.
    """
    del tmp
    _recorded, out = run_cli(
        ['fetch-timings'], [_timings(ABORTED)],
        module=commands_browser,
        plan=[_ext('_fetch_timings', 'fetch-timings', {})], token=TOK)

    assert out == (
        'Fetch timings: 1 entries  nativeToBase64=False\n'
        f'{COLUMNS}\n'
        f'GET    ERR{ERR_GAP}5  '
        'https://cdn.example.com/bad.ts (net::ERR_ABORTED)\n'), repr(out)


def test_do_fetch_timings_prints_no_statistics_when_every_entry_failed(tmp):
    """The summary counts only the entries that completed.

    A buffer of nothing but failures has no median to take, and the
    `if completed` guard is what keeps the block off the output entirely.
    The comparison is a whole string because the absence of the summary
    line is what is being pinned — a substring check would pass a handler
    that printed the block in a different shape.
    """
    del tmp
    _recorded, out = run_cli(
        ['fetch-timings'], [_timings(ABORTED, ABORTED)],
        module=commands_browser,
        plan=[_ext('_fetch_timings', 'fetch-timings', {})], token=TOK)

    row = (f'GET    ERR{ERR_GAP}5  '
           'https://cdn.example.com/bad.ts (net::ERR_ABORTED)\n')
    assert out == (
        'Fetch timings: 2 entries  nativeToBase64=False\n'
        f'{COLUMNS}\n' + row + row), repr(out)


def test_do_fetch_timings_counts_the_whole_buffer_not_the_tail_it_shows(tmp):
    """`-n 1` prints one row and still summarises every entry.

    The summary is taken from the whole buffer, not from the slice that
    was displayed, so the number an operator reads describes the buffer
    rather than the window they happened to ask for.
    """
    del tmp
    _recorded, out = run_cli(
        ['fetch-timings', '-n', '1'], [_timings(FIRST, SECOND)],
        module=commands_browser,
        plan=[_ext('_fetch_timings', 'fetch-timings', {})], token=TOK)

    assert out == (
        'Fetch timings: 2 entries  nativeToBase64=False\n'
        f'{COLUMNS}\n'
        'POST      206      2.00M        3       33        4       40  '
        'https://cdn.example.com/second.ts\n'
        '\n2 successful: total median=27ms mean=27ms '
        'fetch median=22ms encode median=3ms\n'), repr(out)


# ── do_ext_self_reload ───────────────────────────────────────────────

def test_do_ext_self_reload_reports_the_version_it_reloaded_from(tmp):
    """The one call, the version it echoes, and the reconnect sentence."""
    del tmp
    recorded, out = run_cli(
        ['ext-self-reload'], [{'version': '2.7.1'}],
        module=commands_browser, plan=[_ext('_ext_reload', 'ext-reload', {})],
        token=TOK)

    assert recorded.calls == [('_ext_reload', 'ext-reload', {})], \
        recorded.calls
    assert out == ('Extension reloading from v2.7.1 — will reconnect '
                   'SSE automatically\n'), repr(out)


def test_do_ext_self_reload_prints_a_placeholder_for_a_missing_version(tmp):
    """A result with no `version` reads `v?`, not `vNone`."""
    del tmp
    _recorded, out = run_cli(
        ['ext-self-reload'], [{}],
        module=commands_browser, plan=[_ext('_ext_reload', 'ext-reload', {})],
        token=TOK)

    assert out == ('Extension reloading from v? — will reconnect '
                   'SSE automatically\n'), repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clibrowcmd_')


if __name__ == '__main__':
    raise SystemExit(main())
