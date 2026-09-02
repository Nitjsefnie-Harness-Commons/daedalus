#!/usr/bin/env python3
"""Every way a body carrying a sender can be reached must be answered.

A promoting body is reached through more than a named receiver: an inline
literal, an instance, a factory result, `this`, a method value handed
somewhere, a member written after the fact. Each family below runs under
node in both directions; the promoting spelling may never be silent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_js import _runtime_and_guard  # noqa: E402


_SEND = "send('focus-tab', { tab: chromeTab });\n"
_LITERAL = ("const m = {\n"
            "  get hook() { send = %S; return 1; },\n"
            "  set hook(v) { send = %S; },\n"
            "  go() { send = %S; },\n"
            "  tag(s) { send = %S; return 1; } };\n")
_CLASS = ("class K {\n"
          "  get hook() { send = %S; return 1; }\n"
          "  set hook(v) { send = %S; }\n"
          "  go() { send = %S; }\n}\n")
_GETTER = "{ get hook() { send = %S; return 1; } }"
_METHODS = "const m = { go() { send = %S; } };\n"

_FAMILIES = [
    ('inline-literal-getter', "void (" + _GETTER + ").hook;\n"),
    ('inline-literal-bracket', "void (" + _GETTER + ")['hook'];\n"),
    ('class-new-inline-getter', _CLASS + "void new K().hook;\n"),
    ('factory-inline-call',
     "const make = () => (" + _GETTER + ");\nvoid make().hook;\n"),
    ('iife-factory-read',
     "const m = (() => (" + _GETTER + "))();\nvoid m.hook;\n"),
    ('iife-factory-method',
     "const m = (() => ({ go() { send = %S; } }))();\nm.go();\n"),
    ('assign-getter-onto',
     "const m = {};\nObject.assign(m, " + _GETTER + ");\nvoid m.hook;\n"),
    ('assign-method-onto', "const m = {};\n"
     "Object.assign(m, { go() { send = %S; } });\nm.go();\n"),
    ('proxy-inline-handler', "void new Proxy({},\n"
     "  { get() { send = %S; return 1; } }).x;\n"),
    ('define-property-getter', "const m = {};\n"
     "Object.defineProperty(m, 'hook',\n"
     "  { get() { send = %S; return 1; } });\nvoid m.hook;\n"),
    ('backtick-call', _LITERAL + "m.go``;\n"),
    ('fn-proto-call-call', _LITERAL + "Function.prototype.call.call(m.go);\n"),
    ('computed-method-call-expr', _LITERAL + "m['g' + 'o']();\n"),
    ('method-to-unresolved-callee', _LITERAL
     + "const helper = calls.length ? null : ((f) => f());\n"
     "void helper(m.go);\n"),
    ('method-passed-to-resolved', _LITERAL
     + "const run = (f) => f();\nrun(m.go);\n"),
    ('method-passed-to-fn-decl', _LITERAL
     + "function run(f) { f(); }\nrun(m.go);\n"),
    ('method-default-param', _LITERAL
     + "function run(f = m.go) { f(); }\nrun();\n"),
    ('tagged-template-method', _LITERAL + "void m.tag`x`;\n"),
    ('nested-destructure',
     "const wrap = { inner: " + _GETTER + " };\n"
     "const { inner: { hook } } = wrap;\nvoid hook;\n"),
    ('method-destructured', _LITERAL + "const { go } = m;\ngo();\n"),
    ('nonleading-spread', _LITERAL + "const bag = { a: 1, ...m };\n"
     "void bag;\n"),
    ('this-method-literal', "const m = { other() { send = %S; },\n"
     "  go() { this.other(); } };\nm.go();\n"),
    ('this-getter-literal', "const m = {\n"
     "  get hook() { send = %S; return 1; },\n"
     "  go() { return this.hook; } };\nm.go();\n"),
    ('this-private-static', "class K {\n"
     "  static #h() { send = %S; }\n"
     "  static go() { this.#h(); } }\nK.go();\n"),
    ('class-constructor', "class K { constructor() { send = %S; } }\n"
     "new K();\n"),
    ('derived-constructor-super',
     "class B { constructor() { send = %S; } }\n"
     "class K extends B {}\nnew K();\n"),
    ('class-instance-in-literal', _CLASS
     + "const wrap = { k: new K() };\nwrap.k.go();\n"),
    ('instance-passed-to-fn', _CLASS
     + "function run(o) { o.go(); }\nrun(new K());\n"),
    ('getter-returns-factory-call',
     "const m = { get mk() { return () => { send = %S; }; } };\nm.mk();\n"),
    ('sender-via-getter-call', "const m = { get s() { return %S; } };\n"
     "m.s('focus-tab', { tab: chromeTab });\n"),
    ('member-write-dynamic', "const m = {};\nconst k = 'go';\n"
     "m[k] = () => { send = %S; };\nm.go();\n"),
    ('member-write-expr-key', "const m = {};\n"
     "m['g' + 'o'] = () => { send = %S; };\nm.go();\n"),
    ('member-write-class-static', "class K {}\n"
     "K.go = () => { send = %S; };\nK.go();\n"),
    ('prototype-method-call', _CLASS + "K.prototype.go();\n"),
    ('equality-read', _LITERAL + "if (m.hook == 1) { void 0; }\n"),
    ('shift-compound-write', _LITERAL + "m.hook <<= 1;\n"),
    ('newline-expression-key', _LITERAL + "const k = 'ho';\n"
     "void m[k\n  + 'ok'];\n"),
    ('optional-call-method', _LITERAL + "void m.go?.();\n"),
    ('method-then-member', "const m = {\n"
     "  get hook() { send = %S; return 1; },\n"
     "  self() { return this; } };\nvoid m.self().hook;\n"),
    ('new-without-parens', _CLASS + "const m = new K;\nvoid m.hook;\n"),
    ('new-parenthesized-callee', _CLASS
     + "const m = new (K)();\nvoid m.hook;\n"),
    ('class-expression', "const K = class "
     + _GETTER.replace('{ get', '{\n  get') + ";\n"
     "const m = new K();\nvoid m.hook;\n"),
    ('optional-factory-call', "const make = () => (" + _GETTER + ");\n"
     "const m = make?.();\nvoid m.hook;\n"),
    ('bracket-member-value', "const outer = { inner: " + _GETTER + " };\n"
     "const m = outer['inner'];\nvoid m.hook;\n"),
    ('method-call-factory', "const f = { make: () => (" + _GETTER + ") };\n"
     "const m = f.make();\nvoid m.hook;\n"),
    ('class-name-value', "class K {\n"
     "  static get hook() { send = %S; return 1; } }\n"
     "const outer = { value: K };\nvoid outer.value.hook;\n"),
    ('object-destructured-binding',
     "const wrap = { inner: " + _GETTER + " };\n"
     "const { inner } = wrap;\nvoid inner.hook;\n"),
    ('super-getter-read',
     "class B { get hook() { send = %S; return 1; } }\n"
     "class K extends B { get hook() { return super.hook; } }\n"
     "const k = new K();\nvoid k.hook;\n"),
    ('extends-base-binding', "class B { go() { send = %S; } }\n"
     "const m = B;\nclass Sub extends m {}\nnew Sub().go();\n"),
    ('copy-destructure-method', _METHODS + "const { go } = m;\ngo();\n"),
    ('copy-destructure-renamed', _METHODS
     + "const { go: g } = m;\ng();\n"),
    ('copy-values-index', _METHODS
     + "const [fn] = Object.values(m);\nfn();\n"),
    ('copy-assign-target', _METHODS
     + "const t = {};\nObject.assign(t, m);\nt.go();\n"),
    ('copy-spread-argument', _METHODS
     + "const f = (o) => o.go();\nf({ ...m });\n"),
    ('copy-entries-loop', _METHODS
     + "for (const [k, v] of Object.entries(m)) { void k; v(); }\n"),
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


def test_promoting_reaches_are_never_silent(tmp):
    """No promoting spelling may route at runtime with a clean guard."""
    expected = [
        ('inline-literal-getter', True, True),
        ('inline-literal-bracket', True, True),
        ('class-new-inline-getter', True, True),
        ('factory-inline-call', True, True),
        ('iife-factory-read', True, True),
        ('iife-factory-method', True, True),
        ('assign-getter-onto', True, True),
        ('assign-method-onto', True, True),
        ('proxy-inline-handler', True, True),
        ('define-property-getter', True, True),
        ('backtick-call', True, True),
        ('fn-proto-call-call', True, True),
        ('computed-method-call-expr', True, True),
        ('method-to-unresolved-callee', True, True),
        ('method-passed-to-resolved', True, True),
        ('method-passed-to-fn-decl', True, True),
        ('method-default-param', True, True),
        ('tagged-template-method', True, True),
        ('nested-destructure', True, True),
        ('method-destructured', True, True),
        ('nonleading-spread', True, True),
        ('this-method-literal', True, True),
        ('this-getter-literal', True, True),
        ('this-private-static', True, True),
        ('class-constructor', True, True),
        ('derived-constructor-super', True, True),
        ('class-instance-in-literal', True, True),
        ('instance-passed-to-fn', True, True),
        ('getter-returns-factory-call', True, True),
        ('sender-via-getter-call', True, True),
        ('member-write-dynamic', True, True),
        ('member-write-expr-key', True, True),
        ('member-write-class-static', True, True),
        ('prototype-method-call', True, True),
        ('equality-read', True, True),
        ('shift-compound-write', True, True),
        ('newline-expression-key', True, True),
        ('optional-call-method', True, True),
        ('method-then-member', True, True),
        ('new-without-parens', True, True),
        ('new-parenthesized-callee', True, True),
        ('class-expression', True, True),
        ('optional-factory-call', True, True),
        ('bracket-member-value', True, True),
        ('method-call-factory', True, True),
        ('class-name-value', True, True),
        ('object-destructured-binding', True, True),
        ('super-getter-read', True, True),
        ('extends-base-binding', True, True),
        ('copy-destructure-method', True, True),
        ('copy-destructure-renamed', True, True),
        ('copy-values-index', True, True),
        ('copy-assign-target', True, True),
        ('copy-spread-argument', True, True),
        ('copy-entries-loop', True, True),
    ]
    observed = _observed(tmp, 'reach-promote.js', True)
    assert not [row for row in observed if row[1] and not row[2]], observed
    assert observed == expected, observed


def test_demoting_reaches_keep_their_verdicts(tmp):
    """The demoting twin of every family, recorded pair by pair."""
    expected = [
        ('inline-literal-getter', False, True),
        ('inline-literal-bracket', False, True),
        ('class-new-inline-getter', False, True),
        ('factory-inline-call', False, True),
        ('iife-factory-read', False, True),
        ('iife-factory-method', False, True),
        ('assign-getter-onto', False, True),
        ('assign-method-onto', False, True),
        ('proxy-inline-handler', False, True),
        ('define-property-getter', False, True),
        ('backtick-call', False, True),
        ('fn-proto-call-call', False, True),
        ('computed-method-call-expr', False, True),
        ('method-to-unresolved-callee', False, True),
        ('method-passed-to-resolved', False, True),
        ('method-passed-to-fn-decl', False, True),
        ('method-default-param', False, True),
        ('tagged-template-method', False, True),
        ('nested-destructure', False, True),
        ('method-destructured', False, True),
        ('nonleading-spread', False, True),
        ('this-method-literal', False, True),
        ('this-getter-literal', False, True),
        ('this-private-static', False, True),
        ('class-constructor', False, True),
        ('derived-constructor-super', False, True),
        ('class-instance-in-literal', False, True),
        ('instance-passed-to-fn', False, True),
        ('getter-returns-factory-call', False, True),
        ('sender-via-getter-call', True, True),
        ('member-write-dynamic', False, True),
        ('member-write-expr-key', False, True),
        ('member-write-class-static', False, True),
        ('prototype-method-call', False, True),
        ('equality-read', False, True),
        ('shift-compound-write', False, False),
        ('newline-expression-key', False, True),
        ('optional-call-method', False, False),
        ('method-then-member', False, True),
        ('new-without-parens', False, True),
        ('new-parenthesized-callee', False, True),
        ('class-expression', False, True),
        ('optional-factory-call', False, True),
        ('bracket-member-value', False, True),
        ('method-call-factory', False, True),
        ('class-name-value', False, True),
        ('object-destructured-binding', False, True),
        ('super-getter-read', False, True),
        ('extends-base-binding', False, True),
        ('copy-destructure-method', False, True),
        ('copy-destructure-renamed', False, True),
        ('copy-values-index', False, True),
        ('copy-assign-target', False, True),
        ('copy-spread-argument', False, True),
        ('copy-entries-loop', False, True),
    ]
    observed = _observed(tmp, 'reach-demote.js', False)
    assert not [row for row in observed if row[1] and not row[2]], observed
    assert observed == expected, observed


_PAIR = ("const make = () => ({\n"
         "  get hook() { send = %S; return 1; },\n"
         "  set hook(v) { send = %S; } });\n"
         "const m = make();\nvoid m.hook;\n")


def test_factory_accessor_pair_answers_the_getter(tmp):
    """Reading a factory-returned pair runs its getter.

    The demoting spelling is a contract taint: no name holds the
    literal the factory hands back, so nothing the walks recorded
    accounts for it and the net keeps the only handle on it.
    """
    path = Path(tmp) / 'factory-pair.js'
    observed = [(promotes, *_runtime_and_guard(
        _program(_PAIR, promotes), path)) for promotes in (True, False)]
    assert observed == [(True, True, True), (False, False, True)], observed


def test_a_method_value_called_in_a_body_is_reached(tmp):
    """A member read into a name and then called, inside a body.

    Two indexes hold the name's value: the walk that replays a body
    picks the first entry recorded at the declaration, the one that
    answers an invocation picks the last, and only the last carries
    the member. The top-level twin never goes through the first, so
    it is the control that already passed.
    """
    inner = "  const f0 = %R;\n  %C();\n  " + _SEND
    rows = []
    for label, read, call in (('plain', 'm.go', 'f0'),
                              ('parenthesised', '(m.go)', '(f0)')):
        body = inner.replace('%R', read).replace('%C', call)
        for where, holder in (
                ('bound', "  const m = { async go() { send = extCmd; } };\n"),
                ('later-bound', "  let m;\n"
                 "  m = { async go() { send = extCmd; } };\n")):
            rows.append((
                'in-function-' + where + '-' + label,
                "let send = ordinary;\nfunction ctx0() {\n"
                + holder + body + "}\nctx0();\n"))
            rows.append((
                'top-level-' + where + '-' + label,
                "let send = ordinary;\n" + (holder + body).replace(
                    '\n  ', '\n').lstrip()))
    path = Path(tmp) / 'method-value.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source in rows]
    assert observed == [(label, True, True) for label, _ in rows], observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsreach_')


if __name__ == '__main__':
    raise SystemExit(main())
