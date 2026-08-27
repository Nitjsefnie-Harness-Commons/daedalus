#!/usr/bin/env python3
"""Per-mcp_server bridge binding for registered tool closures."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import test_mcp_server  # noqa: E402


def test_registered_tool_closures_keep_their_own_bridge(tmp):
    """A later registration cannot redirect an earlier module's tools."""
    test_mcp_server._need_deps()
    first_tmp = Path(tmp) / 'first'
    second_tmp = Path(tmp) / 'second'
    with _util.bridge(first_tmp) as (first_base, _first_docroot):
        status, body = _util.post_json(first_base + '/sync-tabs', {
            'token': test_mcp_server.TOK,
            'tabs': [{'tabId': 'first-tab',
                      'url': 'https://first.example.com',
                      'title': 'first'}],
        })
        assert status == 200, (status, body)
        status, body = _util.post_json(first_base + '/result', {
            'token': test_mcp_server.TOK,
            'id': 'first-eval',
            'result': 'first',
            'error': None,
            'ts': 1,
        })
        assert status == 200, (status, body)

        with _util.bridge(second_tmp) as (second_base, _second_docroot):
            status, body = _util.post_json(second_base + '/sync-tabs', {
                'token': test_mcp_server.TOK,
                'tabs': [{'tabId': 'second-tab',
                          'url': 'https://second.example.com',
                          'title': 'second'}],
            })
            assert status == 200, (status, body)
            status, body = _util.post_json(second_base + '/result', {
                'token': test_mcp_server.TOK,
                'id': 'second-eval',
                'result': 'second',
                'error': None,
                'ts': 1,
            })
            assert status == 200, (status, body)

            first_mod = test_mcp_server._load_mcp(first_base)
            second_mod = test_mcp_server._load_mcp(second_base)
            first_mod._token.set(test_mcp_server.TOK)
            second_mod._token.set(test_mcp_server.TOK)

            async def call_registered_tools():
                first_tabs = await first_mod.list_tabs()
                first_eval = await first_mod.result()
                second_tabs = await second_mod.list_tabs()
                second_eval = await second_mod.result()
                return {
                    'first': (first_tabs[0]['title'], first_eval['value']),
                    'second': (second_tabs[0]['title'], second_eval['value']),
                }

            observed = asyncio.run(call_registered_tools())
            expected = {
                'first': ('first', 'first'),
                'second': ('second', 'second'),
            }
            assert observed == expected, (
                'registered tools reached the wrong bridge: '
                f'expected {expected}, got {observed}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='mcptools_'))
