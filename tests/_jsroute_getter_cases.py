"""Getter-invocation controls: the callable a getter returns runs too."""

_LET = "let promote = ordinary;\n"
_DEM = "promote = () => ordinary;"
_PRO = "promote = () => extCmd('focus-tab', { tab: chromeTab });"
_TAIL = ";\npromote();\n"


def _both(label, define, call='obj.p()'):
    """One getter shape in both directions.

    `define` wraps the write the call executes; the demotion
    row promotes first so the verdict rests on that write.
    """
    return [
        (label + '-demotion',
         _LET + _PRO + '\n' + define(_DEM) + call + _TAIL, False),
        (label + '-promotion',
         _LET + define(_PRO) + call + _TAIL, True),
    ]


def _literal(head, tail='}'):
    return lambda write: (
        "const obj = { get p() { return " + head + " { " + write
        + " }; " + tail + " };\n")


def _method(head):
    return lambda write: (
        "const obj = { " + head + " { " + write + " } };\n")


def _order(label, first, second, call, expected):
    return (label,
            _LET + "const obj = { get p() { " + first
            + " return () => { " + second + " }; } };\n" + call + _TAIL,
            expected)


_ARROW = _literal('() =>')
_ASYNC_ARROW = _literal('async () =>')

GETTER_CASES = [
    *_both('getter-returns-arrow', _ARROW),
    *_both('getter-returns-function', _literal('function ()')),
    *_both('getter-returns-async-arrow', _ASYNC_ARROW),
    *_both('getter-returns-declared-name', lambda write: (
        "function dem() { " + write + " }\n"
        "const obj = { get p() { return dem; } };\n")),
    *_both('getter-returns-alias', lambda write: (
        "const dem = () => { " + write + " };\nconst alias = dem;\n"
        "const obj = { get p() { return alias; } };\n")),
    *_both('class-getter-returns-arrow', lambda write: (
        "class K { get p() { return () => { " + write + " }; } }\n"
        "const obj = new K();\n")),
    _order('getter-body-demotes-then-return-promotes',
           _DEM, _PRO, 'obj.p()', True),
    _order('getter-body-promotes-then-return-demotes',
           _PRO, _DEM, 'obj.p()', False),
    *_both('getter-body-with-inert-return', lambda write: (
        "const obj = { get p() { " + write
        + " return () => {}; } };\n")),
    ('getter-returned-callable-sends-argument-promotion',
     _LET + _literal('(s) =>')("s('focus-tab', { tab: chromeTab });")
     + "obj.p(extCmd)" + _TAIL, True),
    ('getter-returned-callable-sends-argument-demotion',
     _LET + _literal('(s) =>')("s('focus-tab', { tab: chromeTab });")
     + "obj.p(ordinary)" + _TAIL, False),
    ('getter-body-promotes-then-argument-demotes',
     _LET + "const obj = { get p() { " + _PRO
     + " return () => {}; } };\nobj.p((() => { " + _DEM + " })())"
     + _TAIL, False),
    _order('getter-read-runs-body-not-return',
           _PRO, _DEM, 'void obj.p', True),
    _order('getter-read-skips-returned-promotion',
           _DEM, _PRO, 'void obj.p', False),
    ('getter-read-skips-lone-returned-promotion',
     _LET + _ARROW(_PRO) + "void obj.p" + _TAIL, False),
    *_both('optional-getter-call', _ARROW, 'obj?.p()'),
    *_both('computed-getter-call', _ARROW, "obj['p']()"),
    ('getter-returns-awaiting-arrow-demotion',
     _LET + _PRO + "\n" + _ASYNC_ARROW("await 0; " + _DEM) + "obj.p()"
     + _TAIL, True),
    ('getter-returns-awaiting-arrow-await-last',
     _LET + _PRO + "\n" + _ASYNC_ARROW(_DEM + " await 0;") + "obj.p()"
     + _TAIL, False),
    ('getter-returns-arrow-nested-await-before-write',
     _LET + _PRO + "\n" + _ARROW(
         "const inner = async () => { await 0; }; " + _DEM)
     + "obj.p()" + _TAIL, False),
    ('getter-returns-arrow-nested-await-after-write',
     _LET + _PRO + "\n" + _ARROW(_DEM + " const q = async () => "
                                 "{ await 0; };") + "obj.p()" + _TAIL,
     False),
    ('getter-returns-awaiting-arrow-promotion',
     _LET + _ASYNC_ARROW("await 0; " + _PRO) + "obj.p()" + _TAIL,
     (False, True)),
    ('getter-returns-arrow-naming-awaitable',
     _LET + _PRO + "\n" + _ARROW("const awaitable = 1; " + _DEM)
     + "obj.p()" + _TAIL, False),
    ('getter-returns-arrow-quoting-await',
     _LET + _PRO + "\n" + _ARROW("void 'await 0'; " + _DEM)
     + "obj.p()" + _TAIL, False),
    ('getter-returns-global-or-demoter',
     _LET + _PRO + "\nfunction dem() { " + _DEM + " }\n"
     "const obj = { get p() { return globalThis.mystery || dem; } };\n"
     "obj.p()" + _TAIL, (False, True)),
    ('getter-returns-global-or-promoter',
     _LET + "function pro() { " + _PRO + " }\n"
     "const obj = { get p() { return globalThis.mystery || pro; } };\n"
     "obj.p()" + _TAIL, True),
    ('getter-returns-global-or-inert',
     _LET + _PRO + "\n"
     "const obj = { get p() { return globalThis.mystery || ordinary; } };"
     "\nobj.p()" + _TAIL, True),
    ('getter-returns-chosen-demoter',
     _LET + _PRO + "\nconst choose = true;\n"
     "const obj = { get p() { return choose ? (() => { " + _DEM
     + " }) : ordinary; } };\nobj.p()" + _TAIL, (False, True)),
    ('getter-returns-unchosen-demoter',
     _LET + _PRO + "\nconst choose = false;\n"
     "const obj = { get p() { return choose ? (() => { " + _DEM
     + " }) : ordinary; } };\nobj.p()" + _TAIL, True),
    ('getter-returns-chosen-promoter',
     _LET + "const choose = true;\n"
     "const obj = { get p() { return choose ? (() => { " + _PRO
     + " }) : ordinary; } };\nobj.p()" + _TAIL, True),
    ('getter-returns-branch-demoter',
     _LET + _PRO + "\nconst choose = true;\n"
     "const obj = { get p() { if (choose) { return () => { " + _DEM
     + " }; } return ordinary; } };\nobj.p()" + _TAIL, (False, True)),
    ('getter-returns-branch-promoter',
     _LET + "const choose = true;\n"
     "const obj = { get p() { if (choose) { return () => { " + _PRO
     + " }; } return ordinary; } };\nobj.p()" + _TAIL, True),
    ('generator-method-demotion-stays-reported',
     _LET + _PRO + "\n" + _method('*p()')(_DEM) + "obj.p()" + _TAIL, True),
    ('generator-method-promotion-never-runs',
     _LET + _method('*p()')(_PRO) + "obj.p()" + _TAIL, False),
    *_both('shorthand-method', _method('p()')),
    *_both('function-property', _method('p: function ()')),
    *_both('async-shorthand-method', _method('async p()')),
    *_both('arrow-property', _method('p: () =>')),
]
