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
    payload = {'jsonrpc': '2.0', 'method': method}
    # A notification carries no id. With one, the transport reads it as a
    # request and answers -32601 Method not found, so the handshake the
    # probe performed was initialize followed by a malformed request.
    if not method.startswith('notifications/'):
        payload['id'] = str(uuid.uuid4())
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
    # A notification has no id to answer, so the transport acknowledges it
    # with 202 and nothing else. Reading that nothing as JSON is a traceback
    # before the first tool is listed.
    if r.status_code == 202 or not r.content.strip():
        return None, sid
    ct = r.headers.get('content-type', '')
    if 'text/event-stream' in ct:
        for line in r.text.splitlines():
            if line.startswith('data: '):
                return json.loads(line[6:]), sid
    return r.json(), sid


def answer(client, session_id, method, params=None):
    """rpc() for a request, whose answer must carry a body."""
    data, sid = rpc(client, session_id, method, params)
    if data is None:
        sys.exit(f'{method}: the server answered with no body')
    return data, sid


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'list'
    with httpx.Client(timeout=60) as c:
        _, sid = answer(c, None, 'initialize', {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'mcp_probe', 'version': '0'},
        })
        rpc(c, sid, 'notifications/initialized')
        if action == 'list':
            data, _ = answer(c, sid, 'tools/list')
            for t in data.get('result', {}).get('tools', []):
                print(f'{t["name"]:30}  {t.get("description", "")[:80]}')
        elif action == 'call':
            tool = sys.argv[2]
            args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
            data, _ = answer(
                c, sid, 'tools/call', {'name': tool, 'arguments': args})
            print(json.dumps(data.get('result', data), indent=2, ensure_ascii=False))
        else:
            sys.exit(f'unknown action: {action}')


if __name__ == '__main__':
    main()
