#!/usr/bin/env python3
"""A sink receiver that is a PARAMETER, discharged from its CALL SITES.

`tests/_deadline_reach.py`'s `deadline_reaches_a_child` discharges a
`timeout` parameter twice: on the tree's own bindings, which is
`literal_bindings`, and — where the receiver is a parameter, which no local
writing can prove — at every call site of the function that owns it, in
this module. The second is what recovers the `test_real_browser_harness.py`
site at line 131, and `tests/test_launch_real_files.py` holds it over a
SHIPPED file.

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
from _deadline_plants import (LIST_HANDED, LIST_HANDED_RUNTIME,  # noqa: E402
                              PREDICATES, RECORDER, RENAMED, SPELLINGS)
import _launch_census as census  # noqa: E402
import _util  # noqa: E402


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


def _plant_line_of(source, snippet):
    """The line a snippet is written on, addressed by its own text.

    A line number moves with every unrelated edit to a plant, and a
    control that reds on one teaches the reader to ignore it.
    """
    return next(number for number, text in enumerate(source.splitlines(), 1)
                if snippet in text)


def _run_line(source):
    """The nested `def` carrying the deadline, addressed by its own text.

    Named by what it is rather than by a spelling the plants do not all
    share: every plant nests its double one level in and gives it a
    `timeout`, and the name is `run` in most and `inner` in the one whose
    owner has to be called like a `subprocess` member.
    """
    return next(number for number, text in enumerate(source.splitlines(), 1)
                if text.startswith('    def ') and 'timeout' in text)


def _signature_row(source):
    """The one row on the nested deadline-carrying `def`."""
    line = _run_line(source)
    return [row for row in _census(source) if row[0] == line]


def _assert_refuses(label):
    """The plant's one `timeout parameter` row, on the nested `def`."""
    source = PREDICATES[label]
    assert _signature_row(source) == [
        (_run_line(source), 'timeout parameter')], (
            label, _signature_row(source))


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
    """The false green, and the two claims it makes are DIFFERENT.

    The census leg is the arm's: a real child in a list the caller built,
    handed to a double, with the deadline going into that list. Every
    other condition the arm checks is satisfied -- the receiver is a
    parameter, the owner is called once, the call is inside a function, the
    slot is filled, and the argument IS a container literal -- so the only
    thing left holding the row is the "not a child" reading. An arm that
    stopped asking that discharges this site, and the assertion below
    changes; nothing else in the file does.

    The runtime leg is Python's, not the arm's: a real `Popen` in a real
    list, joined with a one-second deadline against a two-second child,
    reporting `BOUNDED`. It is what makes the census leg a statement about
    a child that can actually be reaped rather than about a list of things
    that are not. It cannot discriminate between two versions of the rule
    -- no runtime can -- and it is not claimed to.
    """
    del tmp
    expected = [(_plant_line_of(LIST_HANDED, 'def run('),
                 'timeout parameter')]
    assert _census(LIST_HANDED) == expected, _census(LIST_HANDED)
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
    """`*recorded` and `**{'recorded': ...}`, refused for different reasons.

    A `Starred` in front of the slot is refused by the star check
    (`test_a_star_before_a_later_slot_is_still_refused` carries the shape
    that check exists for, where the star is NOT on the slot); a `**`
    fills no positional slot at all, so the omitted-argument condition
    refuses it.

    The `**` half is also the reason the check is scoped to the slot and
    is not `_binding_names._spread_args` verbatim: `build(recorded,
    **extra)` is the `recorded` the call wrote, and it must keep
    discharging, while `build(*spread, [])` against a three-parameter
    signature is not the `[]` at index one. Both are pinned.
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


def test_a_call_site_that_is_itself_a_launch_is_still_refused(tmp):
    """`subprocess.run([])`: the call fills the parameter with a child.

    The owning function is named like a `subprocess` member here, which is
    what makes this row reachable at all -- `_is_launch` reads the CALL, not
    the argument, and an owner whose own name is a launcher member is the
    one shape where both readings apply at once. The argument is a literal
    list, so the container condition is satisfied and this row is carried
    by the child condition alone.
    """
    del tmp
    _assert_refuses('a-call-site-that-is-itself-a-launch')


def test_an_argument_is_read_by_its_own_name_not_the_parameter_s(tmp):
    """The discriminating pair, and the one row the file lacked.

    Every other row here spells the parameter and the argument with the
    same identifier, so a reader that looked the argument up by the
    parameter's name passed all of them -- the shared identifier is the
    axis the defect lives on, and a suite that only ever holds one value of
    an axis cannot falsify a reader wrong on it.

    The refusal is the one that discriminates. The caller's scope carries
    a literal `kid` AND a literal `captured`, and the argument is a third
    name, `child`, that the caller takes as a parameter: read by the
    parameter's name this discharges on the unrelated `kid`; read by the
    argument's own name it is a caller's parameter, and a caller's
    parameter is not a proven container.
    """
    del tmp
    assert _census(RENAMED['discharge']) == [
        (_plant_line_of(RENAMED['discharge'], 'return build(captured)'),
         'timeout= keyword')], _census(RENAMED['discharge'])
    _assert_refuses('an-argument-named-other-than-the-parameter')


def test_a_star_before_a_later_slot_is_still_refused(tmp):
    """A `Starred` takes ONE index and expands to a runtime-many.

    `slot` is a position in the SIGNATURE, so every index after a star is
    unprovable -- and a one-parameter signature cannot show it, because
    there the star lands ON the slot and the container condition refuses
    it for a different reason. This row is the shape the check exists for:
    the star is at 0, the parameter is at 1, and the argument the reader
    would pick up is a literal while the value the body receives is a real
    child out of the spread's second element.
    """
    del tmp
    _assert_refuses('a-star-before-a-later-slot')


def test_a_launch_called_inside_a_literal_container_is_still_refused(tmp):
    """`build([Popen(...)])`: a container literal is a container, not a
    receipt.

    Nothing else in the arm sees this. The argument is a literal, so the
    container condition admits it; it is not a `Name`, so the
    launch-bound-names derivation never runs; and the call is not itself a
    launcher. Only the walk over the argument's own subtree finds the
    launch inside the list, which is why this row exists beside the
    `_member_aliases` rows rather than instead of them.
    """
    del tmp
    _assert_refuses('a-launch-called-inside-a-literal-container')


def test_every_member_alias_spelling_of_a_launch_is_still_refused(tmp):
    """`pop = subprocess.Popen` is a receiver spelling the reader knows.

    `tests/_launch_path.py::_is_launch` counts a member bound to a name as
    one of its four ways of naming a launch, and `_launch_bound_names` was
    already handed the caller's own aliases. The walk over the argument
    was not, so the three spellings of one call disagreed: the direct one
    refused and the two aliased ones discharged. All three rows are here
    because they are three spellings of one fact, and a suite holding only
    the direct one cannot tell a reader that reads aliases from one that
    reads the module name.
    """
    del tmp
    for label in ('a-launch-called-inside-a-literal-container',
                  'a-launch-through-a-local-member-alias',
                  'a-launch-through-a-module-member-alias'):
        _assert_refuses(label)


def test_a_call_site_written_at_module_scope_is_still_refused(tmp):
    """The arm reads an enclosing FUNCTION, and this call is in none.

    Reading the whole tree at a module-scope call site is WIDER than the
    table the arm sits behind, not narrower: `literal_bindings` also vetoes
    an `except ... as`, a `match` capture, an import and a `def` name, and
    this join carries none of those. So a call outside every function is
    a refusal rather than a second reading of the module, and the row
    stays whatever the module-wide table says.
    """
    del tmp
    _assert_refuses('a-call-site-written-at-module-scope')


def test_a_nested_helper_that_shadows_the_name_is_still_refused(tmp):
    """The `ast.arg` veto, applied inside the caller's OWN scope.

    `inner` takes a `recorded` of its own and writes a literal to it, so a
    reader that walked the name without the veto would call that a proven
    container. It is not: the name the call site passes is bound to the
    PARAMETER `inner` declares, and a parameter's value is its caller's. The
    veto does not cross scopes -- the double's own `recorded` is not a
    writing in the caller, which is what makes `RECORDER` discharge -- and
    this row is the half of that rule it does reach.
    """
    del tmp
    _assert_refuses('a-nested-helper-that-shadows-the-name')


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
    """A keyword-only slot, and a positional-only one.

    The keyword row is the only thing that reaches the `'kw'` branch of the
    slot reader and the positional-only row the `posonlyargs` half of the
    positional one; neither is reached by a `def f(x)` signature, so a slot
    reader that ignored either would pass every other row here. Each
    carries its refusal beside it, because a spelling that only ever
    discharges is a spelling nobody checked.
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
