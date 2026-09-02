#!/usr/bin/env python3
"""Every use of a receiver that carries a sender is accounted for.

The exact layer answers the direct forms. Anything else that mentions such
a receiver - a chained accessor, a reflective call, a copy, a destructure,
an unresolved callee - has to taint rather than resolve to silence, so no
unenumerated spelling can route a send the guard never saw.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_js import _runtime_and_guard  # noqa: E402


_SEND = "send('focus-tab', { tab: chromeTab });\n"
_MEMBERS = ("get hook() { %S = extCmd; return 1; },\n"
            "  set hook(v) { %S = extCmd; },\n"
            "  go() { %S = extCmd; }")
_KINDS = {
    'literal': "const m = {\n  " + _MEMBERS + " };\n",
    'instance': ("class K {\n  get hook() { %S = extCmd; return 1; }\n"
                 "  set hook(v) { %S = extCmd; }\n"
                 "  go() { %S = extCmd; }\n}\nconst m = new K();\n"),
    'factory': ("const make = () => ({\n  " + _MEMBERS
                + " });\nconst m = make();\n"),
    'classname': ("class K {\n  static get hook() { %S = extCmd; return 1; }\n"
                  "  static set hook(v) { %S = extCmd; }\n"
                  "  static go() { %S = extCmd; }\n}\nconst m = K;\n"),
}

# One statement each, using the receiver `m` the kind above defines.
_SHAPES = [
    ('direct-read', "void m.hook;"),
    ('chain-read', "const outer = { value: m };\nvoid outer.value.hook;"),
    ('chain-write', "const outer = { value: m };\nouter.value.hook = 1;"),
    ('chain-compound', "const outer = { value: m };\n"
     "outer.value.hook += 1;"),
    ('chain-prefix-update', "const outer = { value: m };\n"
     "++outer.value.hook;"),
    ('chain-bracket', "const outer = { value: m };\n"
     "void outer['value']['hook'];"),
    ('call-through-call', "m.go.call(null);"),
    ('call-through-bind', "m.go.bind(m)();"),
    ('reflect-apply', "Reflect.apply(m.go, m, []);"),
    ('object-assign-copy', "void Object.assign({}, m);"),
    ('object-values', "void Object.values(m);"),
    ('object-entries', "void Object.entries(m);"),
    ('structured-clone', "try { void structuredClone(m); }\n"
     "catch (e) { void e; }"),
    ('json-stringify', "void JSON.stringify(m);"),
    ('spread-copy', "const bag = { ...m };\nvoid bag;"),
    ('spread-argument', "void Object.assign({}, { ...m });"),
    ('destructure', "const { hook } = m;\nvoid hook;"),
    ('destructure-renamed', "const { hook: named } = m;\nvoid named;"),
    ('destructure-parameter',
     "const take = ({ hook }) => hook;\nvoid take(m);"),
    ('for-in', "for (const k in m) { void k; }"),
    ('in-operator', "void ('hook' in m);"),
    ('delete-member', "delete m.hook;"),
    ('typeof-member', "void typeof m.hook;"),
    ('optional-dot', "void m?.hook;"),
    ('optional-bracket', "void m?.['hook'];"),
    ('tagged-template', "const tag = (s, ...v) => v;\n"
     "void tag`${m.hook}`;"),
    ('with-statement', "with (m) { void hook; }"),
    ('unresolved-callee', "const helper = calls.length ? null\n"
     "  : ((x) => { void x.hook; });\nvoid helper(m);"),
    ('through-return', "const give = () => m;\nvoid give().hook;"),
    ('array-element', "void [m][0].hook;"),
    ('parenthesized', "void (m).hook;"),
    ('sequence-callee', "(0, m.go)();"),
    ('proxy-wrapper', "void new Proxy(m, {}).hook;"),
    ('alias-parenthesized', "const b = m;\nvoid (b).hook;"),
    ('alias-argument', "const b = m;\n"
     "void JSON.stringify(b);"),
]


# The class-name receiver, pinned rather than left to the
# guard >= runtime property alone.
_CLASSNAME = [
    ('direct-read', True, True),
    ('chain-read', True, True),
    ('chain-write', True, True),
    ('chain-compound', True, True),
    ('chain-prefix-update', True, True),
    ('chain-bracket', True, True),
    ('call-through-call', True, True),
    ('call-through-bind', True, True),
    ('reflect-apply', True, True),
    ('object-assign-copy', False, True),
    ('object-values', False, True),
    ('object-entries', False, True),
    ('structured-clone', False, True),
    ('json-stringify', False, True),
    ('spread-copy', False, False),
    ('spread-argument', False, True),
    ('destructure', True, True),
    ('destructure-renamed', True, True),
    ('destructure-parameter', True, True),
    ('for-in', False, True),
    ('in-operator', False, True),
    ('delete-member', False, False),
    ('typeof-member', True, True),
    ('optional-dot', True, True),
    ('optional-bracket', True, True),
    ('tagged-template', True, True),
    ('with-statement', True, True),
    ('unresolved-callee', True, True),
    ('through-return', True, True),
    ('array-element', True, True),
    ('parenthesized', True, True),
    ('sequence-callee', True, True),
    ('proxy-wrapper', True, True),
    ('alias-parenthesized', True, True),
    ('alias-argument', False, True),
]


def _program(kind, statement, promotes):
    """One control: the receiver, the shape, then the send."""
    seed = 'ordinary' if promotes else 'extCmd'
    inner = 'extCmd' if promotes else 'ordinary'
    return ("let send = " + seed + ";\n"
            + _KINDS[kind].replace('%S', 'send').replace(
                'extCmd', inner)
            + statement + "\n"
            + _SEND)


def _assert_shapes(tmp, name, promotes, expected, kind='literal'):
    """Assert the (runtime, guard) pair of every shape at once."""
    path = Path(tmp) / name
    observed = [(label, *_runtime_and_guard(
        _program(kind, statement, promotes), path))
        for label, statement in _SHAPES]
    assert observed == expected, observed


def test_class_name_receiver_shapes_are_pinned(tmp):
    """The class-name kind, pair by pair rather than by the property."""
    path = Path(tmp) / 'net-classname.js'
    observed = [(label, *_runtime_and_guard(
        _program('classname', statement, True), path))
        for label, statement in _SHAPES]
    assert not [row for row in observed if row[1] and not row[2]], observed
    assert observed == _CLASSNAME, observed


def test_receiver_shapes_never_go_silent_on_a_routed_send(tmp):
    """The domain property: over every shape and every receiver kind the
    guard reports at least wherever the runtime routed."""
    path = Path(tmp) / 'net-domain.js'
    silent = []
    for kind in _KINDS:
        for label, statement in _SHAPES:
            runtime, guard = _runtime_and_guard(
                _program(kind, statement, True), path)
            if runtime and not guard:
                silent.append((kind, label))
    assert not silent, silent


def test_promoting_shapes_report_on_the_literal_receiver(tmp):
    """Each shape's promoting spelling, pinned pair by pair."""
    expected = [
        ('direct-read', True, True), ('chain-read', True, True),
        ('chain-write', True, True), ('chain-compound', True, True),
        ('chain-prefix-update', True, True),
        ('chain-bracket', True, True),
        ('call-through-call', True, True),
        ('call-through-bind', True, True),
        ('reflect-apply', True, True),
        ('object-assign-copy', True, True),
        ('object-values', True, True), ('object-entries', True, True),
        ('structured-clone', True, True),
        ('json-stringify', True, True), ('spread-copy', True, True),
        ('spread-argument', True, True), ('destructure', True, True),
        ('destructure-renamed', True, True),
        ('destructure-parameter', True, True),
        ('for-in', False, True), ('in-operator', False, True),
        ('delete-member', False, False), ('typeof-member', True, True),
        ('optional-dot', True, True), ('optional-bracket', True, True),
        ('tagged-template', True, True), ('with-statement', True, True),
        ('unresolved-callee', True, True),
        ('through-return', True, True), ('array-element', True, True),
        ('parenthesized', True, True), ('sequence-callee', True, True),
        ('proxy-wrapper', True, True),
        ('alias-parenthesized', True, True),
        ('alias-argument', True, True),
    ]
    _assert_shapes(tmp, 'net-promote.js', True, expected)


def test_demoting_shapes_keep_their_verdicts(tmp):
    """Each shape's demoting spelling: exact silence where a pinned direct
    form covers it, a contract taint where only the net carries it."""
    expected = [
        ('direct-read', False, False), ('chain-read', False, True),
        ('chain-write', False, True), ('chain-compound', False, True),
        ('chain-prefix-update', False, True),
        ('chain-bracket', False, True),
        ('call-through-call', False, False),
        ('call-through-bind', False, True),
        ('reflect-apply', False, False),
        ('object-assign-copy', False, True),
        ('object-values', False, True), ('object-entries', False, True),
        ('structured-clone', False, True),
        ('json-stringify', False, True), ('spread-copy', False, True),
        ('spread-argument', False, True), ('destructure', False, True),
        ('destructure-renamed', False, True),
        ('destructure-parameter', False, True),
        ('for-in', True, True), ('in-operator', True, True),
        ('delete-member', True, True), ('typeof-member', False, False),
        ('optional-dot', False, False),
        ('optional-bracket', False, False),
        ('tagged-template', False, False),
        ('with-statement', False, True),
        ('unresolved-callee', False, True),
        ('through-return', False, True), ('array-element', False, True),
        ('parenthesized', False, True), ('sequence-callee', False, True),
        ('proxy-wrapper', False, True),
        ('alias-parenthesized', False, True),
        ('alias-argument', False, True),
    ]
    _assert_shapes(tmp, 'net-demote.js', False, expected)


def _assert_rows(tmp, name, rows):
    """Assert the (runtime, guard) pair of every row at once."""
    path = Path(tmp) / name
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source in rows]
    expected = [(label, True, True) for label, _ in rows]
    assert observed == expected, observed


def test_a_body_writing_the_routed_binding_is_carried(tmp):
    """A dispatcher takes its sender in, so no sender is spelled here.

    `go(s) { send = s; }` writes the binding the timeline judges, which
    makes the container a handle on whatever the caller hands it. Every
    hand-off of that member has to taint.
    """
    rows = [
        ('computed-key-helper-arrow',
         "const m = { ['go'](s) { send = s; } };\n"
         "const run0 = (f) => f(extCmd);\nrun0(m.go);\n"),
        ('ident-key-helper-arrow', "const m = { go(s) { send = s; } };\n"
         "const run0 = (f) => f(extCmd);\nrun0(m.go);\n"),
        ('numeric-key-helper-function',
         "const m = { async 0(s) { send = s; } };\n"
         "function run0(f) { f(extCmd); }\nrun0(m[0]);\n"),
        ('class-numeric-key-default-parameter',
         "class K { async 0(s) { send = s; } }\nconst m = new K();\n"
         "function run0(f = m[0]) { f(extCmd); }\nrun0();\n"),
        ('iife-product-computed-expression-key',
         "const m = (() => ({ ['g' + 'o'](s) { send = s; } }))();\n"
         "m.go(extCmd);\n"),
    ]
    _assert_rows(tmp, 'writes-routed.js',
                 [(label, "let send = ordinary;\n" + source + _SEND)
                  for label, source in rows])


def test_a_callee_behind_an_unreplayed_call_is_not_proof(tmp):
    """A name whose body only exists past a call the walk never ran.

    `callable_body` reads through the call and answers the function it
    is applied to, so the bodiless call record covered its own mention
    while nothing ran.
    """
    rows = [
        ('function-returns-function', "const run0 = (function () "
         "{ return function () { send = extCmd; }; })();\nrun0();\n"),
        ('arrow-returns-arrow', "const run0 = (() => () => "
         "{ send = extCmd; })();\nrun0();\n"),
    ]
    _assert_rows(tmp, 'unreplayed-callee.js',
                 [(label, "let send = ordinary;\n" + source + _SEND)
                  for label, source in rows])


def test_a_class_registers_whatever_its_heritage_spells(tmp):
    """The sender is in K's own body; the heritage only has to parse."""
    rows = [
        ('member-heritage', "const ns = { B: class {} };\n"
         "class K extends ns.B { go() { send = extCmd; } }\n"
         "const f = new K().go;\nf();\n"),
        ('parenthesized-heritage', "class B {}\n"
         "class K extends (B) { go() { send = extCmd; } }\n"
         "const f = new K().go;\nf();\n"),
        ('called-heritage', "const mk = () => class {};\n"
         "class K extends mk() { go() { send = extCmd; } }\n"
         "const f = new K().go;\nf();\n"),
    ]
    _assert_rows(tmp, 'heritage.js',
                 [(label, "let send = ordinary;\n" + source + _SEND)
                  for label, source in rows])


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsnet_')


if __name__ == '__main__':
    raise SystemExit(main())
