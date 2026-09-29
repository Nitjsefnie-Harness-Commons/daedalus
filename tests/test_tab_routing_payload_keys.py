#!/usr/bin/env python3
"""A payload key spelled as a NAME names the key its binding names.

The payload model read a dict-literal key only when it was a string
`ast.Constant`, and a subscript store only on the same test, so a key
bound to a name first contributed no tracked key at all and the payload
read as one carrying no `tab`. That is fail-open: the send is real and the
guard says nothing about it.

`payload_literal_key` folds the position instead of refusing it, and
the fold decides the verdict, because the three answers are three
different runtimes. A string names a tracked key. A non-string literal
is provably not `'tab'` and names none. An expression that will not
fold names none without becoming opaque, which is what keeps the
discriminating rows clean instead of over-reported. That is a claim
about what this reader resolves and not about the runtime - the shapes
a compile-time fold resolves there are #1352.

Every row states the RUNTIME truth beside the guard verdict, and the
truth is not a claim about the row: each module is executed and the
kwargs its own `ext_cmd` receives is what the verdict is measured
against. A row that stated only a verdict could drift from the runtime
and still pass, and a stated truth that stopped being true would fail
here before it was compared to anything.
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
    # Verdict time: written at the call, so only the fold can see the name.
    ('inline-at-call', 1, {'tab': 5}, _CALL, 'def f(args):\n' + _SENDER
     + '    k = "tab"\n'
       "    return ext_cmd('PUT', '/command', **{k: 5})\n"),
    # A member of its own domain the first cut did not consider: a walrus
    # in KEY position carries a real tab at runtime, as the name it binds.
    ('walrus-key', 1, {'tab': 5}, _CALL, _inside('cmd = {(k := "tab"): 5}')),
    ('literal', 1, {'tab': 5}, _CALL, _inside("cmd = {'tab': 5}")),
    # A key the program removes before the send is not a tracked key, and
    # the runtime agrees. Every spelling folds the same key position, and a
    # `clear` names no key and removes them all.
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
    # they all remove `tab` itself, so dropping one key and dropping every
    # key are the same answer on all of them. These remove the other key,
    # so the `tab` survives to the sender and the guard still reports - a
    # fold that took the whole payload's keys would read them clean.
    ('pop-non-tab-literal', 1, {'tab': 5}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'cmd.pop("id")')),
    ('pop-non-tab-name', 1, {'tab': 5}, _CALL, _inside(
        'k = "id"', 'cmd = {"id": "x", "tab": 5}', 'cmd.pop(k)')),
    ('del-non-tab-literal', 1, {'tab': 5}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'del cmd["id"]')),
    ('del-non-tab-name', 1, {'tab': 5}, _CALL, _inside(
        'k = "id"', 'cmd = {"id": "x", "tab": 5}', 'del cmd[k]')),
    # A non-string key is provably not 'tab', and the splat raises before
    # the call returns, so nothing reaches the sender at all.
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

# Two boundaries this change does not cross. Every row below carries a real
# `tab` to the sender while reading clean; the truth is stated because the
# guard cannot see it, which is what makes each a separate defect rather
# than a member this change claims to close.
#
# `state.literals` is empty in a nested body, so a binding made OUTSIDE it
# is unresolved, and the container model's `_literal_key` reads the same
# table under the same boundary (#1341). The walrus rows are the other:
# `walrus-key` reads the walrus in key position, which this reader does
# consider, but the table does not carry the walrus's BINDING, so a name
# it binds resolves nowhere after it (#1343).
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
    """The browser-argument stand-in the rows read a tab id off."""

    chrome_tab = 323


def _rows():
    return _ROWS + _CROSS_SCOPE


def _sent(call, source):
    """What the row's own `ext_cmd` receives when the module is run."""
    scope = {}
    # pylint: disable=exec-used
    exec(compile(source, '<payload-key-row>', 'exec'), scope)
    try:
        # pylint: disable-next=eval-used
        return eval(call, dict(scope, ARGS=_Args()))
    except TypeError:
        return _RAISES


def _verdict(tmp, label, source):
    """How many findings the guard reports for one written module."""
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
    """`dict_assignments` forgets a deleted name, as the flow's writer does.

    The two writers of the name-to-literal table are one per reader and
    have to agree; `ast.Delete` is where they used to differ, this one
    returning early where `_pyroute_mapping` forgets the name. A key
    spelled with a name the program has deleted is a key the program
    cannot spell at all, so it must resolve to nothing.
    """
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
