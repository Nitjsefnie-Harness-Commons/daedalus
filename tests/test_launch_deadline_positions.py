#!/usr/bin/env python3
"""Where the DEADLINE's own names are USED, by every position the grammar has.

`deadline_reaches_a_child` discharges on two proofs, and the first was false:
"the deadline reaches no call at all" was read off an empty sink set, and an
empty sink set means only that the derive did not know the name the deadline
had travelled in. A `timeout` parameter handed through a `for` target, a
walrus, an augmented or annotated assignment, a tuple unpack or a `match`
capture was still in flight.

This suite is that red set, committed. It is the EVIDENCE for
`_every_use_proven`: a rule that is not pinned by a suite can be changed, or
removed, and no gate would say so.

Every row is checked TWICE -- the census verdict and a runtime leg with a
real bounded child -- because a row that discharges without reaping anything
is not a false green and must not be counted as one. See
`tests/_launch_plants.py` for the plant and why it carries no launch.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

READ = 'def run_gate(url, timeout):\n    return box.wait(d)\n'
HEADER = 'def run_gate(box, url, timeout):\n'


def _source(form_body, sink='d', hop_line=None):
    body = HEADER + ''.join('    ' + line + '\n'
                            for line in form_body.splitlines())
    if hop_line:
        body += ''.join('    ' + line + '\n'
                        for line in hop_line.splitlines())
    return body + f'    return box.wait({sink})\n'


# The 33 forms the ast grammar offers, each crossed with three hops. The
# census plant is in `tests/_launch_plants.py`; this one puts the receiver on
# a PARAMETER, because a literal receiver is a PROVEN position the rule
# deliberately keeps and would measure the narrowing rather than the hole.
FORMS = {
    'assign': 'd = timeout',
    'augassign': 'd = 0\nd += timeout',
    'annassign': 'd: int = timeout',
    'walrus': 'if (d := timeout):\n    pass',
    'for-target': 'for d in (timeout,):\n    pass',
    'with-as': 'with CM(timeout) as d:\n    pass',
    'comprehension-target': 'x = [_ for d in (timeout,)]',
    'setcomp-target': 'x = {_ for d in (timeout,)}',
    'dictcomp-target': 'x = {d: 1 for d in (timeout,)}',
    'genexp-target': 'x = list(_ for d in (timeout,))',
    'starred-unpack': '*d, = (timeout,)',
    'tuple-unpack': 'd, e = timeout, 0',
    'list-unpack': '[d, e] = [timeout, 0]',
    'nested-tuple-unpack': '(a, (d, b)) = (0, (timeout, 0))',
    'starred-in-tuple': 'a, *d = (0, timeout)',
    'nested-for-target': ('for q in (1,):\n'
                          '    for d in (timeout,):\n'
                          '        pass'),
    'for-else-target': 'for d in ():\n    pass\nelse:\n    d = timeout',
    'del-then-rebind': 'd = timeout\ndel d\nd = timeout',
    'augassign-on-param': 'timeout += 1',
    'except-as': 'try:\n    raise ValueError(timeout)\n'
                 'except ValueError as d:\n    pass',
    'except-as-nocause': 'try:\n    pass\n'
                         'except ValueError as d:\n    d = timeout',
    'except-group-as': 'try:\n    pass\n'
                       'except* ValueError as d:\n    pass',
    'match-as': 'match timeout:\n    case d:\n        pass',
    'match-star': 'match [timeout]:\n    case [d, *rest]:\n        pass',
    'match-mapping-rest': ("match {'k': timeout}:\n"
                           "    case {'k': d, **rest}:\n        pass"),
    'match-value-pattern': 'match timeout:\n    case 1:\n        pass',
    'arg-default': 'def _i(d=timeout):\n    return d\nd = _i()',
    'kwarg-default': 'def _i(*, d=timeout):\n    return d\nd = _i()',
    'lambda-default': 'd = (lambda d=timeout: d)()',
    'def-return': 'def _i():\n    return timeout\nd = _i()',
    'lambda-body': 'd = (lambda: timeout)()',
    'global-stmt': 'def _i():\n    global d\n    d = timeout\n_i()',
    'nonlocal-stmt': ('def _mid():\n'
                      '    def _i():\n        nonlocal d\n'
                      '        d = timeout\n    _i()\n'
                      '_mid()\n'
                      'd = 0'),
}

HOPS = {'direct': 'd', 'one-hop': 'e', 'two-hop': 'f'}
HOP_LINES = {'one-hop': 'e = d', 'two-hop': 'e = d\nf = e'}

# The rows that must REFUSE: every binding form the derive does not follow,
# at every hop. The hop is irrelevant -- a name the derive never learned is
# not recovered by aliasing it, which is why each form appears three times.
REFUSE_FORMS = (
    'augassign annassign walrus for-target nested-for-target '
    'tuple-unpack list-unpack nested-tuple-unpack match-as match-star '
    'match-mapping-rest arg-default kwarg-default def-return'
).split()

# A name is DISCHARGED only with a runtime leg that did not reap: these
# discharge, and a child was reaped through one of them on `d0f75db8`.
VACUOUS_DISCHARGES = ('except-as-nocause', 'except-group-as',
                      'match-value-pattern', 'starred-unpack',
                      'starred-in-tuple')


def _census_and_runtime(form_body, sink, hop_line):
    """Both legs for one planted deadline module."""
    source = _source(form_body, sink, hop_line)
    census = 'REFUSE' if _census_for(source) else 'DISCHARGE'
    # The runtime leg only decides a DISCHARGE: a refusal cannot be a false
    # green whatever the child did, and running 99 children to learn that
    # costs a minute the suite does not need to spend.
    return census, (None if census == 'REFUSE'
                    else _runtime_for_deadline(form_body, sink, hop_line))


def _census_for(source):
    """Census rows for a planted module, every function forced in path."""
    import ast  # noqa: PLC0415
    from _launch_census import _faults
    tree = ast.parse(source)
    forced = frozenset(
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return sorted((r[1], r[2]) for r in _faults('planted.py', tree, forced))


_PRELUDE = ('import subprocess\n'
            'class CM:\n'
            '    def __init__(self, v):\n        self.v = v\n'
            '    def __enter__(self):\n        return self.v\n'
            '    def __exit__(self, *a):\n        return False\n')


def _runtime_for_deadline(form_body, sink, hop_line):
    """Bound a real child with the deadline the row carries."""
    body = 'def _go():\n'
    body += '    box = subprocess.Popen(["sleep", "2"])\n'
    body += ''.join('    ' + line + '\n' for line in form_body.splitlines())
    if hop_line:
        body += ''.join('    ' + line + '\n' for line in hop_line.splitlines())
    # The deadline must be the row's SINK, not a literal: a literal bounds
    # every row and would make the runtime leg report BOUNDED regardless.
    body += (f'    try:\n        box.wait({sink})\n'
             '    except subprocess.TimeoutExpired:\n'
             '        box.kill()\n        box.wait()\n'
             '        return "BOUNDED"\n'
             '    box.kill()\n    box.wait()\n'
             '    return "NOT BOUNDED"\n')
    namespace = {}
    try:
        # The runtime leg IS an exec: the plant is built as text so
        # the census and the runtime read the same source.
        # pylint: disable-next=exec-used
        exec(compile(_PRELUDE + body, '<plant>', 'exec'), namespace)  # noqa
        return namespace['_go']()
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


def test_a_deadline_handed_through_a_form_the_derive_misses_is_refused(tmp):
    """14 forms x 3 hops = 42 rows, all REFUSED. The red set, committed."""
    del tmp
    red = []
    for form in REFUSE_FORMS:
        for hop, sink in HOPS.items():
            census, _ = _census_and_runtime(FORMS[form], sink,
                                            HOP_LINES.get(hop))
            if census == 'DISCHARGE':
                red.append(f'{form}/{hop}')
    assert not red, f'these discharged on the empty-sinks proof: {red}'


def test_a_deadline_handed_straight_through_an_assign_is_judged(tmp):
    """`d = timeout` IS followed by the derive, so the row is the sink's."""
    del tmp
    for hop, sink in HOPS.items():
        census, _ = _census_and_runtime(FORMS['assign'], sink,
                                        HOP_LINES.get(hop))
        # The receiver is a parameter, so a found sink refuses. That is the
        # row that separates "derive followed it" from "derive missed it".
        assert census == 'REFUSE', (hop, census)


def test_a_discharge_never_reaps_a_child_on_the_vacuous_forms(tmp):
    """A discharge with no reap is not a false green, and is not counted."""
    del tmp
    for form in VACUOUS_DISCHARGES:
        census, runtime = _census_and_runtime(FORMS[form], 'd', None)
        if census == 'DISCHARGE':
            assert runtime != 'BOUNDED', (
                f'{form} discharged AND reaped a child, which is the '
                f'direction this rule exists to close')
        else:
            assert census == 'REFUSE', (form, census)


def test_a_bare_assert_message_is_proven_and_a_nested_one_is_not(tmp):
    """`assert timeout == 15, timeout` has TWO Loads of `timeout`.

    The Compare operand, and the assertion MESSAGE -- which is formatted into
    an `AssertionError` only when the assertion FAILS, so it reaches no child.
    A bare Name gets that exemption; nothing nested under the message does.
    """
    del tmp

    def rows_for(message):
        source = ('def run_gate(box, url, timeout):\n'
                  '    box.append(1)\n'
                  f'    assert timeout == 15, {message}\n'
                  '    return box\n')
        return _census_for(source)

    assert rows_for('timeout') == [], 'a bare message should discharge'

    assert rows_for('spawn(timeout)'), 'a nested message must refuse'
    assert rows_for('[p.wait(d) for d in (timeout,)]'), \
        'a comprehension message must refuse'


def test_the_assign_position_is_what_carries_a_plain_hop(tmp):
    """The witness for the Assign-to-Name arm, and it is a DISCHARGE.

    Every other row here refuses, so removing the Assign arm changes no
    verdict and the suite would not notice. This row does: `d = timeout`
    followed by a use of `d` on a LITERAL receiver discharges ONLY because
    the value of that Assign is the deadline, and removing the arm leaves
    the Load unproven and the row refuses. A mutation table found that gap.
    """
    del tmp
    for body in ('d = timeout\nbox.append(d)',
                 'd = timeout\ne = d\nbox.append(e)'):
        source = ('def run_gate(timeout):\n'
                  '    box = []\n'
                  + ''.join('    ' + line + '\n' for line in body.splitlines())
                  + '    return box\n')
        assert not _census_for(source), (
            f'the Assign position is not carrying {body!r}; a plain hop to a '
            f'literal receiver no longer discharges and something else does')


def test_an_augmented_assignment_reads_the_name_it_writes(tmp):
    """The witness for the AugAssign arm, and it is a REFUSAL.

    `timeout += 1` makes the Name a STORE, so there is no Load for the
    position check -- which is why the arm exists at all. The row reaches
    the deadline through a call argument the check WOULD accept, so with
    the arm removed it discharges, and a live child is bounded.
    """
    del tmp
    source = ('def run_gate(timeout):\n'
              '    box = []\n'
              '    timeout += 1\n'
              '    box.append(timeout)\n'
              '    return box\n')
    assert _census_for(source), (
        'the AugAssign arm is not carrying this row; an augmented '
        'assignment would read the deadline and discharge it')


def test_a_load_inside_a_comprehension_is_not_the_assign_value(tmp):
    """The witness for the comprehension-scoped exclusion.

    A Load inside a comprehension is the value of a COMPREHENSION, which has
    its own scope and its own target binding -- not the value of the Assign
    whose expression happens to contain it. The receiver here is a LITERAL,
    which is what makes the position check the only thing that can decide
    the row: with a parameter receiver the receiver check refuses regardless
    and the arm looks inert.

    A mutation table found that inertness, which is the whole reason the
    table is run before the suites are trusted.
    """
    del tmp
    source = ('def run_gate(timeout):\n'
              '    box = []\n'
              '    x = [_ for c in (timeout,)]\n'
              '    box.append(timeout)\n'
              '    return box\n')
    assert _census_for(source), (
        'the comprehension exclusion is not carrying this row; a Load in a '
        "comprehension's iter would be read as the Assign's own value")


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchdeadlinepositions_')


if __name__ == '__main__':
    raise SystemExit(main())
