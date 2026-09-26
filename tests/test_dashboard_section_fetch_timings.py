#!/usr/bin/env python3
"""The fetch-timings panel, run rather than read.

`dashboard/sections/fetch-timings.js` lays out the service worker's fetch
ring buffer, so what it counts, what it calls the median, and which cell
carries a failure are the difference between an operator reading twenty
entries and reading one. The harness mounts the shipped section over the
real `dashboard/api.js` and the real `_util.js` in Node, drives the
toolbar, and reads the parsed body of every request beside the cells the
section rendered.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import run_scenario  # noqa: E402

SECTION = ('sections/fetch-timings.js',)

EXTRA = r"""
const list = () => container.find('[data-role=list]');
const meta = () => container.find('[data-role=meta]');
const cells = (root) => root.all().filter((el) => el.tag === 'tr')
  .map((tr) => tr.children.map((td) => [td.className, td.textContent]));
const panes = (root) => root.all().filter((el) => el.tag === 'pre')
  .map((el) => [el.className, el.textContent]);
const sent = () => REQUESTS.filter((r) => r.target === '/command')
  .map((r) => r.body);
const withoutToken = () => localStorage.removeItem('daedalus-token');
"""

# One worker entry. `extension/worker/messaging.js` records all nine members
# on the success leg and only `ms_total` on the error leg, which is why the
# default here carries all nine and the error entries below do not.
ENTRY = ("const E = (over) => Object.assign({ url:"
         " 'https://one.example.com/asset.js', method: 'GET',"
         " status: 200, bodySize: 2048, ms_bodyDecode: 1.5,"
         " ms_fetch: 12.25, ms_encode: 0.5, ms_total: 14.25,"
         " ts: 1750000000000 }, over || {});\n")

ONE = ENTRY + 'const T = [E({})];\n'
NONE = 'const T = [];\n'
TWO = ENTRY + ("const T = [E({ url: 'https://one.example.com/a.js' }),\n"
               "  E({ url: 'https://one.example.com/b.js' })];\n")
FOUR = ENTRY + ("const T = [10, 20, 30, 40].map((n) => E({ ms_total: n,\n"
                "  url: 'https://one.example.com/' + n + '.js' }));\n")
FAILED = ("const T = [{ url: 'https://one.example.com/x.m3u8',\n"
          "  method: 'GET', error: 'timeout', ms_total: 1200.5,\n"
          "  ts: 1750000000000 }];\n")
MIXED = ENTRY + ("const T = [E({ url: 'https://one.example.com/a.js' }),\n"
                 "  E({ url: 'https://one.example.com/b.js' }),\n"
                 "  { url: 'https://one.example.com/x.m3u8',\n"
                 "    method: 'POST', error: 'aborted', ms_total: 3.5 }];\n")
NO_STATUS = ENTRY + "const T = [E({ status: undefined })];\n"
ZERO_STATUS = ENTRY + "const T = [E({ status: 0 })];\n"

# A url longer than the 100 characters both row kinds cut it to, and a
# 404 on a row that otherwise succeeded: `messaging.js:102` records
# `status: resp.status` on the success leg, so a 4xx carries no `error`
# and the red class is the only thing that says so.
LONG_URL = 'https://one.example.com/' + 'b' * 90 + '.js'
CUT = LONG_URL[:99] + '…'
LONG = (ENTRY + "const U = 'https://one.example.com/' + 'b'.repeat(90)"
        " + '.js';\n")
OK_AND_404 = LONG + ("const T = [E({ url: U }),\n"
                     "  E({ url: U, status: 404 })];\n")
LONG_ERROR = ("const U = 'https://one.example.com/' + 'b'.repeat(90)"
              " + '.js';\n"
              "const T = [{ url: U, method: 'GET', error: 'timeout',\n"
              "  ms_total: 5, ts: 1750000000000 }];\n")


def answer(expr, native='true'):
    return ("answer('fetch-timings', { result: { timings: " + expr
            + ", hasNativeToBase64: " + native + " } });\n")


# One answer for every case: the setup above it is what says whether T
# holds one entry, four, or only failures.
ANSWER = answer('T')
ANSWER_NO_NATIVE = "answer('fetch-timings', { result: { timings: T } });\n"
ANSWER_NULL = "answer('fetch-timings', { result: null });\n"
ANSWER_REFUSED = ("answer('fetch-timings',"
                  " { error: 'buffer unavailable' });\n")

REFUSED_COMMAND = ("drive.route('/command',"
                   " { status: 503, error: 'bridge down' });\n")

SETTLED = ('await bounded(settle(), "after the click",'
           ' _dashnodeStepTimeoutMs);\n')


def scenario(body, *, setup='', answers=(), plan=shared.COMMAND):
    """One child: seed the token, plan every answer, mount, then drive.

    `setup` lands first because the answer table is written in the scope
    `setup` defines.
    """
    return ('(async () => {\n' + shared.SEED + shared.PRELUDE + EXTRA
            + shared.open_section(SECTION[0]) + setup + plan
            + shared.results(*answers)
            + 'const sub = new El("span");\n'
            + 'drive.selector("#s08 [data-sub]", sub);\n'
            + shared.MOUNT + shared.SETTLE + body
            + '})().catch(leave);\n')


def _run(body, *, setup='', answers=(), plan=shared.COMMAND):
    return run_scenario(
        scenario(body, setup=setup, answers=answers, plan=plan),
        sections=SECTION)


def test_the_mount_sends_a_bare_fetch_timings_and_renders_a_row(_tmp):
    """`load()` is called with no argument at the end of `mount`, so the
    first body is the command's own four keys. A defaulted pair added on
    the way in would be two members the worker does not read."""
    report = _run('report({ sub: sub.textContent,\n'
                  '  head: headers(list()),\n'
                  '  rows: cells(list()) });\n',
                  setup=ONE, answers=(ANSWER,))
    assert shared.types(report) == ['fetch-timings'], report
    assert sorted(shared.commands(report)[0]) == [
        'id', 'tab', 'token', 'type'], report
    assert report['head'] == ['method', 'status', 'size', 'decode',
                              'fetch', 'encode', 'total', 'url'], report
    assert report['rows'][1] == [
        ['mono', 'GET'], ['mono green', '200'], ['num', '2.0 KB'],
        ['num dim', '1.5'], ['num', '12.25'], ['num dim', '0.5'],
        ['num cyan', '14.25'], ['url', 'https://one.example.com/asset.js'],
    ], report
    assert report['unplanned'] == [], report


def test_a_single_entry_is_counted_in_the_singular(_tmp):
    """`t.length === 1` is the whole of the singular test, so one entry
    and no entries are the two counts a shared plural would get wrong."""
    report = _run('report({ sub: sub.textContent });\n',
                  setup=ONE, answers=(ANSWER,))
    assert report['sub'] == '1 entry', report


def test_no_entries_and_two_entries_are_both_plural(_tmp):
    """Zero is `0 entries` and the empty state, because the count is
    written above the `length === 0` return."""
    empty = _run('report({ sub: sub.textContent,'
                 '  list: list().textContent });\n',
                 setup=NONE, answers=(ANSWER,))
    assert empty['sub'] == '0 entries', empty
    assert empty['list'] == 'ring buffer empty.', empty
    two = _run('report({ sub: sub.textContent });\n',
               setup=TWO, answers=(ANSWER,))
    assert two['sub'] == '2 entries', two


def test_rows_are_rendered_in_the_reverse_of_the_buffer_order(_tmp):
    """`t.slice().reverse().map(row)` puts the newest entry first, which is
    the opposite of the order the worker recorded them in. A panel that
    dropped the reverse would show the oldest first."""
    report = _run('report({ rows: cells(list()).slice(1) });\n',
                  setup=TWO, answers=(ANSWER,))
    assert [row[7][1] for row in report['rows']] == [
        'https://one.example.com/b.js',
        'https://one.example.com/a.js'], report


def test_the_native_flag_turns_the_meta_span_green_or_amber(_tmp):
    """`hasNativeToBase64` is read off `r` itself rather than off
    `timings`, so it decides the span's class on its own and its own text
    is the value stringified. The buffer here is all failures, so the
    meta span holds the flag alone and the text is exact."""
    on = _run('report({ text: meta().textContent,\n'
              '  className: meta().children[1].className });\n',
              setup=FAILED, answers=(answer('T', 'true'),))
    assert on['className'] == 'green', on
    assert on['text'] == 'nativeToBase64 = true', on
    off = _run('report({ text: meta().textContent,\n'
               '  className: meta().children[1].className });\n',
               setup=FAILED, answers=(answer('T', 'false'),))
    assert off['className'] == 'amber', off
    assert off['text'] == 'nativeToBase64 = false', off


def test_a_result_with_no_native_flag_reads_as_undefined_and_amber(_tmp):
    """The class is read as a truthiness test, so a result that omits the
    member renders the string `undefined` rather than `false` -- a reader
    told the base64 path is `undefined` is looking at a member the worker
    did not send, not at a falsy one."""
    report = _run('report({ text: meta().textContent,\n'
                  '  className: meta().children[1].className });\n',
                  setup=FAILED, answers=(ANSWER_NO_NATIVE,))
    assert report['text'] == 'nativeToBase64 = undefined', report
    assert report['className'] == 'amber', report


def test_the_count_is_written_before_a_result_that_is_not_an_object(_tmp):
    """`t` is read through `(r && r.timings) || []` and the count is
    written from that, while the span below dereferences `r` unguarded. A
    null result therefore reaches the pane AFTER the count was written,
    which is the only order that leaves `0 entries` standing beside an
    error the operator never sent for."""
    report = _run('report({ sub: sub.textContent,\n'
                  '  pane: panes(list()),\n'
                  '  meta: meta().textContent });\n',
                  setup=ONE, answers=(ANSWER_NULL,))
    assert report['sub'] == '0 entries', report
    assert len(report['pane']) == 1, report
    assert report['pane'][0][0] == 'pane err', report
    assert 'hasNativeToBase64' in report['pane'][0][1], report
    assert report['meta'] == '', report


def test_the_median_of_an_even_count_is_the_upper_middle(_tmp):
    """`totals[Math.floor(totals.length / 2)]` over a sorted array is the
    UPPER middle for an even count, so four entries report 30 where a true
    median reports 25. The mean beside it is the ordinary one, which is
    what makes the pair worth reading together."""
    report = _run('report({ text: meta().textContent });\n',
                  setup=FOUR, answers=(ANSWER,))
    assert report['text'] == ('nativeToBase64 = true'
                              '  ·  median 30ms'
                              '  ·  mean 25ms'
                              '  ·  4/4 ok'), report


def test_a_status_cell_reads_green_when_the_status_is_missing_or_zero(_tmp):
    """`e.status >= 400` is false for both an absent member and a zero, so
    both render the success class, and the text is `String(e.status || '')`
    so a zero renders BLANK while a missing one renders the same blank."""
    missing = _run('report({ rows: cells(list()).slice(1) });\n',
                   setup=NO_STATUS, answers=(answer('T'),))
    assert missing['rows'][0][1] == ['mono green', ''], missing
    zero = _run('report({ rows: cells(list()).slice(1) });\n',
                setup=ZERO_STATUS, answers=(answer('T'),))
    assert zero['rows'][0][1] == ['mono green', ''], zero
    assert shared.commands(zero)[0].get('status') is None, zero


def test_an_error_row_renders_four_bare_cells_and_the_error_in_the_url(_tmp):
    """`e.error` takes the whole row down a different branch: the four
    timing cells carry no class and no text because the worker never
    recorded them on the error leg, and the reason is interpolated raw
    into the url cell behind TWO spaces."""
    report = _run('report({ rows: cells(list()).slice(1) });\n',
                  setup=FAILED, answers=(answer('T'),))
    assert report['rows'] == [[['mono', 'GET'], ['mono red', 'ERR'],
                               ['', ''], ['', ''], ['', ''], ['', ''],
                               ['num', '1200.5'],
                               ['url red', 'https://one.example.com'
                                '/x.m3u8  (timeout)']]], report


def test_entries_that_all_failed_get_no_stats_block(_tmp):
    """The block is guarded on `ok.length > 0`, so a buffer holding only
    failures reports the count and nothing else -- a median over an empty
    array has no value to render."""
    report = _run('report({ text: meta().textContent,'
                  '  rows: cells(list()).slice(1) });\n',
                  setup=FAILED, answers=(answer('T'),))
    assert report['text'] == 'nativeToBase64 = true', report
    assert len(report['rows']) == 1, report


def test_a_success_row_with_a_server_error_status_reads_red(_tmp):
    """`e.status >= 400` is the whole of the red branch, and a 4xx
    response carries no `error` key, so this row takes the SUCCESS branch
    and the status cell is the only thing that says the fetch failed. The
    pairs either side of it are the absent status and the zero status,
    which both read green above."""
    report = _run('report({ rows: cells(list()).slice(1) });\n',
                  setup=OK_AND_404, answers=(ANSWER,))
    # The buffer is rendered reversed, so the 404 is the row on top.
    assert [row[1] for row in report['rows']] == [
        ['mono red', '404'], ['mono green', '200']], report
    # The red row is a success row: the timing cells are filled in, which
    # is what tells it apart from the error row's four bare ones.
    assert report['rows'][0][6] == ['num cyan', '14.25'], report
    assert len(LONG_URL) == 117, LONG_URL


def test_a_url_over_a_hundred_characters_is_cut_in_both_row_kinds(_tmp):
    """Both rows cut the url at 100 and both rejoin the reason behind TWO
    spaces, so a long url on an error row is the cut url and then the
    reason -- and a cap of 140 would show forty more characters of a url
    the table is already too wide for."""
    report = _run('report({ rows: cells(list()).slice(1) });\n',
                  setup=OK_AND_404, answers=(ANSWER,))
    assert report['rows'][0][7] == ['url', CUT], report
    assert report['rows'][1][7] == ['url', CUT], report
    failed = _run('report({ rows: cells(list()).slice(1) });\n',
                  setup=LONG_ERROR, answers=(ANSWER,))
    assert failed['rows'][0][7] == [
        'url red', CUT + '  (timeout)'], failed


def test_the_stats_block_counts_the_entries_that_worked(_tmp):
    """`ok` is the entries with no error, so the ratio reads over the whole
    buffer and an error entry moves the denominator only."""
    report = _run('report({ text: meta().textContent,'
                  '  rows: cells(list()).slice(1) });\n',
                  setup=MIXED, answers=(ANSWER,))
    assert report['text'] == ('nativeToBase64 = true'
                              '  ·  median 14ms'
                              '  ·  mean 14ms'
                              '  ·  2/3 ok'), report
    assert report['rows'][0][7] == [
        'url red',
        'https://one.example.com/x.m3u8  (aborted)'], report


def test_the_meta_span_survives_a_load_that_failed(_tmp):
    """The `catch` clears the list and renders the pane, and `clear(metaEl)`
    is only on the success leg. A reload that failed therefore leaves the
    previous load's native-base64 verdict standing beside the error, which
    is a real reading: the verdict is still true, the load is what
    failed."""
    report = _run('withoutToken();\n'
                  'button("refresh").click();\n' + SETTLED
                  + 'report({ text: meta().textContent,\n'
                    '  pane: panes(list()) });\n',
                  setup=FAILED, answers=(ANSWER,))
    assert report['text'] == 'nativeToBase64 = true', report
    assert len(report['pane']) == 1, report
    assert report['pane'][0][0] == 'pane err', report
    assert report['pane'][0][1].startswith('No token set'), report
    # The token was gone before the click, so nothing reached the wire.
    assert shared.types(report) == ['fetch-timings'], report


def test_the_refresh_click_sends_no_reset(_tmp):
    """`refresh` is bound to `() => load()`, so no event object reaches
    `load` and no `opts` is built. A click that did forward its event
    would turn the argument into a `reset`."""
    report = _run('button("refresh").click();\n' + SETTLED
                  + 'report({});\n',
                  setup=ONE, answers=(ANSWER,))
    first, second = shared.commands(report)
    assert 'reset' not in first, report
    assert 'reset' not in second, report
    assert second['type'] == 'fetch-timings', report


def test_the_reset_arms_on_the_first_click_and_sends_on_the_second(_tmp):
    """The anti-vacuity half of the armed control: the first click parked
    the revert timer, renamed the button and sent NOTHING, so a two-click
    test cannot pass against a handler that ran on the first one."""
    report = _run('const reset = button("reset buffer");\n'
                  'const before = REQUESTS.length;\n'
                  'reset.click();\n'
                  'const mid = { parked: drive.live().length,\n'
                  '  label: reset.textContent, armed: reset.className,\n'
                  '  requests: REQUESTS.length };\n'
                  'reset.click();\n' + SETTLED
                  + 'report({ before, mid, sent: sent().map(\n'
                    '  (b) => [b.type, b.reset]) });\n',
                  setup=ONE, answers=(ANSWER,))
    assert report['mid']['parked'] > 0, report
    assert report['mid']['label'] == 'confirm reset', report
    assert 'armed' in report['mid']['armed'], report
    assert report['mid']['requests'] == report['before'], report
    assert report['sent'] == [
        ['fetch-timings', None], ['fetch-timings', True]], report


def test_a_reset_that_failed_renders_the_error_pane_and_still_toasts_ok(_tmp):
    """`load()` never rejects -- its own catch is total -- so the toast
    beside `await load(...)` is unconditional. Asserting the toast ALONE
    passes against a handler that only toasts, and the pane ALONE passes
    against one that only renders; the pair is the pin, and the reset's
    own `catch` above them is unreachable."""
    report = _run('const reset = button("reset buffer");\n'
                  'reset.click();\n'
                  'reset.click();\n' + SETTLED
                  + 'report({ pane: panes(list()),\n'
                    '  toasts: toasts(),\n'
                    '  sent: sent().map((b) => [b.type, b.reset]) });\n',
                  setup=ONE, answers=(ANSWER_REFUSED,))
    assert report['toasts'] == [{'type': 'ok', 'text': 'reset'}], report
    assert len(report['pane']) == 1, report
    assert report['pane'][0][0] == 'pane err', report
    assert report['pane'][0][1] == 'buffer unavailable', report


def test_a_reset_that_worked_toasts_ok_and_leaves_no_pane(_tmp):
    """The other half of the pair above: the same handler on a load that
    answered. One assertion would pass against either behaviour, so both
    are pinned."""
    report = _run('const reset = button("reset buffer");\n'
                  'reset.click();\n'
                  'reset.click();\n' + SETTLED
                  + 'report({ pane: panes(list()),\n'
                    '  toasts: toasts(), rows: cells(list()).length });\n',
                  setup=ONE, answers=(ANSWER,))
    assert report['toasts'] == [{'type': 'ok', 'text': 'reset'}], report
    assert report['pane'] == [], report
    assert report['rows'] == 2, report


def test_a_command_the_bridge_refused_answers_nothing_at_all(_tmp):
    """`api.js:56` throws on a non-200, so `runCommand` never reaches the
    result loop. What this case pins is that: the two `legs(...)`
    assertions say no poll and no consume leg was opened, and the pane
    says what the operator is left reading. What the bridge does with a
    refused command is not something this tree can see."""
    report = _run('report({ pane: panes(list()) });\n',
                  setup=ONE, answers=(), plan=REFUSED_COMMAND)
    assert shared.types(report) == ['fetch-timings'], report
    assert shared.legs(report)['poll'] == [], report
    assert shared.legs(report)['consume'] == [], report
    assert report['pane'][0][1] == 'HTTP 503: bridge down', report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashfetch_')


if __name__ == '__main__':
    raise SystemExit(main())
