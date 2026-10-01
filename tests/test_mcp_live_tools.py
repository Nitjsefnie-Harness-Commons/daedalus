#!/usr/bin/env python3
"""Every ext-routing MCP tool reaches the real extension as a live row.

Relocated from test_mcp_server, which sat at its size baseline, so the table
has headroom. A completeness guard fails when a registered ext-routing tool
has no row, so the table cannot drift behind the registry.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_load as mcp  # noqa: E402
import _mcp_tool_commands  # noqa: E402
import _util  # noqa: E402

TOK = mcp.TOK
BRIDGE_ENV = mcp.BRIDGE_ENV
_load_mcp = mcp._load_mcp
_need_deps = mcp._need_deps
_answer_mcp_command = mcp._answer_mcp_command


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
        for name, (cmd_type, fields, build) in LIVE_ROWS.items():
            value, queued = _answer_mcp_command(
                base, docroot, mod, build(mod), {})
            assert value == {'command': queued}, (name, value, queued)
            assert queued.get('type') == cmd_type, (name, cmd_type, queued)
            # Routing is consumed at enqueue time, so the queue a command was
            # read from is what proves it addressed the extension worker.
            assert 'tab' not in queued, (name, cmd_type, queued)
            for key, expected in fields.items():
                assert queued.get(key) == expected, (
                    name, cmd_type, key, queued)
            # The reported command is the file the bridge published, not a
            # reconstruction of it.
            queue = Path(docroot) / 'commands' / f'{TOK}_extension'
            published = sorted(queue.glob('*.json'))
            assert published, (name, cmd_type)
            stored = json.loads(published[0].read_text(encoding='utf-8'))
            assert stored == queued, (name, stored, queued)
            published[0].unlink()


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
