#!/usr/bin/env python3
"""The overview panel, run rather than read.

Four counters, a clear button and the live SSE event log. In the shipped
dashboard `dashboard/app.js` mounts it on the way to every other section,
and this suite calls `mount` on the section itself rather than reaching
it through that routing. Each case emits bus events at the shipped
section over the real `api.js` and reads the rows, the stat cells and the
status line back out of the tree.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import section_runner  # noqa: E402

SECTION = ('sections/overview.js',)

# Assigned in `setup`, so it is the clock the mount read `sessionStart`
# from. A test wanting another instant reassigns it in its body.
CLOCK = "Date.now = () => 1750000000000;\n"

TABS = ("const TABS = [{ tabId: 11, title: 'first tab',\n"
        "  url: 'https://one.example.com/one' },\n"
        "  { tabId: 22, title: '', url: 'https://two.example.com/two' }];\n")
TABS_PLAN = "drive.route('/tabs', { json: TABS });\n"
TABS_FAILING = ("drive.route('/tabs',"
                " { status: 500, json: { error: 'tabs unavailable' } });\n")

# The page's status line, which the section writes to and which is not in
# its container. `dashboard/index.html:115` carries it, so the fixture
# carries it in that shape and with that default, and the harness walks
# `document.body` to find it. The selector is not asserted by spelling: a
# name the grammar declines is refused, the refusal is swallowed by the
# same try, and the cell stays on its markup's own `0`.
STATUS_LINE = ("const statusLine = new El('div');\n"
               "const statusCells = (count) => {\n"
               "  const made = [];\n"
               "  for (let i = 0; i < count; i += 1) {\n"
               "    const cell = new El('span');\n"
               "    cell.className = 'sl-meta';\n"
               "    cell.dataset.meta = 'tab-count';\n"
               "    cell.textContent = '0';\n"
               "    statusLine.appendChild(cell);\n"
               "    made.push(cell);\n"
               "  }\n"
               "  document.body.appendChild(statusLine);\n"
               "  return made;\n"
               "};\n")

META_ONE = STATUS_LINE + "const meta = statusCells(1)[0];\n"
META_TWO = STATUS_LINE + "const [metaA, metaB] = statusCells(2);\n"

# A row is `[timestamp, type, body]`. The timestamp is read where the zone
# is known, so the readers skip it. A missing cell reads `null` rather than
# throwing, so a row the section did not build fails as a diff against the
# expected list instead of a TypeError three readers deep.
READERS = ("const logEl = container.find('[data-role=log]');\n"
           "const stat = (key) => container.find('[data-stat=' + key + ']');\n"
           "const cell = (row, at) => (row.children[at]\n"
           "  ? row.children[at].textContent : null);\n"
           "const bodies = () => logEl.children.map((row) => cell(row, 2));\n"
           "const types = () => logEl.children.map((row) => cell(row, 1));\n")


def scenario(body, *, setup=CLOCK + TABS + META_ONE + TABS_PLAN,
             answers=(), plan=shared.COMMAND):
    """One child: seed the token, plan the one GET, mount, then drive."""
    return ('(async () => {\n' + shared.SEED + shared.PRELUDE
            + shared.open_section(SECTION[0]) + setup + plan
            + shared.results(*answers)
            + shared.MOUNT + READERS + shared.SETTLE + body
            + '})().catch(leave);\n')


_run = section_runner(scenario, SECTION, plan=shared.COMMAND,
                      setup=CLOCK + TABS + META_ONE + TABS_PLAN)


def test_the_mount_shows_the_placeholder_over_two_zeroed_clocks(_tmp):
    """`renderRate` replaces the cell's text and appends the suffix as a
    child, so the count and its `ev/min` run together. `0ev/min` is what
    ships, not a typo."""
    report = _run('sectionReport({ rows: logEl.children.length,\n'
                  '  text: logEl.textContent,\n'
                  '  session: stat("session").textContent,\n'
                  '  rate: stat("rate").textContent,\n'
                  '  last: stat("last").textContent,\n'
                  '  tabs: stat("tabs").textContent });\n')
    assert report['unplanned'] == [], report
    assert report['rows'] == 1, report
    assert report['text'] == 'waiting for events…', report
    assert report['session'] == '00:00', report
    assert report['rate'] == '0ev/min', report
    assert report['last'] == '—', report
    assert report['tabs'] == '2', report


def test_the_mount_writes_the_tab_count_onto_the_stat_and_the_meta_bar(_tmp):
    """The LENGTH the bridge returned, onto the stat cell and onto every
    status-line element. That line starts on its markup's own `0`, so the
    `2` is a write the section made."""
    report = _run('sectionReport({ tabs: stat("tabs").textContent,\n'
                  '  meta: meta.textContent,\n'
                  '  targets: REQUESTS.map((r) => r.target) });\n')
    assert report['unplanned'] == [], report
    assert report['targets'] == ['/tabs'], report
    assert report['tabs'] == '2', report
    assert report['meta'] == '2', report


def test_without_a_token_the_refresh_writes_the_dash_and_asks_nothing(_tmp):
    """Re-firing the interval is what separates the guard's two halves:
    the stat holds the count the mount's own fetch wrote, so the dash
    replacing it is this write and not the markup's default."""
    report = _run('const before = { tabs: stat("tabs").textContent,\n'
                  '  meta: meta.textContent, targets: REQUESTS.length };\n'
                  'localStorage.removeItem("daedalus-token");\n'
                  'drive.fire(1);\n'
                  'await bounded(settle(), "after the tokenless refresh",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'sectionReport({ before,\n'
                  '  tabs: stat("tabs").textContent,\n'
                  '  meta: meta.textContent,\n'
                  '  targets: REQUESTS.length });\n',
                  setup=CLOCK + TABS + META_ONE + TABS_PLAN)
    assert report['unplanned'] == [], report
    assert report['before'] == {'tabs': '2', 'meta': '2', 'targets': 1}, \
        report
    assert report['tabs'] == '—', report
    assert report['meta'] == '2', report
    assert report['targets'] == 1, report


def test_a_refused_tab_list_writes_the_dash_and_leaves_the_meta_bar_alone(
        _tmp):
    """The meta-bar query sits below the `await` that threw, so a refused
    refresh leaves the status line on the count the event wrote while the
    stat goes back to the dash. Rendered, not logged, hence the recorder."""
    report = _run('const before = { tabs: stat("tabs").textContent,\n'
                  '  meta: meta.textContent, targets: REQUESTS.length };\n'
                  'bus.emit({ type: "tabs-synced", count: 4 });\n'
                  'const synced = { tabs: stat("tabs").textContent,\n'
                  '  meta: meta.textContent };\n'
                  'drive.fire(1);\n'
                  'await bounded(settle(), "after the refused refresh",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'sectionReport({ before, synced,\n'
                  '  tabs: stat("tabs").textContent,\n'
                  '  meta: meta.textContent, errors: ERRORS,\n'
                  '  targets: REQUESTS.map((r) => r.target) });\n',
                  setup=CLOCK + TABS + META_ONE + TABS_FAILING)
    assert report['unplanned'] == [], report
    assert report['before'] == {'tabs': '—', 'meta': '0', 'targets': 1}, report
    assert report['synced'] == {'tabs': '4', 'meta': '4'}, report
    assert report['tabs'] == '—', report
    assert report['meta'] == '4', report
    assert report['targets'] == ['/tabs', '/tabs'], report
    assert report['errors'] == [], report


def test_a_result_names_its_channel_its_ids_and_whether_it_failed(_tmp):
    """A falsy `tabId` or `resultId` -- the number zero included -- is a
    placeholder, and an unknown world passes through because the channel
    name is the section's to render rather than a set it rejects."""
    report = _run('bus.emit({ type: "result", world: "cdp", ok: true,\n'
                  '  tabId: 5, resultId: "r-9" });\n'
                  'bus.emit({ type: "result", ok: false });\n'
                  'bus.emit({ type: "result", world: "page-main", ok: true,\n'
                  '  tabId: 0, resultId: "" });\n'
                  'bus.emit({ type: "result",\n'
                  '  world: "page:one.example.com",\n'
                  '  ok: true, tabId: 7, resultId: "r-1" });\n'
                  'sectionReport({ types: types(), bodies: bodies(),\n'
                  '  rate: stat("rate").textContent,\n'
                  '  last: stat("last").textContent });\n')
    assert report['unplanned'] == [], report
    assert report['types'] == ['result'] * 4, report
    assert report['bodies'] == [
        'tab=7  id=r-1  channel=page:one.example.com  ok',
        'tab=·  id=·  channel=page-main  ok',
        'tab=·  id=·  channel=·  err',
        'tab=5  id=r-9  channel=cdp  ok',
    ], report
    assert report['last'] == 'result', report
    assert report['rate'] == '4ev/min', report


def test_a_tab_update_names_its_title_then_its_url_then_neither(_tmp):
    """`truncate` at 80 is 79 characters and the ellipsis, not a cut at
    the boundary."""
    report = _run('bus.emit({ type: "tab-updated", tabId: 3,\n'
                  '  title: "A page", url: "https://one.example.com/one" });\n'
                  'bus.emit({ type: "tab-updated", tabId: 4, title: "",\n'
                  '  url: "https://two.example.com/two" });\n'
                  'bus.emit({ type: "tab-updated", tabId: 5, title: "",\n'
                  '  url: "" });\n'
                  'bus.emit({ type: "tab-updated", tabId: 6,\n'
                  '  title: "T".repeat(100), url: "" });\n'
                  'sectionReport({ types: types(), bodies: bodies(),\n'
                  '  rate: stat("rate").textContent });\n')
    assert report['unplanned'] == [], report
    assert report['types'] == ['tab-updated'] * 4, report
    assert report['bodies'] == [
        '6 · ' + 'T' * 79 + '…',
        '5 · ',
        '4 · https://two.example.com/two',
        '3 · A page',
    ], report
    assert report['rate'] == '4ev/min', report


def test_the_first_event_clears_the_placeholder_and_stacks_newest_first(
        _tmp):
    """`!hasEvents` is the only signal that the placeholder is still
    there. An unnamed type falls through to `JSON.stringify` of the event,
    which is what keeps a later bus section readable here first."""
    report = _run('const before = { rows: logEl.children.length,\n'
                  '  text: logEl.textContent };\n'
                  'bus.emit({ type: "tab-unregistered", tabId: 4 });\n'
                  'const afterFirst = { rows: logEl.children.length,\n'
                  '  text: logEl.textContent };\n'
                  'bus.emit({ type: "tabs-synced", count: 3 });\n'
                  'bus.emit({ type: "whatever", n: 1 });\n'
                  'sectionReport({ before, afterFirst, types: types(),\n'
                  '  bodies: bodies(), last: stat("last").textContent });\n')
    assert report['unplanned'] == [], report
    assert report['before'] == {'rows': 1, 'text': 'waiting for events…'}, \
        report
    # The placeholder is gone, not merely covered: the log holds the one row
    # and its whole text is the three cells the row is built from.
    assert report['afterFirst'] == {
        'rows': 1,
        'text': _stamp(1750000000000) + 'tab-unregistered4 removed',
    }, report
    assert report['types'] == [
        'whatever', 'tabs-synced', 'tab-unregistered'], report
    assert report['bodies'] == ['{"type":"whatever","n":1}',
                                '3 tab(s) registered',
                                '4 removed'], report
    assert report['last'] == 'whatever', report


def test_the_log_keeps_two_hundred_rows_while_the_rate_keeps_all_of_them(
        _tmp):
    """A cap shared with the rate would read `200ev/min` here, and one
    applied at push time would leave the sixth event rather than the
    two-hundred-and-fifth on the log."""
    report = _run('for (let i = 1; i <= 205; i += 1) {\n'
                  '  bus.emit({ type: "tab-updated", tabId: i,\n'
                  '    title: "tab " + i });\n'
                  '}\n'
                  'sectionReport({ rows: logEl.children.length,\n'
                  '  newest: bodies()[0], oldest: bodies()[199],\n'
                  '  rate: stat("rate").textContent });\n')
    assert report['unplanned'] == [], report
    assert report['rows'] == 200, report
    assert report['newest'] == '205 · tab 205', report
    assert report['oldest'] == '6 · tab 6', report
    assert report['rate'] == '205ev/min', report


def test_the_clear_button_empties_the_log_and_leaves_the_rate_alone(_tmp):
    """The rate window is a different array and survives the clear. The
    `hasEvents` reset is also what lets the next event remove the
    `cleared.` row."""
    report = _run('bus.emit({ type: "tab-unregistered", tabId: 4 });\n'
                  'bus.emit({ type: "tab-unregistered", tabId: 5 });\n'
                  'const filled = { rows: logEl.children.length,\n'
                  '  rate: stat("rate").textContent };\n'
                  'button("clear").click();\n'
                  'const cleared = { rows: logEl.children.length,\n'
                  '  text: logEl.textContent,\n'
                  '  rate: stat("rate").textContent };\n'
                  'bus.emit({ type: "tab-unregistered", tabId: 6 });\n'
                  'sectionReport({ filled, cleared, types: types(),\n'
                  '  bodies: bodies(), rate: stat("rate").textContent,\n'
                  '  clicks: CLICKS.length });\n')
    assert report['unplanned'] == [], report
    assert report['filled'] == {'rows': 2, 'rate': '2ev/min'}, report
    assert report['cleared'] == {'rows': 1, 'text': 'cleared.',
                                 'rate': '2ev/min'}, report
    assert report['types'] == ['tab-unregistered'], report
    assert report['bodies'] == ['6 removed'], report
    assert report['rate'] == '3ev/min', report
    assert report['clicks'] == 1, report


def test_an_internal_event_is_dropped_whole_and_the_next_one_is_not(_tmp):
    """The guard is first in the listener, so an internal event reaches
    none of the four things behind it -- and the arm a shallow assertion
    misses is the `tabs-synced` branch, which would overwrite the tab count
    the mount spent a fetch on."""
    report = _run('bus.emit({ type: "tab-updated", tabId: 1,\n'
                  '  title: "hidden", __internal: true });\n'
                  'const afterInternal = { rows: logEl.children.length,\n'
                  '  text: logEl.textContent,\n'
                  '  rate: stat("rate").textContent,\n'
                  '  last: stat("last").textContent };\n'
                  'bus.emit({ type: "tab-updated", tabId: 2,\n'
                  '  title: "shown" });\n'
                  'bus.emit({ type: "tabs-synced", count: 8,\n'
                  '  __internal: true });\n'
                  'sectionReport({ afterInternal, types: types(),\n'
                  '  bodies: bodies(), rate: stat("rate").textContent,\n'
                  '  tabs: stat("tabs").textContent,\n'
                  '  meta: meta.textContent,\n'
                  '  errors: ERRORS,\n'
                  '  last: stat("last").textContent });\n')
    assert report['unplanned'] == [], report
    assert report['afterInternal'] == {
        'rows': 1,
        'text': 'waiting for events…',
        'rate': '0ev/min',
        'last': '—'}, report
    assert report['types'] == ['tab-updated'], report
    assert report['bodies'] == ['2 · shown'], report
    assert report['rate'] == '1ev/min', report
    assert report['tabs'] == '2', report
    assert report['meta'] == '2', report
    assert report['last'] == 'tab-updated', report
    # A guard that refused by THROWING would leave no row and no rate bump
    # and be invisible above; the recorder is the only channel it reaches.
    assert report['errors'] == [], report


def test_a_synced_event_rewrites_the_stat_and_every_meta_count(_tmp):
    """Two status-line cells, because a write to only the first would
    leave the second reading the count from before the sync."""
    report = _run('bus.emit({ type: "tabs-synced", count: 4 });\n'
                  'sectionReport({ tabs: stat("tabs").textContent,\n'
                  '  a: metaA.textContent, b: metaB.textContent,\n'
                  '  body: bodies()[0] });\n',
                  setup=TABS + META_TWO + TABS_PLAN)
    assert report['unplanned'] == [], report
    assert report['tabs'] == '4', report
    assert report['a'] == '4', report
    assert report['b'] == '4', report
    assert report['body'] == '4 tab(s) registered', report


def test_the_rate_window_evicts_on_a_new_event_and_on_its_own_trim(_tmp):
    """The window is filtered in two places and this pins them apart: the
    late event at t+70s is the only thing that reaches `bumpRate`'s filter,
    and only the trims reach the interval's. The 59/60 pairs are the
    boundary, reached by driving the clock and never by waiting."""
    report = _run('Date.now = () => 1750000000000;\n'
                  'bus.emit({ type: "tab-updated", tabId: 1,\n'
                  '  title: "one" });\n'
                  'const afterOne = stat("rate").textContent;\n'
                  'Date.now = () => 1750000030000;\n'
                  'bus.emit({ type: "tab-updated", tabId: 2,\n'
                  '  title: "two" });\n'
                  'const afterTwo = stat("rate").textContent;\n'
                  'Date.now = () => 1750000070000;\n'
                  'bus.emit({ type: "tab-updated", tabId: 3, title: "x" });\n'
                  'const afterLate = stat("rate").textContent;\n'
                  'Date.now = () => 1750000089000;\n'
                  'drive.fire(3);\n'
                  'const kept = stat("rate").textContent;\n'
                  'Date.now = () => 1750000090000;\n'
                  'drive.fire(3);\n'
                  'const dropped = stat("rate").textContent;\n'
                  'Date.now = () => 1750000130000;\n'
                  'drive.fire(3);\n'
                  'sectionReport({ afterOne, afterTwo, afterLate, kept,\n'
                  '  dropped, empty: stat("rate").textContent,\n'
                  '  rows: logEl.children.length });\n')
    assert report['unplanned'] == [], report
    assert report['afterOne'] == '1ev/min', report
    assert report['afterTwo'] == '2ev/min', report
    assert report['afterLate'] == '2ev/min', report
    assert report['kept'] == '2ev/min', report
    assert report['dropped'] == '1ev/min', report
    assert report['empty'] == '0ev/min', report
    # The window ages and nothing else: the rows it dropped from the rate
    # are still on the log.
    assert report['rows'] == 3, report


def test_the_mount_parks_exactly_the_three_intervals_it_names(_tmp):
    """Each interval is identified by what firing it does rather than by
    its number, and the periods are asserted because the id carries none of
    them -- a tab list refreshed every nine seconds is the same code with a
    staler number. 3725 s renders `62:05`, which a clock wrapped at an hour
    would not."""
    report = _run('const before = { tabs: stat("tabs").textContent,\n'
                  '  session: stat("session").textContent,\n'
                  '  rate: stat("rate").textContent,\n'
                  '  requests: REQUESTS.length };\n'
                  'Date.now = () => 1750000065000;\n'
                  'drive.fire(1);\n'
                  'await bounded(settle(), "after the tab refresh",'
                  ' _dashnodeStepTimeoutMs);\n'
                  'const afterTabs = { tabs: stat("tabs").textContent,\n'
                  '  session: stat("session").textContent,\n'
                  '  rate: stat("rate").textContent,\n'
                  '  requests: REQUESTS.length };\n'
                  'Date.now = () => 1750000060000;\n'
                  'drive.fire(2);\n'
                  'const oneMinute = stat("session").textContent;\n'
                  'Date.now = () => 1750003725000;\n'
                  'drive.fire(2);\n'
                  'sectionReport({ timers: drive.live(),\n'
                  '  delays: drive.delays(), before, afterTabs,\n'
                  '  oneMinute, over: stat("session").textContent,\n'
                  '  meta: meta.textContent });\n')
    assert report['unplanned'] == [], report
    assert report['timers'] == [1, 2, 3], report
    assert report['delays'] == [8000, 1000, 2000], report
    assert report['before'] == {'tabs': '2', 'session': '00:00',
                                'rate': '0ev/min', 'requests': 1}, report
    # Firing the 8000 ms interval re-asks for the tab list and re-writes the
    # meta bar, and touches neither of the two clocks.
    assert report['afterTabs'] == {'tabs': '2', 'session': '00:00',
                                   'rate': '0ev/min', 'requests': 2}, report
    assert report['oneMinute'] == '01:00', report
    assert report['over'] == '62:05', report
    assert report['meta'] == '2', report


def test_a_row_stamps_the_clock_the_event_arrived_on(_tmp):
    """Read at the push, not at the mount: two events on two assigned
    clocks carry the two stamps."""
    report = _run('Date.now = () => 1750000000000;\n'
                  'bus.emit({ type: "tab-unregistered", tabId: 4 });\n'
                  'const first = logEl.children[0].children[0].textContent;\n'
                  'Date.now = () => 1750003661000;\n'
                  'bus.emit({ type: "tab-unregistered", tabId: 5 });\n'
                  'sectionReport({ first,\n'
                  '  second: logEl.children[0].children[0].textContent,\n'
                  '  order: bodies() });\n')
    assert report['unplanned'] == [], report
    assert report['first'] == _stamp(1750000000000), report
    assert report['second'] == _stamp(1750003661000), report
    assert report['order'] == ['5 removed', '4 removed'], report


def _stamp(ms):
    """`fmtTime` renders `HH:MM:SS` in the child's zone, which is the
    host's, so the expectation is computed in it rather than pinned to a
    literal."""
    return time.strftime('%H:%M:%S', time.localtime(ms / 1000))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashovw_')


if __name__ == '__main__':
    raise SystemExit(main())
