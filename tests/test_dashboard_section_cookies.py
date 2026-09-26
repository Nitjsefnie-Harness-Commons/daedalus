#!/usr/bin/env python3
"""The cookies panel, run rather than read.

`dashboard/sections/cookies.js` reads and edits the cookies of one origin,
so every command it sends carries a credential and a scope, and a field
that is missing, defaulted or trimmed away is a cookie written to the
wrong jar. The harness mounts the shipped section over the real
`dashboard/api.js` and the real `_util.js` in Node, drives its inputs and
its buttons, and reads the parsed body of every request beside the cells
the section rendered.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _dashsection_wave1 as shared  # noqa: E402
from _dashsection import run_scenario  # noqa: E402

SECTION = ('sections/cookies.js',)

LIST = (
    "const LIST = [\n"
    "  { name: 'sid', value: 'abc', domain: '.example.com', path: '/',\n"
    "    secure: true, httpOnly: true, sameSite: 'lax' },\n"
    "  { name: 'plain', value: 'v2',\n"
    "  domain: 'cdn.example.com', path: '/sub' },\n"
    "];\n"
)

SETTLED = ('await bounded(settle(), "after the click",'
           ' _dashnodeStepTimeoutMs);\n')


def scenario(body, *, setup=LIST, plan=shared.PLAN):
    """One child: seed the token, plan every answer, mount, then drive."""
    return ('(async () => {\n' + shared.SEED + shared.ANSWERS + shared.PRELUDE
            + shared.open_section(SECTION[0]) + plan + setup
            + 'const sub = new El("span");\n'
            + 'drive.selector("#s04 [data-sub]", sub);\n'
            + shared.MOUNT + body + '})().catch(leave);\n')


def _run(body, *, setup=LIST, plan=shared.PLAN):
    return run_scenario(scenario(body, setup=setup, plan=plan),
                        sections=SECTION)


def _list_for(query, how='click'):
    """Type a query and ask for the listing the way a scenario names it.

    `how` picks the trigger: the LIST button, or Enter in the query field,
    which reaches the same command through the field's own listener.
    """
    return ('container.find("[data-role=q]").value = "' + query + '";\n'
            + ('button("LIST").click();\n' if how == 'click'
               else 'pressEnter(container.find("[data-role=q]"));\n')
            + SETTLED)


def test_a_query_with_a_scheme_is_sent_as_a_url_and_not_a_domain(_tmp):
    """`q.includes('://')` is the whole discriminator, and the two keys
    are alternatives: sending both would leave the bridge to choose, so
    the case asserts the other key is absent from the parsed body."""
    report = _run(_list_for('https://example.com/app')
                  + 'report({ sub: sub.textContent,\n'
                    '  host: container.find("[data-role=table-host]")'
                    '.textContent });\n',
                  setup=LIST + "answer('cookies', { result: LIST });\n")
    sent = shared.commands(report)[0]
    assert sent['type'] == 'cookies', report
    assert sent.get('url') == 'https://example.com/app', report
    assert 'domain' not in sent, report
    assert report['sub'] == '2 cookie(s)', report
    assert report['unplanned'] == [], report


def test_a_bare_domain_is_sent_as_a_domain_and_not_a_url(_tmp):
    """The other direction, driven through the Enter key rather than the
    button, because the query field carries its own path to the same
    command and a listener wired to nothing would pass a click-only case."""
    report = _run(_list_for('example.com', how='enter')
                  + 'report({ rows: rowTexts(container.all()[0]) });\n',
                  setup=LIST + "answer('cookies', { result: LIST });\n")
    sent = shared.commands(report)[0]
    assert sent.get('domain') == 'example.com', report
    assert 'url' not in sent, report
    assert [row[:5] for row in report['rows'][1:]] == [
        ['sid', 'abc', '.example.com', '/', 'S·H·l'],
        ['plain', 'v2', 'cdn.example.com', '/sub', ''],
    ], report


def test_the_flags_cell_joins_what_is_set_with_a_middle_dot(_tmp):
    """`sameSite` is indexed as a string, so `lax` renders as its first
    letter and a cookie with no flags at all renders an empty cell: the
    join is U+00B7 with no spaces, and a falsy flag contributes nothing
    rather than a placeholder."""
    report = _run(_list_for('example.com')
                  + 'report({ rows: rowTexts(container.all()[0]) });\n',
                  setup=LIST + "answer('cookies', { result: LIST });\n")
    flags = [row[4] for row in report['rows'][1:]]
    assert flags == ['S·H·l', ''], report
    assert flags[0].index('·') == 1, report


def test_a_value_longer_than_the_cap_is_truncated_with_an_ellipsis(_tmp):
    """`truncate(c.value || '', 140)` cuts at 140 characters INCLUDING the
    ellipsis, so a 141-character value arrives as its first 139 plus one
    and a 140-character value is left whole."""
    report = _run(_list_for('a.test')
                  + 'report({ rows: rowTexts(container.all()[0]) });\n',
                  setup='const wide = { name: "wide",'
                        ' value: "x".repeat(141),\n'
                        '  domain: "a.test", path: "/" };\n'
                        'const exact = { name: "exact",'
                        ' value: "y".repeat(140),\n'
                        '  domain: "a.test", path: "/" };\n'
                        "answer('cookies', { result: [wide, exact] });\n")
    cells = report['rows'][1:]
    assert len(cells[0][1]) == 140, report
    assert cells[0][1].endswith('…'), report
    assert cells[0][1].index('…') == 139, report
    assert cells[1][1] == 'y' * 140, report


def test_set_sends_no_flag_field_and_an_untrimmed_value(_tmp):
    """The form sets defaults and says so, so none of the four flag
    fields goes on the wire -- their absence is the contract, so the case
    asserts each key is not in the body. The value is the one field read
    raw: a value whose spaces are part of it arrives with them."""
    report = _run('container.find("[data-role=q]").value = "a.test";\n'
                  'container.find("[data-role=su]")'
                  '.value = " https://shop.example.com/ ";\n'
                  'container.find("[data-role=sn]").value = " k ";\n'
                  'container.find("[data-role=sv]").value = "  pad  ";\n'
                  'container.find("[data-role=sp]").value = "";\n'
                  'button("SET").click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=LIST + "answer('cookies', { result: [] });\n"
                               "answer('set-cookie', { result: {} });\n")
    sent = shared.commands(report)[0]
    assert sent['type'] == 'set-cookie', report
    assert sent['url'] == 'https://shop.example.com/', report
    assert sent['name'] == 'k', report
    assert sent['value'] == '  pad  ', report
    assert sent['path'] == '/', report
    # The domain input was left blank, so the field is absent rather than
    # sent as an empty string.
    assert 'domain' not in sent, report
    for absent in ('httpOnly', 'secure', 'sameSite', 'expirationDate'):
        assert absent not in sent, (absent, report)
    assert report['toasts'] == [{'type': 'ok', 'text': 'set k'}], report


def test_set_sends_the_domain_when_one_was_typed(_tmp):
    """The counterpart: a domain that was typed is sent, so the pair of
    cases between them says the field is present exactly when it holds
    something."""
    report = _run('container.find("[data-role=q]").value = "a.test";\n'
                  'container.find("[data-role=su]")'
                  '.value = "https://shop.example.com/";\n'
                  'container.find("[data-role=sn]").value = "k";\n'
                  'container.find("[data-role=sd]").value = "  .a.test  ";\n'
                  'container.find("[data-role=sp]").value = "/deep";\n'
                  'button("SET").click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=LIST + "answer('cookies', { result: [] });\n"
                               "answer('set-cookie', { result: {} });\n")
    sent = shared.commands(report)[0]
    assert sent['domain'] == '.a.test', report
    assert sent['path'] == '/deep', report
    assert shared.types(report) == ['set-cookie', 'cookies'], report


def test_a_set_without_a_url_or_a_name_sends_nothing(_tmp):
    """The guard is on the two trimmed fields, so an empty one is refused
    before the bridge is asked."""
    report = _run('container.find("[data-role=sv]").value = "v";\n'
                  'button("SET").click();\n' + SETTLED
                  + 'report({ toasts: toasts() });\n',
                  setup=LIST + "answer('set-cookie', { result: {} });\n")
    assert shared.commands(report) == [], report
    assert report['requests'] == [], report
    assert report['toasts'] == [{'type': 'warn',
                                 'text': 'url + name are required'}], report


def test_an_empty_query_asks_the_bridge_for_nothing(_tmp):
    """`load()` refuses an empty query, and the table host keeps the hint
    it shipped with -- nothing was rendered because nothing was asked."""
    report = _run('button("LIST").click();\n' + SETTLED
                  + 'report({ toasts: toasts(), sub: sub.textContent,\n'
                    '  host: container.find("[data-role=table-host]")'
                    '.textContent });\n',
                  setup=LIST + "answer('cookies', { result: LIST });\n")
    assert report['requests'] == [], report
    assert report['sub'] == '', report
    assert report['host'] == 'enter a domain/url and click LIST.', report
    assert report['toasts'] == [{'type': 'warn',
                                 'text': 'enter domain or url'}], report


def test_a_removed_row_reloads_in_silence_and_a_failed_one_toasts(_tmp):
    """The two halves of one handler. On success there is no toast at all
    and the listing is fetched again, so an operator sees the row leave;
    on failure the toast names the refusal and the listing is NOT
    refetched, so the row they are looking at does not vanish under an
    error."""
    ok = _run(_list_for('a.test')
              + 'const host = container.find("[data-role=table-host]");\n'
              'const del = button("remove", host);\n'
              'del.click();\n' + SETTLED
              + 'report({ toasts: toasts(), sub: sub.textContent });\n',
              setup=LIST + "answer('cookies', { result: LIST });\n"
                           "answer('remove-cookie', { result: {} });\n")
    assert shared.types(ok) == ['cookies', 'remove-cookie', 'cookies'], ok
    assert ok['toasts'] == [], ok
    assert ok['sub'] == '2 cookie(s)', ok
    # The url is rebuilt from the cookie: the scheme follows `secure`, the
    # domain's leading dot is dropped and the path is carried.
    removed = shared.commands(ok)[1]
    assert removed['type'] == 'remove-cookie', ok
    assert removed['url'] == 'https://example.com/', ok
    assert removed['name'] == 'sid', ok

    plan = (LIST
            + "answer('cookies', { result: LIST });\n"
            + "answer('remove-cookie', { error: 'cookie not found' });\n")
    bad = _run(_list_for('a.test')
               + 'const host = container.find("[data-role=table-host]");\n'
               + 'const del = button("remove", host);\n'
               + 'del.click();\n' + SETTLED
               + 'report({ toasts: toasts() });\n',
               setup=plan)
    assert shared.types(bad) == ['cookies', 'remove-cookie'], bad
    assert bad['toasts'] == [{'type': 'err',
                              'text': 'cookie not found'}], bad


def test_clear_all_arms_before_it_sends_and_reports_the_removed_count(
        _tmp):
    """`armedAction`'s first click only arms, and the count in the toast
    is the bridge's own `removed`, not the number of rows the panel was
    showing."""
    report = _run('container.find("[data-role=q]").value = "a.test";\n'
                  'const clear = button("clear all");\n'
                  'clear.click();\n' + SETTLED
                  + 'const armed = { text: clear.textContent,\n'
                    '  has: clear.classList.contains("armed"),\n'
                    '  sent: REQUESTS.length };\n'
                  'clear.click();\n' + SETTLED
                  + 'report({ armed, toasts: toasts() });\n',
                  setup=LIST + "answer('cookies', { result: [] });\n"
                               "answer('clear-cookies',"
                               " { result: { removed: 4 } });\n")
    assert report['armed'] == {'text': 'confirm clear all', 'has': True,
                               'sent': 0}, report
    assert shared.types(report) == ['clear-cookies', 'cookies'], report
    bulk = shared.commands(report)[0]
    assert bulk['domain'] == 'a.test', report
    assert 'url' not in bulk, report
    assert report['toasts'] == [{'type': 'ok', 'text': 'cleared 4'}], report


def test_clear_all_with_an_empty_query_sends_nothing(_tmp):
    """The bulk path repeats the query guard with its own wording, so a
    bulk clear refused for an empty query never reaches the bridge."""
    report = _run('const clear = button("clear all");\n'
                  'clear.click();\n' + SETTLED
                  + 'const armed = clear.textContent;\n'
                  'clear.click();\n' + SETTLED
                  + 'report({ armed, toasts: toasts() });\n',
                  setup=LIST + "answer('clear-cookies',"
                               " { result: { removed: 0 } });\n")
    assert report['armed'] == 'confirm clear all', report
    assert report['requests'] == [], report
    assert report['toasts'] == [{'type': 'warn',
                                 'text': 'enter domain or url first'}], \
        report


def test_an_absent_result_renders_the_empty_state_with_a_zero_count(_tmp):
    """`render(cookies || [])` is the defence, so a command that answers
    nothing renders the empty state and the counter reads zero -- and the
    counter never singularises, so one cookie reads `1 cookie(s)`."""
    empty = _run(_list_for('a.test')
                 + 'report({ sub: sub.textContent,\n'
                   '  host: container.find("[data-role=table-host]")'
                   '.textContent });\n',
                 setup="answer('cookies', { result: null });\n")
    assert empty['sub'] == '0 cookie(s)', empty
    assert empty['host'] == 'no cookies.', empty
    one_cookie = ("answer('cookies',"
                  " { result: [{ name: 'k', value: 'v' }] });\n")
    one = _run(_list_for('a.test')
               + 'report({ sub: sub.textContent });\n',
               setup=one_cookie)
    assert one['sub'] == '1 cookie(s)', one


def test_a_failed_listing_renders_the_error_pane(_tmp):
    """`load()`'s catch clears the host and renders the message, so a
    refused listing does not leave the previous rows on screen behind a
    toast."""
    report = _run(_list_for('a.test')
                  + 'const host = container.find("[data-role=table-host]");\n'
                    + 'report({ sub: sub.textContent,\n'
                    + '  host: host.textContent,\n'
                    + '  pane: host.all().some((el) => '
                      'hasClass(el, "pane err")) });\n',
                  setup="answer('cookies',"
                        " { error: 'cookie store unavailable' });\n")
    assert report['pane'] is True, report
    assert report['host'] == 'cookie store unavailable', report
    # The counter is written inside `render`, which never ran.
    assert report['sub'] == '', report
    assert report['unplanned'] == [], report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashcook_')


if __name__ == '__main__':
    raise SystemExit(main())
