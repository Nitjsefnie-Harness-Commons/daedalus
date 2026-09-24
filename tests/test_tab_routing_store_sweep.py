#!/usr/bin/env python3
"""Runtime-and-guard verdict table for every mapping-store form."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute import py_tab_routing_violations  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402

_CALL = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
_LAMBDA = f'lambda: {_CALL}'
_FORWARD = 'lambda *a, **k: send(*a, **k)'
_UNKNOWN = ('pool = []\n'
            'def unknown():\n'
            '    return pool[0]\n')
_TABCALL = "box['k']('_focus', 'focus-tab', tab=int(args.chrome_tab))"
_RELAY = ('def maker():\n    return ' + _LAMBDA + '\n'
          'def relay(): return maker()\n'
          'def pair(): return relay(), ordinary\n')


def _flow(*lines, invoke=None):
    body = ['send = ordinary', *lines]
    if invoke is not None:
        body.extend(('send = ext_cmd', f'return {invoke}'))
    return '\n'.join(body)


def _ext_flow(*lines):
    """A body whose every sender is the extension one."""
    return '\n'.join(('send = ext_cmd', _RELAY, *lines))


CASES = [
    ('subscript-store', _flow(
        'box = {}', f'box["k"] = {_LAMBDA}', invoke='box["k"]()'), (1, 1)),
    ('update-literal', _flow(
        'box = {}', 'box.update({"k": ' + _LAMBDA + '})',
        invoke='box["k"]()'), (1, 1)),
    ('update-name', _flow(
        'box = {}', 'other = {}', f'other["k"] = {_LAMBDA}',
        'box.update(other)', invoke='box["k"]()'), (1, 1)),
    ('update-untracked-call', _flow(
        'box = {}', 'def build():\n    return {"k": ' + _LAMBDA + '}',
        'box.update(build())', invoke='box["k"]()'), (1, 1)),
    ('update-keyword', _flow(
        'box = {}', 'box.update(k=' + _LAMBDA + ')',
        invoke='box["k"]()'), (1, 1)),
    ('update-pairs', _flow(
        'box = {}', 'box.update([("k", ' + _LAMBDA + ')])',
        invoke='box["k"]()'), (1, 1)),
    ('setdefault-store', _flow(
        'box = {}', f'box.setdefault("k", {_LAMBDA})',
        invoke='box["k"]()'), (1, 1)),
    ('setdefault-return', _flow(
        'box = {}', f'box.setdefault("k", {_LAMBDA})',
        invoke='box.setdefault("k", lambda: ordinary())()'), (1, 1)),
    ('dict-comp', _flow(
        'd = {key: ' + _LAMBDA + ' for key in ["a"]}',
        invoke='d["a"]()'), (1, 1)),
    ('dict-pairs', _flow(
        'd = dict([("k", ' + _LAMBDA + ')])', invoke='d["k"]()'), (1, 1)),
    ('dict-kwargs', _flow(
        'd = dict(k=' + _LAMBDA + ')', invoke='d["k"]()'), (1, 1)),
    ('update-positional-unknown', _flow(
        'box = {}', _UNKNOWN, "pool.append({'k': " + _FORWARD + '})',
        'box.update(unknown())', invoke=_TABCALL), (1, 1)),
    ('dict-copy', _flow(
        'box = {}', f'box["k"] = {_LAMBDA}', 'd = dict(box)',
        invoke='d["k"]()'), (1, 1)),
    ('ior-literal', _flow(
        'box = {}', 'box |= {"k": ' + _LAMBDA + '}',
        invoke='box["k"]()'), (1, 1)),
    ('fromkeys', _flow(
        'd = dict.fromkeys(["k"], ' + _LAMBDA + ')',
        invoke='d["k"]()'), (1, 1)),
    ('spread-merge', _flow(
        'box = {}', f'box["j"] = {_LAMBDA}', 'd = {**box}',
        invoke='d["j"]()'), (1, 1)),
    ('pipe-merge', _flow(
        'box = {}', 'box["j"] = lambda: ordinary()',
        'd = box | {"k": ' + _LAMBDA + '}',
        invoke='d["k"]()'), (1, 1)),
    ('computed-key', _flow(
        'box = {}', 'key = "k"', 'box[key] = ' + _LAMBDA,
        invoke='box[key]()'), (1, 1)),
    ('assign-unknown-tracked', _flow(
        'box = {"k": ' + _LAMBDA + '}', _UNKNOWN,
        f'pool.append({_LAMBDA})', 'box["k"] = unknown()',
        invoke='box["k"]()'), (1, 1)),
    ('assign-unknown-untracked', _flow(
        'box = {}', _UNKNOWN, f'pool.append({_FORWARD})',
        'box["k"] = unknown()', invoke=_TABCALL), (1, 1)),
    ('spread-unknown', _flow(
        _UNKNOWN, "pool.append({'j': " + _FORWARD + '})',
        'd = {**unknown()}',
        invoke="d['j']('_focus', 'focus-tab', tab=int(args.chrome_tab))"),
     (1, 1)),
    ('pipe-unknown', _flow(
        'box = {}', _UNKNOWN, "pool.append({'k': " + _FORWARD + '})',
        'd = box | unknown()',
        invoke="d['k']('_focus', 'focus-tab', tab=int(args.chrome_tab))"),
     (1, 1)),
    ('update-keyword-unknown', _flow(
        'box = {}', _UNKNOWN, f'pool.append({_FORWARD})',
        'box.update(k=unknown())', invoke=_TABCALL), (1, 1)),
    ('setdefault-unknown', _flow(
        'box = {}', _UNKNOWN, f'pool.append({_FORWARD})',
        'box.setdefault("k", unknown())', invoke=_TABCALL), (1, 1)),
    ('dict-unknown', _flow(
        _UNKNOWN, "pool.append({'k': " + _FORWARD + '})',
        'd = dict(unknown())',
        invoke="d['k']('_focus', 'focus-tab', tab=int(args.chrome_tab))"),
     (1, 1)),
    ('annassign-unknown', _flow(
        'box = {}', _UNKNOWN, f'pool.append({_FORWARD})',
        'box["k"]: object = unknown()', invoke=_TABCALL), (1, 1)),
    ('spread-unknown-name', _flow(
        _UNKNOWN, "pool.append({'j': " + _FORWARD + '})',
        'box = unknown()', 'd = {**box}',
        invoke="d['j']('_focus', 'focus-tab', tab=int(args.chrome_tab))"),
     (1, 1)),
    ('iteration-of-copy', _flow(
        'box = {}', f'box["k"] = {_LAMBDA}', 'copy = dict(box)',
        'list(copy)', 'list(box)'), (0, 0)),
    ('clean-store-invocation', _flow(
        'box = {}', 'box["k"] = lambda: ordinary()',
        invoke='box["k"]()'), (0, 0)),
    ('delete-keeps-pop', _flow(
        'box = {"k": ' + _LAMBDA + '}', 'del box["k"]',
        invoke='(box["k"]() if "k" in box else None)'), (0, 0)),
    ('issue857-pair-list-dynamic', _flow(
        _RELAY, 'e = {}; e.update([("k", ordinary), (None, relay())])',
        'x = e.setdefault("k", relay())', 'y = e.get(None, ordinary)',
        invoke='x(), y()'), (1, 1)),
    ('issue857-pair-list-literal', _flow(
        _RELAY, 'e = {}; e.update([("k", ordinary), (None, relay())])',
        'x = e.setdefault("k", relay())', invoke='x()'), (0, 0)),
    ('issue857-update-pairs-defect', _ext_flow(
        'e = {}; e.update([("k", relay())])',
        'x = e.setdefault("k", relay())', 'x()'), (1, 1)),
    ('issue857-setdefault-defect', _ext_flow(
        'd = {}', 'x = d.setdefault("k", relay())', 'x()'), (1, 1)),
    ('issue857-pop-direct-defect', _ext_flow(
        'd = {"k": relay()}', 'd.pop("k")', 'd.get("k", relay())()'),
     (1, 1)),
    ('issue857-pop-name-defect', _ext_flow(
        'd = {"k": relay()}; k = "k"', 'd.pop(k)',
        'd.get("k", relay())()'), (1, 1)),

    # A pop that names an ABSENT key is the discriminator between resolving
    # the key and erasing or marking the container: `j` still holds a relay.
    ('issue857-pop-absent-key', _flow(
        _RELAY, 'd = {"k": relay(), "j": relay()}; k = "zz"',
        'd.pop(k, None)', invoke='d.get("j", ordinary)()'), (1, 1)),
]

# The shapes issue 857 over-reported, each with the real defect of the same
# shape: same operator, key kind and receiver kind, the key occupied.
_ISSUE857 = [
    ('update-pairs',
     _flow(_RELAY, 'e = {}; e.update([("k", ordinary)])',
           'x = e.setdefault("k", relay())', invoke='x()'),
     _flow(_RELAY, 'e = {}; e.update([("k", relay())])',
           'x = e.setdefault("k", relay())', invoke='x()')),
    ('setdefault-int-key',
     _flow(_RELAY, 'd = {}; d[1] = ordinary',
           'x = d.setdefault(1, relay())', invoke='x()'),
     _flow(_RELAY, 'd = {}; d[1] = ordinary',
           'x = d.setdefault(2, relay())', invoke='x()')),
    ('pop-via-getd',
     _flow(_RELAY, 'd = {"k": relay()}', 'def getd(): return d',
           'getd().pop("k")', invoke='d.get("k", ordinary)()'),
     _flow(_RELAY, 'd = {"k": relay(), "j": ordinary}',
           'def getd(): return d', 'getd().pop("j")',
           invoke='d.get("k", ordinary)()')),
    ('pop-variable-key',
     _flow(_RELAY, 'd = {"k": relay()}; k = "k"', 'd.pop(k)',
           invoke='d.get("k", ordinary)()'),
     _flow(_RELAY, 'd = {"k": relay(), "j": ordinary}; k = "j"', 'd.pop(k)',
           invoke='d.get("k", ordinary)()')),
]


def test_issue857_shapes_read_clean_and_their_defects_are_caught(tmp):
    for label, over_reported, defect in _ISSUE857:
        actual = _tracked_focus_verdict(tmp, over_reported, counts=True)
        assert actual == (0, 0), f'{label}: expected (0, 0), got {actual}'
        actual = _tracked_focus_verdict(tmp, defect, counts=True)
        assert actual == (1, 1), f'{label}: expected (1, 1), got {actual}'


# The setdefault key forms around the occupancy lookup: a name bound to a
# literal is a known key, a name bound to a non-literal is not.
_ISSUE962 = [
    ('name-key-occupied', _flow(
        _RELAY, 'd = {"k": relay()}; key = "k"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('literal-key-occupied', _flow(
        _RELAY, 'd = {"k": relay()}', 'x = d.setdefault("k", ordinary)',
        invoke='x()'), (1, 1)),
    ('name-key-vacant', _flow(
        _RELAY, 'd = {}; key = "k"', 'x = d.setdefault(key, relay())',
        invoke='x()'), (1, 1)),
    ('name-key-non-string', _flow(
        _RELAY, 'd = {1: relay()}; key = 1',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    # Unresolvable key, deferred default: reported. Unresolvable key,
    # ordinary default: the stored callable is lost and the call reads
    # clean — issue 963, pinned at its current verdict.
    ('name-key-unresolved-relay', _flow(
        _RELAY, 'd = {"k": relay()}; key = "k" + ""',
        'x = d.setdefault(key, relay())', invoke='x()'), (1, 1)),
    ('known-defect-963-name-key-unresolved-ordinary', _flow(
        _RELAY, 'd = {"k": relay()}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 0)),
]


def test_issue962_name_bound_setdefault_key(tmp):
    bad = []
    for label, body, expected in _ISSUE962:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# The literal key forms around that lookup. A key the shared evaluator folds
# reaches the model the same way on every operation that takes one: a dict
# literal's key, a subscript store's, the key a `setdefault` stores, the key
# a `pop` or `del` removes, and the key a `get`, `pop` or subscript reads.
# `test_key_resolution_is_operation_independent` is that property as a
# matrix, and a presence test is the one operation that consults no
# occupancy at all: the guard reads both arms of the `if`, so a resolved
# membership could only ever remove a report. An f-string is not folded by
# the evaluator, so both f-string rows stay unresolved and read clean, as
# issue 967 records; the label carries that, so the row cannot be read as a
# claim that a clean verdict is correct.
_ISSUE967 = [
    ('tuple-key-by-name', _flow(
        _RELAY, 'd = {(1, 2): relay()}; key = (1, 2)',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('tuple-key-literal', _flow(
        _RELAY, 'd = {(1, 2): relay()}',
        'x = d.setdefault((1, 2), ordinary)', invoke='x()'), (1, 1)),
    ('unaryminus-key-by-name', _flow(
        _RELAY, 'd = {-1: relay()}; key = -1',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('known-defect-967-fstring-key-by-name', _flow(
        _RELAY, 'd = {"k": relay()}; key = f"k"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 0)),
    ('known-defect-967-fstring-key-interpolated', _flow(
        _RELAY, 'd = {"k": relay()}; key = f"{\'k\'}"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 0)),
]


def test_issue967_literal_key_forms(tmp):
    bad = []
    for label, body, expected in _ISSUE967:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# A rebound name must not resolve to its first binding, and the rebind has
# to be to something the table cannot hold: rebinding to a second literal
# overwrites the entry and proves nothing. Both rows rebind to a call, so a
# guard that keeps the first binding resolves the key it must not know. The
# pop row is the false-green discriminator and the setdefault row the
# over-report one: a guard keeping the first binding turns the pair into
# `(1, 0)` and `(1, 1)`.
_REBINDING = [
    ('pop-rebound-to-non-literal', _flow(
        _RELAY, 'd = {"k": relay(), "j": relay()}; k = "k"; k = relay()',
        'd.pop(k, None)', invoke='d.get("k", ordinary)()'), (1, 1)),
    ('setdefault-rebound-to-non-literal', _flow(
        _RELAY, 'd = {"k": relay()}; k = "k"; k = relay()',
        'x = d.setdefault(k, ordinary)', invoke='x()'), (0, 0)),
]

# A key the runtime rejects is a key the guard must read unprovable rather
# than crash on: `dict.setdefault` raises `TypeError: unhashable type` for
# every spelling below before it routes anything, so the guard's verdict is
# about the call, and a guard that raises must fail a row instead of a suite.
_UNUSABLE_KEYS = [
    ('unusable-key-list', 'key = [1]'),
    ('unusable-key-tuple-of-list', 'key = ([1],)'),
    ('unusable-key-dict', 'key = {"a": 1}'),
    ('unusable-key-set', 'key = {1, 2}'),
]

# One key, three operations. Every row pops a key and reads that same key
# back, so a guard that removes the key and then cannot resolve the read
# reports a value the program never reads. The string-keyed twins are the
# negative space: the point is that the evaluable form behaves like the
# constant form, not that every pop reads clean. The last row is the read
# whose key the guard cannot evaluate at all, which stays #963's class.
_TUPLE_D = 'd = {(1, 2): relay(), (3, 4): relay()}'
_STRING_D = 'd = {"k": relay(), "j": relay()}'
_POP_THEN_READ = [
    ('pop-tuple-lit', _flow(
        _RELAY, _TUPLE_D, 'd.pop((1, 2), None)',
        invoke='d.get((1, 2), ordinary)()'), (0, 0)),
    ('pop-tuple-name', _flow(
        _RELAY, f'{_TUPLE_D}; key = (1, 2)', 'd.pop(key, None)',
        invoke='d.get((1, 2), ordinary)()'), (0, 0)),
    ('pop-tuple-read-by-name', _flow(
        _RELAY, f'{_TUPLE_D}; r = (1, 2)', 'd.pop((1, 2), None)',
        invoke='d.get(r, ordinary)()'), (0, 0)),
    ('rebind-to-tuple-then-pop', _flow(
        _RELAY, 'key = (1, 2)', _TUPLE_D, 'd.pop(key, None)',
        invoke='d.get((1, 2), ordinary)()'), (0, 0)),
    ('pop-str-lit', _flow(
        _RELAY, _STRING_D, 'd.pop("k", None)',
        invoke='d.get("k", ordinary)()'), (0, 0)),
    ('pop-str-name', _flow(
        _RELAY, f'{_STRING_D}; key = "k"', 'd.pop(key, None)',
        invoke='d.get("k", ordinary)()'), (0, 0)),
    ('pop-str-read-by-name', _flow(
        _RELAY, f'{_STRING_D}; r = "k"', 'd.pop("k", None)',
        invoke='d.get(r, ordinary)()'), (0, 0)),
    ('rebind-to-str-then-pop', _flow(
        _RELAY, 'key = "k"', _STRING_D, 'd.pop(key, None)',
        invoke='d.get("k", ordinary)()'), (0, 0)),
    ('read-unevaluable-key', _flow(
        _RELAY, 'd = {"k": relay()}', 'x = d.get("k" + "", ordinary)',
        invoke='x()'), (1, 1)),
]

# The pop direction's over-reports on a key the guard cannot evaluate. Each
# was measured at the pre-wave-2 head and at this one, where all four read
# the same, so they predate every wave here; the runtime sends nothing in
# any of them. The attribution is by mechanism, not by resemblance: an
# f-string is the expression #967's setdefault rows carry, and a
# concatenation is #963's, whether it reaches the pop directly, through a
# rebind or through a name bound to it.
_POP_DIRECTION = [
    ('known-defect-967-pop-fstring-name', _flow(
        _RELAY, f'{_STRING_D}; key = f"k"', 'd.pop(key, None)',
        invoke='d.get("k", ordinary)()'), (0, 1)),
    ('known-defect-967-rebind-to-fstring', _flow(
        _RELAY, 'k = "k"; k = f"k"', _STRING_D, 'd.pop(k, None)',
        invoke='d.get("k", ordinary)()'), (0, 1)),
    ('known-defect-963-rebind-to-nonliteral-name', _flow(
        _RELAY, 'k = "k"; j = "k" + ""; k = j', _STRING_D, 'd.pop(k, None)',
        invoke='d.get("k", ordinary)()'), (0, 1)),
    ('known-defect-963-pop-concat', _flow(
        _RELAY, 'k = "k"; k = "k" + ""', _STRING_D, 'd.pop(k, None)',
        invoke='d.get("k", ordinary)()'), (0, 1)),
]


def test_rebinding_drops_the_first_literal(tmp):
    bad = []
    for label, body, expected in _REBINDING:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


def test_unusable_key_reads_unprovable(tmp):
    quiet = []
    for label, binding in _UNUSABLE_KEYS:
        source = Path(tmp) / f'{label}.py'
        source.write_text(
            'def ordinary(*a, **k):\n'
            '    return 0\n'
            'def probe():\n'
            '    d = {"k": 1}\n'
            f'    {binding}\n'
            '    send = d.setdefault(key, ordinary)\n'
            '    return send("_focus", "focus-tab", tab=5)\n',
            encoding='utf-8')
        if not py_tab_routing_violations(source, source.name):
            quiet.append(label)
    assert not quiet, quiet


def test_pop_direction_known_defects(tmp):
    bad = []
    for label, body, expected in _POP_DIRECTION:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


def test_pop_then_read_resolves_one_key(tmp):
    bad = []
    for label, body, expected in _POP_THEN_READ:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# The key-resolution property as a table: every key form against every
# operation that touches a key, each measured with the runtime beside it.
# The three evaluable forms must agree operation-for-operation with the
# string constant, and the two the evaluator cannot fold must agree with
# each other, because both take the unresolved arm. The labels name the
# forms whose verdicts are known defects, not the operations.
_KEY_FORMS = {
    'str-const': ([], '"k"', '"k"'),
    'tuple-lit': ([], '(1, 2)', '(1, 2)'),
    'name-lit': (['r = "k"'], 'r', 'r'),
    'known-defect-963-form-name-nonliteral': (['r = "k" + ""'], 'r', 'r'),
    'known-defect-967-form-fstring': ([], 'f"k"', 'f"k"'),
}
_EVALUABLE_FORMS = ('str-const', 'tuple-lit', 'name-lit')
_UNRESOLVED_FORMS = ('known-defect-963-form-name-nonliteral',
                     'known-defect-967-form-fstring')

# What each operation reads, per key form. An occupied store holds the
# relay under the key, so a read of that key is a defect the guard must
# report; a removal of it is clean; a clean store holds `ordinary` under
# it, so a read of it is clean and a read of an absent key is not.
_KEY_OPERATIONS = [
    ('get-read', '{store}\nx = d.get({key}, ordinary)', 'x()'),
    ('subscript-read', '{store}\nx = d[{key}]', 'x()'),
    ('subscript-store+get', 'd = {{}}\nd[{key}] = relay()\n'
     'x = d.get({key}, ordinary)', 'x()'),
    ('subscript-store+read', 'd = {{}}\nd[{key}] = relay()\n'
     'x = d[{key}]', 'x()'),
    ('setdefault-read', '{store}\nx = d.setdefault({key}, ordinary)',
     'x()'),
    ('setdefault-store+get', 'd = {{}}\nd.setdefault({key}, relay())\n'
     'x = d.get({key}, ordinary)', 'x()'),
    ('pop+get',
     '{store}\nd.pop({key}, None)\nx = d.get({key}, ordinary)', 'x()'),
    ('del+get', '{store}\ndel d[{key}]\nx = d.get({key}, ordinary)',
     'x()'),
    ('del+presence', '{store}\ndel d[{key}]',
     '(d.get({key}, ordinary)() if {key} in d else None)'),
    ('presence+get', '{store}',
     '(d.get({key}, ordinary)() if {key} in d else None)'),
    ('clean-store+get', '{clean}\nx = d.get({key}, relay())', 'x()'),
    ('clean-subscript-store+get', 'd = {{}}\nd[{key}] = ordinary\n'
     'x = d.get({key}, relay())', 'x()'),
    ('clean-setdefault+get', 'd = {{}}\nd.setdefault({key}, ordinary)\n'
     'x = d.get({key}, relay())', 'x()'),
]

# The verdicts, measured form by form. The evaluable rows are the ideal:
# the form behaves exactly as the string constant does.
_KEY_EXPECTED = {
    'get-read': (1, 1), 'subscript-read': (1, 1),
    'subscript-store+get': (1, 1), 'subscript-store+read': (1, 1),
    'setdefault-read': (1, 1), 'setdefault-store+get': (1, 1),
    'pop+get': (0, 0), 'del+get': (0, 0), 'del+presence': (0, 0),
    'presence+get': (1, 1), 'clean-store+get': (0, 0),
    'clean-subscript-store+get': (0, 0), 'clean-setdefault+get': (0, 0),
}
# The evaluator cannot fold either of these, so the guard reads the key as
# absent on every operation: a read reports because the store's contents
# are not known to miss it, and a removal is not applied, so what the
# program removed the guard still holds. The setdefault read is issue
# 963's known defect; the rest are the consistency of the unresolved arm.
_UNRESOLVED_EXPECTED = {
    'get-read': (1, 1), 'subscript-read': (1, 1),
    'subscript-store+get': (1, 1), 'subscript-store+read': (1, 1),
    'setdefault-read': (1, 0), 'setdefault-store+get': (1, 1),
    'pop+get': (0, 1), 'del+get': (0, 1), 'del+presence': (0, 1),
    'presence+get': (1, 1), 'clean-store+get': (0, 1),
    'clean-subscript-store+get': (0, 1), 'clean-setdefault+get': (0, 1),
}


def _key_matrix_rows():
    rows = []
    for form, (bind, key, literal) in _KEY_FORMS.items():
        expected = (_UNRESOLVED_EXPECTED if form in _UNRESOLVED_FORMS
                    else _KEY_EXPECTED)
        for operation, shape, invoke in _KEY_OPERATIONS:
            store = f'd = {{{literal}: relay()}}'
            clean = f'd = {{{literal}: ordinary}}'
            body = _flow(_RELAY, *bind, shape.format(
                store=store, clean=clean, key=key),
                invoke=invoke.format(key=key))
            rows.append((f'{form}/{operation}', body, expected[operation]))
    return rows


_KEY_MATRIX = _key_matrix_rows()


# The pair-list store of issue 857's first facet, whose key the evaluator
# folds but the pair helper used to gate on a raw `ast.Constant`: a tuple
# key and a name-bound key both landed in the dynamic slot. The first two
# rows are the real defect the guard read clean and the clean program it
# read dirty, the last two the string-key controls they must equal.
_PAIR_LIST_KEYS = [
    ('pair-tuple-key-setdefault', _flow(
        _RELAY, 'e = {}; e.update([((1, 2), relay())])',
        'x = e.setdefault((1, 2), ordinary)', invoke='x()'), (1, 1)),
    ('pair-name-key-setdefault', _flow(
        _RELAY, 'e = {}; k = "k"; e.update([(k, relay())])',
        'x = e.setdefault("k", ordinary)', invoke='x()'), (1, 1)),
    ('pair-tuple-key-clean-get', _flow(
        _RELAY, 'e = {}; e.update([((1, 2), ordinary)])',
        'x = e.get((1, 2), relay())', invoke='x()'), (0, 0)),
    ('pair-str-key-setdefault', _flow(
        _RELAY, 'e = {}; e.update([("k", relay())])',
        'x = e.setdefault("k", ordinary)', invoke='x()'), (1, 1)),
    ('pair-str-key-clean-get', _flow(
        _RELAY, 'e = {}; e.update([("k", ordinary)])',
        'x = e.get("k", relay())', invoke='x()'), (0, 0)),
]


def test_pair_list_key_forms(tmp):
    bad = []
    for label, body, expected in _PAIR_LIST_KEYS:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# The `literals` term in `state_signature` decides whether two paths that
# bind one name to different literals collapse at a dedupe point. Without it
# they do, and the path whose key selected a relay is the one dropped: these
# two bodies read `(1, 0)` with the term removed and `(1, 1)` with it, and
# nothing else in the suite notices the difference.
_SIGNATURE_TERM = [
    ('branch-binds-two-literals', _flow(
        _RELAY, 'd = {"a": ordinary, "b": relay()}',
        'if not args.flag:\n    k = "a"\nelse:\n    k = "b"',
        'x = d.get(k, ordinary)', invoke='x()'), (1, 1)),
    ('loop-rebinds-one-literal', _flow(
        _RELAY, 'd = {"a": ordinary, "b": relay()}; k = "a"',
        'while args.flag:\n    k = "b"\n    break',
        'x = d.get(k, ordinary)', invoke='x()'), (1, 1)),
]

# A subscript read whose key the guard cannot resolve names every item of a
# dict, because no position is known. With the all-items arm removed the
# read finds nothing and the stored relay reads clean, which is why this row
# exists beside the route assertion the yielded-sender suite keeps.
_SUBSCRIPT_UNRESOLVED = [
    # Two items, the ordinary one FIRST: the key the guard cannot resolve
    # evaluates to "k" at runtime, which is the second item, so naming only
    # the first candidate picks `ordinary` and misses the relay. A one-item
    # dict cannot tell "every item" from "the first".
    ('subscript-unresolvable-key', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}', 'x = d["k" + ""]',
        invoke='x()'), (1, 1)),
]


def test_signature_term_separates_two_literal_bindings(tmp):
    bad = []
    for label, body, expected in _SIGNATURE_TERM:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


def test_unresolvable_subscript_read_names_every_item(tmp):
    bad = []
    for label, body, expected in _SUBSCRIPT_UNRESOLVED:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# A resolvable subscript-store key is recorded UNDER THAT KEY, not in the
# dynamic slot. The row reads a DIFFERENT key, so a store that parks the
# relay dynamically leaks it into this read: the plain value sits first and
# the parked relay would be a second candidate.
_SUBSCRIPT_STORE_NAMES = [
    ('subscript-store-by-name', _flow(
        _RELAY, 'd = {"a": ordinary}; d["k"] = relay()',
        'x = d.get("a", relay())', invoke='x()'), (0, 0)),
]


def test_subscript_store_records_a_resolvable_key_under_it(tmp):
    bad = []
    for label, body, expected in _SUBSCRIPT_STORE_NAMES:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


def test_key_form_matrix_and_operation_independence(tmp):
    """The matrix measured, and the property read off the measurement.

    The property is that an evaluable key form behaves as the string
    constant does on every operation, and that the two forms the evaluator
    cannot fold behave as each other do. Three checks run on the MEASURED
    verdicts: the evaluable forms against the string constant, the two
    unevaluable forms against each other, and the string-constant column
    against the recorded ideal. The first two are comparisons between
    measured columns, never against the table, so a site that stops
    resolving one form reds this test on the divergence rather than on a
    cell; the third is the anchor, so a cell that is wrong for every form
    cannot pass by being the reference the others are compared with. A
    fourth check, every cell against its own recorded expectation, closes
    the test.
    """
    measured, cells = {}, {}
    for label, body, expected in _KEY_MATRIX:
        form, operation = label.split('/', 1)
        verdict = _tracked_focus_verdict(tmp, body, counts=True)
        measured.setdefault(form, {})[operation] = verdict
        cells.setdefault(form, {})[operation] = (verdict, expected)
    reference = measured['str-const']
    diverged = [(form, operation, verdict, reference[operation])
                for form in _EVALUABLE_FORMS
                for operation, verdict in sorted(measured[form].items())
                if verdict != reference[operation]]
    assert not diverged, diverged
    unresolved = _UNRESOLVED_FORMS[0]
    for form in _UNRESOLVED_FORMS[1:]:
        assert measured[form] == measured[unresolved], form
    assert reference == _KEY_EXPECTED, reference
    bad = [(f'{form}/{operation}', verdict, expected)
           for form, entries in sorted(cells.items())
           for operation, (verdict, expected) in sorted(entries.items())
           if verdict != expected]
    assert not bad, bad


def test_store_form_verdicts(tmp):
    bad = []
    for label, body, expected in CASES:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='storesweep_')


if __name__ == '__main__':
    raise SystemExit(main())
