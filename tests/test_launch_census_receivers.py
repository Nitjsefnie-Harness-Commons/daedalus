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

Every shape here is PLANTED. The same census read over the three shipped
files these shapes live in — and the set of rows it emits there — is
`tests/test_launch_real_files.py`, which is where a shape the fixture
happens not to spell is caught.
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

# The modules whose members the census derives a network read from. The
# derivation is the stdlib's own, read through `inspect`; nothing here
# names a member.
NETWORK_MODULES = ('urllib.request', 'http.client', 'socket')


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
    that names no `run_gate` is still read: the tables below are measured
    against `origin/main` and this is how they were taken.
    """
    tree = ast.parse(source)
    return sorted((line, route) for _, line, route, _ in
                  census._faults('planted.py', tree, _every_function(tree)))


def _every_function(tree):
    """The forced `in_path` the planted controls read the tree through."""
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    return frozenset(node.name for node in ast.walk(tree)
                     if isinstance(node, functions))


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
     '    return urlopen(url, timeout=10)\n', 4),
    ('a-local-lambda-shadows-it',
     'from urllib.request import urlopen\n\ndef run_gate(url):\n'
     '    urlopen = lambda u: u\n'
     '    return urlopen(url, timeout=10)\n', 5),
    ('a-call-result-rebinds-the-alias',
     'import urllib.request\n\ndef run_gate(url):\n'
     '    urllib = object()\n'
     '    return urllib.request.urlopen(url, timeout=10)\n', 5),
    ('a-subscript-rebinds-the-alias',
     'import urllib.request\n\ndef run_gate(url, table):\n'
     '    urllib = table["x"]\n'
     '    return urllib.request.urlopen(url, timeout=10)\n', 5),
)


def test_a_shadowed_import_binding_is_refused_not_discharged(tmp):
    """A rebinding the walk cannot resolve is not still the import.

    `_dotted_bindings` records an assignment only when its right-hand side
    resolves to a dotted name, so a parameter, a lambda, a call result or
    a subscript that rebinds an imported name left the IMPORT binding
    standing and the call below it was discharged. Four lines pop the
    binding instead, and the direction is the one that cannot be wrong: an
    unresolved callee is not a network read, so the deadline is refused.

    This was a disclosure for one wave and is a fix now. A name in a
    review corpus recorded that naming a miss is not what converts it into
    a non-recurrence, and that the disclosure naming the gap is what made
    a reader trust a hole — the exact species this is.
    """
    del tmp
    for label, source, line in SHADOWED:
        assert _rows(source) == [(line, 'timeout= keyword')], (
            label, _rows(source))


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

# The list is bound in the ENCLOSING scope, which is what
# `tests/test_bridge_startup.py:182` does with `spent = []` and what the
# proof rests on: the receiver the deadline reaches is a literal.
NESTED_DOUBLE = '''def run_gate(proc):
    spent = []

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
    anything. `refusing_await` is defined in a test to refuse to wait,
    and `SELF_BOUND_DOUBLE` is the same double with its recorder bound in
    its own body. None launches and none hands the number on, so the
    SIGNATURE is not the row — and the third is here precisely because the
    second could not falsify it.

    The CALL is still one, and that is the point: the number the caller
    wrote is the caller's, which is the mechanism `_parameter_bound_faults`
    already describes, and `test_bridge_startup.py:259` is the shipped
    site where it is refused. `SELF_BOUND_DOUBLE` has no caller, so only
    the signature route is asserted for it — there is no call site to
    refuse.
    """
    del tmp
    for source, in_path, call_line in (
            (THREAD_DOUBLE, ('run_gate', 'join'), 14),
            (NESTED_DOUBLE, ('run_gate', 'refusing_await'), 10),
            # The SAME double with its recorder bound in its OWN body
            # rather than one scope out. It discharges for the same
            # reason and it is here because the two are indistinguishable
            # in the fixture that motivated the fix: a control built from
            # one cannot falsify the other, and this is the class that let
            # a regression through two waves.
            (SELF_BOUND_DOUBLE, ('g',), None)):
        rows = _rows(source, in_path)
        assert 'timeout parameter' not in [route for _, route in rows], rows
        if call_line is not None:
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
        [(5, 'timeout parameter'), (6, 'timeout= keyword')]),
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
    silenced and cannot be satisfied by re-weakening the rule. All five
    shapes are measured against `origin/main` rather than argued, and all
    five now match it row for row, the method hand-off included: the
    receiver of that call is `self`, which the tree does not bind to a
    literal, so the deadline reaches a call the census cannot show is
    harmless and the signature is refused exactly as main refuses it.
    """
    del tmp
    for label, (source, expected) in UNTRACEABLE.items():
        assert _forced_rows(source) == expected, (
            label, _forced_rows(source))


# The reapers the census cannot NAME. Every row set here is what
# `origin/main` emits, measured there. A deadline that reaches a call the
# walk cannot show is harmless is a bound the gate is blind to, whether
# the reaper is `Popen.wait` or a third-party one — so the control is
# about the OPERATION being unknown, not about a list of known ones.
UNNAMED_REAPERS = {
    'an-unresolved-receiver': UNTRACEABLE['an-unresolved-receiver'],
    'a-derived-local':
        UNTRACEABLE['a-local-derived-from-the-deadline'],
    'a-bare-name-reaper':
        ('from psutil import wait_procs\n\n\n'
         'def run_gate(procs, timeout):\n'
         '    return wait_procs(procs, timeout)\n',
         [(4, 'timeout parameter')]),
    'a-method-reaper-on-a-receiver':
        ('def run_gate(pool, timeout):\n'
         '    return pool.reap_all(timeout)\n',
         [(1, 'timeout parameter')]),
    'a-method-reaper-and-its-caller':
        ('class Pool:\n'
         '    def join(self, proc, timeout):\n'
         '        return proc.wait(timeout)\n\n\n'
         'def go(pool, proc, timeout):\n'
         '    return pool.join(proc, timeout)\n',
         [(2, 'timeout parameter'), (6, 'timeout parameter')]),
    'a-spread-in-front-of-the-deadline':
        ('import subprocess\n\n\n'
         'def _start(argv):\n'
         '    return subprocess.Popen(argv)\n\n\n'
         'def run_gate(argv, *, timeout=None):\n'
         '    child = _start(argv)\n'
         '    return child.wait(*[], timeout)\n',
         [(8, 'timeout parameter')]),
    'a-spread-with-a-derived-deadline':
        ('import subprocess\n\n\n'
         'def _start(argv):\n'
         '    return subprocess.Popen(argv)\n\n\n'
         'def run_gate(argv, *, timeout=None):\n'
         '    child = _start(argv)\n'
         '    d = timeout\n'
         '    return child.wait(*[], d)\n',
         [(8, 'timeout parameter')]),
}


def test_a_reaper_the_census_cannot_name_is_still_refused(tmp):
    """The class, not the two names: a deadline reaching an unknown sink.

    `Popen.wait` and `Popen.communicate` are two names off one stdlib
    class, and a rule built on them does not read the CLASS. A third-party
    reaper called `wait_procs`, a pool's own `reap_all`, a spread in front
    of the argument slot — none of them is a name the census holds, and
    all of them end a child. A guard that detects two instances of a class
    has not been shown to detect the class, so the arm is about the
    RECEIVER being provably child-free rather than the operation being
    known, and every expected row set is `origin/main`'s.
    """
    del tmp
    for label, (source, expected) in UNNAMED_REAPERS.items():
        assert _forced_rows(source) == expected, (label,
                                                  _forced_rows(source))


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
        source = f'def run_gate(proc, *, timeout=None):\n{tail}'
        assert _rows(source) == [
            (1, 'timeout parameter'),
            (call_line, 'timeout= keyword')], (
            label, _rows(source), _routes(source, ('run_gate',)))


# A function that makes its own container and puts the deadline in it. The
# dict spelling of the same double, `spent['seen'] = timeout`, is NOT here
# and is a REFUSAL rather than a discharge, because a subscript store hands
# the number to a container the census resolves no body for. It was here as a
# discharge and the two contradicted; the control that pins the refusal is
# `test_a_subscript_store_is_not_a_proven_position` in
# `tests/test_launch_deadline_positions.py`.
SELF_BOUND_DOUBLE = '''def g(argv, timeout=None):
    spent = []
    spent.append(timeout)
    raise RuntimeError('no')
'''


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchcensusreceivers_')


if __name__ == '__main__':
    raise SystemExit(main())
