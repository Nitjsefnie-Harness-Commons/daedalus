#!/usr/bin/env python3
"""Focused real-tree regressions for the static guard suites."""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _binding_assertions import (  # noqa: E402
    _assert_binding_pair, _binding_snippets, _binding_violation,
    _inserted_line, _module_text, _scope_cases, _scope_violations,
    _unfollowable_snippets)
from _coverage_guard import (  # noqa: E402
    _BINDING_MESSAGE, _coverage_environment_violations,
    _synthetic_violations)
from _coverage_mutation_specs import _BASH_MUTATION_SPECS  # noqa: E402
from _coverage_source_fixtures import _real_module_copy  # noqa: E402
from _coverage_scopes import (  # noqa: E402
    _evaluation_scopes, _scope_bindings)
from _mutation_sweep import mutation_sweep  # noqa: E402
from _owned_writes import copy_test_tree  # noqa: E402
from _sweep_launch_scan import SWEEP_ENTRY, sweep_launches  # noqa: E402


def test_mutation_gate_accepts_crlf_copied_helpers(tmp):
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    bindings = root / 'tests' / '_coverage_bindings.py'
    scopes = root / 'tests' / '_coverage_scopes.py'
    bash = root / 'tests' / '_bash_resolver_scan.py'
    sources = [bindings.read_bytes(), scopes.read_bytes(), bash.read_bytes()]
    crlf_sources = [source.replace(b'\r\n', b'\n').replace(
        b'\n', b'\r\n') for source in sources]
    assert all(b'\r\n' in source
               and b'\n' not in source.replace(b'\r\n', b'')
               for source in crlf_sources)
    bindings.write_bytes(crlf_sources[0])
    scopes.write_bytes(crlf_sources[1])
    bash.write_bytes(crlf_sources[2])
    mutation_tmp = Path(tmp) / 'mutations'
    program = (
        "import sys\nsys.path.insert(0, 'tests')\n"
        "import test_coverage_bindings as suite\n"
        'suite.test_each_new_binding_and_match_arm_is_mutation_sensitive('
        f'{str(mutation_tmp)!r})\n')
    result = subprocess.run(
        [sys.executable, '-B', '-c', program], cwd=root,
        env=_util.child_coverage('scrub'), capture_output=True,
        text=True)
    assert result.returncode == 0, result.stderr
    assert [bindings.read_bytes(), scopes.read_bytes(),
            bash.read_bytes()] == crlf_sources


def _cache_collision_sequence(tmp, seed):
    from unittest.mock import patch

    ordinary = next(spec for spec in _BASH_MUTATION_SPECS
                    if spec[0] == 'MatchAs scope')
    needle = ordinary[2][0][0]
    replacement = needle.replace('ast.MatchAs', 'ast.MatchOr')
    assert replacement != needle and len(replacement) == len(needle)
    harmless = ('Scope and repository-root facts',
                'scope and repository-root facts')
    specs = (('caught same-size mutant', 'scopes',
              ((needle, replacement),), ordinary[3]),
             ('stale bytecode cache control: tests/_coverage_scopes.py',
              'scopes', (harmless,), ordinary[3]))
    records = []
    real_run = subprocess.run
    target = Path(tmp) / 'repository/tests/_coverage_scopes.py'

    def seed_cache(root):
        cache = root / 'nested/deeper/__pycache__'
        cache.mkdir(parents=True, exist_ok=True)
        (cache / 'sentinel.pyc').write_bytes(b'preexisting cache')

    def copy(root):
        copy_test_tree(root)
        if seed:
            seed_cache(root)

    def run(*args, **kwargs):
        root = Path(kwargs['cwd'])
        source = target.read_text()
        assert (replacement if not records else harmless[1]) in source
        # Force the legal same-size, same-second schedule without retries.
        os.utime(target, (1800000000, 1800000000))
        before = list(root.rglob('__pycache__'))
        assert not (seed and before), f'stale bytecode cache: {target}'
        result = real_run(
            *args, env=_util.child_coverage('scrub', kwargs.pop('env')),
            **kwargs)
        after = list(root.rglob('__pycache__'))
        records.append((result, before, after, target.stat().st_size))
        if seed:
            seed_cache(root)
        return result

    rejection = None
    with patch('_owned_writes.copy_test_tree', side_effect=copy), \
            patch('subprocess.run', side_effect=run):
        try:
            mutation_sweep(tmp, specs)
        except AssertionError as error:
            rejection = str(error)
    assert len(records) == 2, (rejection, records)
    assert rejection is not None, f'stale bytecode cache: {target}'
    assert specs[1][0] in rejection, rejection
    assert records[0][0].returncode != 0, records[0][0].stderr
    assert 'AssertionError' in records[0][0].stderr
    assert records[1][0].returncode == 0, records[1][0].stderr
    assert records[0][3] == records[1][3]
    assert all(not before for _, before, _, _ in records), (
        f'stale bytecode cache not cleared: {target}', records)
    assert all(not after for _, _, after, _ in records), (
        f'nested child wrote stale bytecode cache: {target}', records)
    original = Path(__file__).parent / '_coverage_scopes.py'
    assert target.read_bytes() == original.read_bytes()


def test_mutation_gate_rejects_a_cached_equivalent_edit(tmp):
    _cache_collision_sequence(tmp, False)


def test_mutation_gate_clears_caches_before_every_child(tmp):
    _cache_collision_sequence(tmp, True)


def test_the_shared_sweep_catches_a_planted_mutation(tmp):
    """The relocated sweep keeps the property this suite used to rent.

    It imported another suite's test function and called it as a runner,
    so the mutation sensitivity lived in that suite and this one could
    only observe it. The mechanism is shared now, so this suite runs a
    row of the shared table itself: returning at all is the assertion,
    because the sweep raises unless the planted mutation turned the child
    red. A second row carries a needle the target does not have, and that
    one must fail by name rather than run a child that was never mutated.
    """
    from _wffixtures import _refuses  # noqa: PLC0415

    spec = next(row for row in _BASH_MUTATION_SPECS
                if row[0] == 'MatchAs scope')
    mutation_sweep(str(Path(tmp) / 'planted'), (spec,))
    _refuses(
        mutation_sweep, str(Path(tmp) / 'unplantable'),
        (('unplantable row', 'scopes',
          (('a needle this target does not carry', ''),), 'assert False'),),
        contains='a needle this target does not carry')


def test_mutation_gate_refuses_site_initialization(tmp):
    from unittest.mock import patch

    spec = ('site control', 'scopes',
            (('Scope and repository-root facts',
              'scope and repository-root facts'),), 'assert False')
    real_run = subprocess.run

    def run(command, **kwargs):
        return real_run(
            [arg for arg in command if arg != '-S'],
            env=_util.child_coverage('scrub', kwargs.pop('env')), **kwargs)

    rejection = None
    with patch('subprocess.run', side_effect=run):
        try:
            mutation_sweep(tmp, (spec,))
        except AssertionError as error:
            rejection = str(error)
    assert rejection and 'site initialization enabled' in rejection, rejection


def test_bytecode_cleanup_refuses_checkout_paths(tmp):
    from unittest.mock import patch
    from _owned_writes import clear_bytecode
    from _control_writes import control_write_violations

    checkout = Path(tmp) / 'checkout'
    cache = checkout / 'child/__pycache__'
    cache.mkdir(parents=True)
    (cache / 'keep.pyc').write_bytes(b'untouched')
    with patch('_owned_writes.ROOT', checkout):
        for root in (checkout, checkout / 'child'):
            try:
                clear_bytecode(root)
            except ValueError as error:
                assert 'inside the checkout' in str(error)
            else:
                raise AssertionError('checkout cache removal was allowed')
    assert (cache / 'keep.pyc').read_bytes() == b'untouched'
    for path, allowed in (('tmp', True), ('ROOT', False)):
        source = ('from _owned_writes import clear_bytecode\n'
                  'from _util import child_coverage\n'
                  'from _repo import ROOT\ndef test_control(tmp):\n'
                  "    env = child_coverage('scrub')\n"
                  f'    clear_bytecode({path})\n')
        control = Path(tmp) / 'test_control.py'
        control.write_text(source)
        assert bool(control_write_violations(control, tmp)) is not allowed


def test_subscripted_dict_carriers_refuse_hidden_launchers(tmp):
    del tmp
    pairs = (
        (
            """import os
import subprocess
launcher = {'sp': subprocess}['sp']
os.chdir(tmp)
launcher.run(['python3', 'child.py'])
""",
            3,
            """import os
import subprocess
result = {'sp': subprocess.run(
        ['python3', 'child.py'], cwd=tmp)}['sp']
""",
            3),
        (
            """import os
import subprocess
os.chdir(tmp)
def go(launcher={'sp': subprocess}['sp']):
    launcher.run(['python3', 'child.py'])
go()
""",
            4,
            """import os
import subprocess
def go(result={'sp': subprocess.run(
        ['python3', 'child.py'], cwd=tmp)}['sp']):
    return result
""",
            3),
        (
            """import os
import subprocess
launchers = {subprocess: 'sp'}
os.chdir(tmp)
for launcher in launchers:
    launcher.run(['python3', 'child.py'])
""",
            3,
            """import os
import subprocess
results = {subprocess.run(
        ['python3', 'child.py'], cwd=tmp): 'sp'}
""",
            3),
    )
    for unsafe, unsafe_line, explicit, explicit_line in pairs:
        _assert_binding_pair(
            unsafe, unsafe_line, explicit, explicit_line)


def test_subscripted_unresolved_callee_stays_unresolved(tmp):
    del tmp
    source = """import subprocess
launchers = {'sp': mystery}
launchers['sp'](['python3', 'child.py'], cwd=tmp)
"""
    assert _synthetic_violations(source) == [
        "tests/synthetic.py:3: unresolved callee launchers['sp'] "
        'cwd=tmp declares no env='
    ]


def test_inline_dict_receivers_refuse_hidden_launchers(tmp):
    del tmp
    unsafe_sources = (
        ("""import os
import subprocess
os.chdir(tmp)
{'sp': subprocess}['sp'].run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
for launcher in {'sp': subprocess}.values():
    launcher.run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
for launcher in {'outer': {'sp': subprocess}}['outer'].values():
    launcher.run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
[{'sp': subprocess}][0]['sp'].run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
({'sp': subprocess},)[0]['sp'].run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
for launcher in [{'sp': subprocess}][0].values():
    launcher.run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
for launcher in ([{'sp': subprocess}],)[0][0].values():
    launcher.run(['python3', 'child.py'])
""", 4),
    )
    for source, line in unsafe_sources:
        assert _synthetic_violations(source) == [
            _binding_violation(line)]

    assert _synthetic_violations(
        """import os
import subprocess
from _repo import ROOT
os.chdir(tmp)
{'sp': subprocess}['sp'].run(['python3', 'child.py'], cwd=ROOT)
""") == []

    assert _synthetic_violations(
        """import os
import subprocess
os.chdir(tmp)
{'sp': subprocess}['sp'].run(['python3', 'child.py'], cwd=tmp)
""") == [
        "tests/synthetic.py:4: unresolved callee "
        "{'sp': subprocess}['sp'].run cwd=tmp declares no env="
    ]


def test_guard_and_binding_scans_share_cwd_predicate(tmp):
    del tmp
    from _coverage_bindings import _has_cwd_control  # noqa: PLC0415
    from _coverage_guard import (  # noqa: PLC0415
        _has_cwd_control as guard_has_cwd_control)

    assert guard_has_cwd_control is _has_cwd_control


def test_inline_dict_receiver_keeps_unresolved_callee_diagnostic(tmp):
    del tmp
    source = """import os
import mystery
os.chdir(tmp)
{'sp': mystery}['sp'](['python3', 'child.py'], cwd=tmp)
"""
    assert _synthetic_violations(source) == [
        "tests/synthetic.py:4: unresolved callee {'sp': mystery}['sp'] "
        'cwd=tmp declares no env='
    ]


def test_nonlauncher_binding_controls_stay_clean(tmp):
    del tmp
    assert _synthetic_violations(
        """import subprocess
from _repo import ROOT
match subprocess:
    case None:
        pass
result = subprocess.run(['python3', 'child.py'], cwd=ROOT)
""") == []


def test_named_unreadable_spread_remains_a_violation(tmp):
    del tmp
    violations = _synthetic_violations(
        """import subprocess
kw = dict({'cwd': tmp})
subprocess.run(['python3', 'child.py'], **kw)
""")
    assert len(violations) == 1, violations
    assert 'cwd may arrive through a ** spread' in violations[0], violations


def test_real_tree_refuses_each_complete_binding_bypass(tmp):
    root, target = _real_module_copy(tmp, Path('tests/test_diff_coverage.py'))
    source = _module_text(target)
    anchor = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    assert anchor in source, 'the coverage declaration shape changed'
    original = target.read_bytes()
    for name, unsafe, marker, explicit in (
            _binding_snippets() + _unfollowable_snippets()):
        mutated, line = _inserted_line(
            source, anchor, unsafe, marker)
        try:
            target.write_bytes(mutated.encode('utf-8'))
            violations = _coverage_environment_violations(root)
            expected = (
                f'tests/test_diff_coverage.py:{line}: '
                f'{_BINDING_MESSAGE}')
            assert expected in violations, (name, violations)
        finally:
            target.write_bytes(original)
        restored = _coverage_environment_violations(root)
        assert not any(v.startswith(
            f'tests/test_diff_coverage.py:{line}:') for v in restored), (
                name, restored)

        explicit_source, _ = _inserted_line(
            source, anchor, explicit, 'def _binding_probe')
        try:
            target.write_bytes(explicit_source.encode('utf-8'))
            explicit_violations = _coverage_environment_violations(root)
            expected_count = 2 if name == 'defaults' else 1
            assert len(explicit_violations) == expected_count, (
                name, explicit_violations)
            assert all('subprocess.run cwd=tmp declares no env=' in item
                       for item in explicit_violations), (
                           name, explicit_violations)
            assert all(_BINDING_MESSAGE not in item
                       for item in explicit_violations), (
                           name, explicit_violations)
        finally:
            target.write_bytes(original)


def test_real_tree_allows_an_unshadowed_builtin_dict(tmp):
    root, target = _real_module_copy(tmp, Path('tests/test_diff_coverage.py'))
    source = _module_text(target)
    anchor = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    snippet = "_BUILTIN_DICT_CONTROL = dict(cwd='x')\n"
    target.write_bytes(source.replace(
        anchor, snippet + anchor, 1).encode('utf-8'))
    assert _coverage_environment_violations(root) == []


def test_a_star_import_removes_the_builtin_exemption(tmp):
    del tmp
    assert _synthetic_violations(
        "from helpers import *\nkw = dict(cwd='x')\n") == [
            "tests/synthetic.py:2: unresolved callee dict "
            "cwd='x' declares no env="]
    tree = ast.parse('from helpers import *\n')
    bindings = _scope_bindings(*_evaluation_scopes(tree))
    assert '*' in bindings[tree], bindings[tree]


def test_real_tree_applies_python_evaluation_scopes(tmp):
    root, target = _real_module_copy(tmp, Path('tests/test_diff_coverage.py'))
    source = _module_text(target)
    anchor = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    original = target.read_bytes()
    for name, snippet, expected in _scope_cases():
        mutated = source.replace(anchor, snippet + anchor, 1)
        try:
            target.write_bytes(mutated.encode('utf-8'))
            violations = _coverage_environment_violations(root)
        finally:
            target.write_bytes(original)
        wanted = _scope_violations(
            'tests/test_diff_coverage.py', mutated, expected)
        assert violations == wanted, (name, violations)


def test_a_sweep_launch_carries_no_wall_clock_bound(tmp):
    """No suite bounds the mutation sweep's child with a wall clock.

    That child runs at least 142 mutation rows, one individually-bounded
    grandchild each, so an outer bound on it decides a verdict its own
    work does not own: 142 x 30s = 4260s is the work a runaway backstop
    would have to cover, and 120s truncates it about thirty-five times
    over. The worst case is the one that has to be safe on every
    interpreter, and the larger count 3.12 and later run grows as main
    adds coverage rows, so it is deliberately not pinned here. The rows
    that separate the two versions come from two chunks of
    tests/_coverage_mutation_specs.py's spec table, each closed by
    `if hasattr(ast, 'TypeVar') else ()` — 3.12+ because PEP 695
    type-parameter nodes arrived in it — so on 3.11 both are `()` and
    their rows are never added.

    A wall bound is legitimate where the child always spends it on real
    work — the freeze controls busy-wait on purpose, so a wedged child
    is the only failure a ceiling names — but this child is real work
    that runs long, and the aggregate is observed to take minutes where a
    margin allows two.

    That observation supports the argument rather than carrying it,
    because it does not reproduce to a figure: the same child has been
    measured from 16s to 145s, `returncode 0` every time. What moves the
    number is the host's AMBIENT load, not the measurement — these boxes
    run at loadavg 25 on 12 cores while the suites beside this one want
    the cores; the sweep itself is a sequential loop running one child
    at a time, so the contention is the aggregate's other legs and not
    this child. The arithmetic above is what the removal rests on.
    Re-derive the timing by running the entry test under as many burners
    as the cgroup quota allows, reading /proc/loadavg as it runs — note
    that `nproc` reports the cgroup affinity (11 here) against 12
    physical cores, so `nproc` burners is not saturation.

    What bounds a wedged child now, in symbols that cannot drift:
    `scripts/ci/suite_bound.py` holds the one definition of the per-suite
    bound (`DEFAULT_SUITE_TIMEOUT_S = 900`, overridable through
    `DAEDALUS_SUITE_TIMEOUT`), and both launchers import it. `run_tests.py`
    applies it through `process.wait(timeout=timeout)` and
    `scripts/ci/coverage_suites.py` through the same module's bounded
    launch; both report `SUITE TIMED OUT` naming the suite, and both kill
    the child's whole process group, so under the `suites` job and under
    `coverage-matrix` a wedged sweep child hangs for that bound and then
    says which suite it was. `tests/_util.py`'s `runner` still has no
    per-test bound. The removal trades a 120s red for those; it does not
    remove the need for a bound, only this one.

    Enforced structurally over every `tests/test_*.py`, so a site added
    later is covered without a list to maintain: no `timeout` keyword on
    a bounded launcher — `subprocess.run` / `call` / `check_call` /
    `check_output`, reached through `import subprocess`, an alias, a
    from-import, a star-import or a dotted import — carrying the sweep's
    entry test in one of its arguments, read from a literal, an
    f-string, a name, or a list or mapping those are elements of. The
    control also fails when the scan reaches no such launch, and when
    the entry name it keys on is no function any tracked suite defines,
    which a rename that stranded the program strings would leave green.

    The cost of that rule is a program the scope defines BELOW its
    launch, which is not read into a call it did not run — except a
    MULTI-LINE call, where `ast.Call.lineno` is the line the call opens
    on, so a binding written between the parentheses is below that line
    and is still what the call runs.

    Binding forms the scan READS: `=`, `+=`, an annotated `=`, a
    walrus, a `for`/`in` target, a match capture, `append` and
    `extend`. A `for` target binds the iterated expression and a match
    capture binds the whole match subject, both over-approximations of
    the value they will really hold, and both fail toward finding a
    launch rather than past one. The list was read off this docstring by
    the sweep scan suite, which sliced the two lists apart on these two
    headings and failed if they overlapped; that disjointness check went
    with cut 6 (#1483), so the heading is a contract nothing enforces and
    the words under it are the whole of it.

    Binding forms it does NOT read: `with ... as`, `except ... as`,
    `except* ... as`, an `import` binding, a comprehension target, a
    parameter default, and any target that is not a bare name — a tuple
    unpacking, a subscript, a class attribute.

    Three cost arms this control accepts on purpose. Each had a row in the
    sweep scan suite asserting the refusal rather than the miss; that
    suite went with cut 6 (#1483), so each arm below is declared and no
    longer asserted. (i) It scans the files the caller names, so a bound on
    the sweep's own grandchildren — bounded individually in
    `tests/_mutation_sweep.py` — is outside it: a bound on one child is
    a backstop, only a bound on the aggregate a margin. (ii) The scan
    reads the entry NAME, not the call it belongs to, so a bounded
    launch whose program only mentions that name in a string is
    refused. (iii) The match-capture over-approximation refuses a
    capture spent on a launch the sweep is not in: `case [_, _, host]:`
    on a subject holding the sweep elsewhere reads as the sweep and
    refuses a ping. A false red on a correct suite is the worse failure
    for a control this wide, so each is declared, not closed.

    The routes between those three were enumerated in `_DECLARED_MISSES`,
    the deleted sweep scan suite's own table, so a widening was one
    visible edit. That table, and the 25 program-binding routes beside
    it, went with cut 6 (#1483) and nothing asserts them now. Four of the
    analyser's own behaviours are pinned where they survive, in
    `tests/test_tree_analyser_helpers.py`: a bare-name launcher, the two
    passes of a scope's body, the binding in force at a launch's line, and
    each scope node's own program. A row in a table like that one can only
    red when the analyser IMPROVES; it is a claim, not a control, and
    with the table gone a new row is a claim with nothing behind it.
    """
    del tmp
    tests_dir = Path(__file__).resolve().parent
    trees = [(f'tests/{path.name}', ast.parse(path.read_text(
        encoding='utf-8'))) for path in sorted(tests_dir.glob('test_*.py'))]
    assert any(isinstance(node, ast.FunctionDef)
               and node.name == SWEEP_ENTRY
               for _, tree in trees for node in ast.walk(tree)), SWEEP_ENTRY
    found = [sweep_launches(tree, relative) for relative, tree in trees]
    launches = [site for pair in found for site in pair[0]]
    timed = [site for pair in found for site in pair[1]]
    assert launches, 'no tracked suite launches the mutation sweep at all'
    assert not timed, timed


# Only the FLOOR is pinned — the smallest count any version runs, and the
# one the removal rests on. A hard-pinned ceiling is red again on the next
# batch of coverage rows, and `derived >= floor` is the claim the disclosure
# makes, so the inequality is not the weaker check. Which version is checked
# how is stated once, in the sweep-bound test's docstring, which is the text
# the disclosure assertion below is keyed on.
_SWEEP_FLOOR_3_11 = 142
_CHILD_BOUND_S = 30
_TRUNCATED_BY_S = 120


def _specs_gate():
    """The module and expression that gate the version-dependent rows.

    Read out of the specs module rather than copied here, so a renamed
    guard makes the disclosure assertion demand the new spelling instead
    of pinning a stale one.
    """
    module = Path(__file__).parent / '_coverage_mutation_specs.py'
    source = module.read_text(encoding='utf-8')
    found = re.search(r"\) (if hasattr\(ast, '\w+'\) else \(\))", source)
    return module.name, found.group(1) if found else ''


def _guard_disclosure():
    """The docstring of the sweep-bound test, as the tree reads it."""
    tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
    name = 'test_a_sweep_launch_carries_no_wall_clock_bound'
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_docstring(node) or ''


def _child_deadlines():
    """Every `timeout=` a launch inside tests/_mutation_sweep.py carries."""
    tree = ast.parse(
        (Path(__file__).parent / '_mutation_sweep.py').read_text(
            encoding='utf-8'))
    return {kw.value.value for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'run'
            for kw in node.keywords
            if kw.arg == 'timeout'
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, int)}


def test_the_sweep_disclosure_numbers_are_derived_not_carried(tmp):
    """The row count, the child bound and the arithmetic are measured.

    The removal's whole argument scales with the row count: a row
    dropped makes 3660s wrong, and 120s stops being a thirty-fold
    margin. Nothing else on this branch would notice, because a
    docstring is prose and prose does not fail.

    The count is version-dependent, so this derives it on the RUNNING
    interpreter against one floor, `_SWEEP_FLOOR_3_11`, which 3.11 is
    checked against exactly and every later version is checked against as
    a floor. Requiring one figure everywhere is the defect that replaced
    an even earlier pin: it can only ever be right on one version, and it
    went red on the other three CI legs. The last two assertions require
    the disclosure to state the floor, the product and the gate the
    runner really uses, so the constant and the prose cannot drift from
    each other. That last link is a check on prose, not on behaviour: it
    fails on a reworded sentence, which is loud rather than silent, and
    it is the price of keeping the numbers in the sentence a reader of
    the control actually reads.
    """
    del tmp
    child = subprocess.run(
        [sys.executable, '-B', '-c',
         'import sys; sys.path.insert(0, "tests");'
         ' import test_coverage_bindings as s;'
         ' print(len(s._mutation_specs()))'],
        cwd=_util.ROOT, capture_output=True, text=True, check=True)
    rows = int(child.stdout.strip())
    bounds = _child_deadlines()
    assert bounds == {30}, bounds
    child_bound = int(next(iter(bounds)))
    assert child_bound == _CHILD_BOUND_S, child_bound
    floor = _SWEEP_FLOOR_3_11
    if sys.version_info[:2] < (3, 12):
        # The version that pins the floor is the one checked against it
        # exactly; 3.11 is where the floor IS, so any drift is a finding.
        assert rows == floor, (sys.version_info[:2], rows, floor)
    else:
        # Elsewhere the claim is the inequality the disclosure makes, and
        # the count only grows as main adds coverage rows.
        assert rows >= floor, (sys.version_info[:2], rows, floor)
    worst = floor * child_bound
    # Prose is checked with its wrapping collapsed, so a rewrap is not a
    # failure and a reword still is.
    disclosure = ' '.join(_guard_disclosure().split())
    assert f'{floor} x {child_bound}s = {worst}s' in disclosure, worst
    assert 'about thirty-five times over' in disclosure
    assert 'deliberately not pinned' in disclosure
    assert 35 <= worst / _TRUNCATED_BY_S < 36, worst
    # The REASON, not only the number: a disclosure that got the count
    # right for the wrong reason would pass everything above, and a
    # reader would go looking for a missing needle in the wrong module.
    # This couples the control to a spelling — a reworded sentence goes
    # red — and that is the intended kind of failure.
    gate_module, gate_expression = _specs_gate()
    assert gate_expression, 'the specs module no longer carries a guard'
    assert gate_module in disclosure, gate_module
    assert gate_expression in disclosure, gate_expression


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals())),
                                  tmp_prefix='staticguards_'))
