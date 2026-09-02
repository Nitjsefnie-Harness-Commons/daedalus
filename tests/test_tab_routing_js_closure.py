#!/usr/bin/env python3
"""What accounts for a mention is what a walk recorded, and nothing else.

Every family here is a spelling an earlier round covered by reading the
syntax around a mention rather than by a span some walk wrote down. The
promoting direction may never be silent, and ordinary values in a file
that happens to name a sender may not be dragged in either.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_js import _runtime_and_guard  # noqa: E402


_SEND = "send('focus-tab', { tab: chromeTab });\n"
_METHODS = "const m = { go() { send = %S; } };\n"
_PAIR = ("const m = { get hook() { return 1; },\n"
         "  set hook(v) { send = %S; } };\n")
_CTOR = "class K { constructor() { send = %S; } }\n"
_INLINE = "{ get hook() { send = %S; return 1; } }"

_FAMILIES = [
    ('arg-middle-comma', _METHODS
     + "const use = (a, o, b) => o.go();\nuse(1, m, 2);\n"),
    ('arg-middle-comma-unresolved', _METHODS
     + "const use = calls.length ? null : ((a, o, b) => o.go());\n"
     "use(1, m, 2);\n"),
    ('template-interp-tostring',
     "const m = { toString() { send = %S; return ''; } };\n"
     "void `${m}`;\n"),
    ('template-interp-class',
     "class K { toString() { send = %S; return ''; } }\n"
     "const k = new K();\nvoid `${k}`;\n"),
    ('value-paren-receiver', _METHODS + "const f = (m).go;\nf();\n"),
    ('value-bracket-receiver', _METHODS
     + "const f = [m.go][0];\nf();\n"),
    ('ctor-bound-const', _CTOR + "const k = new K();\nvoid k;\n"),
    ('ctor-bound-let', _CTOR + "let k;\nk = new K();\nvoid k;\n"),
    ('ctor-bound-no-parens', _CTOR + "const k = new K;\nvoid k;\n"),
    ('destructured-param-arrow', _METHODS
     + "const f = ({ go }) => go();\nf(m);\n"),
    ('destructured-default', _METHODS
     + "const { go = null } = m;\ngo();\n"),
    ('shift-left-setter', _PAIR + "m.hook <<= 1;\n"),
    ('shift-right-setter', _PAIR + "m.hook >>= 1;\n"),
    ('shift-unsigned-setter', _PAIR + "m.hook >>>= 1;\n"),
    ('bit-and-setter', _PAIR + "m.hook &= 1;\n"),
    ('bit-or-setter', _PAIR + "m.hook |= 1;\n"),
    ('bit-xor-setter', _PAIR + "m.hook ^= 1;\n"),
    ('bracket-shift-setter', _PAIR + "m['hook'] <<= 1;\n"),
    ('instance-shift-setter',
     "class K { get hook() { return 1; }\n"
     "  set hook(v) { send = %S; } }\n"
     "const m = new K();\nm.hook <<= 1;\n"),
    ('self-optional-hook',
     "const m = { get hook() { send = %S; return 1; },\n"
     "  self() { return this; } };\nvoid m.self()?.hook;\n"),
    ('self-optional-bracket',
     "const m = { get hook() { send = %S; return 1; },\n"
     "  self() { return this; } };\nvoid m.self()?.['hook'];\n"),
    ('member-write-two-level',
     "const m = { a: {} };\nm.a.b = () => { send = %S; };\nm.a.b();\n"),
    ('member-write-nullish',
     "const m = {};\nm.go ??= () => { send = %S; };\nm.go();\n"),
    ('member-write-logical-or',
     "const m = {};\nm.go ||= () => { send = %S; };\nm.go();\n"),
    ('member-write-sender-dynamic',
     "const m = {};\nconst k = 'hook';\nm[k] = %S;\n"
     "m.hook('focus-tab', { tab: chromeTab });\n"),
    ('member-write-sender-expr',
     "const m = {};\nm['ho' + 'ok'] = %S;\n"
     "m.hook('focus-tab', { tab: chromeTab });\n"),
    ('alias-const-handed', "const s = %S;\n"
     "const m = { go() { send = s; } };\n"
     "const use = (f) => f();\nuse(m.go);\n"),
    ('alias-helper-called', "function helper() { send = %S; }\n"
     "const m = { go() { helper(); } };\n"
     "const use = (f) => f();\nuse(m.go);\n"),
    ('literal-after-or',
     "void (calls.length || " + _INLINE + ").hook;\n"),
    ('literal-after-and', "void (1 && " + _INLINE + ").hook;\n"),
    ('literal-after-not', "void (!" + _INLINE + ".hook);\n"),
    ('literal-in-template', "void `${ " + _INLINE + ".hook }`;\n"),
    ('call-then-block', _METHODS
     + "const { go } = m;\ngo()\n{ void 0; }\n"),
    ('factory-arg-handed',
     "function mk(s) { return { go() { send = s; } }; }\n"
     "const m = mk(%S);\nconst use = (f) => f();\nuse(m.go);\n"),
    ('factory-arg-direct',
     "function mk(s) { return { go() { send = s; } }; }\n"
     "const m = mk(%S);\nm.go();\n"),
    ('alias-getter-inline-handed', "const s = %S;\n"
     "const m = { get hook() { send = s; return 1; } };\n"
     "const f = m;\nvoid [f][0].hook;\n"),
    ('member-write-sender-handed', "const m = {};\n"
     "m['ho' + 'ok'] = %S;\nconst f = m.hook;\n"
     "f('focus-tab', { tab: chromeTab });\n"),
    ('value-kept-paren', _METHODS + "const f = (m.go);\nf();\n"),
    ('value-kept-paren-call', _METHODS + "const f = (m).go;\n(f)();\n"),
    ('ctor-class-expression',
     "const K = class { constructor() { send = %S; } };\n"
     "const k = new K();\nvoid k;\n"),
    ('ctor-default-param',
     "class K { constructor(s = %S) { send = s; } }\n"
     "const k = new K();\nvoid k;\n"),
    ('destructured-default-fn', _METHODS
     + "const { go = ordinary } = m;\ngo();\n"),
    ('self-optional-paren',
     "const m = { get hook() { send = %S; return 1; },\n"
     "  self() { return this; } };\nvoid (m.self())?.hook;\n"),
    ('setter-destructure-assign', _PAIR + "({ hook: m.hook } = "
     "{ hook: 1 });\n"),
    ('setter-array-destructure', _PAIR + "[m.hook] = [1];\n"),
    ('setter-for-of-target', _PAIR
     + "for (m.hook of [1]) { void 0; }\n"),
]

# Values a file that happens to name a sender must not drag in.
_ORDINARY = [
    ('arithmetic', "const n = 1 + 2;\nvoid n;\n"),
    ('negation', "const flag = !calls.length;\nvoid flag;\n"),
    ('ternary', "const v = calls.length ? 1 : 2;\nvoid v;\n"),
    ('typeof', "const t = typeof calls;\nvoid t;\n"),
    ('concat', "const s2 = 'a' + 'b';\nvoid s2;\n"),
    ('regex', "const re = /x/;\nvoid re;\n"),
    ('builtin-date', "const d = new Date();\nvoid d.getTime();\n"),
    ('builtin-map', "const mp = new Map();\nmp.set(1, 2);\n"),
    ('builtin-set', "const seen = new Set();\nseen.add(1);\n"),
    # `p.then` on a value the reader cannot open is unprovable in the
    # exact layer on every tree, this branch included.
    ('builtin-promise',
     "const p = Promise.resolve(1);\np.then(() => undefined);\n", True),
    ('accumulator', "let total = 0;\nfor (const c of [1]) "
     "{ total = total + c; }\nvoid total;\n"),
]


def _program(body, promotes):
    seed = 'ordinary' if promotes else 'extCmd'
    return ("let send = " + seed + ";\n"
            + body.replace('%S', 'extCmd' if promotes else 'ordinary')
            + _SEND)


def _observed(tmp, name, promotes):
    path = Path(tmp) / name
    return [(label, *_runtime_and_guard(_program(body, promotes), path))
            for label, body in _FAMILIES]


# The family still silent while it routes: a sender taken out of a
# pattern's default reaches the body through a parameter no mention
# names.
_KNOWN_SILENT = {'destructured-default-fn'}


def test_recorded_spans_alone_account_for_a_mention(tmp):
    """No promoting spelling may route at runtime with a clean guard."""
    expected = [
        ('arg-middle-comma', True, True),
        ('arg-middle-comma-unresolved', True, True),
        ('template-interp-tostring', True, True),
        ('template-interp-class', True, True),
        ('value-paren-receiver', True, True),
        ('value-bracket-receiver', True, True),
        ('ctor-bound-const', True, True),
        ('ctor-bound-let', True, True),
        ('ctor-bound-no-parens', True, True),
        ('destructured-param-arrow', True, True),
        ('destructured-default', True, True),
        ('shift-left-setter', True, True),
        ('shift-right-setter', True, True),
        ('shift-unsigned-setter', True, True),
        ('bit-and-setter', True, True),
        ('bit-or-setter', True, True),
        ('bit-xor-setter', True, True),
        ('bracket-shift-setter', True, True),
        ('instance-shift-setter', True, True),
        ('self-optional-hook', True, True),
        ('self-optional-bracket', True, True),
        ('member-write-two-level', True, True),
        ('member-write-nullish', True, True),
        ('member-write-logical-or', True, True),
        ('member-write-sender-dynamic', True, True),
        ('member-write-sender-expr', True, True),
        ('alias-const-handed', True, True),
        ('alias-helper-called', True, True),
        ('literal-after-or', True, True),
        ('literal-after-and', True, True),
        ('literal-after-not', True, True),
        ('literal-in-template', True, True),
        ('call-then-block', True, True),
        ('factory-arg-handed', True, True),
        ('factory-arg-direct', True, True),
        ('alias-getter-inline-handed', True, True),
        ('member-write-sender-handed', True, True),
        ('value-kept-paren', True, True),
        ('value-kept-paren-call', True, True),
        ('ctor-class-expression', True, True),
        ('ctor-default-param', True, True),
        ('destructured-default-fn', True, False),
        ('self-optional-paren', True, True),
        ('setter-destructure-assign', True, True),
        ('setter-array-destructure', True, True),
        ('setter-for-of-target', True, True),
    ]
    observed = _observed(tmp, 'closure-promote.js', True)
    silent = [row for row in observed
              if row[1] and not row[2] and row[0] not in _KNOWN_SILENT]
    assert not silent, silent
    assert observed == expected, observed


def test_demoting_closure_shapes_keep_their_verdicts(tmp):
    """The demoting twin of every family, recorded pair by pair."""
    expected = [
        ('arg-middle-comma', False, True),
        ('arg-middle-comma-unresolved', False, True),
        ('template-interp-tostring', False, True),
        ('template-interp-class', False, True),
        ('value-paren-receiver', False, True),
        ('value-bracket-receiver', False, True),
        ('ctor-bound-const', False, True),
        ('ctor-bound-let', False, True),
        ('ctor-bound-no-parens', False, True),
        ('destructured-param-arrow', False, True),
        ('destructured-default', False, True),
        ('shift-left-setter', False, False),
        ('shift-right-setter', False, False),
        ('shift-unsigned-setter', False, False),
        ('bit-and-setter', False, False),
        ('bit-or-setter', False, False),
        ('bit-xor-setter', False, False),
        ('bracket-shift-setter', False, False),
        ('instance-shift-setter', False, False),
        ('self-optional-hook', False, True),
        ('self-optional-bracket', False, True),
        ('member-write-two-level', False, True),
        ('member-write-nullish', False, True),
        ('member-write-logical-or', False, True),
        ('member-write-sender-dynamic', True, True),
        ('member-write-sender-expr', True, True),
        ('alias-const-handed', False, True),
        ('alias-helper-called', False, True),
        ('literal-after-or', False, True),
        ('literal-after-and', False, True),
        ('literal-after-not', False, True),
        ('literal-in-template', False, True),
        ('call-then-block', False, True),
        ('factory-arg-handed', False, True),
        ('factory-arg-direct', False, True),
        ('alias-getter-inline-handed', False, True),
        ('member-write-sender-handed', True, True),
        ('value-kept-paren', False, False),
        ('value-kept-paren-call', False, True),
        ('ctor-class-expression', False, True),
        ('ctor-default-param', False, True),
        ('destructured-default-fn', False, True),
        ('self-optional-paren', False, True),
        ('setter-destructure-assign', False, True),
        ('setter-array-destructure', False, True),
        ('setter-for-of-target', False, True),
    ]
    observed = _observed(tmp, 'closure-demote.js', False)
    assert not [row for row in observed if row[1] and not row[2]], observed
    assert observed == expected, observed


def test_ordinary_values_stay_out_of_the_net(tmp):
    """A value the reader cannot classify is not a receiver by default."""
    path = Path(tmp) / 'closure-ordinary.js'
    observed = []
    for row in _ORDINARY:
        label, body = row[0], row[1]
        source = "let send = extCmd;\nsend = ordinary;\n" + body + _SEND
        observed.append((label, *_runtime_and_guard(source, path)))
    assert observed == [(row[0], False, len(row) > 2)
                        for row in _ORDINARY], observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsclose_')


if __name__ == '__main__':
    raise SystemExit(main())
