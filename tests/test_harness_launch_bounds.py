#!/usr/bin/env python3
"""No wall bound reaches a harness child, and the bound it has is a detector.

Every rule below is pinned in BOTH directions, because a rule that only
fires true is a false-positive generator and one that never fires is the
false green this suite exists to prevent. The positive cases are the routes
a number can take to a child's death, planted as source; the negative cases
are the near misses a rule must NOT refuse, which are the ones that decide
whether a guard survives its first real false positive.

The scope is derived, not listed: the shared gate's two launcher modules,
plus every module under `tests/` that names one of their functions at a
call site, found recursively. The census and its routes live in
`tests/_launch_census.py`; this suite is what holds them, and the controls
here are why deleting a rule from the census turns this file red rather than
leaving it green.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent

# The routes, as the source a plant is written in. Each is a complete,
# self-contained shape: a launcher that hands a number to a child, and the
# expiry handling that makes it a detector rather than a margin.
_DETECTOR_TAIL = """
    try:
        returncode = process.wait(timeout=DEADLINE_S)
    except subprocess.TimeoutExpired:
        cleanup_process_tree(process, CLEANUP_S)
        raise ChildDeadlineExceeded(argv, DEADLINE_S, cleanup) from None
"""

# A detector, whole: the permitted shape. Used as the base for the rules
# whose near miss is the same file with one thing changed.
_DETECTOR = """
import subprocess
import sys

from _processtree import cleanup_process_tree

DEADLINE_S = round(SLOWEST_S * MULTIPLE)
CLEANUP_S = 5


class ChildDeadlineExceeded(Exception):
    pass


def launch(argv):
    process = subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != 'win32')
    try:
        returncode = process.wait(timeout=DEADLINE_S)
    except subprocess.TimeoutExpired:
        cleanup_process_tree(process, CLEANUP_S)
        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None
    return returncode
"""


def _routes(body, in_path=('launch',)):
    """Run the census over one planted source and return what it found.

    The whole module is read, module-level statements included, because half
    the routes are reachable only from code outside any function — a module
    constant consumed by a launch, a `functools.partial` built at import —
    and a control that could not plant those would leave the route untested
    rather than proven absent.
    """
    faults = census._faults('planted.py', ast.parse(body),
                            frozenset(in_path))
    return [detail for _, _, _, detail in faults]


def _codes(body, in_path=('launch',)):
    """The route labels, which say which rule fired rather than why."""
    return [route for _, _, route, _ in census._faults(
        'planted.py', ast.parse(body), frozenset(in_path))]


def test_the_shipped_tree_carries_no_undeclared_wall_bound(tmp):
    """The real path is clean, which is the property this suite exists for."""
    del tmp
    assert census.LAUNCHER_MODULES, 'the seed set emptied; it cannot be empty'
    faults = census.census(TESTS)
    assert not faults, '\n'.join(
        f'{relative}:{line}  {route}  {detail}'
        for relative, line, route, detail in faults)


def test_the_scope_reaches_every_module_that_calls_the_gate(tmp):
    """A caller is on the path because it calls the gate, wherever it lives.

    Pinned in both directions: the two harness modules that reach the gate
    only through `run_inline_gate` are on it, and a module that reaches
    nothing is not. A scope that admitted everything would pass the first
    half of this and fail to mean anything.
    """
    del tmp
    paths = census.path_functions(TESTS)
    for expected in ('_relayharness.py', '_cdpharness.py', '_boundary.py',
                     '_worker_runtime.py', 'test_bridge_fake_oracle.py'):
        assert expected in paths, (expected, sorted(paths))
    assert '_util.py' not in paths, sorted(paths)
    # A module that reaches the gate AND launches a child of its own is on
    # the path for the first only. `test_worker_runtime.py` calls
    # `run_node_program` and also runs another SUITE as a subprocess with a
    # 30 s margin; the second is a different mechanism and the one issue
    # #1121 lists, and the path must not swallow it. So the module is in and
    # the suite-launching test is not one of its path functions.
    runtime = paths['test_worker_runtime.py']
    assert 'test_sibling_mutation_failure_names_module_type_and_handlers' \
        not in runtime, sorted(runtime)
    assert 'test_node_harness_decodes_utf8_independent_of_locale' in runtime


def test_a_caller_in_a_subdirectory_is_on_the_path(tmp):
    """A `tests/` subdirectory is not outside the tree.

    A real tree, not a string: the derivation walks `rglob`, and the only
    way to pin that is to hand it a subdirectory and read what it found.
    `tests/` is flat today, so this is latent — which is exactly why it needs
    a control rather than a note.
    """
    root = Path(tmp)
    (root / '_noderun.py').write_text(
        _DETECTOR.replace('def launch(argv):',
                          'def run_node_program(argv):'), encoding='utf-8')
    (root / '_stream_fake.py').write_text(
        'def run_gate(*a, **k):\n    return run_node_program(*a, **k)\n',
        encoding='utf-8')
    (root / 'pkg').mkdir()
    (root / 'pkg' / 'child.py').write_text(
        'from _noderun import run_node_program\n'
        'def go():\n    return run_node_program(1, 2, 3, 4)\n',
        encoding='utf-8')
    (root / 'flat.py').write_text(
        'from _noderun import run_node_program\n'
        'def go():\n    return run_node_program(1, 2, 3, 4)\n',
        encoding='utf-8')
    paths = census.path_functions(root, ('_noderun.py', '_stream_fake.py'))
    assert 'pkg/child.py' in paths, sorted(paths)
    assert 'flat.py' in paths, sorted(paths)


def test_a_bare_margin_on_a_launch_is_refused(tmp):
    """R2 positive: the original defect, spelled the way it shipped.

    A literal at the launch is a number chosen at the call site, so nothing
    records what it is and the shape is a margin. The expiry handling around
    it changes nothing: permission needs BOTH conditions and this has only
    the second.
    """
    del tmp
    body = _DETECTOR.replace(
        'argv, stdout=subprocess.DEVNULL',
        'argv, timeout=30, stdout=subprocess.DEVNULL')
    assert 'timeout= at a launch' in _codes(body), _routes(body)
    assert any('written at the call site' in r for r in _routes(body))
    # The same number on the child's own wait is the same live bound, and
    # was the shape the previous version of this audit did read, so it must
    # keep being read.
    on_wait = _DETECTOR.replace('timeout=DEADLINE_S', 'timeout=30')
    assert 'positional timeout on a launched child' in _codes(on_wait), \
        _routes(on_wait)


def test_a_bound_named_from_a_bare_literal_is_refused(tmp):
    """R2 positive, one step removed: a constant spelled `DEADLINE_S = 30`.

    This is the spelling that got away last round. Pinned here because the
    distinction it turns on is the whole of the permission rule: a constant
    whose value is an EXPRESSION is derived, and a constant whose value is a
    literal is the number written twice.
    """
    del tmp
    body = _DETECTOR.replace('DEADLINE_S = round(SLOWEST_S * MULTIPLE)',
                             'DEADLINE_S = 30')
    assert 'positional timeout on a launched child' in _codes(body), \
        _routes(body)
    assert any('bare literal' in r for r in _routes(body))
    at_launch = body.replace('timeout=DEADLINE_S',
                             'timeout=DEADLINE_S').replace(
        'argv, stdout=subprocess.DEVNULL',
        'argv, timeout=DEADLINE_S, stdout=subprocess.DEVNULL')
    assert 'timeout= at a launch' in _codes(at_launch), _routes(at_launch)


def test_a_detector_that_swallows_its_expiry_is_refused(tmp):
    """P2, second half: catching the expiry is not the same as reporting it.

    This is the whole reason the concept is an amendment and not a
    prohibition. A bound whose handler catches the timeout and falls through
    is a bound that ends the child silently — the child's output is never
    read, nothing is named, and the caller sees an empty result as if the
    child had answered. Permission needs the handler to RAISE, so this shape
    is refused even though its number is derived and it does catch.

    Without this control the second half of P2 is deletable and nothing
    goes red, which is how a rule becomes decoration.
    """
    del tmp
    swallowed = _DETECTOR.replace(
        "        cleanup_process_tree(process, CLEANUP_S)\n"
        "        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None",
        "        cleanup_process_tree(process, CLEANUP_S)\n"
        "        return None")
    codes = _codes(swallowed)
    assert 'positional timeout on a launched child' in codes, \
        _routes(swallowed)
    assert any('named failure' in reason for reason in _routes(swallowed)), \
        _routes(swallowed)
    # And the two halves are separable: a handler that raises but is not
    # there at all is refused too, which is the other half.
    unhandled = _DETECTOR.replace(
        "    try:\n"
        "        returncode = process.wait(timeout=DEADLINE_S)\n"
        "    except subprocess.TimeoutExpired:\n"
        "        cleanup_process_tree(process, CLEANUP_S)\n"
        "        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None\n",
        "    returncode = process.wait(timeout=DEADLINE_S)\n")
    assert any('named failure' in reason for reason in _routes(unhandled)), \
        _routes(unhandled)


def test_a_keyword_the_subprocess_does_not_take_is_refused(tmp):
    """R1 positive: the same number under a name the stdlib rejects.

    A misspelt or renamed launch keyword is a `TypeError` at runtime, not a
    bound — so this route is a bug either way, and the audit names it rather
    than passing it because the word is not `timeout`.
    """
    del tmp
    body = _DETECTOR.replace('stdout=subprocess.DEVNULL',
                             'deadline=30, stdout=subprocess.DEVNULL')
    assert 'deadline= at a launch' in _codes(body), _routes(body)


def test_a_positional_timeout_on_a_launched_child_is_refused(tmp):
    """R3 positive: `process.wait(30)`, which is a live bound.

    `Popen.wait`'s FIRST positional parameter is its timeout, so this shape
    carries a wall bound with no keyword, no default and no dict anywhere in
    it. It survived the previous version of this audit, which read keywords
    only.
    """
    del tmp
    body = _DETECTOR.replace('process.wait(timeout=DEADLINE_S)',
                             'process.wait(30)')
    assert 'positional timeout on a launched child' in _codes(body)
    assert any('written at the call site' in r for r in _routes(body))


def test_a_timeout_waits_second_positional_is_refused(tmp):
    """R3 positive again, on the other method and the other slot.

    `communicate`'s first positional is `input`, so `communicate(30)` sets
    `input` and bounds nothing — which is why a reviewer planting it got a
    survivor that was correct. The second slot is the timeout, and this pins
    the position rather than the method name.
    """
    del tmp
    body = _DETECTOR.replace('process.wait(timeout=DEADLINE_S)',
                             'process.communicate(None, 30)')
    assert 'positional timeout on a launched child' in _codes(body)
    benign = _DETECTOR.replace('process.wait(timeout=DEADLINE_S)',
                               'process.communicate(30)')
    assert 'positional timeout on a launched child' not in _codes(benign)


def test_a_wait_on_something_that_is_not_a_child_is_not_a_bound(tmp):
    """R3 negative: a receiver the launch never bound is not a child."""
    del tmp
    body = _DETECTOR.replace(
        'process = subprocess.Popen(',
        'process = _session()\n    other = subprocess.Popen(')
    codes = _codes(body)
    assert 'positional timeout on a launched child' not in codes, codes


def test_a_deadline_in_the_launch_argv_is_refused(tmp):
    """R4 positive: `['timeout', '30', node, …]`, a bound wearing an argv.

    GNU `timeout(1)` is the standard tool for this and its number is a
    STRING, so neither a numeric-default rule nor a keyword rule can see it.
    The detector belongs in the launcher, where the expiry can be classified;
    a second spelling of the same bound is the route this closes.
    """
    del tmp
    for argv in ("['timeout', '30'] + argv", "['timeout', '30', 'node']"):
        body = _DETECTOR.replace('argv, stdout=subprocess.DEVNULL',
                                 f'{argv}, stdout=subprocess.DEVNULL')
        assert 'timeout(1) in the launch argv' in _codes(body), (
            argv, _routes(body))


def test_a_bound_passed_at_a_call_site_is_refused(tmp):
    """R5 positive: the route both reviewers proved live, end to end.

    The call site supplies the key and the launcher forwards it, so nothing
    in the launcher's own body mentions a bound. Reading the call site
    rather than the launch is what closes it; a spread is a `**` keyword
    with no name, so the launcher half of this rule can never see it.
    """
    del tmp
    body = _DETECTOR.replace('    process = subprocess.Popen(',
                             '    process = subprocess.Popen(')
    caller = ast.parse('def go():\n'
                       '    return launch([], timeout=30)\n')
    call = next(n for n in ast.walk(caller) if isinstance(n, ast.Call))
    assert census._is_bound_keyword(call, caller)
    # The `**spread` spelling, where the key is in the forwarded mapping and
    # the call site names nothing — the chain both reviewers ran end to end.
    spread = ast.parse('opts = {"timeout": 30}\n'
                       'def go():\n'
                       '    return launch([], **opts)\n')
    spread_call = next(n for n in ast.walk(spread) if isinstance(n, ast.Call))
    assert census._is_bound_keyword(spread_call, spread)
    # And the launcher's own body is still clean, which is the point: the
    # fault lives in the caller's file, so the audit has to name that file.
    launcher = ast.parse(body)
    assert 'bound key at a call site' not in [
        route for _, _, route, _ in census._faults('launcher.py', launcher)]


def test_a_process_deadline_is_refused_without_permission(tmp):
    """R6/R7 positive: `signal.alarm(30)` and `os.waitfor(pid, 30)`.

    Neither is a `subprocess` call and both end the process, so a rule that
    only reads launches would pass them. The permission rule applies here
    too, which is why the same shape with a derived constant and an expiry
    handler is green below.
    """
    del tmp
    alarm = 'import signal\nsignal.alarm(30)\n'
    assert 'signal.alarm ends the process' in _codes(alarm), _routes(alarm)
    waitfor = 'import os\nos.waitfor(1234, 30)\n'
    assert 'os.waitfor ends the process' in _codes(waitfor), _routes(waitfor)
    permitted = ('import signal\n'
                 'SIGNAL_S = round(BASE_S * MULTIPLE)\n'
                 'try:\n'
                 '    signal.alarm(SIGNAL_S)\n'
                 'except TimeoutError:\n'
                 '    raise ChildDeadlineExceeded(SIGNAL_S) from None\n'
                 'CLEANUP_S = 5\n'
                 'class ChildDeadlineExceeded(Exception):\n    pass\n')
    assert 'signal.alarm ends the process' not in _codes(permitted), \
        _routes(permitted)


def test_the_a_launchers_detector_is_the_permitted_shape(tmp):
    """The green direction: the shipped launcher is what permission allows.

    Without this the suite would only prove the rules can refuse, and a
    rule set that refuses everything passes every other control here.
    """
    del tmp
    assert not _routes(_DETECTOR), _routes(_DETECTOR)
    shipped = (TESTS / '_noderun.py').read_text(encoding='utf-8')
    faults = census._faults('_noderun.py', ast.parse(shipped))
    assert not faults, faults


def test_a_benign_number_is_not_a_wall_bound(tmp):
    """R-negative: the two false reds the previous version produced.

    `max_bytes=4096` on a helper and `attempts=3` on a gate are not bounds,
    and a rule that refuses them teaches the next maintainer to delete it.
    An attempt count is the idiom these very docstrings recommend, so a rule
    that blocks one blocks the fix this repository prefers.
    """
    del tmp
    benign = (
        'def prologue(payload, max_bytes=4096):\n    return payload\n',
        'def run_gate(node, plan, *, attempts=3):\n    return plan\n',
        'def run_gate(node, plan, *, retries=2, attempts=3):\n'
        '    return plan\n')
    for body in benign:
        assert not _routes(body), (body, _routes(body))


def test_a_real_subprocess_keyword_is_not_a_bound(tmp):
    """R1 negative: the previous allowlist's five missing keywords.

    `pipesize`, `creationflags`, `process_group`, `startupinfo` and
    `preexec_fn` are real `Popen` keywords on this interpreter and were
    absent from a hand list, so `pipesize=65536` was refused. The set is
    read from the stdlib signature now, and this pins that a keyword the
    stdlib takes is admitted — including `start_new_session`, which the
    launcher's own cleanup relies on.
    """
    del tmp
    body = _DETECTOR.replace('stdout=subprocess.DEVNULL',
                             'pipesize=65536, '
                             'start_new_session=sys.platform != "win32", '
                             'stdout=subprocess.DEVNULL')
    assert not _routes(body), _routes(body)
    for keyword in ('pipesize', 'creationflags', 'process_group',
                    'startupinfo', 'preexec_fn', 'start_new_session'):
        assert keyword in census._LAUNCH_KEYWORDS, keyword


def test_a_bound_reached_through_an_aliased_receiver_is_refused(tmp):
    """M6 negative-control: all four receiver bindings are read.

    `import subprocess`, `import subprocess as sp`,
    `from subprocess import run as launch` and `sub = subprocess` are four
    ways to name one call. A census reading three of them misses a bound
    placed behind the fourth.
    """
    del tmp
    for prologue, call in (
            ('import subprocess\n', 'subprocess.run('),
            ('import subprocess as sp\n', 'sp.run('),
            ('from subprocess import run as launch\n', 'launch('),
            ('import subprocess\nsub = subprocess\n', 'sub.run(')):
        body = prologue + (
            'def launch(argv):\n'
            f'    return {call}argv, timeout=30)\n')
        codes = _codes(body)
        assert 'timeout= at a launch' in codes, (prologue, _routes(body))


def test_a_fault_names_the_file_it_is_in(tmp):
    """M1: the diagnostic locates the fault, not the module that found it.

    With a multi-module census the outer label is otherwise always the
    alphabetically-first caller, so an operator sent to the named file finds
    an import where the fault is not.
    """
    del tmp
    faults = census._faults('_noderun.py', ast.parse(_DETECTOR.replace(
        'timeout=DEADLINE_S', 'timeout=30')), frozenset({'launch'}))
    assert len(faults) == 1, faults
    relative, line, route, _ = faults[0]
    assert relative == '_noderun.py', faults
    assert line > 0, faults
    assert route == 'positional timeout on a launched child', faults
    # And the file the fault is IN is the launcher, not whichever caller the
    # audit happened to start from.
    assert not any(f[0].endswith('_boundary.py') for f in faults), faults


def test_the_broad_and_the_narrow_binding_agree_on_the_scope(tmp):
    """A census reading the keyword allowlist from memory goes red here.

    The set `_LAUNCH_MEMBERS` is a fixed point over the stdlib's own source,
    so a member the stdlib adds is inside it without an edit. This asserts
    the closure actually closed rather than having been written out by hand:
    `check_call` reaches a child through `run`, and its own text never says
    `Popen`.
    """
    del tmp
    assert {'Popen', 'run', 'call', 'check_call',
            'check_output'} <= census._LAUNCH_MEMBERS
    assert 'CompletedProcess' not in census._LAUNCH_MEMBERS


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='harnesslaunchbounds_')


if __name__ == '__main__':
    raise SystemExit(main())
