"""Getter-returned callables handed to a callee as an argument.

The callee's invocation of the parameter must report, like the method
and data-property twins do, rather than read as nothing-to-replay.
"""

_LET = "let promote = ordinary;\n"
_RUN = "function runIt(fn) { fn(); }\n"
_PRO = "promote = () => extCmd('focus-tab', { tab: chromeTab });"
_DEM = "promote = () => ordinary;"
_TAIL = ";\npromote();\n"

_GETTER = "const obj = { get p() { return %s; } };\n"
_METHOD = "const obj = { p() { %s } };\n"
_DATA = "const obj = { p: %s };\n"


def _row(label, getter_body, call, expected, prelude=''):
    return (label,
            _LET + _GETTER % getter_body + prelude + _RUN + call + _TAIL,
            expected)


GETTER_ARG_CASES = [
    # The issue rows and their sibling spellings.
    _row('getter-arrow-argument', '() => { ' + _PRO + ' }',
         'runIt(obj.p)', True),
    _row('curried-getter-argument', '() => () => { ' + _PRO + ' }',
         'runIt(obj.p())', True),
    _row('computed-getter-argument', '() => { ' + _PRO + ' }',
         "runIt(obj['p'])", True),
    _row('optional-chain-getter-argument', '() => { ' + _PRO + ' }',
         'runIt(obj?.p)', True),
    _row('nested-member-getter-argument', '() => { ' + _PRO + ' }',
         'runIt(holder.inner.p)', True,
         'const holder = { inner: obj };\n'),

    # What must not change: resolvable arguments keep their verdicts.
    ('identifier-argument-promotion',
     _LET + _PRO + ';\n' + _RUN + 'runIt(promote)' + _TAIL, True),
    # An alias demoter beside a live promotion is already an over-report
    # on the base tree (the closing net flags it); the fix leaves it be.
    ('identifier-argument-demotion',
     _LET + _PRO + ';\nconst demoter = () => { ' + _DEM + ' };\n'
     + _RUN + 'runIt(demoter)' + _TAIL, (False, True)),
    ('inline-arrow-argument-promotion',
     _LET + _RUN + 'runIt(() => { ' + _PRO + ' })' + _TAIL, True),
    ('inline-arrow-argument-demotion',
     _LET + _PRO + ';\n' + _RUN + 'runIt(() => { ' + _DEM + ' })' + _TAIL,
     False),
    ('method-twin-stays-reported',
     _LET + _METHOD % _PRO + _RUN + 'runIt(obj.p)' + _TAIL, True),
    ('data-twin-stays-reported',
     _LET + _DATA % ('() => { ' + _PRO + ' }') + _RUN + 'runIt(obj.p)'
     + _TAIL, True),
    _row('getter-direct-call-promotion', '() => { ' + _PRO + ' }',
         'obj.p()', True),
    _row('getter-direct-call-demotion', '() => { ' + _DEM + ' }',
         'obj.p()', False),

    # Fail-closed boundary: the carry cannot prove a handed-away callable
    # inert or demoting, so those report too (Node runs clean).
    _row('getter-argument-demotion', '() => { ' + _DEM + ' }',
         'runIt(obj.p)', (False, True)),
    _row('getter-argument-inert', '() => {}', 'runIt(obj.p)', (False, True)),
    # A structured declaration leaves its binding no timeline entry, so
    # the alias path carries it empty too: fail closed (Node runs clean).
    ('alias-empty-timeline-argument',
     _LET + "const { handler: extracted } = { handler: ordinary };\n"
     + _RUN + 'runIt(extracted)' + _TAIL, (False, True)),
]
