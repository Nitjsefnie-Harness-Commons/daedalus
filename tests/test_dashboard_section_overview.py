#!/usr/bin/env python3
"""The overview panel, run rather than read.

`dashboard/sections/overview.js` is the dashboard's landing section: four
counters, a clear button and the live SSE event log. Nothing mounts it
directly -- `dashboard/app.js` reaches it on the way to every other section
-- so its whole event log ran for years without one assertion against it.
The harness mounts the shipped section over the real `dashboard/api.js` and
the real `_util.js` in Node, emits bus events at it and reads the rows, the
stats and the meta bar back out of the tree.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import section_runner  # noqa: E402

SECTION = ('sections/overview.js',)

# Assigned in `setup`, so it is the clock the mount read `sessionStart` from
# AND the clock every parked interval and every row timestamp reads later.
# A test that wants a different instant reassigns it in its own body.
CLOCK = "Date.now = () => 1750000000000;\n"

TABS = ("const TABS = [{ tabId: 11, title: 'first tab',\n"
        "  url: 'https://one.example.com/one' },\n"
        "  { tabId: 22, title: '', url: 'https://two.example.com/two' }];\n")
TABS_PLAN = "drive.route('/tabs', { json: TABS });\n"
TABS_FAILING = ("drive.route('/tabs',"
                " { status: 500, json: { error: 'tabs unavailable' } });\n")

# The page's status line, which `overview.js` writes the tab count to and
# which is not part of the section's container. `dashboard/index.html:115`
# carries it, so the fixture carries it in the same shape and the same
# default the markup has, and the harness walks `document.body` to find it:
# a status line built here is the page, not a shim answering a call. The
# selector the section reaches it by is not asserted by spelling -- the
# write landing is the assertion, and a section that reached for a name the
# grammar declines would have the refusal swallowed by the same try and
# leave the cell at its markup's own `0`.
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

# The stat cells carry `data: { stat: key }` and the shell answers that
# selector, so they are read the way the section itself reaches them rather
# than by walking the tree for a dataset key. A row is
# `[timestamp, type, body]`, and the timestamp is left out of the readers
# because `fmtTime` renders it in the host's zone: the row's own clock is
# pinned where the test assigned one. A cell that is not there reads `null`
# rather than throwing, so a row the section did not build fails as a diff
# against the expected list rather than as a TypeError three readers deep.
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
    """The log ships one placeholder row and the counters are written by the
    mount's own `renderSession`/`renderRate` calls, so an operator opening
    the dashboard reads a session and a rate before any event has arrived.
    The `rate` cell has no separator: the number replaces the cell's text and
    the `ev/min` suffix is appended as a child, so the two run together."""
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
    """`refreshTabs` answers `api.get('/tabs')` and writes the LENGTH onto
    two different places: the section's own stat cell and every meta-bar
    element on the page. The count is the number the bridge returned, not the
    list. The status line starts on its markup's own `0`, so the `2` here
    is a write the section made and not a value the fixture carried."""
    report = _run('sectionReport({ tabs: stat("tabs").textContent,\n'
                  '  meta: meta.textContent,\n'
                  '  targets: REQUESTS.map((r) => r.target) });\n')
    assert report['unplanned'] == [], report
    assert report['targets'] == ['/tabs'], report
    assert report['tabs'] == '2', report
    assert report['meta'] == '2', report


def test_without_a_token_the_refresh_writes_the_dash_and_asks_nothing(_tmp):
    """`getToken()` is read before the fetch, and the 8000 ms interval is
    the same `refreshTabs` the mount called, so a token removed under a
    running dashboard is the way to see the guard's two halves apart. The
    stat holds the count the mount's own fetch wrote, so the dash that
    replaces it is this guard's write and not the markup's own default; and
    the meta bar, which the guard never reaches, keeps the count it had."""
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
    """The `catch` is on the fetch alone, so a 500 writes the same dash the
    no-token path writes and touches nothing the successful path wrote: the
    meta-bar count survives a refused refresh, because the query that
    updates it is below the `await` that threw. The stat is driven to `4`
    by a `tabs-synced` event first, so the dash after the refresh is the
    catch's own write over a value the markup never held. The rejection is
    rendered rather than logged, so the console recorder is asserted empty."""
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
    """All four fields are spelled out in one row. A falsy `tabId` -- the
    number zero, and the empty id -- is a placeholder rather than a value,
    so the row reads `·` where the bridge sent nothing; `formatEvalWorld`
    falls the same way for an absent world, and passes an unknown one
    through, because the channel name is the section's to render and not a
    set it gets to reject."""
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
    """`ev.title || ev.url || ''` is a three-step fallback, so a tab with an
    empty title is offered by its url and a tab with neither is offered by
    the separator alone. A hundred-character title is `truncate`d at 80 --
    79 characters and the ellipsis, not a cut at the boundary."""
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
    """`events.length === 0` is the only signal that the placeholder is still
    on the log, and `unshift` plus `insertBefore` at the head put the newest
    row on top. An event type the switch does not know falls through to
    `JSON.stringify` of the event itself, which is what keeps a section
    added to the bus later readable in the log before it is named here."""
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
    """`MAX_EVENTS` trims the DOM, and the rate window is bounded by TIME
    alone -- 205 events on one frozen clock leave 200 rows and a rate of
    205. A cap shared with the rate would read `200ev/min` here, and one
    applied to the log at push time would leave the sixth event on the log
    instead of the two-hundred-and-fifth."""
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
    """`clearLog` empties the event array, wipes the log and writes the
    `cleared.` row. The rate window is a different array and is not touched,
    so a cleared log still reports every event of the last minute. The next
    event after a clear is the only thing that removes the `cleared.` row --
    and it does, because the emptied array is what says the log is not
    showing anything yet."""
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
    """The `__internal` guard is first in the listener, so an internal event
    reaches none of the four things behind it: no row, no rate, no `last`,
    and -- the arm a shallow assertion misses -- not the `tabs-synced`
    branch, which would otherwise overwrite the tab count the mount spent a
    fetch on. The operator's own event after it is rendered normally."""
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


def test_a_synced_event_rewrites_the_stat_and_every_meta_count(_tmp):
    """A `tabs-synced` event carries the count the bridge reported, and it
    goes to every `[data-meta="tab-count"]` element on the page -- here two
    of them, which is what a dashboard with the count in its bar and in its
    header actually has. A write to only the first would leave the second
    reading the count from before the sync."""
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
    """The window is a sliding sixty seconds, and it is written in two
    places: `bumpRate` filters it when an event arrives, and the parked
    2000 ms interval filters it when none does. Both are pinned here, and
    they are pinned apart -- the late event at t+70s is what reaches
    `bumpRate`'s own filter, and only the `drive.fire(3)` trims reach the
    interval's. Three events at t, t+30s and t+70s leave two in the window
    (the first is seventy seconds old), a trim at t+89s keeps both, the
    same trim one second later drops the t+30s one, and a trim at t+130s
    drops the rest. The 59/60 pairs are the boundary, reached by driving the
    clock the test assigned and never by waiting."""
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
    """Ids come from `drive.live()` in creation order, so 1 is the 8000 ms
    `refreshTabs`, 2 the 1000 ms `renderSession` and 3 the 2000 ms trim --
    and this case identifies each by what firing it does rather than by the
    number. The session clock is the one `setup` assigned, so the mount read
    `sessionStart` from it: 60 s renders `01:00` (both halves zero-padded)
    and 3725 s renders `62:05`, which a clock wrapped at an hour would not."""
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
                  'sectionReport({ timers: drive.live(), before, afterTabs,\n'
                  '  oneMinute, over: stat("session").textContent,\n'
                  '  meta: meta.textContent });\n')
    assert report['unplanned'] == [], report
    assert report['timers'] == [1, 2, 3], report
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
    """The timestamp is `fmtTime(Date.now())` read at the push, not at the
    mount and not at the report: two events on two different assigned clocks
    carry the two stamps, and neither is the session start."""
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
    """`fmtTime` renders `new Date(ms).toTimeString()`'s first eight
    characters, which is `HH:MM:SS` in whatever zone the child ran in. The
    expectation is computed here in that same zone rather than pinned to a
    literal, because the test supplies the instant and the zone is the
    host's."""
    return time.strftime('%H:%M:%S', time.localtime(ms / 1000))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashovw_')


if __name__ == '__main__':
    raise SystemExit(main())
