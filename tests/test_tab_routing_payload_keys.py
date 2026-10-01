#!/usr/bin/env python3
"""A payload key spelled as a NAME names the key its binding names.

The guard read a dict-literal key only when it was a string
`ast.Constant`, so a key bound to a name contributed no tracked key at
all and a real send read as carrying no `tab`: fail-open, silent on the
traffic the guard exists to police.

`payload_literal_key` folds the position instead of refusing it, and the
fold decides the verdict, because the three answers are three different
runtimes. A string names a tracked key; a non-string literal is provably
not `'tab'` and names none; an expression that will not fold names none
without going opaque, which is what keeps the discriminating rows clean
instead of over-reported. That is a claim about what THIS reader
resolves - the shapes a compile-time fold resolves are #1352.

Each row states the RUNTIME truth beside the guard verdict, observed by
executing the row rather than claimed, and a clean row reads clean
because the RUNTIME agrees - not because the fold gave up on the
position. That is what makes each a control, not a snapshot.
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
_RAISES_VALUE = 'raises ValueError'


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
    # Written at the call itself, so no binding table is consulted and
    # only the fold can see the name.
    ('inline-at-call', 1, {'tab': 5}, _CALL, 'def f(args):\n' + _SENDER
     + '    k = "tab"\n'
       "    return ext_cmd('PUT', '/command', **{k: 5})\n"),
    # A walrus in KEY position folds inside this reader, whether or not the
    # table carries the name it binds, and carries a real `tab` at runtime.
    ('walrus-key', 1, {'tab': 5}, _CALL, _inside('cmd = {(k := "tab"): 5}')),
    ('literal', 1, {'tab': 5}, _CALL, _inside("cmd = {'tab': 5}")),
    # Removal, in seven spellings of one behaviour: `del`, `pop` and
    # `pop` with a default, each by literal and by name, then `clear` in
    # both. They fold the same key position, so the block buys spelling
    # coverage and not seven distinct positions. A `clear` names no key
    # and so takes every one of them.
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
    # A removal takes the ONE key it names, and no row above can see it:
    # every one of those removes `tab` itself, so dropping one key and
    # dropping every key score the same there. These remove the OTHER
    # key instead, so `tab` survives to the sender while the guard still
    # reports - a fold that swept the whole payload's keys would read
    # them clean.
    ('pop-non-tab-literal', 1, {'tab': 5}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'cmd.pop("id")')),
    ('pop-non-tab-name', 1, {'tab': 5}, _CALL, _inside(
        'k = "id"', 'cmd = {"id": "x", "tab": 5}', 'cmd.pop(k)')),
    ('del-non-tab-literal', 1, {'tab': 5}, _CALL, _inside(
        'cmd = {"id": "x", "tab": 5}', 'del cmd["id"]')),
    ('del-non-tab-name', 1, {'tab': 5}, _CALL, _inside(
        'k = "id"', 'cmd = {"id": "x", "tab": 5}', 'del cmd[k]')),
    # A non-string key is provably not `'tab'`, and the splat raises
    # before the call returns, so nothing reaches the sender at all.
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
    # The binding forms a table enumerating only `=` leaves out. Each
    # reaches the sender carrying a `tab` as a name a key position may
    # spell, so a table carrying none of them reads a violation clean.
    ('walrus-binding-then-name-key', 1, {'tab': 5}, _CALL, _inside(
        '(w := "tab")', 'k = w', 'cmd = {k: 5}')),
    ('walrus-binding-then-subscript', 1, {'tab': 5}, _CALL, _inside(
        'cmd = {}', '(k := "tab")', 'cmd[k] = 5')),
    ('tuple-unpack', 1, {'tab': 5}, _CALL, _inside(
        'j, = ("tab",)', 'cmd = {j: 5}')),
    ('unpack-over-name', 1, {'tab': 5}, _CALL, _inside(
        'keys = ("tab",)', 'j, = keys', 'cmd = {j: 5}')),
    ('for-target-dictkey', 1, {'tab': 5}, _CALL, _inside(
        'for j in ("tab",): cmd = {j: 5}')),
    ('for-target-block', 1, {'tab': 5}, _CALL, 'def f(args):\n' + _SENDER
     + '    for j in ("tab",):\n        cmd = {j: 5}\n' + _SPREAD),
    ('for-target-over-name', 1, {'tab': 5}, _CALL, _inside(
        'keys = ("tab",)', 'for j in keys: cmd = {j: 5}')),
    # The same three forms binding another key, and a later binding taking
    # the later value: a writer that recorded the form and then let an `=`
    # or a second turn of the loop stand would over-report all of these.
    ('walrus-binds-other', 0, {'id': 5}, _CALL, _inside(
        '(w := "id")', 'k = w', 'cmd = {k: 5}')),
    ('tuple-unpack-other', 0, {'id': 5}, _CALL, _inside(
        'j, = ("id",)', 'cmd = {j: 5}')),
    ('for-target-other', 0, {'id': 5}, _CALL, _inside(
        'for j in ("id",): cmd = {j: 5}')),
    ('rebound-after-walrus', 0, {'id': 5}, _CALL, _inside(
        '(w := "tab")', 'k = w', 'k = "id"', 'cmd = {k: 5}')),
    ('rebound-after-unpack', 0, {'id': 5}, _CALL, _inside(
        'j, = ("tab",)', 'j = "id"', 'cmd = {j: 5}')),
    ('rebound-after-loop', 0, {'id': 5}, _CALL, _inside(
        'for j in ("tab",): pass', 'j = "id"', 'cmd = {j: 5}')),
    # A loop target carries the union of its iterable, so two elements
    # that differ name no key and two that agree do. Parts that do not
    # line up with their value raise before the target binds anything.
    ('loop-union', 0, {'id': 5}, _CALL, _inside(
        'for j in ("tab", "id"): cmd = {j: 5}')),
    ('loop-union-agrees', 1, {'tab': 5}, _CALL, _inside(
        'for j in ("tab", "tab"): cmd = {j: 5}')),
    ('unpack-mismatch', 0, _RAISES, _CALL, _inside(
        'j, k = 5', 'cmd = {j: 5}')),
    # A `*` part binds a LIST, which no key position can name, so the name
    # must end the statement holding nothing rather than what it held
    # before. The prior binding is what makes that observable: a writer that
    # dropped the arm would leave the name on its old literal. A
    # destructured target whose parts outnumber its value raises before it
    # binds any of them, so its names end unbound.
    ('unpack-star-binds-list', 0, _RAISES, _CALL, _inside(
        'b = "tab"', 'a, *b = ("z", "tab")', 'cmd = {b: 5}')),
    ('unpack-sequence-arity', 0, _RAISES_VALUE, _CALL, _inside(
        'j, k = ("tab",)', 'cmd = {j: 5}')),
    # One statement that binds a name and reads it: the walrus sits in a
    # VALUE position, so the key is read from the table as the statement
    # began and `tab` reaches the sender as a value. A writer that runs
    # before the statement's own keys are resolved sees the table as the
    # statement ends, and reports a key the program never spells.
    ('order-key-then-walrus', 0, {'id': 'tab'}, _CALL, _inside(
        'k = "id"', 'cmd = {k: (k := "tab")}')),
    ('order-update-then-walrus', 0, {'id': 'tab'}, _CALL, _inside(
        'k = "id"', 'cmd = {}', 'cmd.update({k: (k := "tab")})')),
    # A walrus in a comprehension binds in the scope the comprehension
    # stands in, on every supported version and in either clause, so the
    # table carries it and the key it names resolves. All four forms
    # positively; both clauses in both directions, in list and dict.
    ('comp-list-element', 1, {'tab': 5}, _CALL, _inside(
        '[(k := "tab") for _ in [1]]', 'cmd = {k: 5}')),
    ('comp-list-element-other', 0, {'id': 5}, _CALL, _inside(
        '[(k := "id") for _ in [1]]', 'cmd = {k: 5}')),
    ('comp-dict-element', 1, {'tab': 5}, _CALL, _inside(
        '{(k := "tab"): 1 for _ in [1]}', 'cmd = {k: 5}')),
    ('comp-dict-element-other', 0, {'id': 5}, _CALL, _inside(
        '{(k := "id"): 1 for _ in [1]}', 'cmd = {k: 5}')),
    ('comp-list-ifs', 1, {'tab': 5}, _CALL, _inside(
        '[k for _ in [1] if (k := "tab")]', 'cmd = {k: 5}')),
    ('comp-list-ifs-other', 0, {'id': 5}, _CALL, _inside(
        '[k for _ in [1] if (k := "id")]', 'cmd = {k: 5}')),
    ('comp-set-ifs', 1, {'tab': 5}, _CALL, _inside(
        '{k for _ in [1] if (k := "tab")}', 'cmd = {k: 5}')),
    ('comp-dict-ifs', 1, {'tab': 5}, _CALL, _inside(
        '{k: 1 for _ in [1] if (k := "tab")}', 'cmd = {k: 5}')),
    ('comp-gen-ifs', 1, {'tab': 5}, _CALL, _inside(
        'list(k for _ in [1] if (k := "tab"))', 'cmd = {k: 5}')),
    # An unpack pairs each part with one element, so a part that is not a
    # plain name has no element to take: nested one-element sequences are a
    # list the key position cannot name, and a subscript part may raise
    # before the parts after it are bound. All four raise, so all four read
    # clean.
    ('unpack-nested-element', 0, _RAISES, _CALL, _inside(
        'j, = [("tab",)]', 'cmd = {j: 5}')),
    ('unpack-star-subscript-part', 0, 'raises NameError', _CALL, _inside(
        '*a[0], b = (1, "tab")', 'cmd = {b: 5}')),
    ('unpack-star-subscript-midway', 0, 'raises NameError', _CALL, _inside(
        'a, *b[0], c = (1, 2, 3)', 'cmd = {c: 5}')),
    # A loop binds its target and nothing else from its own shape: the body
    # and the `else` arm are walked as statements of their own, and a walrus
    # hoisted out of them binds a name the loop never bound.
    ('empty-loop-body-unreached', 0, 'raises UnboundLocalError', _CALL,
     _inside('for j in (): (k := "tab")', 'cmd = {k: 5}')),
    ('loop-else-binds-after-the-body', 0, {'id': 5}, _CALL, _inside(
        'for k in ("id",): cmd = {k: 5}', 'else: (k := "tab")')),
    # A walrus's own value is what it binds, so the name it leaves behind
    # carries it.
    ('walrus-value-binds-its-name', 1, {'tab': 5}, _CALL, _inside(
        'k = (j := "tab")', 'cmd = {k: 5}')),
]

# Three boundaries this change does not cross. Every row below carries a
# real `tab` to the sender while reading clean, which is what makes each a
# separate defect rather than a member this change claims to close. The
# literals table is empty in a nested body, so a binding made OUTSIDE one
# is unresolved, and the container model's `_literal_key` reads that same
# table under the same boundary (#1341). The binding forms themselves -
# the walrus, the unpack and the loop target - are `_ROWS` above, because
# they were members of the mechanism this change closes (#1343).
_CROSS_SCOPE = [
    ('module-scope', 0, {'tab': 5}, _CALL,
     'TAB = "tab"\n' + _inside('cmd = {TAB: 5}')),
    ('default-argument', 0, {'tab': 5}, _CALL,
     'def f(args, key="tab"):\n' + _SENDER + '    cmd = {key: 5}\n' + _SPREAD),
    ('parameter', 0, {'tab': 5}, "f(ARGS, 'tab')",
     'def f(args, key):\n' + _SENDER + '    cmd = {key: 5}\n' + _SPREAD),
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
    except ValueError:
        return _RAISES_VALUE
    except NameError as error:
        return f'raises {type(error).__name__}'


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
    """The two writers of the name-to-literal table, one per reader.

    `ast.Delete` is where they used to differ - this one returning early
    where `_pyroute_mapping` forgot the name - so a key spelled with a
    name the program has deleted is a key the program cannot spell at
    all, and must resolve to nothing. The binding forms are the other
    half of the same contract: a writer that taught one model the walrus,
    the unpack and the loop target, and not the other, would leave the
    two disagreeing about the same program.
    """
    path = Path(tmp) / 'deleted-name.py'
    path.write_text('k = "tab"\n'
                    'cmd = {k: 5}\n'
                    'del k\n'
                    'other = {k: 5}\n', encoding='utf-8')
    found = dict_assignments(ast.parse(path.read_text(encoding='utf-8')))
    assert 'tab' in found['cmd']
    assert found['other'] == {}
    forms = {
        'walrus': ('(w := "tab")\nk = w\ncmd = {k: 5}\n', ['tab']),
        'unpack': ('j, = ("tab",)\ncmd = {j: 5}\n', ['tab']),
        'loop': ('for j in ("tab",):\n    cmd = {j: 5}\n', ['tab']),
        'loop-over-name': ('keys = ("tab",)\nfor j in keys:\n'
                           '    cmd = {j: 5}\n', ['tab']),
        'augmented': ('k = "tab"\nk += "x"\ncmd = {k: 5}\n', []),
        'del-tuple-target': ('k = "tab"\na = 1\ndel (a, k)\n'
                             'cmd = {k: 5}\n', []),
        'order-key-then-walrus': ('k = "id"\ncmd = {k: (k := "tab")}\n',
                                  ['id']),
    }
    spelled = {label: sorted(dict_assignments(ast.parse(source))['cmd'])
               for label, (source, _) in forms.items()}
    assert spelled == {label: expected
                       for label, (_, expected) in forms.items()}, spelled


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='payloadkey_')


if __name__ == '__main__':
    raise SystemExit(main())
