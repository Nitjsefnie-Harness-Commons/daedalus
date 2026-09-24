#!/usr/bin/env python3
"""Every ext-routing MCP tool reaches the real extension as a live row.

Relocated from test_mcp_server (which sat at its size baseline) so the table
has headroom. Each row drives one tool through a real bridge and pins the
command it puts on the wire, read back from the queue the bridge routed it
into. A completeness guard fails when a registered ext-routing tool has no
row, so the table cannot drift behind the registry.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _daedalus_env  # noqa: E402,F401
import _util  # noqa: E402
import _mcp_tool_commands  # noqa: E402
import test_mcp_server as mcp  # noqa: E402

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
                     lambda mod: lambda: mod.open_tab('https://example.com')),
    'open_tabs': _row('open-tabs', {'urls': ['https://example.com/a']},
                      lambda mod: lambda: mod.open_tabs(
                          ['https://example.com/a'])),
    'focus_tab': _row('focus-tab', {'tabId': 7},
                      lambda mod: lambda: mod.focus_tab(7)),
    'close_tab': _row('close-tab', {'tabId': 5},
                      lambda mod: lambda: mod.close_tab([5])),
    'ext_navigate': _row('navigate', {'url': 'https://example.com'},
                         lambda mod: lambda: mod.ext_navigate(
                             'https://example.com')),
    'ext_reload': _row('reload', {}, lambda mod: mod.ext_reload),
    'get_cookies': _row('cookies', {'domain': 'example.com'},
                        lambda mod: lambda: mod.get_cookies(
                            domain='example.com')),
    'set_cookie': _row('set-cookie',
                       {'url': 'https://example.com', 'name': 'sid',
                        'value': 'abc'},
                       lambda mod: lambda: mod.set_cookie(
                           'https://example.com', 'sid', 'abc')),
    'remove_cookie': _row('remove-cookie',
                          {'url': 'https://example.com', 'name': 'sid'},
                          lambda mod: lambda: mod.remove_cookie(
                              'https://example.com', 'sid')),
    'clear_cookies': _row('clear-cookies', {'domain': 'example.com'},
                          lambda mod: lambda: mod.clear_cookies(
                              domain='example.com')),
    'inject_css': _row('inject-css', {'css': 'a{color:red}'},
                       lambda mod: lambda: mod.inject_css('a{color:red}')),
    'remove_css': _row('remove-css', {'css': 'a{color:red}'},
                       lambda mod: lambda: mod.remove_css('a{color:red}')),
    'block_requests': _row('block-requests', {'pattern': '*.example/*'},
                           lambda mod: lambda: mod.block_requests(
                               '*.example/*')),
    'unblock_requests': _row('unblock-requests', {},
                             lambda mod: mod.unblock_requests),
    'list_block_rules': _row('list-block-rules', {},
                             lambda mod: mod.list_block_rules),
    'store_hotfix': _row('store-hotfix',
                         {'fixId': 'fix1', 'code': 'console.log(1)'},
                         lambda mod: lambda: mod.store_hotfix(
                             'fix1', 'console.log(1)')),
    'clear_hotfix': _row('clear-hotfix', {'fixId': 'fix1'},
                         lambda mod: lambda: mod.clear_hotfix('fix1')),
    'clear_hotfixes': _row('clear-all-hotfixes', {},
                           lambda mod: mod.clear_hotfixes),
    'list_hotfixes': _row('list-hotfixes', {}, lambda mod: mod.list_hotfixes),
    'set_permanent': _row('set-permanent',
                          {'fixId': 'fix1', 'permanent': True},
                          lambda mod: lambda: mod.set_permanent('fix1', True)),
    'net_capture': _row('net-capture', {}, lambda mod: mod.net_capture),
    'net_capture_stop': _row('net-capture-stop', {},
                             lambda mod: mod.net_capture_stop),
    'net_capture_get': _row('net-capture-get', {},
                            lambda mod: mod.net_capture_get),
    'cdp': _row('cdp', {'method': 'Page.enable', 'params': {}},
                lambda mod: lambda: mod.cdp('Page.enable')),
    'fetch_timings': _row('fetch-timings', {}, lambda mod: mod.fetch_timings),
    'ext_self_reload': _row('ext-reload', {},
                            lambda mod: mod.ext_self_reload),
    # screenshot: the default form sends one ext_cmd and awaits a result, so
    # it shares the ext-routing path. The include_image form is a different
    # shape (it fetches bytes over get_raw after the result) and is pinned by
    # test_screenshot_returns_the_bytes_its_own_result_named instead.
    'screenshot': _row('screenshot', {'format': 'png'},
                       lambda mod: lambda: mod.screenshot()),
    'allow_segment_origin': _row(
        'allow-segment-origin', {'origin': 'https://example.com'},
        lambda mod: lambda: mod.allow_segment_origin('https://example.com')),
    'revoke_segment_origin': _row(
        'revoke-segment-origin', {'origin': 'https://example.com'},
        lambda mod: lambda: mod.revoke_segment_origin('https://example.com')),
    'list_segment_origins': _row('list-segment-origins', {},
                                 lambda mod: mod.list_segment_origins),
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
    disagree about a `type` or field name unnoticed; this pins the wire,
    read back from the queue the bridge routed it into.
    """
    _need_deps()
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        mod = _load_mcp(base)
        # what daedalus_mcp.auth.BearerAuth does per request
        mod._token.set(TOK)
        for name, (cmd_type, fields, build) in LIVE_ROWS.items():
            _value, queued = _answer_mcp_command(
                base, docroot, mod, build(mod), {})
            assert queued.get('type') == cmd_type, (name, cmd_type, queued)
            # Routing is consumed at enqueue time, so the queue a command was
            # read from is what proves it addressed the extension worker.
            assert 'tab' not in queued, (name, cmd_type, queued)
            for key, value in fields.items():
                assert queued.get(key) == value, (name, cmd_type, key, queued)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
