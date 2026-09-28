#!/usr/bin/env python3
"""A receiver the census has RESOLVED, which is not the bound concept.

Issue #1299. `_is_launch` proves a call IS a launch; what was missing is
the complementary statement for a RECEIVER — a callee the census can
prove is not a child — so a keyword named `timeout` was judged by the
concept it spells rather than by what the call is a call ON. Four shapes
reached that rule and are no bound: a network read, a test double's own
modelled signature (two sites), and a helper that takes its deadline from
its caller and launches nothing.

`tests/test_harness_launch_bounds.py` holds the routes this narrowing may
NOT weaken and is at its size ceiling, so the narrowing's own controls
are here. Every claim the census's docstring makes about what it does not
read has a plant below that would fail if the claim stopped being true.

Nothing here runs the shipped tree for its verdict. Those three files are
not on the launch path on main, so the census reports nothing at all for
them and a control that read only that proves nothing; the real-file
controls force the path instead, which is how the defect goes live when
`_realbrowser.py` reaches `_noderun.py`.
"""
import ast
import importlib
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
from _launch_fixtures import (  # noqa: E402
    HANG_DETECTOR_PROGRAM as _DETECTOR, write_source_tree as _tree)
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent

# The modules whose members the census derives a network read from. The
# derivation is the stdlib's own, read through `inspect`; nothing here
# names a member.
NETWORK_MODULES = ('urllib.request', 'http.client', 'socket')

# The fourteen sites of #1299 that are NOT on the launch path, so a
# control that only ran the shipped tree would be green before and after.
REAL_FILES = ('test_bridge_startup.py', 'test_parent_watch.py',
              'test_real_browser_harness.py')


def _rows(source, in_path=('run_gate',)):
    """Every census row for one planted module, as `(line, route)`."""
    return sorted((line, route) for _, line, route, _
                  in census._faults('planted.py', ast.parse(source),
                                    frozenset(in_path)))


def _full_census(root):
    """`census()` over a planted tree, the way the tree-wide control runs."""
    return census.census(Path(root), ('_noderun.py', '_stream_fake.py'))


def _forced_rows(source):
    """Census rows for a planted module with EVERY function in path.

    The same derivation the real-file controls use, so a planted shape
    that names no `run_gate` is still read: the table below is the lead's
    F1 table, and it was measured that way.
    """
    tree = ast.parse(source)
    in_path = frozenset(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return sorted((line, route) for _, line, route, _ in
                  census._faults('planted.py', tree, in_path))


def _real_rows(relative):
    """The census reading a REAL file with every function forced in path.

    The only way to reach these three files today: the path is forced, so
    a row here is a row the census will emit the day the real-browser
    harness joins the path.

    Forcing can only OVER-report, never under-report. It puts every
    function in the file into `in_path`, which widens the callee set the
    reachability arm matches a bare name against and adds an enclosing
    scope per function, so a row may appear that the real derivation
    would not produce — and no row the real derivation produces is
    missing. So a DISCHARGED row here is proof the rule is right about
    those lines, and a KEPT row is weaker evidence than it looks, being a
    bound under a reading strictly wider than the shipped one. Measured
    on all three files at `6b055a10`, the forced row set equals the union
    of the per-function solo derivations, so nothing is overstated today;
    when issue 1121's branch lands the forcing becomes redundant and this
    should read the real `census()` instead.
    """
    tree = ast.parse((TESTS / relative).read_text(encoding='utf-8'))
    in_path = frozenset(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return census._faults(relative, tree, in_path)


def _line_holding(relative, snippet, occurrence=1):
    """The line number of a snippet in a real file, or a loud failure.

    Addressed by TEXT rather than by a number, because the number moves
    with every edit above it and a control that reds on an unrelated
    addition teaches the reader to ignore it. A snippet that is GONE is
    still a failure: the site was deleted or reworded, and the claim it
    carried has to be re-decided rather than quietly dropped.
    """
    lines = (TESTS / relative).read_text(encoding='utf-8').splitlines()
    found = [number for number, text in enumerate(lines, 1)
             if snippet in text]
    if len(found) < occurrence:
        raise AssertionError((relative, snippet, occurrence, found))
    return found[occurrence - 1]


# --- a network read is not a child bound ----------------------------------

NETWORK_GATE = '''import subprocess
import urllib.request


def run_gate(url):
    with urllib.request.urlopen(url, timeout=10) as reply:
        child = subprocess.Popen(['node', 'x.js'])
        return child.wait(timeout=10), reply.status
'''


def test_a_network_read_is_not_a_bound_but_a_launch_beside_it_is(tmp):
    """The strong form: both verdicts in ONE module, one command's read.

    A narrowing that cannot tell the two apart is a narrowing that is
    either inert or a false-positive generator, and both are invisible
    when the two shapes live in different controls. So they share a
    module here, and the assertion is the EXACT row set: the read is
    gone, the child's wait is not, and the child's wait is on line 7.
    """
    del tmp
    assert _rows(NETWORK_GATE) == [
        (8, 'positional timeout on a launched child'),
        (8, 'timeout= keyword')], _rows(NETWORK_GATE)


def _network_members():
    """Every stdlib member a network read is spelled with, from the source.

    Derived here and NOT read back from the census, so a member list that
    went stale is a failure rather than a restatement of the list the
    control is checking. It is the stdlib's own member table filtered to
    the members whose signature takes a `timeout`, which is what a read
    is bounded with — the same derivation the census performs, written
    out twice on purpose.
    """
    found = []
    for module_name in NETWORK_MODULES:
        module = importlib.import_module(module_name)
        for name, value in sorted(vars(module).items()):
            if not callable(value):
                continue
            try:
                signature = inspect.signature(value)
            except (TypeError, ValueError):
                continue
            if 'timeout' in signature.parameters:
                found.append((module_name, name))
    return found


def test_every_network_read_the_stdlib_carries_is_discharged(tmp):
    """Each member, at a real call site — the list cannot be tuned.

    A census that carried `urlopen` alone would pass a suite holding only
    `urlopen`. This holds every member the stdlib derives, so a narrowing
    tuned to the one spelling the tree happens to use is red here.
    """
    del tmp
    members = _network_members()
    assert members, 'the derivation found no network read at all'
    for module_name, name in members:
        source = (f'import {module_name}\n\n'
                  'def run_gate(url):\n'
                  f'    return {module_name}.{name}(url, timeout=10)\n')
        assert not _rows(source), (module_name, name, _rows(source))


SPELLINGS = {
    'dotted': ('import urllib.request', 'urllib.request.urlopen'),
    'module-aliased': ('import urllib.request as urlr', 'urlr.urlopen'),
    'package-then-attribute': ('import urllib', 'urllib.request.urlopen'),
    'from-import': ('from urllib.request import urlopen', 'urlopen'),
    'from-import-aliased': (
        'from urllib.request import urlopen as fetch', 'fetch'),
    'module-then-from-import': (
        'import urllib.request\nfrom urllib.request import urlopen as read',
        'read'),
    'bound-to-a-local': (
        'import urllib.request\n_open = urllib.request.urlopen', '_open'),
    'local-chained-twice': (
        'import urllib.request\n_open = urllib.request.urlopen\n'
        '_again = _open', '_again'),
}


SHADOWED = (
    ('a-parameter-shadows-a-from-import',
     'from urllib.request import urlopen\n\ndef run_gate(urlopen, url):\n'
     '    return urlopen(url, timeout=10)\n'),
    ('a-local-lambda-shadows-it',
     'from urllib.request import urlopen\n\ndef run_gate(url):\n'
     '    urlopen = lambda u: u\n'
     '    return urlopen(url, timeout=10)\n'),
    ('a-call-result-rebinds-the-alias',
     'import urllib.request\n\ndef run_gate(url):\n'
     '    urllib = object()\n'
     '    return urllib.request.urlopen(url, timeout=10)\n'),
    ('a-subscript-rebinds-the-alias',
     'import urllib.request\n\ndef run_gate(url, table):\n'
     '    urllib = table["x"]\n'
     '    return urllib.request.urlopen(url, timeout=10)\n'),
)


def test_a_shadowed_import_binding_is_still_a_fault(tmp):
    """F4: the known false GREEN, pinned, and named in the disclosure.

    `_dotted_bindings` records an assignment only when its right-hand side
    resolves to a dotted name, so a parameter, a lambda, a call result or
    a subscript that rebinds an imported name leaves the IMPORT binding
    standing and the call below it is discharged. Empty expectations here
    are the bug, not the verdict: this control exists so that fixing the
    shadowing turns it red and forces the census's disclosure to be
    rewritten rather than quietly invalidated.

    None of the four occurs in the three real files, so this is latent
    rather than live. The direction is the one that matters — a narrowing
    whose failure mode is a DISCHARGE has its holes here — and it is the
    same shape of disclosure the module already carries for
    `sock.settimeout`.
    """
    del tmp
    for label, source in SHADOWED:
        assert _rows(source) == [], (label, _rows(source))


def test_the_receiver_is_resolved_rather_than_the_callee_spelled(tmp):
    """The mutation detector: a list of spellings is red here.

    Every entry is the SAME stdlib object reached a different way. A
    census keyed on the name as written reads four of the eight and
    refuses the rest, so this control is what makes "the arm fires for
    any network read" distinguishable from "it fires for `urlopen`
    spelled that way".
    """
    del tmp
    for label, (prologue, callee) in SPELLINGS.items():
        source = (f'{prologue}\n\n'
                  'def run_gate(url):\n'
                  f'    return {callee}(url, timeout=10)\n')
        assert not _rows(source), (label, _rows(source))


def test_a_call_that_is_not_a_read_keeps_its_timeout_fault(tmp):
    """The negative of the arm, in the two directions a resolver can fail.

    A callee the census cannot RESOLVE (`queue.get`, `thread.join`, a
    socket method) is not a network read, and neither is a callee it
    resolves to a live object that is not a read. The two are kept apart
    on purpose: a suite holding only unresolvable receivers cannot tell a
    read-set narrowed to nothing from one that admits every member of the
    three modules, and the second is the mutation that turns this arm into
    a blanket discharge. `socket.socket` and `socket.getaddrinfo` are the
    resolvable pair — both live members of a seed module, neither a read,
    because the read set is the members whose own signature takes a
    `timeout` and neither one's does.
    """
    del tmp
    for label, body in (
            ('unresolvable-queue',
             'import queue\n\ndef run_gate(q):\n'
             '    return q.get(timeout=5)\n'),
            ('unresolvable-thread',
             'import threading\n\ndef run_gate(worker):\n'
             '    return worker.join(timeout=2)\n'),
            ('unresolvable-socket-method',
             'import socket\n\ndef run_gate(probe):\n'
             '    return probe.settimeout(timeout=5)\n'),
            ('resolvable-not-a-read-socket-class',
             'import socket\n\ndef run_gate(url):\n'
             '    return socket.socket(url, timeout=10)\n'),
            ('resolvable-not-a-read-lookup',
             'import socket\n\ndef run_gate(host):\n'
             '    return socket.getaddrinfo(host, 80, timeout=10)\n')):
        assert _rows(body) == [(4, 'timeout= keyword')], (label, _rows(body))


# --- a deadline the function cannot put on a child ------------------------

THREAD_DOUBLE = '''class _DrainThreadDouble:
    def __init__(self):
        self.join_timeouts = []
        self.fail_on_join = False

    def join(self, timeout=None):
        if self.fail_on_join:
            raise AssertionError('live child drain was joined')
        self.join_timeouts.append(timeout)


def run_gate():
    worker = _DrainThreadDouble()
    return worker.join(timeout=2)
'''

NESTED_DOUBLE = '''def run_gate(proc, spent):
    def refusing_await(proc, drained, timeout=None):
        """Record the allowance the fixture chose, and refuse to wait."""
        del proc, drained
        spent.append(timeout)
        raise RuntimeError('the stand-in refused to wait')

    return refusing_await(proc, [], timeout=1)
'''


def test_a_doubles_modelled_signature_is_not_a_launchers_deadline(tmp):
    """Both real shapes: a method, and a def nested in a test.

    `_DrainThreadDouble.join`'s signature IS the API it models for
    `threading.Thread.join`; there is no number in it to derive from
    anything. `refusing_await` is defined in a test to refuse to wait.
    Neither launches and neither hands the number on, so the SIGNATURE is
    not the row.

    The CALL is still one, and that is the point: the number the caller
    wrote is the caller's, which is the mechanism `_parameter_bound_faults`
    already describes, and `test_bridge_startup.py:259` is the shipped
    site where it is refused.
    """
    del tmp
    for source, in_path, call_line in (
            (THREAD_DOUBLE, ('run_gate', 'join'), 14),
            (NESTED_DOUBLE, ('run_gate', 'refusing_await'), 8)):
        rows = _rows(source, in_path)
        assert 'timeout parameter' not in [route for _, route in rows], rows
        assert (call_line, 'timeout= keyword') in rows, rows


def _routes(source, in_path):
    """The reason each row carries, for a failure message worth reading."""
    return [reason for _, _, _, reason
            in census._faults('planted.py', ast.parse(source),
                              frozenset(in_path))]


def test_a_double_that_forwards_its_deadline_to_a_child_is_still_refused(tmp):
    """The near miss, in both spellings of "reaches a child".

    One double owns the child itself and one hands the number to a path
    function that does. Both signatures are exactly as the shapes above
    are — a `timeout=None` parameter, no launch of their own — and both
    are faults, so the arm is about reaching a child rather than about
    the shape of the signature.
    """
    owner = ('import subprocess\n\n'
             'def run_gate(argv, *, timeout=None):\n'
             '    child = subprocess.Popen(argv)\n'
             '    return child.wait(timeout=timeout)\n')
    assert _rows(owner) == [
        (3, 'timeout parameter'),
        (5, 'positional timeout on a launched child'),
        (5, 'timeout= keyword')], _routes(owner, ('run_gate',))
    root = _tree(tmp, {
        '_noderun.py': _DETECTOR.replace(
            'def launch(argv):', 'def run_node_program(argv):'),
        '_stream_fake.py': (
            'from _noderun import run_node_program\n'
            'def run_gate(argv, *, timeout=None):\n'
            '    return run_node_program(argv, timeout=timeout)\n'),
    })
    faults = _full_census(root)
    assert [row[:3] for row in faults
            if row[2] == 'timeout parameter'] == [
                ('_stream_fake.py', 2, 'timeout parameter')], faults


# The F1 shapes: a real `subprocess.Popen` child reaped through a receiver
# the census cannot resolve. Each expected row set is what `origin/main`
# emits, measured there and not reasoned about.
UNTRACEABLE = {
    'an-unresolved-receiver': (
        'import subprocess\n\n\n'
        'def _start(argv):\n'
        '    return subprocess.Popen(argv)\n\n\n'
        'def run_gate(argv, *, timeout=None):\n'
        '    child = _start(argv)\n'
        '    return child.wait(timeout)\n',
        [(8, 'timeout parameter')]),
    'a-local-derived-from-the-deadline': (
        'import subprocess\n\n\n'
        'def _start(argv):\n'
        '    return subprocess.Popen(argv)\n\n\n'
        'def run_gate(argv, *, timeout=None):\n'
        '    child = _start(argv)\n'
        '    budget = timeout * 2\n'
        '    return child.wait(budget)\n',
        [(8, 'timeout parameter')]),
    'a-child-on-self': (
        'import subprocess\n\n\n'
        'def _start(argv):\n'
        '    return subprocess.Popen(argv)\n\n\n'
        'class _D:\n'
        '    def __init__(self, child):\n'
        '        self.child = child\n\n'
        '    def drain(self, timeout=None):\n'
        '        return self.child.wait(timeout)\n',
        [(12, 'timeout parameter')]),
    'a-hand-off-to-an-unresolved-method': (
        'import subprocess\n\n\n'
        'class H:\n'
        '    def run(self, argv, timeout=None):\n'
        '        return self.run_node_program(argv, timeout=timeout)\n',
        [(6, 'timeout= keyword')]),
    'the-control-the-function-owns': (
        'import subprocess\n\n\n'
        'def run_gate(argv, *, timeout=None):\n'
        '    child = subprocess.Popen(argv)\n'
        '    return child.wait(timeout)\n',
        [(4, 'timeout parameter'),
         (6, 'positional timeout on a launched child')]),
}


def test_a_deadline_the_census_cannot_trace_is_still_refused(tmp):
    """F1: the receiver failing to resolve is NOT a proof of safety.

    The unconditional signature refusal this arm replaced existed for
    exactly this case, and the issue's Expected Behavior says the
    existing refusals may not weaken. "The census cannot trace where the
    number went" is a statement about the walk, not about the number, and
    a walk that cannot trace it is the shape a bound hides in.

    Every expected row set is what `origin/main` emits for the same
    source, so this pins the four rows the first version of the arm
    silenced and cannot be satisfied by re-weakening the rule. The fourth
    shape is the one that legitimately keeps less: its call site is
    already refused as a `timeout= keyword` on line 6, which is where the
    number is written, and a method call on an unresolved receiver is not
    a child-ending operation.
    """
    del tmp
    for label, (source, expected) in UNTRACEABLE.items():
        assert _forced_rows(source) == expected, (
            label, _forced_rows(source))


def test_a_deadline_handed_to_a_child_slot_is_refused_with_no_launch_near(
        tmp):
    """F2: the child-SLOT route, and nothing else holding it up.

    The sibling control above cannot cover this: its shapes are caught by
    the launch-placement half, so a mutant in the slot half is masked by a
    passing assertion next door. Here the function places no launch, the
    receiver is a bare parameter the census never resolves, and the only
    route left is the operation: the deadline goes into `Popen.wait`'s
    timeout slot, on an object the census cannot prove is anything.

    Both spellings of "into the slot" are here — the `timeout=` keyword
    and a name computed from the parameter — because they are two lines of
    the same check and one of them can go without the other.
    """
    del tmp
    for label, tail, call_line in (
            ('keyword',
             '    return proc.wait(timeout=timeout)\n', 2),
            ('derived-local',
             '    deadline = timeout * 2\n'
             '    return proc.wait(timeout=deadline)\n', 3)):
        source = (f'def run_gate(proc, *, timeout=None):\n{tail}')
        assert _rows(source) == [
            (1, 'timeout parameter'),
            (call_line, 'timeout= keyword')], (
            label, _rows(source), _routes(source, ('run_gate',)))


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


# --- the real files, which are not on the path ----------------------------

# The four shapes, in the files they live in. `urlopen` at 567 and 574
# are the same line of text, so the second carries its occurrence.
DISCHARGED = (
    ('test_bridge_startup.py', 'def join(self, timeout=None):', 1),
    ('test_bridge_startup.py', 'def refusing_await(', 1),
    ('test_parent_watch.py', 'def _wait_for_exit(proc, info=None,', 1),
    ('test_bridge_startup.py', 'urllib.request.urlopen(request, timeout=10)',
     1),
    ('test_real_browser_harness.py',
     'urllib.request.urlopen(page_url, timeout=2)', 1),
    ('test_real_browser_harness.py',
     'urllib.request.urlopen(page_url, timeout=2)', 2),
    ('test_real_browser_harness.py', 'def timed_out(', 1),
    ('test_real_browser_harness.py', 'def outer_timeout(', 1),
    ('test_real_browser_harness.py', 'def websocket_failed(', 1),
    ('test_real_browser_harness.py',
     'def run(args, *, cwd, capture_output, text, timeout):', 1),
)

# The genuine child bounds, with the route and the reason each carries
# today. `tests/test_real_browser_harness.py` also has a `thread.join`
# site, which no rule may discharge: it is the shape the previous
# narrowing of this rule missed, in a real file.
WRITTEN = 'the bound is written at the call site, not named'
UNCHAINED = ('the constant WAIT_TIMEOUT is not computed from a named '
             'chain, so the figure behind it is written rather than '
             'composed')
CHILD_BOUNDS = (
    ('test_bridge_startup.py', 'proc.wait(timeout=10)', 1, WRITTEN),
    ('test_bridge_startup.py', 'await_listening_line(proc,', 1, WRITTEN),
    ('test_bridge_startup.py', 'proc, drained, timeout=1)', 1, WRITTEN),
    ('test_bridge_startup.py', 'proc.wait(timeout=10)', 2, WRITTEN),
    ('test_bridge_startup.py', 'timeout=_util.COLD_START_TIMEOUT', 1,
     WRITTEN),
    ('test_parent_watch.py', 'proc.communicate(timeout=WAIT_TIMEOUT)', 1,
     UNCHAINED),
    ('test_parent_watch.py', 'proc.communicate(timeout=WAIT_TIMEOUT)', 2,
     UNCHAINED),
    ('test_parent_watch.py', 'proc.wait(timeout=10)', 1, WRITTEN),
    ('test_parent_watch.py', '_wait_for_exit(process, info, timeout=0)', 1,
     WRITTEN),
    ('test_real_browser_harness.py', 'process.wait(timeout=10) == 0', 1,
     WRITTEN),
    ('test_real_browser_harness.py', 'process.wait(timeout=10)', 1, WRITTEN),
    ('test_real_browser_harness.py', 'text=True, timeout=10)', 1, WRITTEN),
    ('test_real_browser_harness.py', 'text=True, timeout=10)', 2, WRITTEN),
    ('test_real_browser_harness.py', 'thread.join(timeout=2 *', 1, WRITTEN),
)


def test_the_real_files_discharge_the_four_shapes(tmp):
    """Proof 1 on the real tree: the false positives are GONE.

    Every site is a row the census emits today from these three files and
    nothing else, so this is a live control rather than a restatement of
    the shape: delete the narrowing and all ten come back.
    """
    del tmp
    for relative, snippet, occurrence in DISCHARGED:
        line = _line_holding(relative, snippet, occurrence)
        rows = [row for row in _real_rows(relative) if row[1] == line]
        assert not rows, (relative, line, snippet, rows)


def test_the_real_files_keep_every_genuine_child_bound(tmp):
    """Proof 2 on the real tree: the negative space holds.

    Fourteen sites, the route each carries and the reason each carries,
    byte for byte. Twelve of the table in the issue are here; its last
    two rows name a `test_real_browser_harness.py` that has no
    `communicate(timeout=WAIT_TIMEOUT)` at 487 or 490 — those lines are
    `test_parent_watch.py`'s, and are in the list twice over — and the
    `thread.join` site that table omits is here in their place, because
    it is the shape a "not a subprocess" narrowing would discharge.
    """
    del tmp
    for relative, snippet, occurrence, reason in CHILD_BOUNDS:
        line = _line_holding(relative, snippet, occurrence)
        rows = sorted(row for row in _real_rows(relative) if row[1] == line)
        assert rows, (relative, line, snippet, 'the bound is no longer read')
        assert all(row[3] == reason for row in rows), (relative, line, rows)
        assert {row[2] for row in rows} <= {
            'timeout= keyword',
            'positional timeout on a launched child'}, (relative, line, rows)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchcensusreceivers_')


if __name__ == '__main__':
    raise SystemExit(main())
