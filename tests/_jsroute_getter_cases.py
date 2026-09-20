"""Getter-invocation controls: the callable a getter returns runs too."""

_LET = "let promote = ordinary;\n"
_DEM = "promote = () => ordinary;"
_PRO = "promote = () => extCmd('focus-tab', { tab: chromeTab });"
_TAIL = ";\npromote();\n"


def _both(label, define, call='obj.p()'):
    """One getter shape in both directions.

    `define` builds the receiver around the write its returned callable
    performs; a demotion follows a promotion so the verdict rests on it.
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

GETTER_CASES = [
    *_both('getter-returns-arrow', _ARROW),
    *_both('getter-returns-function', _literal('function ()')),
    *_both('getter-returns-async-arrow', _literal('async () =>')),
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
    _order('getter-read-runs-body-not-return',
           _PRO, _DEM, 'void obj.p', True),
    _order('getter-read-skips-returned-promotion',
           _DEM, _PRO, 'void obj.p', False),
    ('getter-read-skips-lone-returned-promotion',
     _LET + _ARROW(_PRO) + "void obj.p" + _TAIL, False),
    *_both('optional-getter-call', _ARROW, 'obj?.p()'),
    *_both('computed-getter-call', _ARROW, "obj['p']()"),
    ('getter-returns-global-or-demoter',
     _LET + _PRO + "\nfunction dem() { " + _DEM + " }\n"
     "const obj = { get p() { return globalThis.mystery || dem; } };\n"
     "obj.p()" + _TAIL, (False, True)),
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
    ('generator-method-demotion-stays-reported',
     _LET + _PRO + "\n" + _method('*p()')(_DEM) + "obj.p()" + _TAIL, True),
    ('generator-method-promotion-never-runs',
     _LET + _method('*p()')(_PRO) + "obj.p()" + _TAIL, False),
    *_both('shorthand-method', _method('p()')),
    *_both('function-property', _method('p: function ()')),
    *_both('async-shorthand-method', _method('async p()')),
    *_both('arrow-property', _method('p: () =>')),
]
