#!/usr/bin/env python3
"""Minimal MCP client for manual verification of daedalus MCP tools.

Usage:
  TOKEN=<bridge-token> python3 scripts/mcp_probe.py list
  TOKEN=<bridge-token> python3 scripts/mcp_probe.py call <tool> [json-args]
  TOKEN=<bridge-token> DAEDALUS_MCP_URL=http://127.0.0.1:8086/mcp python3 scripts/mcp_probe.py ...
"""
import json, os, sys, uuid
import httpx

URL = os.environ.get('DAEDALUS_MCP_URL', 'http://127.0.0.1:8086/mcp')
TOKEN = os.environ.get('TOKEN', '')
if not TOKEN:
    sys.exit('TOKEN env var required')


def rpc(client, session_id, method, params=None):
    payload = {'jsonrpc': '2.0', 'id': str(uuid.uuid4()), 'method': method}
    if params is not None:
        payload['params'] = params
    headers = {
        'Authorization': f'Bearer {TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
    }
    if session_id:
        headers['Mcp-Session-Id'] = session_id
    r = client.post(URL, json=payload, headers=headers)
    r.raise_for_status()
    sid = r.headers.get('Mcp-Session-Id', session_id)
    ct = r.headers.get('content-type', '')
    if 'text/event-stream' in ct:
        for line in r.text.splitlines():
            if line.startswith('data: '):
                return json.loads(line[6:]), sid
    return r.json(), sid


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'list'
    with httpx.Client(timeout=60) as c:
        _, sid = rpc(c, None, 'initialize', {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'mcp_probe', 'version': '0'},
        })
        rpc(c, sid, 'notifications/initialized')
        if action == 'list':
            data, _ = rpc(c, sid, 'tools/list')
            for t in data.get('result', {}).get('tools', []):
                print(f'{t["name"]:30}  {t.get("description", "")[:80]}')
        elif action == 'call':
            tool = sys.argv[2]
            args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
            data, _ = rpc(c, sid, 'tools/call', {'name': tool, 'arguments': args})
            print(json.dumps(data.get('result', data), indent=2, ensure_ascii=False))
        else:
            sys.exit(f'unknown action: {action}')


if __name__ == '__main__':
    main()
