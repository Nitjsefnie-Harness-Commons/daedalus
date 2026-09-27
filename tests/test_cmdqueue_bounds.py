#!/usr/bin/env python3
"""Controls for the poll-attempt bound on test-side command queue readers.

`_bounded_polls` counts queue probes, not elapsed time. The controls
that drive the reader live with it.
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
    _bound_blocks,
    _bound_to,
    _bounded_polls,
    _callee_name,
    _enclosing_bound_blocks,
    _enclosing_scopes,
    _poll_budget,
    _reached_through,
    _scope_map,
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
    """The bound counts the probes, and spares a reader under the ceiling."""
    for max_polls, over, under in ((0, 1, 0), (10, 11, 10)):
        failure, spent = _polls_under_a_ceiling(tmp, max_polls, over)
        assert isinstance(failure, AssertionError), (max_polls, over, failure)
        assert spent == max_polls + 1, (max_polls, over, spent)
        assert str(max_polls) in _polls_named_in(str(failure)), failure
        assert 'poll' in str(failure), failure
        assert _polls_under_a_ceiling(tmp, max_polls, under)[0] is None, (
            max_polls, under)


def test_the_poll_ceiling_states_the_probes_and_names_no_cause(tmp):
    """The refusal names what it counted, never a sleep it never saw."""
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

    The tolerance is `_POLL_HEADROOM`: the ceiling observes nothing and
    does not move when the reader does.
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
    """The ceiling is the product of both terms, not one fitted number."""
    for timeout in (0.01, 0.1, 0.2, 0.35, 1.0, 15.0):
        attempts = math.ceil(timeout / _cmdqueue.POLL_DELAY)
        assert _poll_budget(timeout) == (
            _PROBES_PER_ATTEMPT * _POLL_HEADROOM * attempts), (
            timeout, attempts)


def test_the_reader_probes_the_queue_through_the_bounded_names(tmp):
    """The bound's coverage is the reader's spelling, so it is pinned.

    The oracle is asserted live first: a walker finding nothing would
    make the absence below trivially true.
    """
    del tmp
    source = (Path(__file__).resolve().parent / '_cmdqueue.py').read_text()
    tree = ast.parse(source)
    reader = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_poll_queue_reads')
    scope_by_node = _scope_map(tree)
    roots = _bound_to(_enclosing_scopes(reader, scope_by_node) + [reader],
                      {arg.arg for arg in reader.args.args})
    reached = _reached_through(reader, roots)
    direct = {_callee_name(node) for node in ast.walk(reader)
              if isinstance(node, ast.Call)
              and any(isinstance(arg, ast.Name) and arg.id in roots
                      for arg in node.args)}
    reached |= {name for name in direct if name}
    for probe in _QUEUE_PROBES:
        assert probe in reached, (
            'the probe classifier is blind: the reader no longer shows a '
            f'{probe!r} probe, so the absence below proves nothing: '
            f'{sorted(reached)}')
    uncharged = reached - set(_QUEUE_PROBES)
    assert not uncharged, (
        'the reader reaches the queue through names the bound does not '
        f'count, so a runaway is uncharged and hangs: {sorted(uncharged)}; '
        f'the bound counts {list(_QUEUE_PROBES)}')


def test_the_poll_bound_names_what_it_was_entered_for(tmp):
    """A traceback says which of several bounds in one control raised."""
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
    """A reader spinning on a missing queue spends the count too."""
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


def _unbounded_reader_calls(source):
    """Reader call sites in `source` that no `_bounded_polls` block covers."""
    tree = ast.parse(source)
    scope_by_node = _scope_map(tree)
    loose = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        scope = scope_by_node.get(node)
        if scope is None:
            loose.append((node.lineno, '<module scope>'))
            continue
        scopes = _enclosing_scopes(node, scope_by_node) + [scope]
        names = _bound_to(scopes, _READER_ENTRY_POINTS)
        if _callee_name(node) not in names:
            continue
        if not _enclosing_bound_blocks(
                node, scope_by_node, source, '_bounded_polls'):
            loose.append((node.lineno, _scope_label(scope)))
    return loose


def _scope_label(scope):
    return getattr(scope, 'name', type(scope).__name__)


def test_every_reader_call_in_the_scanned_suites_is_inside_a_bound(tmp):
    """Each read in `test_cmdqueue.py` and `test_queued_command.py` is
    bounded, and only the read is.

    Nothing outside those two files is scanned. Without this control the
    property holds by construction: drop a wrapper and nothing reds.
    """
    del tmp
    tests_dir = Path(__file__).resolve().parent
    for suite in _SCANNED_SUITES:
        loose = _unbounded_reader_calls((tests_dir / suite).read_text())
        assert not loose, (
            f'{suite}: reader call sites no _bounded_polls block covers, so a '
            f'runaway read is uncharged and hangs: {loose}')


def test_the_poll_bound_is_never_charged_for_a_control_s_own_probes(tmp):
    """A reader's budget is never spent on the control's own setup."""
    del tmp
    tests_dir = Path(__file__).resolve().parent
    charged_to_the_control = []
    own_work = _QUEUE_PROBES + ('exists', 'mkdir', 'rmdir', 'write_text',
                                'unlink', 'read_text')
    for suite in _SCANNED_SUITES:
        source = (tests_dir / suite).read_text()
        tree = ast.parse(source)
        for block in _bound_blocks(tree, source, '_bounded_polls'):
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
