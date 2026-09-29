#!/usr/bin/env python3
"""A sink receiver that is a PARAMETER, discharged from its CALL SITES.

`tests/_deadline_reach.py`'s `deadline_reaches_a_child` discharges a
`timeout` parameter twice: on the tree's own bindings, which is
`literal_bindings`, and — where the receiver is a parameter, which no local
writing can prove — at every call site of the function that owns it, in
this module. The second is what recovers `tests/test_real_browser_harness.py:131`,
and `tests/test_launch_real_files.py` holds that site over a SHIPPED file.

This is the arm's own controls, and the false-green direction is the whole
of it. A rule that discharges more than its proof is invisible to every
other suite here: the shipped-file control above is one site, and a second
site an arm got wrong would be a second line nobody reads. So every
predicate the arm is built from has a row of its own, each a SINGLE
DELTA from a shape the arm discharges, so a mutant that drops one
predicate turns exactly one row red and the rest stay green. A suite
whose rows all move together cannot tell which predicate a mutation
removed.

The first control is the one that has to exist at all. A real child in a
list, the list handed to a nested double, and the double joining the child
with a `timeout` is the false green an arm like this admits if it stops
checking that the caller's container is not a child — and it carries a
RUNTIME leg, so the row is proved against a child that was really reaped
rather than a fabricated one alone. See `tests/_launch_plants.py` for why
a plant that reports a verdict without reaping anything is not counted.

The one thing this arm does not know is named beside it rather than here:
a call from a module the census does not read is not among the sites
consulted, and that limit lives in `tests/_launch_census.py`'s own "Not
enforced" list with `test_the_cross_module_limit_is_named_beside_a_control`
as the control that would fail if the reading changed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
import _util  # noqa: E402

# The arm's positive shape, and the shape every row below is ONE delta
# from. A test double is handed a recorder list, the deadline goes into
# it, and the module writes the list as a container literal at the only
# call site -- which is `tests/test_real_browser_harness.py:130-175` in
# miniature, down to the recorder arriving as a PARAMETER.
RECORDER = '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = []
    return build(recorded)
'''

# One row per predicate the arm is built from. Each is `RECORDER` with
# exactly one line changed, so the delta is readable without a diff and a
# mutation is attributable to one condition.
PREDICATES = {
    'a-call-passing-a-launch': RECORDER.replace(
        '    return build(recorded)',
        "    return build(subprocess.Popen(['x']))").replace(
            'def build(recorded):', 'import subprocess\n\n\n'
            'def build(recorded):', 1),
    'a-container-the-caller-built-from-a-launch': '''import subprocess


def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = [subprocess.Popen(['x'])]
    return build(recorded)
''',
    'a-spread-positional': RECORDER.replace(
        '    return build(recorded)', '    return build(*[recorded])'),
    'a-spread-keyword': RECORDER.replace(
        '    return build(recorded)',
        "    return build(**{'recorded': recorded})"),
    'an-omitted-argument': '''def build(recorded=None):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    return build()
''',
    'a-name-the-caller-does-not-write-as-a-literal': '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go(items):
    recorded = items
    return build(recorded)
''',
    'a-second-call-site-the-reader-cannot-see': '''import subprocess


def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = []
    return build(recorded)


def other():
    return build(subprocess.Popen(['x']))
''',
    'no-call-site-in-the-module': '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run
''',
    'a-receiver-that-is-not-a-parameter': '''def build():
    def run(args, *, timeout):
        return kids.wait(timeout)

    return run
''',
}

# The two call-site SPELLINGS the arm reads, each with the refusal beside
# its discharge so neither line is untested: a keyword fills a keyword-only
# parameter, and a call written at module scope has no enclosing function
# to read a `Name` argument's writings from.
SPELLINGS = {
    'keyword': ('''def build(*, recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go():
    recorded = []
    return build(recorded=recorded)
''', '''def build(*, recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


def go(items):
    recorded = items
    return build(recorded=recorded)
'''),
    'module-scope': ('''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


recorded = []
RUN = build(recorded)
''', '''def build(recorded):
    def run(args, *, timeout):
        recorded.append((list(args), timeout))
        return None

    return run


child = object()
recorded = child
RUN = build(recorded)
'''),
}

# The false green, in the shape the arm would admit if it stopped asking
# whether the caller's container is a child. `drain` receives the child out
# of a list the caller built, and the row the census must keep is the one
# on `drain` -- `run_gate` beside it refuses for the launch it places, so
# the two rows are read apart and only the second is the arm's.
LIST_HANDED = '''import subprocess


def run_gate(timeout):
    kids = [subprocess.Popen(['sleep', '2'])]

    def drain(kid, timeout=None):
        return kid.wait(timeout)

    return drain(kids[0], timeout=timeout)
'''

# The runtime leg of the same shape, and a REAL child: `drain` takes the
# list and joins the child in it with a one-second deadline, against a
# child that sleeps two. The margin is what makes BOUNDED an answer
# rather than a race, and the deadline is the parameter's own value rather
# than a literal so the leg exercises the route the row is about.
LIST_HANDED_RUNTIME = '''import subprocess


def spawn():
    return subprocess.Popen(["sleep", "2"])


def run_gate(timeout):
    def drain(kids, timeout=None):
        return kids[0].wait(timeout)

    kids = [spawn()]
    try:
        drain(kids, timeout=timeout)
    except subprocess.TimeoutExpired:
        kids[0].kill()
        kids[0].wait()
        return "BOUNDED"
    kids[0].kill()
    kids[0].wait()
    return "NOT BOUNDED"
'''


def _census(source):
    """Census rows for a planted module, every function forced in path.

    Forcing is what lets a nested `def` be read at all: the arm judges the
    parameter's OWN function, and a fixture that names no `run_gate` never
    puts that body in scope otherwise.
    """
    import ast  # noqa: PLC0415
    tree = ast.parse(source)
    forced = frozenset(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return sorted((row[1], row[2]) for row in
                  census._faults('planted.py', tree, forced))


def _run_line(source):
    """The line the nested `def run` is written on, addressed by text."""
    return next(number for number, text in enumerate(source.splitlines(), 1)
                if text.startswith('    def run('))


def _assert_refuses(label):
    """The plant's one `timeout parameter` row, on the nested `def run`."""
    source = PREDICATES[label]
    assert _signature_row(source) == [
        (_run_line(source), 'timeout parameter')], (
            label, _signature_row(source))


def _signature_row(source):
    """The one row on the nested `def run`, located by its own text.

    A line number moves with every unrelated edit to a plant, and a
    control that reds on one teaches the reader to ignore it, so the row
    is found the way the shipped-file controls find theirs. The plants
    carry the launch import or not, so the `def run` line differs between
    them and one constant would be wrong for half.
    """
    line = next(number for number, text in enumerate(source.splitlines(), 1)
                if text.startswith('    def run('))
    return [row for row in _census(source) if row[0] == line]


def _runtime(source):
    """What actually happened to the live child, or the raised type."""
    namespace = {}
    try:
        # The runtime leg IS an exec: the plant is built as text so the
        # census and the runtime read the same source shape.
        # pylint: disable-next=exec-used
        exec(compile(source, '<plant>', 'exec'), namespace)  # noqa: S102
        return namespace['run_gate'](1)
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


def test_a_recorder_handed_a_literal_at_every_call_site_discharges(tmp):
    """The arm's own positive, and the base every row below is a delta from.

    Without it each refusal row could pass because the arm is inert, which
    is a suite that cannot tell a working rule from a dead one. With it,
    every row is one line away from a discharge and the distance is the
    predicate under test.
    """
    del tmp
    assert _census(RECORDER) == [], _census(RECORDER)


def test_a_real_child_handed_through_a_list_to_a_double_is_still_refused(
        tmp):
    """The false green, with a runtime leg that reaped something real.

    The census row is on `drain`, which launches nothing and whose only
    hand-off is the `kids[0]` a caller passes it -- an argument the arm
    cannot call a container, so the parameter is a value it knows nothing
    about and the signature stays refused. Drop the "must be a proven
    container" condition and this row disappears while every other row here
    holds.

    The runtime leg is the same shape with a real `Popen` in a real list,
    so the refusal is not an artefact of a fixture that could not have run:
    the child sleeps two seconds, the deadline is one, and `BOUNDED` is
    what the census would be failing to report.
    """
    del tmp
    assert _census(LIST_HANDED) == [(4, 'timeout parameter'),
                                    (7, 'timeout parameter'),
                                    (10, 'timeout= keyword')], _census(
                                        LIST_HANDED)
    assert _runtime(LIST_HANDED_RUNTIME) == 'BOUNDED', _runtime(
        LIST_HANDED_RUNTIME)


def test_a_call_passing_a_launch_is_still_refused(tmp):
    """The argument is the child itself, and a launch is not a container."""
    del tmp
    label = 'a-call-passing-a-launch'
    assert _signature_row(PREDICATES[label]) == [
        (_run_line(PREDICATES[label]), 'timeout parameter')], label


def test_a_container_the_caller_built_from_a_launch_is_still_refused(tmp):
    """The reader's own derivation, reused: the flag from the `:531` entry.

    `recorded` IS written as a container literal, so the shape reads as a
    recorder for the first two conditions and only the third refuses it.
    That third is `_launch_bound_names` over the caller's body -- the
    derivation `tests/_launch_path.py` already records beside every
    caller-supplied name -- and it is a refusal because a list holding a
    `Popen` is a list that can be joined with a timeout.
    """
    del tmp
    _assert_refuses('a-container-the-caller-built-from-a-launch')


def test_a_spread_call_is_still_refused(tmp):
    """`*recorded` and `**{'recorded': ...}` name no position to read.

    A spread's shape is decided by a runtime length the walk cannot know,
    so an index into `call.args` lands on the star rather than on the value
    behind it. `_binding_names._spread_args` answers it for both spellings
    and both are here, because a rule that read one of them would still be
    right by accident on the other.
    """
    del tmp
    for label in ('a-spread-positional', 'a-spread-keyword'):
        _assert_refuses(label)


def test_an_omitted_argument_is_still_refused(tmp):
    """`build()` reaches the body with the DEFAULT, not with a list.

    A default is the callee's own, and a default this walk cannot read is
    a value no call site proved -- reading the omitted slot as an empty
    container would be assuming the answer.
    """
    del tmp
    _assert_refuses('an-omitted-argument')


def test_a_name_argument_the_caller_does_not_write_as_a_literal_is_refused(
        tmp):
    """`recorded = items` is a Name, and its value is the caller's own.

    The join is conservative for the same reason `literal_bindings` is: a
    name bound to a list on one line and to a child on the next is a child,
    and a last-write-wins reading of the order they appear in would
    discharge it. `items` is a parameter of the CALLER, which is the veto
    scoped rather than module-wide -- the same `ast.arg` refusal
    `literal_bindings` makes, applied inside the one scope it applies to.
    """
    del tmp
    _assert_refuses('a-name-the-caller-does-not-write-as-a-literal')


def test_every_call_site_must_agree_not_one_of_them(tmp):
    """Two sites, one provable and one not: the answer is the worse one.

    Reading the first satisfying site instead of the last is the failure
    a `next(...)` over the sites would have, and it is the one a suite
    holding only single-call-site rows cannot see.
    """
    del tmp
    _assert_refuses('a-second-call-site-the-reader-cannot-see')


def test_a_receiver_with_no_call_site_in_the_module_is_still_refused(tmp):
    """Zero call sites prove nothing, and this is also the cross-module one.

    A function nothing in this module calls is the same evidence as a
    function called only from a module the census does not read, and the
    second is a real shape: a test double handed a recorder list by a
    module no census pass parses. Refusing is the only reading that cannot
    be wrong, and it is what keeps a half-read value out of the discharge
    side. Named as a limit in `tests/_launch_census.py`.
    """
    del tmp
    _assert_refuses('no-call-site-in-the-module')


def test_a_receiver_that_is_not_a_parameter_is_still_refused(tmp):
    """`kids` is a name this function never binds, so it is not a value.

    A receiver that is not a parameter of the judged function or of one
    enclosing it has no call site to read, and the arm's answer to "no
    owner" is the refusal the file already made. This is the row that
    separates the arm from a blanket allowance for unproven receivers.
    """
    del tmp
    _assert_refuses('a-receiver-that-is-not-a-parameter')


def test_both_call_site_spellings_are_read(tmp):
    """A keyword-only slot, and a call written at module scope.

    The keyword row is the only thing that reaches the `'kw'` branch of the
    slot reader, and the module row is the only thing that reaches the
    "no enclosing function" reading, which is the wider of the two and so
    the one that refuses more. Each carries its refusal beside it, because
    a spelling that only ever discharges is a spelling nobody checked.
    """
    del tmp
    for label, (discharged, refused) in SPELLINGS.items():
        assert _census(discharged) == [], (label, 'discharge',
                                           _census(discharged))
        assert _signature_row(refused) == [
            (_run_line(refused), 'timeout parameter')], (
                label, 'refuse', _signature_row(refused))


def test_the_cross_module_limit_is_named_beside_a_control(tmp):
    """The arm's one incompleteness, disclosed where the rules are read.

    A narrowing that is not in the "not enforced" list is a narrowing no
    reader can audit, and this one is a disclosure rather than a fix: a
    call from outside the tree is invisible to a rule that reads call
    sites, and closing it means reading call sites across modules, which
    is the census's own boundary question.
    """
    del tmp
    # Unwrapped: a 79-column disclosure breaks a phrase across a newline,
    # and a control that had to match the break would pin the LINE LENGTH
    # rather than the reading.
    disclosed = ' '.join((census.__doc__ or '').split())
    for item in ('PARAMETER', 'OUTSIDE the tree', 'Not enforced',
                 'test_launch_deadline_reach.py'):
        assert item in disclosed, item
    assert (Path(__file__).resolve().parent
            / 'test_launch_deadline_reach.py').is_file()


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchdeadlinereach_')


if __name__ == '__main__':
    raise SystemExit(main())
