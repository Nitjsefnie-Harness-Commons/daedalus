#!/usr/bin/env python3
"""No MCP tool accepts a host path, and none may leak host files.

Split from tests/test_mcp_server.py: the live-surface control that
refuses every path-bearing tool call and plants exfiltration sentinels
against list_hotfixes.
"""
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _mcp_load  # noqa: E402
from _queueread import queued_command  # noqa: E402

DEPS = _mcp_load.DEPS
if DEPS:
    import logging
    logging.getLogger('httpx').setLevel(
        logging.WARNING)  # quiet per-request logs
    logging.getLogger('mcp').setLevel(logging.WARNING)  # quiet mcp INFO logs


def _relative_or_synthetic(target):
    """A traversal-shaped path to `target`, or a synthetic one if impossible.

    Windows puts the temporary directory and the checkout on different
    drives, and os.path.relpath raises rather than answering across a mount
    boundary; the caller needs only a path-shaped argument the MCP tools
    must refuse, so whether it resolves to the sentinel is beside the point.
    """
    try:
        return os.path.relpath(target, _util.ROOT)
    except ValueError:
        return os.path.join(*(['..'] * 6), *target.parts[-2:])


def test_live_mcp_has_no_server_path_authority(tmp):
    """A bearer can submit inline source but cannot make MCP read host paths.

    The schema check covers every tool that formerly accepted a path. The
    store/list round trip then exercises the original exfiltration chain with
    an absolute path, traversal, and a symlink: every call must be rejected,
    and none of the sentinel source may come back through list_hotfixes.
    """
    _mcp_load._need_deps()
    if importlib.util.find_spec('uvicorn') is None:
        _util.skip('uvicorn not installed — MCP thread cannot serve')

    root = Path(tmp)
    absolute_secret = root / 'absolute-secret.js'
    traversal_secret = root / 'traversal-secret.js'
    symlink_secret = root / 'symlink-secret.js'
    sentinels = ('ABSOLUTE_MCP_SENTINEL', 'TRAVERSAL_MCP_SENTINEL',
                 'SYMLINK_MCP_SENTINEL')
    for path, sentinel in zip(
            (absolute_secret, traversal_secret, symlink_secret), sentinels):
        path.write_text(sentinel, encoding='utf-8')
    link = root / 'source-link.js'
    try:
        link.symlink_to(symlink_secret)
    except OSError as exc:
        _util.skip(f'symlink creation unavailable: {type(exc).__name__}')

    traversal = _relative_or_synthetic(traversal_secret)
    symlink_escape = _relative_or_synthetic(link)
    attempted_paths = (str(absolute_secret), traversal, symlink_escape)
    with _util.bridge(tmp, env=_mcp_load.BRIDGE_ENV) as (base, docroot):
        _, port = _mcp_load._start_mcp_in_process(base)
        session_id = _mcp_load._open_mcp_session(port)
        qdir = Path(docroot) / 'commands' / f'{_mcp_load.TOK}_extension'
        handled = set()
        stored = []
        simulator_errors = []
        stop = threading.Event()

        def extension_simulator():
            deadline = time.time() + 20
            try:
                while not stop.is_set() and time.time() < deadline:
                    for command_path in sorted(qdir.glob('*.json')):
                        if command_path.name in handled:
                            continue
                        command = queued_command(
                            qdir, 'the hotfix command', exclude=handled)
                        handled.add(command_path.name)
                        if command.get('type') == 'store-hotfix':
                            stored.append({
                                'id': command['fixId'],
                                'ts': 1,
                                'code': command['code'],
                            })
                            result = {
                                'stored': command['fixId'],
                                'total': len(stored),
                                'permanent': False,
                            }
                        elif command.get('type') == 'list-hotfixes':
                            result = {'version': '0.18.0', 'fixes': stored}
                        else:
                            continue
                        status, body = _util.post_json(base + '/result', {
                            'token': _mcp_load.TOK,
                            'tabId': 'extension',
                            'id': command['id'],
                            'result': result,
                            'error': None,
                            'ts': 1,
                            '_did': command['_did'],
                        })
                        if status != 200:
                            simulator_errors.append((status, body))
                    time.sleep(0.02)
            except Exception as exc:  # test-thread diagnosis, surfaced below
                simulator_errors.append(exc)

        simulator = threading.Thread(target=extension_simulator)
        simulator.start()
        with _mcp_load.surface_responder_errors(
                simulator, simulator_errors, 20, stop):
            replies = [
                _mcp_load._call_mcp_tool(
                    port, session_id, f'path-{index}', 'store_hotfix',
                    {'fix_id': f'path-{index}', 'path': path})
                for index, path in enumerate(attempted_paths)
            ]
            listed = _mcp_load._call_mcp_tool(
                port, session_id, 'list-after-paths', 'list_hotfixes')
            status, _unused, raw = _mcp_load._mcp_request(
                port,
                {'jsonrpc': '2.0', 'id': 'schemas', 'method': 'tools/list',
                 'params': {}},
                session_ids=(session_id,))
            assert status == 200, (status, raw)
            schemas = _mcp_load._mcp_payload(raw)['result']['tools']

        path_tools = {'put': 'code', 'inject_css': 'css',
                      'remove_css': 'css', 'store_hotfix': 'code'}
        by_name = {tool['name']: tool for tool in schemas}
        schema_failures = []
        for name, inline_field in path_tools.items():
            schema = by_name[name]['inputSchema']
            properties = schema.get('properties', {})
            if 'path' in properties or inline_field not in properties:
                schema_failures.append(name)
        returned = json.dumps(listed, ensure_ascii=False)
        rejected = [reply.get('result', {}).get('isError') is True
                    for reply in replies]
        leaked = [sentinel for sentinel in sentinels if sentinel in returned]
        assert simulator_errors == [], simulator_errors
        assert all(rejected) and not schema_failures and not leaked, {
            'path_calls_rejected': rejected,
            'schemas_with_path_authority': schema_failures,
            'recovered_sentinels': leaked,
        }


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
