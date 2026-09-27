#!/usr/bin/env python3
"""Controls for the poll-attempt bound on test-side command queue readers.

`_cmdqueue_faults._bounded_polls` ends a reader whose poll loop stops
terminating, and it counts queue probes rather than elapsed time. These
are its own controls; the controls that drive the reader live with the
reader, in `test_cmdqueue.py` and `test_queued_command.py`.
"""
import ast
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _cmdqueue  # noqa: E402
from _cmdqueue_faults import (  # noqa: E402
    _POLL_HEADROOM,
    _PROBES_PER_ATTEMPT,
    _QUEUE_PROBES,
    _bounded_polls,
    _poll_budget,
)


def _polls_named_in(message):
    """The numbers the message carries, so '100' never answers for '10'."""
    return re.findall(r'\d+', message)


def _polls_under_a_ceiling(tmp, max_polls, globs):
    """Glob `globs` times under a `max_polls` ceiling; report the refusal."""
    queue = Path(tmp) / f'ceiling-{max_polls}-{globs}'
    queue.mkdir()
    failure = None
    spent = 0
    with _bounded_polls(max_polls):
        for _ in range(globs):
            spent += 1
            try:
                list(queue.glob('*.json'))
            except AssertionError as caught:
                failure = caught
                break
    return failure, spent


def test_the_poll_bound_refuses_a_reader_that_polls_past_it(tmp):
    """The bound counts the probes, so a reader that never stops is charged.

    It needs no clock, and every other bound a reader is given is
    consulted from inside one, so a reader that stops consulting the
    clock is charged by none of them. The `under` case is the direction
    that matters most: a ceiling that also refuses a bounded reader is a
    red the correct reader causes.
    """
    for max_polls, over, under in ((0, 1, 0), (10, 11, 10)):
        failure, spent = _polls_under_a_ceiling(tmp, max_polls, over)
        assert isinstance(failure, AssertionError), (max_polls, over, failure)
        assert spent == max_polls + 1, (max_polls, over, spent)
        assert str(max_polls) in _polls_named_in(str(failure)), failure
        assert 'poll' in str(failure), failure
        assert _polls_under_a_ceiling(tmp, max_polls, under)[0] is None, (
            max_polls, under)


def test_the_poll_ceiling_states_the_probes_and_names_no_cause(tmp):
    """The refusal reports what was counted, never why it blames a sleep.

    A reader that probes more per pass than today's spends the budget
    while sleeping on every pass, so a message naming the sleep is a
    diagnosis the guard never made.
    """
    ceiling = 4
    failure, spent = _polls_under_a_ceiling(tmp, ceiling, ceiling + 1)
    assert isinstance(failure, AssertionError), failure
    assert spent == ceiling + 1, (ceiling, spent)
    message = str(failure).lower()
    assert str(ceiling) in _polls_named_in(message), message
    for probe in _QUEUE_PROBES:
        assert probe in message, (probe, message)
    for blamed in ('sleep', 'without sleeping', 'no clock guard'):
        assert blamed not in message, (blamed, message)


def test_the_poll_ceiling_spires_a_reader_that_probes_often(tmp):
    """A reader spending more probes per pass than today's stays under it.

    Semantically identical to today's reader, sleeping on every pass, but
    probing the queue five times where it probes twice.

    The tolerance is `_POLL_HEADROOM`, not the derivation: the ceiling is
    a fixed product of two declared constants and does not observe the
    reader, so it does not move when the reader does. Five probes per
    pass fits because headroom is 4 and the correct spend is 2, and the
    derivation control is what pins the two terms — not this one.
    """
    attempts = 3
    per_pass = _PROBES_PER_ATTEMPT + 3
    ceiling = _poll_budget(attempts * _cmdqueue.POLL_DELAY)
    queue = Path(tmp) / 'busy'
    queue.mkdir()
    with _bounded_polls(ceiling) as spent:
        for _ in range(attempts):
            queue.is_dir()
            for _ in range(per_pass - 1):
                queue.glob('*.json')
    assert spent[0] == attempts * per_pass, spent[0]
    assert spent[0] < ceiling, (spent[0], ceiling)


def test_the_poll_ceiling_is_derived_from_the_probes_one_pass_costs(tmp):
    """The ceiling is a product of the domain's terms, not a fitted number.

    Asserting only `_poll_budget(t) == 8 * attempts` would let the
    headroom and the per-pass cost trade places unnoticed; asserting the
    product keeps both terms named in the code that sets the ceiling.
    """
    for timeout in (0.01, 0.1, 0.2, 0.35, 1.0, 15.0):
        attempts = math.ceil(timeout / _cmdqueue.POLL_DELAY)
        assert _poll_budget(timeout) == (
            _PROBES_PER_ATTEMPT * _POLL_HEADROOM * attempts), (
            timeout, attempts)


def test_the_reader_probes_the_queue_through_the_bounded_names(tmp):
    """The bound's coverage is this spelling, so the spelling is pinned.

    `_bounded_polls` counts the names in `_QUEUE_PROBES` and nothing else,
    so a reader that reaches the queue by any other API is uncharged and
    hangs rather than failing by name. A control naming the reader's
    probe is what makes that a red instead of a hang.
    """
    del tmp
    source = (Path(__file__).resolve().parent / '_cmdqueue.py').read_text()
    tree = ast.parse(source)
    reader = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_poll_queue_reads')
    reached = set()
    for node in ast.walk(reader):
        if not isinstance(node, ast.Call):
            continue
        if not any(isinstance(arg, ast.Name) and arg.id == 'directory'
                   for arg in node.args):
            continue
        callee = node.func
        if isinstance(callee, ast.Attribute):
            reached.add(callee.attr)
        elif isinstance(callee, ast.Name):
            reached.add(callee.id)
    uncharged = reached - set(_QUEUE_PROBES)
    assert not uncharged, (
        'the reader reaches the queue through names the bound does not '
        f'count, so a runaway is uncharged and hangs: {sorted(uncharged)}; '
        f'the bound counts {list(_QUEUE_PROBES)}')


def test_the_poll_bound_names_what_it_was_entered_for(tmp):
    """Three bounds in one control are told apart by what they are passed.

    `test_observed_file_or_queue_loss_keeps_dead_producer_wait_bounded`
    enters the bound three times with one ceiling, so the label is the
    only thing that says which wait a traceback is about.
    """
    unlabelled, _ = _polls_under_a_ceiling(tmp, 0, 1)
    queue = Path(tmp) / 'labelled'
    queue.mkdir()
    failure = None
    try:
        with _bounded_polls(0, what='the third same-id client wait'):
            queue.is_dir()
    except AssertionError as caught:
        failure = caught
    assert isinstance(failure, AssertionError), failure
    assert 'the third same-id client wait' in str(failure), failure
    assert 'the third same-id client wait' not in str(unlabelled), unlabelled


def test_the_poll_bound_restores_every_probe_it_patched(tmp):
    """A refusal propagating out of a control leaves no patch installed."""
    real_probes = {name: getattr(Path, name) for name in _QUEUE_PROBES}
    failure = None
    try:
        with _bounded_polls(0):
            Path(tmp).is_dir()
    except AssertionError as caught:
        failure = caught
    assert isinstance(failure, AssertionError), failure
    for name, real in real_probes.items():
        assert getattr(Path, name) is real, f'Path.{name} left patched'


def test_the_poll_bound_reaches_a_queue_that_does_not_exist(tmp):
    """A reader spinning on a missing queue spends the count and is bounded.

    The reader probes `is_dir` and only globs when the queue is there, so
    a ceiling counting globs alone would never see this one spend.
    """
    queue = Path(tmp) / 'never-created'
    spent = 0
    failure = None
    try:
        with _bounded_polls(2):
            for _ in range(3):
                spent += 1
                assert not queue.is_dir(), 'the queue was created'
    except AssertionError as caught:
        failure = caught
    assert spent == 3, spent
    assert isinstance(failure, AssertionError), failure
    assert '2' in _polls_named_in(str(failure)), failure


_SCANNED_SUITES = ('test_cmdqueue.py', 'test_queued_command.py')
_READER_ENTRY_POINTS = frozenset({
    'wait_for_command', 'wait_for_commands',
    'queued_command', 'queued_commands',
    '_wait_for_client_commands',
    '_answer_one_ext_command', '_answer_mcp_command',
})


def _callee_name(node):
    callee = node.func
    if isinstance(callee, ast.Attribute):
        return callee.attr
    if isinstance(callee, ast.Name):
        return callee.id
    return None


def _reader_names(owner):
    """The reader entry points `owner` can reach, aliases included."""
    names = set(_READER_ENTRY_POINTS)
    for statement in ast.walk(owner):
        if not isinstance(statement, ast.Assign):
            continue
        if _callee_name_for_value(statement.value) not in names:
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _callee_name_for_value(value):
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Name):
        return value.id
    return None


def _unbounded_reader_calls(source):
    """Reader call sites in `source` that no `_bounded_polls` block covers.

    A call with no enclosing function is resolved against the module body
    and reported as `<module scope>` rather than searched, so a reader
    call at module scope is a named red instead of a `min()` over an
    empty sequence.
    """
    tree = ast.parse(source)
    funcs = [node for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef)]
    loose = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        owners = [f for f in funcs
                  if f.lineno <= node.lineno <= (f.end_lineno or 0)]
        owner = min(owners, key=lambda f: (f.end_lineno or f.lineno)
                    - f.lineno) if owners else tree
        if _callee_name(node) not in _reader_names(owner):
            continue
        if not owners:
            loose.append((node.lineno, '<module scope>'))
            continue
        covered = any(
            isinstance(item, ast.With) and item.lineno <= node.lineno
            <= (item.end_lineno or 0)
            and any((ast.get_source_segment(source, entry.context_expr) or '')
                    .startswith('_bounded_polls') for entry in item.items)
            for item in ast.walk(owner))
        if not covered:
            loose.append((node.lineno, owner.name))
    return loose


def test_every_reader_call_in_the_scanned_suites_is_inside_a_bound(tmp):
    """Each read in these two suites is bounded, and only the read is.

    The scan covers `tests/test_cmdqueue.py` and
    `tests/test_queued_command.py` and nothing else. Other suites reach
    the reader through `tests/_cli_helpers.py` and `tests/_mcp_load.py`
    and are outside this control's world, which is why the name says
    "the scanned suites".

    Without it the property holds by construction: drop a wrapper and
    nothing reds until a runaway probe, which hangs — the one failure
    mode the bound exists to remove.
    """
    del tmp
    tests_dir = Path(__file__).resolve().parent
    for suite in _SCANNED_SUITES:
        loose = _unbounded_reader_calls((tests_dir / suite).read_text())
        assert not loose, (
            f'{suite}: reader call sites no _bounded_polls block covers, so a '
            f'runaway read is uncharged and hangs: {loose}')


def test_the_poll_bound_is_never_charged_for_a_control_s_own_probes(tmp):
    """A reader's bound holds the read and nothing of the control's own.

    A control's `is_dir` or `write_text` inside the block spends the
    reader's budget, so a control that probes a queue it just built
    would spend the ceiling on its own bookkeeping. Only the read call
    itself may sit inside; the guard's own controls probe deliberately
    and are not reader-driving blocks.
    """
    del tmp
    tests_dir = Path(__file__).resolve().parent
    charged_to_the_control = []
    own_work = _QUEUE_PROBES + ('exists', 'mkdir', 'rmdir', 'write_text',
                                'unlink', 'read_text')
    for suite in _SCANNED_SUITES:
        source = (tests_dir / suite).read_text()
        tree = ast.parse(source)
        for block in ast.walk(tree):
            if not isinstance(block, ast.With):
                continue
            if not any((ast.get_source_segment(source, entry.context_expr)
                        or '').startswith('_bounded_polls')
                       for entry in block.items):
                continue
            reads = [node for node in ast.walk(block)
                     if isinstance(node, ast.Call)
                     and _callee_name(node) in _READER_ENTRY_POINTS]
            if not reads:
                continue
            spans = [(node.lineno, node.end_lineno or node.lineno)
                     for node in reads]
            for node in ast.walk(block):
                if not isinstance(node, ast.Call):
                    continue
                callee = node.func
                if not isinstance(callee, ast.Attribute):
                    continue
                if callee.attr not in own_work:
                    continue
                if any(first <= node.lineno <= last
                       for first, last in spans):
                    continue
                charged_to_the_control.append(
                    (suite, node.lineno, callee.attr))
    assert not charged_to_the_control, (
        'a control does its own filesystem work inside a reader\'s poll '
        'bound, so the reader is charged for the control: '
        f'{sorted(charged_to_the_control)}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cmdqueue_bounds_'))
