"""The wire entries a CLI-handler test plans, and the answer it gets back.

`tests/_cli_dispatch.py` records what a handler sent as an entry carrying a
`via` key, and hands back the next record on a list the test supplied. Every
CLI-handler suite wrote that entry itself: one `def` per transport arm,
re-spelled in each of the four suites that needed it, so a change to the
recorded shape reached some of the cases and not the others. They are here
so there is one copy of each entry to fix.

Every name says which arm the entry it builds travels on, and every name
is one `main` does not already bind. `_api_put`, `_api_get` and `_ext_cmd`
spell the `via` / `method` pair the entry itself carries; `_wait_result`
spells the `wait_for_result` arm and is the two-site variant that pins the
tab to `extension` (the four-argument one in `test_cli_eval_handlers.py`
takes the tab and is a different body, so it is not a copy of this);
`_answered_result` builds the record the recorder hands back rather than a
request. They are renamed because the short names are held already:
`_put`, `_ext` and `_get` belong to `tests/_mcp_tool_commands.py` with a
different body, and a shared helper that adopted one of them would make
that module an offender of it along with the suites that stayed
(`test_helper_reimplementation.py`).
"""


def _api_put(body):
    return {'via': 'api', 'method': 'PUT', 'path': '/command', 'body': body,
            'timeout': 30}
