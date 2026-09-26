#!/usr/bin/env python3
"""How a number may reach a harness child, and what may bound one.

Every rule below is pinned in BOTH directions, because a rule that only
fires true is a false-positive generator and one that never fires is the
false green this suite exists to prevent. The positive cases are the routes
a number can take to a child's death, planted as source; the negative cases
are the near misses a rule must NOT refuse, which are what decide whether a
guard survives its first real false positive.

The scope is derived, not listed: the shared gate's launcher modules, every
module under `tests/` that reaches one, recursively, and the module the
child is HANDED to — which is `tests/_processtree.py`, named because it is
where the child is actually ended. The census and its routes live in
`tests/_launch_census.py`; this suite is what holds them, and the controls
here are why deleting a rule from the census turns this file red rather than
leaving it green.

The launch call itself is NOT this suite's subject. `tests/_launch_audit.py`
owns it, and the three routes that live on a launch call — a `**` mapping
unpacked on one, a keyword the `subprocess` does not take, and a launcher
built by `functools.partial` — are pinned in `tests/test_repo_layout.py`,
beside the analyser and the row tables it reads. That is where the boundary
between the two controls is checked, because that is where the audit is.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent

# A detector, whole: the permitted shape. Each rule's near miss is this file
# with one thing changed, so a rule and its near miss cannot drift apart.
_DETECTOR = """
import subprocess
import sys

from _processtree import cleanup_process_tree

SAMPLES = (1.0, 1.5, 2.0)
SLOWEST_S = max(SAMPLES)
MULTIPLE = 10
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


def _tree(root, files):
    """Write a tree of planted sources and return the directory."""
    root = Path(root)
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return root


def _routes(body, in_path=('launch',)):
    """Run the census over one planted source and return the reasons.

    Module-level statements are read with the function bodies, because half
    the routes are reachable only from code outside any function — a module
    constant consumed by a wait, a `**kwargs` dict built at import — and a
    control that could not plant those would leave the route untested rather
    than proven absent.
    """
    faults = census._faults('planted.py', ast.parse(body), frozenset(in_path))
    return [detail for _, _, _, detail in faults]


def _codes(body, in_path=('launch',)):
    """The route labels, which say which rule fired rather than why."""
    return [route for _, _, route, _ in census._faults(
        'planted.py', ast.parse(body), frozenset(in_path))]


def _full_census(root):
    """`census()` over a planted tree, the way the tree-wide control runs."""
    return census.census(Path(root), ('_noderun.py', '_stream_fake.py'))


def _swapped(old, new, in_path=('launch',)):
    """The detector with one thing changed, and the route that fires on it.

    Twenty of this suite's assertions are this shape, and spelling it out
    each time is twenty chances to plant the wrong thing.
    """
    body = _DETECTOR.replace(old, new)
    return body, _codes(body, in_path), _routes(body, in_path)


# --- the scope ------------------------------------------------------------


# --- R3: the timeout slot of a launched child -----------------------------

def test_a_positional_timeout_on_a_launched_child_is_refused(tmp):
    """R3 positive: `process.wait(30)`, which is a live bound.

    `Popen.wait`'s FIRST positional parameter is its timeout, so this shape
    carries a wall bound with no keyword, no default and no dict anywhere in
    it.
    """
    del tmp
    body = _DETECTOR.replace('process.wait(timeout=DEADLINE_S)',
                             'process.wait(30)')
    assert 'positional timeout on a launched child' in _codes(body)
    assert any('written at the call site' in r for r in _routes(body))


def test_the_other_slot_of_the_other_method_is_refused(tmp):
    """R3 again, on `communicate`'s SECOND positional, and its near miss.

    `communicate`'s first positional is `input`, so `communicate(30)` sets
    `input` and bounds nothing — a survivor that is correct, and pinned here
    because a mutation battery producing a survivor is not reporting nothing.
    """
    del tmp
    second = _DETECTOR.replace('process.wait(timeout=DEADLINE_S)',
                               'process.communicate(None, 30)')
    assert 'positional timeout on a launched child' in _codes(second)
    first = _DETECTOR.replace('process.wait(timeout=DEADLINE_S)',
                              'process.communicate(30)')
    assert 'positional timeout on a launched child' not in _codes(first)


def test_a_child_reached_through_an_attribute_a_chain_or_a_parameter(tmp):
    """R3 positive, three ways the receiver is not a bare local.

    `self.child.wait(30)`, `Popen(...).wait(30)` and a child the caller
    hands the function as a parameter are all the same thing the route
    describes — a method of a launched object — and the first version of
    this suite read only a local assignment.
    """
    del tmp
    attribute = (
        'class Gate:\n'
        '    def launch(self, argv):\n'
        '        self.child = subprocess.Popen(\n'
        '            argv, stdout=subprocess.DEVNULL)\n'
        '        return self.child.wait(30)\n')
    assert 'positional timeout on a launched child' in _codes(attribute)
    chained = _DETECTOR.replace(
        'process.wait(timeout=DEADLINE_S)',
        'subprocess.Popen(argv, stdout=subprocess.DEVNULL,\n'
        '                 stderr=subprocess.DEVNULL).wait(30)')
    assert 'positional timeout on a launched child' in _codes(chained)
    # The parameter case is the shipped one: `_processtree.py` receives the
    # child and the census resolves it from the call that fills it.
    assert '_processtree.py' in census.path_functions(TESTS)


def test_a_wait_on_something_that_is_not_a_child_is_not_a_bound(tmp):
    """R3 negative: a receiver the launch never bound is not a child."""
    del tmp
    body = _DETECTOR.replace(
        'process = subprocess.Popen(',
        'process = _session()\n    other = subprocess.Popen(')
    assert 'positional timeout on a launched child' not in _codes(body)


def test_a_launcher_reached_through_every_binding_is_read(tmp):
    """The receiver and the member are both bindings, and both are read.

    `import subprocess`, `import subprocess as sp`,
    `from subprocess import run as launch`, `sub = subprocess` and
    `pop = subprocess.Popen` are five ways to name one call. A census
    reading four of them misses a bound placed behind the fifth.
    """
    del tmp
    for prologue, call in (
            ('import subprocess\n', 'subprocess.Popen('),
            ('import subprocess as sp\n', 'sp.Popen('),
            ('from subprocess import Popen as launch\n', 'launch('),
            ('import subprocess\nsub = subprocess\n', 'sub.Popen('),
            ('import subprocess\npop = subprocess.Popen\n', 'pop(')):
        body = prologue + (
            'def launch(argv):\n'
            f'    process = {call}argv, stdout=subprocess.DEVNULL)\n'
            '    return process.wait(30)\n')
        assert 'positional timeout on a launched child' in _codes(body), (
            prologue, _routes(body))


# --- R6: a deadline that ends the process --------------------------------

def test_a_process_deadline_is_refused_without_permission(tmp):
    """R6 positive: the three process-level calls, and the near miss.

    None of them is a `subprocess` call and each ends the process, so a
    rule that only reads launches would pass them. `setitimer`'s bound is
    its SECOND argument — the first is the timer — so a rule reading
    `args[0]` reads the timer and misses the bound.
    """
    del tmp
    for source in ('import signal\nsignal.alarm(30)\n',
                   'import os\nos.waitfor(1234, 30)\n',
                   'import signal\n'
                   'signal.setitimer(signal.ITIMER_REAL, 30.0, 0.5)\n'):
        assert 'ends the process' in ' '.join(_codes(source)), (
            source, _routes(source))
    permitted = ('import signal\n'
                 'SAMPLES = (1.0, 2.0)\n'
                 'SLOWEST_S = max(SAMPLES)\n'
                 'SIGNAL_S = round(SLOWEST_S * 10)\n'
                 'CLEANUP_S = 5\n'
                 'class ChildDeadlineExceeded(Exception):\n    pass\n'
                 'try:\n'
                 '    signal.alarm(SIGNAL_S)\n'
                 'except TimeoutError:\n'
                 '    raise ChildDeadlineExceeded(SIGNAL_S) from None\n'
                 'try:\n'
                 '    signal.setitimer(signal.ITIMER_REAL, SIGNAL_S, 0.5)\n'
                 'except TimeoutError:\n'
                 '    raise ChildDeadlineExceeded(SIGNAL_S) from None\n')
    assert not _codes(permitted), _routes(permitted)


def test_the_interval_timer_that_carries_no_number_is_disclosed(tmp):
    """`os.waitid` blocks and carries no number, so no rule can read it.

    Recorded as a control rather than a plant: the disclosure in
    `tests/_launch_census.py` names it, and this fails if a future version
    starts reading a timeout out of a call that has none.
    """
    del tmp
    assert 'waitid' not in census._PROCESS_DEADLINES, (
        'a rule now reads a timeout out of a call that has none')
    disclosed = census.__doc__ or ''
    assert 'os.waitid' in disclosed, 'the disclosure lost its item'


# --- R7: a deadline in the argv ------------------------------------------

def test_a_deadline_in_the_launch_argv_is_refused(tmp):
    """R7 positive: `['timeout', '30', node, …]`, a bound wearing an argv.

    GNU `timeout(1)` is the standard tool for this and its number is a
    STRING, so neither a numeric rule nor a keyword rule can see it. Planted
    three ways: literal, behind a `+`, and behind a module constant.
    """
    del tmp
    for head in ("['timeout', '30']", "['timeout', '30'] + argv",
                 '[_WRAP, str(30)]'):
        extra = ('_WRAP = "timeout"\n' if '_WRAP' in head else '')
        body = extra + _DETECTOR.replace(
            '    process = subprocess.Popen(\n        argv,',
            f'    process = subprocess.Popen(\n        {head},')
        assert 'timeout(1) in the launch argv' in _codes(body), (
            head, _routes(body))


def test_a_wrapper_handed_to_the_launcher_itself_is_refused(tmp):
    """R7 positive in the shape the tree is actually written in, and it
    needs two modules: the caller is on the path only by reaching the
    launcher, so this is driven through `census()`.

    The child is reached through `run_node_program`, not through a
    `subprocess` call, so a rule that reads argv only at a launch it can
    recognise never sees this — which is the blind spot the previous round
    planted and left standing.
    """
    root = _tree(tmp, {
        '_noderun.py': _DETECTOR.replace(
            'def launch(argv):', 'def run_node_program(argv):'),
        '_stream_fake.py': (
            'from _noderun import run_node_program\n'
            'def run_gate(node, program):\n'
            "    return run_node_program(\n"
            "        ['timeout', '30', 'node', 'x.js'], program)\n"),
    })
    faults = _full_census(root)
    assert any(route == 'timeout(1) in the launch argv'
               for _, _, route, _ in faults), faults


def test_a_launcher_given_no_wrapper_is_not_a_bound(tmp):
    """R7 negative: an ordinary argv is an ordinary argv."""
    root = _tree(tmp, {
        '_noderun.py': _DETECTOR.replace(
            'def launch(argv):', 'def run_node_program(argv):'),
        '_stream_fake.py': (
            'from _noderun import run_node_program\n'
            'def run_gate(node, program):\n'
            '    return run_node_program(node, [node, program])\n'),
    })
    assert not _full_census(root), _full_census(root)


# --- P1: the permission on the number -------------------------------------

def test_a_flat_multiple_does_not_satisfy_the_number_rule(tmp):
    """P1 positive: `round(30 * 10)` is the margin with a multiplication.

    The chain of one references no constant, so nothing records what the
    thirty was. This is the shape issue #1117 was filed about, and the
    shipped file was one edit away from it.
    """
    del tmp
    body = _DETECTOR.replace('DEADLINE_S = round(SLOWEST_S * MULTIPLE)',
                             'DEADLINE_S = round(30 * 10)')
    assert 'positional timeout on a launched child' in _codes(body)
    assert any('not computed from a named chain' in r for r in _routes(body))


def test_a_retyped_literal_one_link_down_does_not_satisfy_it_either(tmp):
    """P1 positive: a named link whose value is a typed number.

    `SLOWEST_S = 30` beside `round(SLOWEST_S * 10)` is the same defect one
    link further from the call, and it is what a retype produces.
    """
    del tmp
    body = _DETECTOR.replace('SLOWEST_S = max(SAMPLES)', 'SLOWEST_S = 30')
    assert 'positional timeout on a launched child' in _codes(body)
    assert any('not computed from a named chain' in r for r in _routes(body))


def test_a_measured_table_satisfies_the_number_rule(tmp):
    """P1 negative: the shipped shape is the composed one.

    A table of samples is a `Tuple`, not a `Constant`, so the figure a
    maintainer re-derives passes while a number typed beside it does not.
    """
    del tmp
    assert not _routes(_DETECTOR), _routes(_DETECTOR)
    shipped = (TESTS / '_noderun.py').read_text(encoding='utf-8')
    assert not census._faults('_noderun.py', ast.parse(shipped),
                              frozenset({'run_node_program'}))


# --- P2: the permission on the expiry -------------------------------------

def test_a_detector_that_swallows_its_expiry_is_refused(tmp):
    """P2, second half: catching the expiry is not the same as reporting it.

    This is why the concept is an amendment and not a prohibition. A bound
    whose handler catches the timeout and falls through ends the child
    silently: its output is never read, nothing is named, and the caller
    sees an empty result as if the child had answered. Two separate
    failures pin the two halves — a handler that returns instead of raising,
    and no handler at all.
    """
    del tmp
    swallowed = _DETECTOR.replace(
        '        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None',
        '        return None')
    codes = _codes(swallowed)
    assert 'positional timeout on a launched child' in codes
    assert any('named failure' in reason for reason in _routes(swallowed)), \
        _routes(swallowed)
    unhandled = _DETECTOR.replace(
        '    try:\n        returncode = process.wait(timeout=DEADLINE_S)\n'
        '    except subprocess.TimeoutExpired:\n'
        '        cleanup_process_tree(process, CLEANUP_S)\n'
        '        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None\n',
        '    returncode = process.wait(timeout=DEADLINE_S)\n')
    assert any('named failure' in reason for reason in _routes(unhandled)), \
        _routes(unhandled)


def test_a_raise_in_a_sibling_handler_does_not_report_the_expiry(tmp):
    """P2 positive: the raise has to be in the handler that MATCHED.

    A raise beside an expiry handler that only cleans up satisfies
    "something is raised somewhere" and not "the expiry is reported", and
    the child is still ended silently.
    """
    del tmp
    sibling = _DETECTOR.replace(
        '    except subprocess.TimeoutExpired:\n'
        '        cleanup_process_tree(process, CLEANUP_S)\n'
        '        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None\n',
        '    except ValueError:\n'
        '        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None\n'
        '    except subprocess.TimeoutExpired:\n'
        '        cleanup_process_tree(process, CLEANUP_S)\n')
    assert any('named failure' in reason for reason in _routes(sibling)), \
        _routes(sibling)


def test_a_class_whose_name_merely_contains_an_expiry_word_is_not_one(tmp):
    """P2 positive: the match is the final component, not a substring.

    `unrelated.TimeoutErrorLike` has nothing to do with an expiry, and a
    substring match would read it as one.
    """
    del tmp
    lookalike = _DETECTOR.replace(
        'except subprocess.TimeoutExpired:',
        'except unrelated.TimeoutErrorLike:')
    assert any('named failure' in reason for reason in _routes(lookalike)), \
        _routes(lookalike)


def test_the_expiry_the_launcher_raises_is_its_own_name(tmp):
    """P2 negative: a launcher may raise its OWN error, not the stdlib's.

    `ChildDeadlineExceeded` is what the shipped launcher raises, and forcing
    the stdlib spelling would make the permitted shape unexpressible.
    """
    del tmp
    assert 'ChildDeadlineExceeded' in census._EXPIRY_NAMES
    own = _DETECTOR.replace('except subprocess.TimeoutExpired:',
                            'except ChildDeadlineExceeded:')
    assert not _routes(own), _routes(own)


# --- the negative space of every rule together ----------------------------

def test_a_benign_number_is_not_a_wall_bound(tmp):
    """The two false reds the first version of this guard produced.

    `max_bytes=4096` on a helper and `attempts=3` on a gate are not bounds,
    and an attempt count is the idiom these very docstrings recommend — a
    rule that blocks one blocks the fix this repository prefers. The rules
    that would read them are gone, and this is what holds them gone.
    """
    del tmp
    benign = (
        'def prologue(payload, max_bytes=4096):\n    return payload\n',
        'def run_gate(node, plan, *, attempts=3):\n    return plan\n',
        'def run_gate(node, plan, *, retries=2, attempts=3):\n'
        '    return plan\n',
        'def launch(argv, *, chunk_bytes=65536):\n    return argv\n')
    for body in benign:
        assert not _routes(body), (body, _routes(body))


def test_a_real_subprocess_keyword_is_not_a_bound(tmp):
    """A launch taking only what the stdlib takes emits nothing.

    The first version of this guard read a hand list of the keywords a
    launch may carry, and the list was missing five real ones, so
    `pipesize=65536` was a false red. The list is gone with the rule that
    used it — the audit reads the signature — and this is the direction
    that keeps a launch from reading as a bound.
    """
    del tmp
    body = _DETECTOR.replace(
        'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,',
        'pipesize=65536, stdout=subprocess.DEVNULL,\n'
        '        stderr=subprocess.DEVNULL, start_new_session=True,')
    assert not _routes(body), _routes(body)


# --- the census has to be read through its own entry point ----------------

def test_a_fault_in_a_later_module_is_labelled_with_that_module(tmp):
    """M1: the diagnostic locates the fault, and `census()` is the seam.

    A control that calls `_faults` directly cannot see a `census()`
    regression, and restoring the first version's behaviour — every fault
    relabelled with the alphabetically-first caller — left every suite
    green. So this drives the real entry point over a two-module tree with
    the fault in the SECOND module.
    """
    root = _tree(tmp, {
        '_noderun.py': _DETECTOR.replace(
            'def launch(argv):', 'def run_node_program(argv):'),
        '_stream_fake.py': (
            'from _noderun import run_node_program\n'
            'def run_gate(argv):\n'
            '    return run_node_program(argv)\n'),
        'caller.py': (
            'from _stream_fake import run_gate\n'
            'def go(argv):\n'
            '    return run_gate(argv)\n'),
    })
    faults = _full_census(root)
    assert not faults, faults
    launcher = root / '_noderun.py'
    launcher.write_text(
        launcher.read_text(encoding='utf-8').replace(
            'process.wait(timeout=DEADLINE_S)', 'process.wait(30)'),
        encoding='utf-8')
    faults = _full_census(root)
    assert faults, 'the planted bound was not found through census()'
    assert all(f[0] == '_noderun.py' for f in faults), faults
    assert any(f[1] > 1 for f in faults), faults


def test_the_child_handed_to_the_cleanup_is_resolved_through_its_caller(tmp):
    """The parameter hand-off is wired, and only this says so.

    Without it the cleanup's own `wait` has no receiver at all — the child
    arrives as a parameter, and a walk that resolves only local bindings
    reads no bound there, which is silent rather than red. So the wiring is
    asserted directly: the census knows which module is handed the child,
    and which parameter of it.
    """
    del tmp
    census.path_functions(TESTS)
    for module in census.CHILD_ENDING_MODULES:
        assert module in census._CHILD_PARAMETERS, (
            module, sorted(census._CHILD_PARAMETERS))
        assert census._CHILD_PARAMETERS[module], module
        assert census._parameters_for(module), module


def test_a_bound_in_the_cleanup_module_is_located_at_its_call_site(tmp):
    """A deadline handed to the cleanup is the CALLER's number.

    `tests/_processtree.py` takes its bound as a parameter, by design — the
    two callers do not share a reason for one number — so the constant that
    decides it, and the line it is decided on, are the caller's. This drives
    the real entry point, so it also pins the `census()` half of the
    parameter hand-off rather than the helper's.
    """
    del tmp
    body = _DETECTOR.replace(
        '        cleanup_process_tree(process, CLEANUP_S)',
        '        cleanup_process_tree(process, 5)')
    assert not _routes(body), _routes(body)
    faults = _full_census(TESTS)
    assert not faults, faults


def test_the_launch_call_routes_the_audit_owns_are_refused_and_pinned(
        tmp):
    """The routes a launch call owns, in both directions, and their rows.

    A `**{'timeout': 30}` unpacked on a launch is a launch-call site, so the
    census does not read it — `tests/_launch_audit.py` does, and refuses it
    at every head. A keyword the `subprocess` does not take is the route
    this branch added to that analyser, because a misspelled bound
    (`timout=30`) never runs and is invisible to anything reading the word
    `timeout`. A launcher built by `functools.partial` is main's `unplaced`
    arm, and it is planted here so the route cannot be lost silently.

    One control, one owner: this fails if the analyser stops refusing any of
    them, if it starts refusing a clean launch, or if the row that pins one
    is deleted. The two former controls in `tests/test_repo_layout.py` said
    this in two functions over the same three routes; they are one control
    here, and the file they left is not edited by this branch.
    """
    del tmp
    from _bound_site_rows import BOUND_SITE_ROWS  # noqa: E402
    from _launch_audit import bound_sites  # noqa: E402
    from _launch_refusal_rows import LAUNCH_REFUSAL_ROWS  # noqa: E402
    plants = {
        'unpack-at-a-non-git-launch':
            ("import subprocess\n"
             "def probe():\n"
             "    subprocess.run(['node', 'x.js'], **{'timeout': 30})\n",
             [(3, 'non-git', 'unpack')]),
        'foreign-keyword-on-a-launch':
            ("import subprocess\n"
             "def probe():\n"
             "    subprocess.run(['git', 'status'], check=True, timout=30)\n",
             [(3, 'git', 'keyword')]),
        'a-clean-launch-emits-nothing':
            ("import subprocess\n"
             "def probe():\n"
             "    return subprocess.run(['git', 'status'], check=True,\n"
             "                        cwd='/tmp')\n",
             []),
    }
    for label, (source, expected) in plants.items():
        assert bound_sites(source, label) == expected, label
    partial = ("import functools\n"
               "import subprocess\n"
               "def probe():\n"
               "    _r = functools.partial(subprocess.run, timeout=30)\n"
               "    return _r(['git', 'status'])\n")
    assert bound_sites(partial, 'partial-as-a-launcher') == [
        (4, 'unreadable', 'unplaced')], 'the unplaced arm stopped covering it'
    sunk = {label for label, _, _ in BOUND_SITE_ROWS}
    refused = {label for label, _, _ in LAUNCH_REFUSAL_ROWS}
    for label in ('unpack-at-a-non-git-launch', 'foreign-keyword-on-a-launch',
                  'a-clean-launch-emits-nothing'):
        assert label in sunk, (label, sorted(sunk))
    assert 'foreign-keyword-on-a-launch' in refused, sorted(refused)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='harnesslaunchbounds_')


if __name__ == '__main__':
    raise SystemExit(main())
