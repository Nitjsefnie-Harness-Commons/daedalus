#!/usr/bin/env python3
"""Every ext-routing MCP tool reaches the real extension as a live row.

Relocated from test_mcp_server, which sat at its size baseline, so the table
has headroom. A completeness guard fails when a registered ext-routing tool
has no row, so the table cannot drift behind the registry.

The cases below the table are the other half of that headroom: live tool
behaviour a real bridge is the only thing that can see, and which the pinned
table drives through a double. They live here for the same reason the table
does — test_mcp_server has none of the room and never will.
"""
import asyncio
import base64
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_load as mcp  # noqa: E402
import _mcp_tool_commands  # noqa: E402
import _util  # noqa: E402
from _queueread import queued_command  # noqa: E402

TOK = mcp.TOK
BRIDGE_ENV = mcp.BRIDGE_ENV
_load_mcp = mcp._load_mcp
_need_deps = mcp._need_deps
_answer_mcp_command = mcp._answer_mcp_command
_surface_responder_errors = mcp.surface_responder_errors


def _ext_routing_tools():
    """Registered tools whose pinned command routes a typed ext command.

    TOOL_COMMANDS is the registry-backed inventory: test_mcp_tools pins its
    keys equal to the registered tool set, so a tool is in scope exactly when
    one of its expected calls is an 'ext_cmd'.
    """
    return {
        name
        for name, cases in _mcp_tool_commands.TOOL_COMMANDS.items()
        if any(expected[0] == 'ext_cmd'
               for _overrides, calls in cases
               for expected in calls)
    }


def _row(cmd_type, fields, build):
    return cmd_type, fields, build


LIVE_ROWS = {
    'open_tab': _row('open-tab', {'url': 'https://example.com'},
                     lambda mod: lambda: mod.open_tab(
                         'https://example.com', wait=False)),
    'open_tabs': _row('open-tabs', {'urls': ['https://example.com/a']},
                      lambda mod: lambda: mod.open_tabs(
                          ['https://example.com/a'], wait=False)),
    'focus_tab': _row('focus-tab', {'tabId': 7},
                      lambda mod: lambda: mod.focus_tab(7, wait=False)),
    'close_tab': _row('close-tab', {'tabId': 5},
                      lambda mod: lambda: mod.close_tab([5], wait=False)),
    'ext_navigate': _row('navigate', {'url': 'https://example.com'},
                         lambda mod: lambda: mod.ext_navigate(
                             'https://example.com', wait=False)),
    'ext_reload': _row('reload', {}, lambda mod: lambda: mod.ext_reload(
        wait=False)),
    'get_cookies': _row('cookies', {'domain': 'example.com'},
                        lambda mod: lambda: mod.get_cookies(
                            domain='example.com', wait=False)),
    'set_cookie': _row('set-cookie',
                       {'url': 'https://example.com', 'name': 'sid',
                        'value': 'abc'},
                       lambda mod: lambda: mod.set_cookie(
                           'https://example.com', 'sid', 'abc', wait=False)),
    'remove_cookie': _row('remove-cookie',
                          {'url': 'https://example.com', 'name': 'sid'},
                          lambda mod: lambda: mod.remove_cookie(
                              'https://example.com', 'sid', wait=False)),
    'clear_cookies': _row('clear-cookies', {'domain': 'example.com'},
                          lambda mod: lambda: mod.clear_cookies(
                              domain='example.com', wait=False)),
    'inject_css': _row('inject-css', {'css': 'a{color:red}'},
                       lambda mod: lambda: mod.inject_css(
                           'a{color:red}', wait=False)),
    'remove_css': _row('remove-css', {'css': 'a{color:red}'},
                       lambda mod: lambda: mod.remove_css(
                           'a{color:red}', wait=False)),
    'block_requests': _row('block-requests', {'pattern': '*.example/*'},
                           lambda mod: lambda: mod.block_requests(
                               '*.example/*', wait=False)),
    'unblock_requests': _row('unblock-requests', {},
                             lambda mod: lambda: mod.unblock_requests(
                                 wait=False)),
    'list_block_rules': _row('list-block-rules', {},
                             lambda mod: lambda: mod.list_block_rules(
                                 wait=False)),
    'store_hotfix': _row('store-hotfix',
                         {'fixId': 'fix1', 'code': 'console.log(1)',
                          'match': '*://*.example.com/*'},
                         lambda mod: lambda: mod.store_hotfix(
                             'fix1', 'console.log(1)',
                             match='*://*.example.com/*', wait=False)),
    'clear_hotfix': _row('clear-hotfix', {'fixId': 'fix1'},
                         lambda mod: lambda: mod.clear_hotfix(
                             'fix1', wait=False)),
    'clear_hotfixes': _row('clear-all-hotfixes', {},
                           lambda mod: lambda: mod.clear_hotfixes(
                               wait=False)),
    'list_hotfixes': _row('list-hotfixes', {},
                          lambda mod: lambda: mod.list_hotfixes(
                              wait=False)),
    'set_permanent': _row('set-permanent',
                          {'fixId': 'fix1', 'permanent': True},
                          lambda mod: lambda: mod.set_permanent(
                              'fix1', True, wait=False)),
    'net_capture': _row('net-capture', {},
                        lambda mod: lambda: mod.net_capture(wait=False)),
    'net_capture_stop': _row('net-capture-stop', {},
                             lambda mod: lambda: mod.net_capture_stop(
                                 wait=False)),
    'net_capture_get': _row('net-capture-get', {},
                            lambda mod: lambda: mod.net_capture_get(
                                wait=False)),
    'cdp': _row('cdp', {'method': 'Page.enable', 'params': {}},
                lambda mod: lambda: mod.cdp('Page.enable', wait=False)),
    'fetch_timings': _row('fetch-timings', {},
                          lambda mod: lambda: mod.fetch_timings(wait=False)),
    'ext_self_reload': _row('ext-reload', {},
                            lambda mod: lambda: mod.ext_self_reload(
                                wait=False)),
    # screenshot: the default form shares the ext-routing path; the
    # include_image form fetches bytes over get_raw and is pinned by
    # test_screenshot_returns_the_bytes_its_own_result_named.
    'screenshot': _row('screenshot', {'format': 'png'},
                       lambda mod: lambda: mod.screenshot(wait=False)),
    'allow_segment_origin': _row(
        'allow-segment-origin', {'origin': 'https://example.com'},
        lambda mod: lambda: mod.allow_segment_origin(
            'https://example.com', wait=False)),
    'revoke_segment_origin': _row(
        'revoke-segment-origin', {'origin': 'https://example.com'},
        lambda mod: lambda: mod.revoke_segment_origin(
            'https://example.com', wait=False)),
    'list_segment_origins': _row('list-segment-origins', {},
                                 lambda mod: lambda: (
                                     mod.list_segment_origins(wait=False))),
}


def test_every_ext_routing_tool_has_a_live_bridge_row(_tmp):
    """A registered ext-routing tool with no live row fails, by name."""
    routing = _ext_routing_tools()
    covered = set(LIVE_ROWS)
    missing = sorted(routing - covered)
    extra = sorted(covered - routing)
    assert not missing and not extra, (
        f'live-bridge table coverage mismatch: missing rows {missing}; '
        f'rows for non-ext-routing tools {extra}')


def test_every_mcp_command_tool_sends_its_documented_command(tmp):
    """Each MCP tool reaches the extension as the command it claims.

    The MCP surface is a second sender of the CLI's wire protocol and can
    disagree about a `type` or field name unnoticed; this pins the wire, read
    back from the command the bridge itself reported enqueuing. That report is
    what a caller that must answer the command has to work from, so the file
    the bridge published is checked against it here rather than polled for.
    """
    _need_deps()
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        mod = _load_mcp(base)
        # what daedalus_mcp.auth.BearerAuth does per request
        mod._token.set(TOK)
        queue = Path(docroot) / 'commands' / f'{TOK}_extension'
        for name, (cmd_type, fields, build) in LIVE_ROWS.items():
            # A file an earlier iteration left behind is the OLDER of the
            # two, so comparing the first would pass against the wrong
            # command for the rest of the loop. Empty the queue before the
            # send instead of unlinking after it.
            for leftover in queue.glob('*.json'):
                leftover.unlink()
            _value, queued = _answer_mcp_command(
                base, docroot, mod, build(mod), {})
            assert queued.get('type') == cmd_type, (name, cmd_type, queued)
            # Routing is consumed at enqueue time, so the queue a command was
            # read from is what proves it addressed the extension worker.
            assert 'tab' not in queued, (name, cmd_type, queued)
            for key, expected in fields.items():
                assert queued.get(key) == expected, (
                    name, cmd_type, key, queued)
            # The reported command is the file the bridge published, not a
            # reconstruction of it, and this queue is empty before the send
            # — so a comparison that held would be a comparison against
            # this row's own file, never a leftover's.
            published = sorted(queue.glob('*.json'))
            assert len(published) == 1, (name, published, queued)
            stored = json.loads(published[0].read_text(encoding='utf-8'))
            assert stored == queued, (name, stored, queued)


def test_a_waited_eval_round_trip_reaches_the_extension(tmp):
    """`wait=True` still answers its result, live.

    The branch's headline claim is that a waited send is byte-for-byte what
    it was, and rewriting the harness to send unwaited took the last live
    EVAL round trip with it: the pinned table answers through a double, and
    the typed path survives live only through the coalesced second call in
    test_client_credentials. The extension simulator answers a tab-targeted
    command exactly as it answers a broadcast one.
    """
    _need_deps()
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        mod = _load_mcp(base)
        mod._token.set(TOK)
        qdir = Path(docroot) / 'commands' / f'{TOK}_waited'
        failure = []

        def extension():
            try:
                command = queued_command(qdir, 'the waited command')
                status, body = _util.post_json(base + '/result', {
                    'token': TOK, 'tabId': 'waited', 'id': command['id'],
                    'result': 'waited value', 'error': None, 'ts': 1,
                    'world': 'page-main', '_did': command['_did']})
                if status != 200:
                    failure.append((status, body))
            except Exception as exc:  # test-thread diagnosis, surfaced below
                failure.append(exc)

        responder = threading.Thread(target=extension)
        responder.start()
        with _surface_responder_errors(responder, failure, 30):
            evaluated = asyncio.run(mod.exec(
                tab_id='waited', cmd_id='_waited', code='1 + 1'))

    # The envelope comes back whole, with `value` grafted; the two per-run
    # fields in it are the bridge's own and are not pinned.
    assert evaluated['id'] == '_waited', evaluated
    assert evaluated['tabId'] == 'waited', evaluated
    assert evaluated['world'] == 'page-main', evaluated
    assert evaluated['value'] == 'waited value', evaluated
    assert evaluated['error'] is None, evaluated


def test_an_image_returning_tool_reaches_the_caller_through_the_manager(tmp):
    """The conversion boundary a return annotation crosses.

    A tool with a return annotation gets an output schema, and the server
    then runs its answer through `dump_python(..., mode='json')`. `Image` has
    no JSON form, so annotating an image-returning tool's return turns every
    such call into `UnexpectedToolError` — while the bare function, which is
    what the rest of the tree drives, still returns `[meta, Image]` and reads
    green. Only the manager's own path is the one an MCP client takes.
    """
    _need_deps()
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        mod = _load_mcp(base)
        mod._token.set(TOK)
        qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
        failure = []

        def extension():
            try:
                command = queued_command(qdir, 'the screenshot command')
                payload = b'this-invocation'
                status, body = _util.post_json(base + '/upload', {
                    'token': TOK, 'id': '_ss', 'filename': 'shot.png',
                    'data': base64.b64encode(payload).decode()})
                if status != 200:
                    failure.append(('upload', status, body))
                status, body = _util.post_json(base + '/result', {
                    'token': TOK, 'tabId': 'extension', 'id': command['id'],
                    'result': {'path': f'{TOK}/_ss/shot.png',
                               'size': len(payload)},
                    'error': None, 'ts': 1, '_did': command['_did']})
                if status != 200:
                    failure.append((status, body))
            except Exception as exc:  # test-thread diagnosis, surfaced below
                failure.append(exc)

        responder = threading.Thread(target=extension)
        responder.start()
        with _surface_responder_errors(responder, failure, 30):
            answer = asyncio.run(mod.mcp.call_tool(
                'screenshot', {'include_image': True, 'timeout': 25}))

        schemas = {tool.name: tool.output_schema
                   for tool in asyncio.run(mod.mcp.list_tools())}
        # Unannotated is what keeps the schema off, and the schema is what
        # puts the conversion in the caller's way.
        assert schemas['screenshot'] is None, schemas['screenshot']
        assert [item.type for item in answer.content] == ['text', 'image'], (
            answer.content)


def test_a_nonpositive_timeout_admits_no_command_on_either_send_path(tmp):
    """The pre-PUT refusal, on the eval path and the typed one.

    `poll_result` evaluates the deadline only after the command has been
    submitted, so a non-positive timeout polled zero times, raised a timeout
    for a command the browser has already been handed, and left the caller
    believing nothing ran. `ext_cmd` validates on the same terms, and a guard
    on one path and not the other is the same failure one level down.
    """
    _need_deps()
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        mod = _load_mcp(base)
        mod._token.set(TOK)
        for name, send in (('exec', lambda timeout: mod.exec(
                cmd_id='_timeout', code='1', timeout=timeout)),
                ('screenshot', lambda timeout: mod.screenshot(
                    timeout=timeout))):
            for timeout in (0, -1.0, float('nan'), float('inf')):
                try:
                    asyncio.run(send(timeout))
                except ValueError as error:
                    assert 'finite positive' in str(error), (
                        name, timeout, error)
                else:
                    raise AssertionError(
                        f'{name}: timeout {timeout!r} was accepted')
        for target in (f'{TOK}_extension', TOK):
            qdir = Path(docroot) / 'commands' / target
            queued = sorted(qdir.glob('*.json')) if qdir.is_dir() else []
            assert queued == [], (target, queued)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
