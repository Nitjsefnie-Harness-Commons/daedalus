#!/usr/bin/env python3
"""`tab` routes to a server queue; `tabId` names a browser tab.

Sending the browser's own id as the routing field enqueues a command into a
queue nothing drains, and the bridge answers 200 either way — so nothing
fails, the command simply never arrives. The two analysers behind this read
every client in the tree, in Python and in JavaScript, and the test runs them
over it.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402
from _pyroute import (dict_assignments, payload_keys,  # noqa: E402
                      py_tab_routing_violations)
from _repo import ROOT  # noqa: E402


def test_positional_dict_copy_is_opaque_but_later_tab_write_is_tracked(tmp):
    """A positional dict source is opaque; later explicit keys are tracked."""
    del tmp
    tree = ast.parse(
        "cmd = dict(BASE)\n"
        "cmd['tab'] = tab_id\n"
        "api('PUT', '/command', cmd)\n")
    assert payload_keys(tree.body[0].value, {}) is None
    keys = dict_assignments(tree)['cmd']
    assert keys['tab'][0] == 2


def test_no_client_sends_the_browser_target_as_the_routing_field(tmp):
    r"""`tab` routes to a server queue; `tabId` names a browser tab.

    Overloading them is not hypothetical: screenshot, CDP and the tabs panel
    all sent the browser target as `tab`, which the server strips for routing,
    so those buttons silently captured the active tab instead of the selected
    one. One sender also wrote the target over the routing value and sent the
    command to a queue nothing drains.

    This checks the SENDERS rather than the wire. A test that builds the
    payload itself passes while a sender that builds it wrongly ships — which
    is exactly what happened to the first version of this fix: the wire test
    was green and `dashboard/sections/tabs.js` was still wrong.

    What is enforced: in statically resolved sender shapes in mcp_server.py,
    daedalus_cli/ and dashboard/, a typed extension command (one routed
    through ext_cmd/_ext_cmd/extCmd/runCommand, or sent to /command with a
    visible `type` key) may carry `tab` only as the literal 'extension'. The
    eval path is exempt by structure: eval payloads carry `code` instead of
    `type`, and eval genuinely routes by tab. Python payloads are followed
    through literals (annotated or not), `dict(...)`, subscript assignments,
    `update({...})`, `|= {...}`, source order and `if`/`else` branches.
    JavaScript inline literals, names initialized by object literals,
    aliases of those names, ternary initializers whose branches resolve,
    `Object.assign` writes, tracked object spreads, literal computed keys,
    same-name direct `tab` property assignments, calls to same-file helpers
    that return the object they built, and the third extCmd argument are
    checked in source order. An object handed to any other call stops being
    provable there, and an unprovable object reaching a sender is reported
    rather than trusted — a helper that writes through its parameter cannot
    be followed, so it is not silently believed.

    What is NOT enforced:
    - A name this scanner never saw assigned — a parameter, an import — is
      unknown rather than unprovable, and unknown stays silent. That is what
      keeps `extCmd('fetch-timings', opts)` and the `...opts` spread inside
      `extCmd` itself quiet; the third-argument check covers the override
      those spreads could carry.
    - A Python /command payload built by a non-`dict(...)` call or by
      `dict(...)` with a positional argument is skipped. An untracked
      `**spread` after a visible `type` is rejected, but an untracked spread
      with no visible `type` could introduce both `type` and `tab` without
      being recognized as a typed payload.
    - Python control flow other than straight-line code and `if`/`else` is
      not modeled. Assignments inside loops, `try`, `with`, or `match` may be
      inspected against incomplete state.
    - A Python dict rebound to or mutated through an opaque expression
      (`d = f()`, `d.update(f())`, `d |= g()`) is dropped from tracking from
      that point rather than trusted on stale keys; a later `d['tab'] = ...`
      is tracked again.
    - Computed keys other than string literals are skipped. Inline object
      spreads and spreads of tracked names, plus literal computed keys such
      as `['tab']`, are checked; a spread of an unknown name is skipped.
    - For a named runCommand object, `type`/`code` are read from its literal
      initializer; later property assignments to those two keys are not
      tracked. `.tab` and `['tab']` assignments are tracked through the name
      and through an alias of it.
    - A helper is resolved from its first declaration in the file, and only
      through three levels of helper calls.
    - JavaScript name state is file-wide and source-ordered, not
      block-scoped or execution-ordered: a call is judged against every
      same-named assignment that precedes it in the file — including ones in
      other functions — and an assignment written after the call is
      invisible to it even when it runs first.
    - The JavaScript mask does not understand regex literals: a literal
      containing a quote character (e.g. `s.replace(/['"]/g, '')`) would be
      read as the start of a string, blanking real code up to the next quote
      and under-reporting everything after it. The dashboard's regex literal
      patterns today are `/\s+/g`, `/^\./`, `/\s+/`,
      `/\.(png|jpe?g|gif|webp)$/i` and `/\.(png|jpe?g)$/i`; none contains a
      quote, so nothing is masked wrongly — but adding one with a quote
      silently weakens this guard.
    """
    senders_py = [ROOT / 'mcp_server.py',
                  *ROOT.glob('mcp_tools_*.py'),
                  *(ROOT / 'daedalus_cli').glob('*.py')]
    senders_js = sorted((ROOT / 'dashboard').rglob('*.js'))
    scanned_py = [p for p in senders_py if p.is_file()]
    # A floor, not a glob of whatever happens to exist: with the senders
    # moved aside the scan above finds nothing and passes vacuously.
    assert len(scanned_py) >= 18, (
        f'found {len(scanned_py)} Python senders (mcp_server.py + '
        'mcp_tools_*.py + daedalus_cli/*.py), expected at least 18 — '
        'one composition point, seven MCP tool modules, and ten CLI modules; '
        'the senders moved and this guard is stale')
    assert len(senders_js) >= 10, (
        f'found {len(senders_js)} dashboard .js files, expected at least '
        '10 — the senders moved and this guard is stale')
    violations = []
    for path in scanned_py:
        violations.extend(py_tab_routing_violations(
            path, path.relative_to(ROOT)))
    for path in senders_js:
        violations.extend(js_tab_routing_violations(
            path, path.relative_to(ROOT)))
    assert not violations, (
        'these senders pass a browser tab as the routing field `tab`; the '
        "browser target is `tabId` and a typed command routes to "
        "`tab: 'extension'`:\n" + '\n'.join(violations))

    # The scan above passing on a correct tree proves nothing by itself — the
    # previous two versions of this guard passed on the real tree while
    # missing every reversion an auditor tried. Each shape below is checked
    # against the same scanner functions the tree scan uses.
    reversions = [
        ('py', "async def f(chrome_tab):\n"
               "    fields = {}\n"
               "    fields['tab'] = str(chrome_tab)\n"
               "    return await _ext_cmd('_ss', 'screenshot', **fields)\n"),
        ('py', "async def f(tab_id):\n"
               "    fields = {}\n"
               "    fields['tab'] = tab_id\n"
               "    return await _ext_cmd('_ss', 'screenshot', **fields)\n"),
        ('py', "def f(args):\n"
               "    cmd = {'id': '_ss', 'type': 'screenshot', 'tab': 'extension'}\n"
               "    cmd[\"tab\"] = int(args.chrome_tab)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "async def f(t):\n"
               "    extra = {'tab': str(t)}\n"
               "    return await _ext_cmd('_cdp', 'cdp', **extra)\n"),
        ('js', "async function f() {\n"
               "  const fields = {};\n"
               "  fields.tab = Number(tabSel.value);\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "async function f(m, tid) {\n"
               "  await extCmd('cdp', { method: m.trim(), params: {}, tab: tid });\n"
               "}\n"),
        ('js', "async function f(tid) {\n"
               "  const f = { tab: tid };\n"
               "  await extCmd('cdp', f);\n"
               "}\n"),
        ('js', "async function f(tid) {\n"
               "  await extCmd('net-capture', { method: 'Network.enable',"
               " params: { maxTotalBufferSize: 10000000, maxResourceBufferSize:"
               " 5000000, maxPostDataSize: 65536 }, note: 'padding padding"
               " padding padding padding padding padding padding', tab: tid });\n"
               "}\n"),
        # The shapes a mutation sweep found this guard missing open on:
        # annotated assignments (cli.py and mcp_server.py spell every payload
        # `fields: dict = {...}` / `cmd: dict = {...}`) ...
        ('py', "def f(args):\n"
               "    cmd: dict = {'id': '_x', 'type': 'close-tab', 'tab': 'extension'}\n"
               "    cmd['tab'] = int(args.chrome_tab)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "async def f(t):\n"
               "    fields: dict = {'css': 'x'}\n"
               "    fields['tab'] = t\n"
               "    return await _ext_cmd('_css', 'inject-css', **fields)\n"),
        # ... payloads assembled without a literal in scope ...
        ('py', "def f(tid):\n"
               "    cmd = dict(id='_x', type='close-tab', tab=tid)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "def f(tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab'}\n"
               "    cmd.update({'tab': tid})\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "def f(tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab'}\n"
               "    cmd |= {'tab': tid}\n"
               "    api('PUT', '/command', cmd)\n"),
        # ... a later unrelated re-declaration erasing an earlier violation
        # (the live shape in dashboard/sections/cookies.js) ...
        ('js', "async function load() {\n"
               "  const fields = {};\n"
               "  fields.tab = Number(tabSel.value);\n"
               "  await extCmd('cookies', fields);\n"
               "}\n"
               "function later(q) {\n"
               "  const fields = { url: q };\n"
               "  extCmd('set-cookie', fields);\n"
               "}\n"),
        # ... and the bracket form, which must still be caught now that
        # comment/string mentions of it are filtered out.
        ('js', "async function f(tid) {\n"
               "  const fields = {};\n"
               "  fields['tab'] = tid;\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        # An opaque spread after the visible routing value can replace it at
        # runtime; visible keys must not make that spread look safe.
        ('py', "def f(tid):\n"
               "    spread = build_fields(tid)\n"
               "    cmd = {'id': '_x', 'type': 'close-tab',"
               " 'tab': 'extension', **spread}\n"
               "    api('PUT', '/command', cmd)\n"),
        # Calls are checked against the state that reaches the call, not the
        # flattened final assignment from a mutually exclusive branch.
        ('py', "def f(flag, tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab',"
               " 'tab': 'extension'}\n"
               "    if flag:\n"
               "        cmd['tab'] = tid\n"
               "        api('PUT', '/command', cmd)\n"
               "    else:\n"
               "        cmd['tab'] = 'extension'\n"),
        # Object spreads and literal computed keys have ordinary runtime
        # object semantics and therefore must participate in the scan.
        ('js', "async function f(tid) {\n"
               "  await extCmd('cdp', { ...{ ['tab']: tid } });\n"
               "}\n"),
        # runCommand accepts named objects as well as inline literals.
        ('js', "async function f(tid) {\n"
               "  const command = { type: 'cdp', tab: tid };\n"
               "  await runCommand(command);\n"
               "}\n"),
        # api.js applies opts after `tab: 'extension'`, so the third argument
        # can really retarget a typed command.
        ('js', "async function f(target) {\n"
               "  await extCmd('cdp', {}, { tab: target });\n"
               "}\n"),
        # Shapes that used to be disclosed gaps, each promoted here when it
        # started being caught: an alias, a helper writing through its
        # parameter, a ternary initializer, an Object.assign write, and a
        # helper that returns the object it built.
        ('js', "async function f(tid) {\n"
               "  const fields = {};\n"
               "  const alias = fields;\n"
               "  alias.tab = tid;\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "function addTab(target, tid) {\n"
               "  target.tab = tid;\n"
               "}\n"
               "async function f(tid) {\n"
               "  const fields = {};\n"
               "  addTab(fields, tid);\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "async function f(flag, tid) {\n"
               "  const fields = flag ? { tab: tid } : {};\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "async function f(tid) {\n"
               "  const fields = {};\n"
               "  Object.assign(fields, { tab: tid });\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        ('js', "function build(tid) {\n"
               "  const f = {};\n"
               "  f.tab = tid;\n"
               "  return f;\n"
               "}\n"
               "async function g(tid) {\n"
               "  await extCmd('screenshot', build(tid));\n"
               "}\n"),
    ]

    legitimate = [
        ('py', "async def f(cmd_id, code, tab_id):\n"
               "    payload = {'id': cmd_id, 'code': code}\n"
               "    payload['tab'] = tab_id\n"
               "    await _put('/command', payload)\n"),
        ('py', "async def f(chrome_tab):\n"
               "    return await _ext_cmd('_focus', 'focus-tab',"
               " tabId=int(chrome_tab))\n"),
        ('js', "async function f(tabId, code) {\n"
               "  await runCommand({ tab: tabId, code });\n"
               "}\n"),
        ('js', "async function f(tid) {\n"
               "  await extCmd('screenshot', { tabId: Number(tid) });\n"
               "}\n"),
        # An annotated assignment used correctly: the annotation changes
        # nothing, and tabId was never the routing field.
        ('py', "async def f(chrome_tab):\n"
               "    fields: dict = {'css': 'x'}\n"
               "    fields['tabId'] = int(chrome_tab)\n"
               "    return await _ext_cmd('_css', 'inject-css', **fields)\n"),
        # A bracket-form mention inside a comment is not code: it used to
        # crash the scanner (no later `=`) or invent a violation (a later
        # `let done = false;` supplied a garbage value span).
        ('js', "async function f(fields) {\n"
               "  // never do fields['tab'] = Number(tabSel.value) here\n"
               "  await extCmd('cookies', fields);\n"
               "}\n"),
        ('js', "async function f(q) {\n"
               "  const fields = { url: q };\n"
               "  // fields['tab'] = Number(tabSel.value) would be wrong\n"
               "  let done = false;\n"
               "  await extCmd('cookies', fields);\n"
               "}\n"),
    ]

    disclosed_js_limits = [
        # Name state is file-wide and source-ordered rather than scoped and
        # execution-ordered: an assignment written after the call is invisible
        # to it even when it runs first.
        ('assignment after the call that runs before it',
         "async function send() {\n"
         "  await extCmd('screenshot', fields);\n"
         "}\n"
         "const fields = { tab: 'not-extension' };\n"),
        # A name this scanner never saw assigned — a parameter, an import —
        # is unknown rather than unprovable, and unknown stays silent.
        ('fields arriving as a parameter',
         "async function f(fields) {\n"
         "  await extCmd('screenshot', fields);\n"
         "}\n"),
    ]
    fixture = Path(tmp) / 'sender'
    for i, (lang, src) in enumerate(reversions):
        path = fixture.with_suffix('.py' if lang == 'py' else '.js')
        path.write_text(src, encoding='utf-8')
        found = py_tab_routing_violations(path, f'reversion-{i}') if lang == 'py' \
            else js_tab_routing_violations(path, f'reversion-{i}')
        assert found, (
            f'reversion {i} was NOT caught — the guard asserts a contract it '
            f'does not enforce:\n{src}')
    for i, (lang, src) in enumerate(legitimate):
        path = fixture.with_suffix('.py' if lang == 'py' else '.js')
        path.write_text(src, encoding='utf-8')
        found = py_tab_routing_violations(path, f'legitimate-{i}') if lang == 'py' \
            else js_tab_routing_violations(path, f'legitimate-{i}')
        assert not found, (
            f'legitimate shape {i} was flagged — eval routing and `tabId` are '
            f'correct:\n{src}\n{found}')
    for i, (label, src) in enumerate(disclosed_js_limits):
        path = fixture.with_suffix('.js')
        path.write_text(src, encoding='utf-8')
        found = js_tab_routing_violations(path, f'disclosed-{i}')
        assert not found, (
            f'disclosed JavaScript limit {label!r} is now caught — remove or '
            f'narrow its docstring disclosure and promote this fixture to a '
            f'reversion:\n{src}\n{found}')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='tabrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
