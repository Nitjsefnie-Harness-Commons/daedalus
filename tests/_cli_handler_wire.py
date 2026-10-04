"""The wire entries a CLI-handler test plans, and the answer it gets back.

`tests/_cli_dispatch.py` records what a handler sent as an entry carrying a
`via` key, and hands back the next record on a list the test supplied. Every
CLI-handler suite wrote that entry itself: one `def` per transport arm,
re-spelled in each of the suites that needed it, so a change to the recorded
shape reached some of the cases and not the others.

Every name says which arm the entry it builds travels on. `_api_put`,
`_api_get` and `_ext_cmd` spell the `via` / `method` pair the entry itself
carries; `_wait_result` spells the
`wait_for_result` arm and is the two-site variant that pins the tab to
`extension` (the four-argument one in `test_cli_eval_handlers.py` takes the
tab and is a different body, so it is not a copy of this); `_answered_result`
builds the record the recorder hands back rather than a request. They are
renamed because the short names are held already: `_put`, `_ext` and `_get`
belong to `tests/_mcp_tool_commands.py` with a different body, so a shared
helper does not adopt one of them.
"""


def _api_put(body):
    return {'via': 'api', 'method': 'PUT', 'path': '/command', 'body': body,
            'timeout': 30}


def _api_get(path):
    return {'via': 'api', 'method': 'GET', 'path': path, 'body': None,
            'timeout': 30}


def _answered_result(**over):
    base = {'id': 'job1', 'result': 'ok', 'error': None, 'ts': 1}
    return dict(base, **over)


def _wait_result(cmd_id, delivery, timeout, interval=0.5):
    return {'via': 'wait_for_result', 'id': cmd_id, 'tab': 'extension',
            'delivery': delivery, 'timeout': timeout, 'interval': interval}


def _ext_cmd(cmd_id, cmd_type, fields, timeout=10):
    return {'via': 'ext_cmd', 'id': cmd_id, 'type': cmd_type,
            'fields': fields, 'timeout': timeout}
