#!/usr/bin/env python3
"""A payload key spelled as a NAME names the key its binding names.

Each row states the RUNTIME truth beside the guard verdict, observed by
executing the row rather than claimed, and a clean row reads clean
because the RUNTIME agrees - not because the fold gave up on the
position. That is what makes each a control, not a snapshot. The fold's
boundary is `_pyroute_keys`'s (#1352).
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute import dict_assignments, py_tab_routing_violations  # noqa: E402

_SENDER = "    def ext_cmd(*a, **k): return k\n"
_SPREAD = "    return ext_cmd('PUT', '/command', **cmd)\n"
_CALL = 'f(ARGS)'
_RAISES = 'raises TypeError'


def _inside(*lines, tail=_SPREAD):
    return 'def f(args):\n' + _SENDER + ''.join(
        f'    {line}\n' for line in lines) + tail


_ROWS = [
    ('spread', 1, {'tab': 5}, _CALL, _inside('k = "tab"', 'cmd = {k: 5}')),
    ('subscript', 1, {'id': 'x', 'tab': 5}, _CALL, _inside(
        'k = "tab"', 'cmd = {"id": "x"}', 'cmd[k] = 5')),
    ('dict-star', 1, {'tab': 5}, _CALL, _inside(
        'k = "tab"', 'cmd = dict(**{k: 5})')),
    ('dict-star-twostep', 1, {'tab': 5}, _CALL, _inside(
        'k = "tab"', 'd = {k: 5}', 'cmd = dict(**d)')),
    ('update', 1, {'tab': 5}, _CALL, _inside(
        'k = "tab"', 'cmd = {}', 'cmd.update({k: 5})')),
    ('update-twostep', 1, {'tab': 5}, _CALL, _inside(
        'k = "tab"', 'd = {k: 5}', 'cmd = {}', 'cmd.update(d)')),
    ('ior', 1, {'tab': 5}, _CALL, _inside(
        'k = "tab"', 'cmd = {}', 'cmd |= {k: 5}')),
    ('beside-spread', 1, {'id': 'x', 'tab': 5}, _CALL, _inside(
        'k = "tab"', 'other = {"id": "x"}', 'cmd = {**other, k: 5}')),
    ('inline-at-call', 1, {'tab': 5}, _CALL, 'def f(args):\n' + _SENDER
     + '    k = "tab"\n'
       "    return ext_cmd('PUT', '/command', **{k: 5})\n"),
    ('walrus-key', 1, {'tab': 5}, _CALL, _inside('cmd = {(k := "tab"): 5}')),
    ('literal', 1, {'tab': 5}, _CALL, _inside("cmd = {'tab': 5}")),
    # A removed key is not a tracked key; a `clear` names none.
    ('del-literal', 0, {'id': 'x'}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'del cmd["tab"]')),
    ('del-name', 0, {'id': 'x'}, _CALL, _inside(
        'k = "tab"', 'cmd = {"id": "x", k: 5}', 'del cmd[k]')),
    ('pop-literal', 0, {'id': 'x'}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'cmd.pop("tab")')),
    ('pop-name', 0, {'id': 'x'}, _CALL, _inside(
        'k = "tab"', 'cmd = {"id": "x", k: 5}', 'cmd.pop(k)')),
    ('pop-default', 0, {'id': 'x'}, _CALL, _inside(
        'k = "tab"', 'cmd = {"id": "x", k: 5}', 'cmd.pop(k, None)')),
    ('clear-literal', 0, {}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'cmd.clear()')),
    ('clear-name', 0, {}, _CALL, _inside(
        'k = "tab"', 'cmd = {"id": "x", k: 5}', 'cmd.clear()')),
    # A removal takes the ONE key it names, which no row above can see:
    # they all remove `tab` itself, so dropping one and every key agree.
    ('pop-non-tab-literal', 1, {'tab': 5}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'cmd.pop("id")')),
    ('pop-non-tab-name', 1, {'tab': 5}, _CALL, _inside(
        'k = "id"', 'cmd = {"id": "x", "tab": 5}', 'cmd.pop(k)')),
    ('del-non-tab-literal', 1, {'tab': 5}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'del cmd["id"]')),
    ('del-non-tab-name', 1, {'tab': 5}, _CALL, _inside(
        'k = "id"', 'cmd = {"id": "x", "tab": 5}', 'del cmd[k]')),
    # The splat raises first, so nothing reaches the sender at all.
    ('nonstring', 0, _RAISES, _CALL, _inside(
        "cmd = {5: 'x', 'tab': 'extension'}")),
    ('other-string', 0, {'type': 'focus-tab'}, _CALL, _inside(
        'k = "type"', 'cmd = {k: "focus-tab"}')),
    ('rebound', 0, {'id': 5}, _CALL, _inside(
        'k = "tab"', 'k = "id"', 'cmd = {k: 5}')),
    ('routed', 0, {'tab': 'extension'}, _CALL, _inside(
        'k = "tab"', "cmd = {k: 'extension'}")),
    ('unfoldable', 0, {'323': 5}, _CALL, _inside(
        'k = str(args.chrome_tab)', 'cmd = {k: 5}')),
    ('ctor', 1, {'tab': 5}, _CALL, 'def f(args):\n' + _SENDER
     + '    k = "tab"\n    cmd = dict([(k, 5)])\n'
       '    return ext_cmd("PUT", "/command", **cmd)\n'),
]

# Two boundaries this change does not cross. Each row below carries a real
# `tab` to the sender while reading clean: the literals table is empty in
# a nested body (#1341) and carries no walrus BINDING (#1343).
_CROSS_SCOPE = [
    ('module-scope', 0, {'tab': 5}, _CALL,
     'TAB = "tab"\n' + _inside('cmd = {TAB: 5}')),
    ('default-argument', 0, {'tab': 5}, _CALL,
     'def f(args, key="tab"):\n' + _SENDER + '    cmd = {key: 5}\n' + _SPREAD),
    ('parameter', 0, {'tab': 5}, "f(ARGS, 'tab')",
     'def f(args, key):\n' + _SENDER + '    cmd = {key: 5}\n' + _SPREAD),
    ('walrus-binding-then-name-key', 0, {'tab': 5}, _CALL, _inside(
        '(w := "tab")', 'k = w', 'cmd = {k: 5}')),
    ('walrus-binding-then-subscript', 0, {'tab': 5}, _CALL, _inside(
        'cmd = {}', '(k := "tab")', 'cmd[k] = 5')),
]


class _Args:
    chrome_tab = 323


def _rows():
    return _ROWS + _CROSS_SCOPE


def _sent(call, source):
    scope = {}
    # pylint: disable=exec-used
    exec(compile(source, '<payload-key-row>', 'exec'), scope)
    try:
        # pylint: disable-next=eval-used
        return eval(call, dict(scope, ARGS=_Args()))
    except TypeError:
        return _RAISES


def _verdict(tmp, label, source):
    path = Path(tmp) / f'{label}.py'
    path.write_text(source, encoding='utf-8')
    return len(py_tab_routing_violations(path, f'{label}.py'))


def test_every_row_states_the_runtime_it_actually_has(tmp):
    wrong = []
    for label, _, sent, call, source in _rows():
        observed = _sent(call, source)
        if observed != sent:
            wrong.append((label, observed, sent))
    assert wrong == [], wrong


def test_a_name_key_that_names_tab_is_reported(tmp):
    quiet = [label for label, expected, _, _, source in _rows()
             if expected and not _verdict(tmp, label, source)]
    assert not quiet, quiet


def test_every_row_the_runtime_clears_still_reads_clean(tmp):
    loud = []
    for label, expected, _, _, source in _rows():
        if not expected and _verdict(tmp, label, source):
            loud.append(label)
    assert loud == [], loud


def test_a_delete_drops_the_literal_the_other_writer_drops(tmp):
    """The two literal-table writers are one per reader and must agree."""
    path = Path(tmp) / 'deleted-name.py'
    path.write_text('k = "tab"\n'
                    'cmd = {k: 5}\n'
                    'del k\n'
                    'other = {k: 5}\n', encoding='utf-8')
    found = dict_assignments(ast.parse(path.read_text(encoding='utf-8')))
    assert 'tab' in found['cmd']
    assert found['other'] == {}


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='payloadkey_')


if __name__ == '__main__':
    raise SystemExit(main())
