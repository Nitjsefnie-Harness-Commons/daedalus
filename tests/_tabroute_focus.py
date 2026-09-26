"""The real target for a routing-guard row: a real `do_focus_tab` body,
executed for real and then read by the guard.

A suite that reports a routing violation has to show it against code
that runs, not a fixture describing what the guard thinks runs. This
mutates the body of the real `do_focus_tab` in
`daedalus_cli/commands_browser.py`, executes the result against
recording stubs, and answers `(runtime_calls, guard_violations)` — the
two halves a guard has to agree with.

It lives in a `_`-prefixed module rather than beside the suites that use
it: a suite importing a sibling suite re-executes that suite's whole
module body, and a private helper reached that way is a copy rather than
the shared one.
"""
import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from _pyroute import py_tab_routing_violations
from _repo import ROOT


def _tracked_focus_verdict(tmp, body, before='', after='', counts=False):
    source = ROOT / 'daedalus_cli' / 'commands_browser.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(
        node, ast.FunctionDef) and node.name == 'do_focus_tab')
    function.body = ast.parse(body).body
    before_nodes, after_nodes = (ast.parse(value).body
                                 for value in (before, after))
    future_nodes = [node for node in before_nodes if isinstance(
        node, ast.ImportFrom) and node.module == '__future__']
    before_nodes = [node for node in before_nodes if node not in future_nodes]
    tree.body[1:1] = future_nodes
    index = tree.body.index(function)
    tree.body[index:index] = before_nodes
    tree.body.extend(after_nodes)
    ast.fix_missing_locations(tree)
    mutated = Path(tmp) / 'commands_browser.py'
    mutated.write_text(ast.unparse(tree) + '\n', encoding='utf-8')
    calls = []

    def ext_cmd(*args, **kwargs):
        calls.append((args, kwargs))
        return 0

    def tab_sink(*args, **kwargs):
        if any('tab' in payload for payload in (*args, *kwargs.values())):
            calls.append((args, kwargs))
        return 0

    namespace = {'ext_cmd': ext_cmd,
                 'ordinary': lambda *args, **kwargs: 0, 'tab_sink': tab_sink,
                 '_args': SimpleNamespace(
                     chrome_tab=323, flag=True, values=(1, 2))}
    sender_module = ModuleType('_pyroute_test_sender')
    sender_module.__dict__.update(namespace)
    sys.modules['_pyroute_test_sender'] = sender_module
    isolated = ast.fix_missing_locations(ast.Module(
        body=[*future_nodes, *before_nodes, function, *after_nodes],
        type_ignores=[]))
    try:
        # pylint: disable-next=exec-used
        exec(compile(isolated, str(mutated), 'exec'), namespace)
        if not after_nodes: namespace['do_focus_tab'](namespace['_args'])
    finally: sys.modules.pop('_pyroute_test_sender', None)
    verdict = (len(calls), len(py_tab_routing_violations(
        mutated, mutated.name)))
    return verdict if counts else tuple(bool(value) for value in verdict)
