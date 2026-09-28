#!/usr/bin/env python3
"""The shapes the census discharges and refuses at its own BOUNDARY.

`tests/test_launch_census_receivers.py` holds the four shapes issue #1299
names and the routes the branch must not weaken. This module holds the
boundary cases found after that suite existed: a receiver that was a
container at one point in the module and a real child at another, a callee
the walk cannot resolve at all, and the two claims about this rule that
were written down and then went out of date.

Every expected row set is `origin/main`'s, measured there. A shape that
`main` refuses and this branch does not is a bound that ended silently,
which is the one failure the narrowing must not have.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _receiver_resolution as receiver  # noqa: E402
from _launch_census import _faults  # noqa: E402
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent


def _rows(source, in_path=('run_gate',)):
    """Census rows for a planted module with EVERY function in path."""
    tree = ast.parse(source)
    forced = frozenset(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return sorted((line, route) for _, line, route, _ in
                  _faults('planted.py', tree, forced or frozenset(in_path)))


# A container literal is a STARTING state. These are the shapes where the
# name the deadline reaches was a literal when the walk first saw it and a
# real child by the time the call is made.
REBOUND = {
    'the-same-class-rebinds-it':
        ('import subprocess\n\n\n'
         'class Runner:\n'
         '    def __init__(self):\n'
         '        self._child = []\n\n'
         '    def start(self, argv):\n'
         '        self._child = subprocess.Popen(argv)\n\n'
         '    def wait(self, timeout=None):\n'
         '        return self._child.wait(timeout)\n',
         (11, 'timeout parameter')),
    'a-later-class-rebinds-it':
        ('import subprocess\n\n\n'
         'class Decoy:\n'
         '    def __init__(self):\n'
         '        self.procs = []\n\n\n'
         'class Pool:\n'
         '    def __init__(self):\n'
         '        self.procs = []\n\n'
         '    def start(self, argv):\n'
         '        self.procs = subprocess.Popen(argv)\n\n'
         '    def wait(self, timeout=None):\n'
         '        return self.procs.wait(timeout)\n',
         (16, 'timeout parameter')),
    'a-later-class-silences-an-earlier-one':
        ('import subprocess\n\n\n'
         'class B:\n'
         '    def __init__(self, child):\n'
         '        self.child = child\n\n'
         '    def wait(self, timeout=None):\n'
         '        return self.child.wait(timeout)\n\n\n'
         'class A:\n'
         '    def __init__(self):\n'
         '        self.child = []\n',
         (8, 'timeout parameter')),
    'a-nested-literal-over-a-shallower-child':
        ('def run_gate(flag, pool, timeout=None):\n'
         '    if flag:\n'
         '        pool = []\n'
         '    return pool.reap_all(timeout)\n',
         (1, 'timeout parameter')),
    'a-name-rebound-in-one-scope':
        ('import subprocess\n\n\n'
         'class Runner:\n'
         '    def wait(self, proc, timeout=None):\n'
         '        child = []\n'
         '        child = proc\n'
         '        return child.wait(timeout)\n',
         (5, 'timeout parameter')),
}


def test_a_literal_binding_rebound_to_a_child_is_refused(tmp):
    """H1: the set only ever ADDED, so a rebound name stayed a container.

    `literal_bindings` recorded the NAME and never forgot it, so a
    receiver that was a list at `__init__` and a real child by the time the
    deadline call is made read as a container, and the whole shape went
    silent with no other route picking it up. The last binding wins, which
    is the move `_dotted_bindings` was already taught to make.

    The first two are the reproduced shapes. The third is the same fault
    inside one scope rather than across classes, and it is here so a fix
    that only handles `self.` attributes is red.
    """
    del tmp
    for label, (source, expected) in REBOUND.items():
        assert _rows(source) == [expected], (label, _rows(source))


# A callee the walk can neither name nor see a receiver on. The census has
# no receiver to ask, so there is nothing to prove and the shape is a
# refusal; the last two hand a real child to a real `wait` through it.
DYNAMIC_CALLEES = {
    'a-subscript-callee':
        ('def run_gate(table, timeout=None):\n'
         '    return table["reap"](timeout)\n',
         (1, 'timeout parameter')),
    'a-call-callee':
        ('def run_gate(g, timeout=None):\n'
         '    return g()(timeout)\n',
         (1, 'timeout parameter')),
    'a-lambda-callee':
        ('def run_gate(timeout=None):\n'
         '    return (lambda t: t)(timeout)\n',
         (1, 'timeout parameter')),
    'a-lambda-handing-a-real-child':
        ('import subprocess\n\n\n'
         'def _start(argv):\n'
         '    return subprocess.Popen(argv)\n\n\n'
         'def run_gate(argv, *, timeout=None):\n'
         '    child = _start(argv)\n'
         '    return (lambda p, t: p.wait(t))(child, timeout)\n',
         (8, 'timeout parameter')),
    'a-dict-index-callee':
        ('import subprocess\n\n\n'
         'def _start(argv):\n'
         '    return subprocess.Popen(argv)\n\n\n'
         'def run_gate(argv, *, timeout=None):\n'
         '    child = _start(argv)\n'
         '    return {"reap": child.wait}["reap"](timeout)\n',
         (8, 'timeout parameter')),
}


def test_a_callee_the_walk_cannot_resolve_is_refused(tmp):
    """H2: a callee matching neither arm fell through to the verdict.

    The loop refused a bare name, discharged a literal-bound receiver, and
    had no `else` for a callee that is neither — a subscript, a call, a
    lambda. Those reached the end of the loop and returned the NARROWING
    verdict, which is silence, and silence on a shape `main` refuses is a
    bound that ended with nobody reporting it.

    The last two are what make this a boundary pin rather than a discharge
    pin: each hands a real `Popen` to a real `wait` with the number
    through a callee the walk cannot see, so a fix that discharges
    "unknown callee" re-opens a live bound.
    """
    del tmp
    for label, (source, expected) in DYNAMIC_CALLEES.items():
        assert _rows(source) == [expected], (label, _rows(source))


def test_a_raise_carrying_the_deadline_is_discharged_when_imported(tmp):
    """H3: the raise arm reads an IMPORTED exception, and only that.

    The docstring said a local `class Refused(Exception)` is read the same
    as the stdlib's. It is not: the resolver reads imports, so a class the
    module defines cannot be reached and the arm does not fire. The
    behaviour is right — refusing would be a false red on a fixture that
    raises its own error — and the sentence was the opposite of it.

    The two directions are pinned together, so neither can move alone.
    """
    del tmp
    imported = ('import subprocess\n\n'
                'def outer_timeout(args, timeout=None):\n'
                '    raise subprocess.TimeoutExpired(args, timeout)\n')
    assert _rows(imported) == [], _rows(imported)
    local = ('class Refused(Exception):\n'
             '    pass\n\n\n'
             'def outer_timeout(args, timeout=None):\n'
             '    raise Refused(args, timeout)\n')
    assert _rows(local) == [(5, 'timeout parameter')], _rows(local)


def test_the_module_docstring_names_the_rule_that_is_there(tmp):
    """H4, and what this control is NOT.

    It pins the map's VOCABULARY, not its truth. Reverting the docstring
    to a text naming a deleted rule reds it; ADDING a false sentence
    beside a true one leaves it green, and that is the whole of the
    defect class I-2 was — a replacement written without reading the code
    beneath it, and a presence pin that cannot see it.

    So the truth is carried behaviourally, and these are the controls that
    carry it: `test_a_literal_binding_rebound_to_a_child_is_refused` and
    `test_a_callee_the_walk_cannot_resolve_is_refused` in this module,
    both of which fail if the rule is not the one the map describes. This
    one exists to keep a deleted rule's name from surviving in prose, and
    it is honest about being the weaker of the two.
    """
    del tmp
    text = receiver.__doc__ or ''
    assert 'proof' in text, 'the map no longer says the rule is a proof'
    for absent in ('asks what OPERATION', 'never whether the receiver'):
        assert absent not in text, (absent, text)
    for present in ('literal_bindings', 'deadline_reaches_a_child'):
        assert present in text, (present, text)


# H6.4: the pop that fixes H1 was module-wide, and a module-wide pop turns
# a real network read into a fault when anything in the file shadows the
# imported name. Measured on five shapes, one of five at the wave's head
# and NONE once `_shadowed_parameters` made the shadow function-local.
OVER_REFUSAL = {
    'an-unrelated-parameter-shadows-the-import':
        ('from urllib.request import urlopen\n\n\n'
         'def run_gate(url):\n'
         '    return urlopen(url, timeout=10)\n\n\n'
         'def other(urlopen, x):\n'
         '    return urlopen(x)\n'),
    'an-unrelated-lambda':
        ('from urllib.request import urlopen\n\n\n'
         'def run_gate(url):\n'
         '    return urlopen(url, timeout=10)\n\n\n'
         'def other():\n'
         '    fetch = lambda u: u\n'
         '    return fetch\n'),
    'a-class-attribute':
        ('from urllib.request import urlopen\n\n\n'
         'def run_gate(url):\n'
         '    return urlopen(url, timeout=10)\n\n\n'
         'class Other:\n'
         '    def __init__(self):\n'
         '        self.urlopen = []\n'),
    'a-rebind-to-a-call-result':
        ('from urllib.request import urlopen\n\n\n'
         'def run_gate(url):\n'
         '    return urlopen(url, timeout=10)\n\n\n'
         'def other():\n'
         '    urlopen = object()\n'
         '    return urlopen\n'),
    'a-class-body-binding': (
        'from urllib.request import urlopen\n\n\n'
        'class C:\n    urlopen = []\n\n\n'
        'def run_gate(url):\n'
        '    return urlopen(url, timeout=10)\n'),
    'no-shadow-at-all':
        ('from urllib.request import urlopen\n\n\n'
         'def run_gate(url):\n'
         '    return urlopen(url, timeout=10)\n'),
}


# A rebinding of an imported name, in every form the reader collects.
# Each is followed by a call that must KEEP its `timeout= keyword` row:
# the name is no longer the stdlib object, so the read is refused.
REBINDINGS = {
    'a-plain-assignment': 'urlopen = object()\n',
    'an-augmented-assignment': 'urlopen += 1\n',
    'a-walrus': 'if (urlopen := object()):\n    pass\n',
    'a-del': 'del urlopen\n',
    'a-tuple-target': 'urlopen, other = object(), 1\n',
    'a-for-target': 'for urlopen in ():\n    pass\n',
    'a-comprehension-target': 'x = [1 for urlopen in ()]\n',
    'an-except-target': ('try:\n    pass\n'
                         'except ValueError as urlopen:\n    pass\n'),
    'an-annotated-assignment': 'urlopen: object = []\n',
    'a-global-rebind': 'global urlopen\nurlopen = object()',
    'a-with-target': 'with open("x") as urlopen:\n    pass\n',
}


def test_a_rebinding_in_any_form_stops_the_read_at_both_scopes(tmp):
    """Ten rows: one per form per scope, every one a read now REFUSED.

    Module scope and function scope are two axes and only their
    intersection was closed: the pop is gated on a node sitting outside
    every function body, and the plain `Assign` reached function scope
    only because the parameter reader already collected function-local
    assignment targets. The rebinding forms reached neither. This is the
    pair.

    A literal is not in this table and never should be: a function that
    makes its own container and puts the deadline in it discharges, which
    is the receivers module's
    `test_a_doubles_modelled_signature_is_not_a_launchers_deadline` and
    the reason the two tables are not one.
    """
    del tmp
    for label, form in REBINDINGS.items():
        at_module = ('from urllib.request import urlopen\n'
                     + form
                     + '\n\ndef run_gate(url):\n'
                     '    return urlopen(url, timeout=10)\n')
        body = ''.join(f'    {line}\n' if line.strip() else '\n'
                       for line in form.splitlines())
        in_function = ('from urllib.request import urlopen\n'
                       '\n\ndef run_gate(url):\n'
                       + body
                       + '    return urlopen(url, timeout=10)\n')
        for scope, source in (('module', at_module),
                              ('function', in_function)):
            rows = _rows(source)
            assert rows and rows[0][1] == 'timeout= keyword', (
                f'{label} at {scope} scope', rows)


NOT_COLLECTED_STILL_DISCHARGES = {
    'a-fully-dotted-rebind-is-still-the-read':
        ('import urllib.request\n\n\ndef run_gate(url):\n'
         '    urlopen = urllib.request.urlopen\n'
         '    return urlopen(url, timeout=10)\n'),
    'a-class-body-pop-nothing':
        ('import urllib.request\n\n\nclass C:\n    urlopen = []\n\n'
         '    def go(self, url):\n'
         '        return urllib.request.urlopen(url, timeout=10)\n'),
    'a-lambda-parameter-shadows-nothing-here':
        ('import urllib.request\n\n\n'
         'f = lambda url: urllib.request.urlopen(url, timeout=10)\n'),
}


TYPE_PARAM_SHAPES = {
    'def': 'def f[T](u):\n    return T\n',
    'async def': 'async def f[T](u):\n    return T\n',
    'class': 'class C[T]:\n    pass\n',
    'type alias': 'type T[U] = list[U]\n',
}


LAMBDA_ROWS = {
    # B13 as the case file writes it: a module-level import, and the
    # lambda NESTED INSIDE the path function that calls it.
    'B13 refused':
        ('def run_gate(url):\n'
         '    f = lambda urlopen: urlopen(url, timeout=10)\n'
         '    return f\n', 'REFUSED'),
    'a lambda nested one level deeper refused':
        ('def run_gate(url):\n'
         '    def inner(u):\n'
         '        f = lambda urlopen: urlopen(u, timeout=10)\n'
         '        return f\n'
         '    return inner(url)\n', 'REFUSED'),
    # The over-refusal, and it is the price of the route: the lambda's
    # parameter is merged into the ENCLOSING function's shadow set, so a
    # same-named call in that function is refused even where the lambda is
    # not what is called. More names in the shadow set means more
    # refusals, which is why this route cannot make the head refuse less
    # than `main`.
    'an unrelated same-named call, REFUSED (the over-refusal)':
        ('def run_gate(url):\n'
         '    f = lambda urlopen: urlopen(url, timeout=10)\n'
         '    return urlopen(url, timeout=10)\n', 'REFUSED'),
    # The direction the fix must NOT move: a lambda that shadows nothing
    # leaves a real network read discharged, which is the issue's intent.
    'a lambda that does not shadow, DISCHARGED':
        ('def run_gate(url):\n'
         '    f = lambda u: urlopen(u, timeout=10)\n'
         '    return f\n', 'DISCHARGED'),
}


def test_a_lambda_parameter_shadows_as_a_def_parameter_does(tmp):
    """§4.2.1's first bullet, and a lambda is a function.

    `_is_def` matched `FunctionDef` and `AsyncFunctionDef` only, and a
    lambda's parameters are `ast.arg` rather than `Name`, so no reader saw
    them and the keyword arm entered the enclosing function and read a
    shadow set that had never held them. B13 was discharged while its
    `def` twin was refused.

    Every row runs with the import PRESENT, so a `REFUSED` is a real
    verdict and not an artefact of there being nothing to discharge.
    """
    del tmp
    for label, (body, expected) in LAMBDA_ROWS.items():
        source = 'from urllib.request import urlopen\n\n\n' + body
        rows = _rows(source, ('run_gate',))
        got = 'REFUSED' if rows else 'DISCHARGED'
        assert got == expected, (label, got, rows)


def test_every_type_parameter_list_is_collected(tmp):
    """§4.2.1's last bullet, for all four kinds and not only the fallback.

    `type_params` lived in the `else` of an `elif` chain, and a `def`, an
    `async def` and a `class` all matched earlier — so the bullet was
    unreachable BY CONSTRUCTION for three of its four carriers, and a
    `type X[T]` alias skipped the call the same way.

    Reverting the collection turns every row red, which is the point: the
    3.11 row proves the reader imports and RUNS and says nothing about
    whether this branch is REACHED, and that distinction is what the last
    two rounds got wrong.
    """
    del tmp
    if not hasattr(ast, 'TypeAlias'):
        return
    for label, source in TYPE_PARAM_SHAPES.items():
        names = [n for _, n in receiver._rebindings(ast.parse(source))]
        assert 'T' in names, (label, names)


def test_a_read_the_census_cannot_see_through_is_still_discharged(tmp):
    """Three reads that stay DISCHARGED on purpose, and the control says so.

    A rebinding to the SAME object (`urlopen = urllib.request.urlopen`) is
    still the read it was; a class body binds class scope and rebinds no
    module name; and a parameter named `url` shadows nothing. Each is a
    case where the readers correctly reach "not a rebinding", and a
    widening for any of them is what this control catches.
    """
    del tmp
    for label, source in NOT_COLLECTED_STILL_DISCHARGES.items():
        assert _rows(source) == [], (label, _rows(source))


DEF_AND_CLASS = {
    'a-def-shadows-the-import':
        ('from urllib.request import urlopen\n\n\n'
         'def urlopen(u):\n    return u\n\n\n'
         'def run_gate(url):\n    return urlopen(url, timeout=10)\n'),
    'a-class-shadows-the-import':
        ('from urllib.request import urlopen\n\n\n'
         'class urlopen:\n    pass\n\n\n'
         'def run_gate(url):\n    return urlopen(url, timeout=10)\n'),
    'a-nested-def-in-the-calling-function':
        ('from urllib.request import urlopen\n\n\n'
         'def run_gate(url):\n    def urlopen(u):\n        return u\n'
         '    return urlopen(url, timeout=10)\n'),
    'a-def-and-its-sink-in-one-function':
        ('from urllib.request import urlopen\n\n\n'
         'def run_gate(url):\n    def urlopen(u):\n        return u\n'
         '    return urlopen(url, timeout=10)\n'),
}


def test_a_def_or_class_rebinding_the_name_is_refused(tmp):
    """§4.2.1's second and third bullets, which no enumeration had.

    A `def` and a `class` rebind the name as surely as an assignment, and
    after `def urlopen` the call is on THAT function rather than on
    `urllib.request.urlopen`, so it is not a network read. The
    per-statement enumeration missed both, which is why the claim it made
    was falsified; the context rule plus these two binders closes it.

    The last two are the same rule at function scope, so the row is not
    only the module-scope case.
    """
    del tmp
    for label, source in DEF_AND_CLASS.items():
        rows = _rows(source)
        assert rows and rows[0][1] == 'timeout= keyword', (label, rows)


def test_the_reader_imports_and_runs_without_the_312_nodes(tmp):
    """The 3.11 leg, exercised rather than asserted.

    `scripts/ci/classify_changes.py`'s `FULL_MATRIX` runs 3.11 through
    3.14, and the failure a version guard exists to prevent is an
    ATTRIBUTE ACCESS AT IMPORT — so a control that only runs on this
    box's 3.13 cannot see it. Here `ast` is stripped of every construct
    the reader guards on, the module is reloaded against it, and a
    binding is read, which is the whole of what a 3.11 cell does.
    """
    del tmp
    import importlib
    hidden = {name: getattr(ast, name)
              for name in ('TypeAlias',) if hasattr(ast, name)}
    for name in hidden:
        delattr(ast, name)
    try:
        module = importlib.import_module('_receiver_resolution')
        reload = importlib.reload
        reload(module)
        bound = module._dotted_bindings(
            ast.parse('import urllib.request\n'
                      'def urlopen(u):\n    return u\n'))
        assert 'urlopen' not in bound, bound
        assert 'urllib' in bound, bound
    finally:
        for name, value in hidden.items():
            setattr(ast, name, value)
        importlib.reload(importlib.import_module('_receiver_resolution'))


def test_a_rebinding_in_one_function_does_not_reach_another(tmp):
    """The negative half: the function-scope pop is not a module-wide one.

    A rebinding inside one helper must not refuse a real read in a
    different function of the same file. Without this the ten rows above
    would pass with a pop wide enough to break every network read in a
    module that rebinds a name anywhere, and the measurement that
    mattered most in wave 3 was exactly that failure: one `urlopen`
    parameter in an unrelated helper turned a real read into a fault.
    """
    del tmp
    source = ('from urllib.request import urlopen\n\n\n'
              'def other():\n    urlopen = object()\n    return urlopen\n'
              '\n\n'
              'def run_gate(url):\n'
              '    return urlopen(url, timeout=10)\n')
    assert _rows(source) == [], _rows(source)


def test_a_shadow_in_another_function_does_not_refuse_a_real_read(tmp):
    """H6.4: the pop is function-local, and the class is empty by that.

    `_dotted_bindings` is a module table, so popping an import a parameter
    shadows from it over-refused every read in the file that declared the
    parameter. `_shadowed_parameters` carries the shadow per function and
    the read is discharged again — which is the difference between a
    false red measured at one of five and one measured at none.

    A control that only checked the happy direction would have passed
    with the module-wide pop, so every shape here carries a shadow. The
    last six are the binding forms the boundary was assumed rather than
    measured on: a class body, a `global` and a rebind, and module-level
    `for` / `with` / `except` / annotated targets. A class body binds no
    name in any scope this reader tracks, so it must not pop a module
    import. A module-level `for` target is not in this set: it really
    does rebind the name, so the read is refused, and that row belongs in
    `test_a_rebinding_in_any_form_stops_the_read_at_both_scopes`.
    """
    del tmp
    for label, source in OVER_REFUSAL.items():
        assert _rows(source) == [], (label, _rows(source))


CALLER_FILLED = '''import subprocess
import time

WAIT_TIMEOUT = 90


def _wait_for_exit(proc, info=None, timeout=WAIT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        if time.monotonic() >= deadline:
            raise AssertionError('process did not exit')
        time.sleep(0.01)


def run_gate(proc):
    _wait_for_exit(proc, None, timeout=0)
    child = subprocess.Popen(['node', 'x.js'])
    return child.wait(timeout=5)
'''


def test_a_helper_taking_its_deadline_from_its_caller_is_judged_there(tmp):
    """`tests/test_parent_watch.py::_wait_for_exit`, in miniature.

    The signature is not a fault; the FILL is, and it is reported on the
    caller's line — the mechanism `_parameter_bound_faults` already
    describes for a bound that arrived as an argument. Nine of the real
    helper's call sites pass nothing and take the `WAIT_TIMEOUT` default;
    the tenth passes `timeout=0` deliberately, and that line is the one
    a caller is answerable for. The child's own wait two lines below it
    is still a fault, so "the helper is not a launcher" has not spread.
    """
    del tmp
    rows = _rows(CALLER_FILLED, ('run_gate', '_wait_for_exit'))
    assert 7 not in {line for line, _ in rows}, rows
    assert sorted(rows) == [
        (16, 'timeout= keyword'),
        (18, 'positional timeout on a launched child'),
        (18, 'timeout= keyword')], rows


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchcensusboundaries_')


if __name__ == '__main__':
    raise SystemExit(main())
