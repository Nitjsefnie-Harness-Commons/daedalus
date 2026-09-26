#!/usr/bin/env python3
"""Focused real-tree regressions for the static guard suites."""
import ast
import os
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
from _coverage_scopes import (  # noqa: E402
    _evaluation_scopes, _scope_bindings)
from _mutation_sweep import mutation_sweep  # noqa: E402
from _owned_writes import copy_test_tree  # noqa: E402
from _sweep_launch_scan import SWEEP_ENTRY, sweep_launches  # noqa: E402


def _real_module_copy(tmp, relative):
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    return root, root / relative


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

    That child runs 127 mutation rows, one individually-bounded
    grandchild each, so an outer bound on it decides a verdict its own
    work does not own: 127 x 30s = 3810s is the work a runaway backstop
    would have to cover, and 120s truncates it about thirty-two times
    over. A wall bound is legitimate where the child always spends it on
    real work — the freeze controls busy-wait on purpose, so a wedged
    child is the only failure a ceiling names — but this child is real
    work that runs long, and the aggregate is observed to take minutes
    where a margin allows two.

    That observation supports the argument rather than carrying it,
    because it does not reproduce to a figure: the same child has been
    measured from 16s to 145s, `returncode 0` every time. What moves the
    number is the host's AMBIENT load, not the measurement — these boxes
    run at loadavg 25 on 12 cores while a 127-child sweep wants all of
    them — so a saturation figure says more about the neighbours than
    about the bound. The arithmetic above is what the removal rests on;
    the timing only shows the margin is not comfortably large. Re-derive
    it by timing the entry test under `nproc` burners while reading
    /proc/loadavg.

    What bounds a wedged child now: `run_tests.py:14`
    `DEFAULT_SUITE_TIMEOUT_S = 900`, applied at `:95` by
    `process.wait(timeout=timeout)` and reported as `SUITE TIMED OUT` at
    `:101`, so under the `suites` job a wedged sweep child hangs for 900s
    and then says so. `scripts/ci/coverage_suites.py:28-34` passes no
    `timeout=` at all and `tests/_util.py:621` has no per-test bound, so
    under `coverage-matrix` the only escape is the job's
    `timeout-minutes: 30` (`.github/workflows/tests.yml:716`) — a
    30-minute hang the runner kills. The removal trades a 120s red for
    that; it does not remove the need for a bound, only this one.

    Enforced structurally over every `tests/test_*.py`, so a site added
    later is covered without a list to maintain: no `timeout` keyword on
    a `subprocess.run` / `call` / `check_call` / `check_output` call —
    spelled by `import subprocess`, `import subprocess as x`,
    `from subprocess import run [as x]` or `from subprocess import *` —
    carrying the sweep's entry test in an argument, as a literal, an
    f-string, a name, or a list those are elements of. It also fails
    when the scan reaches no such launch, and when the entry name it
    keys on is no function any tracked suite defines, which a rename
    that stranded the two program strings would have left green.

    Each launch is judged on the binding in force AT ITS OWN LINE — the
    LAST one at or before it, never the join of every binding that ever
    existed by then. So a scope that reuses a name reds nothing in
    either direction: not for the launch that ran before the reuse, and
    not for one that ran after an unrelated binding replaced the program
    it holds. The cost is a program the scope defines BELOW its launch,
    which is not read into a call it did not run.

    Not enforced, and not claimed to be: (1) a `timeout` unpacked from
    a `**` mapping on the same call, which the scan does not read; (2) a
    program no readable binding reaches — a `str.join`, a value read
    from a mapping or off disk, or a template built at runtime; (3) a
    program bound in another scope — read in a nested function, class or
    lambda, named by a comprehension target, a parameter default, or a
    `global` / `nonlocal` declaration, or imported from another module,
    because each scope reads only its own bindings; (4) a bare-name
    target bound by a form the scan does not read — `with ... as`,
    `except ... as` and `except* ... as`, which name a context manager
    and an exception, and a target that is not a bare name at all: a
    tuple unpacking, a subscript, a class attribute. `=`, `+=`, an
    annotated `=`, a walrus, a `for ... in` target, a match capture and
    `append` / `extend` ARE read; a `for` target binds the iterated
    expression and a match capture binds the match subject, both
    over-approximations of the value they will really hold, which fail
    toward finding a launch rather than past one; (5) an argv grown by
    anything but `append` / `extend` — an `insert`, say; (6) a launcher
    the import scan does not type — `__import__`, `getattr`,
    `sys.modules`, or a `subprocess.Popen` whose `wait` or
    `communicate` carries the deadline; (7) a deadline spelled without
    the word, a clock comparison plus a kill or a `signal.alarm`; (8) a
    `timeout` defaulted inside a helper the launch is routed through;
    (9) any file
    outside `tests/test_*.py` — the sweep's own grandchildren are
    bounded at 30s each in `tests/_mutation_sweep.py`, which this
    control leaves alone: a bound on one child is a backstop, only a
    bound on the aggregate a margin; and (10) the flip side of the key —
    the scan reads the entry NAME, not the call it belongs to, so a
    bounded launch whose program only mentions that name in a string is
    refused. Separating them means parsing the child program itself, and
    a false red on a correct suite is the worse failure here, so the arm
    is declared, not closed.

    Arms 1 to 8 are the analyser's, and each has a row pinned as missed
    in `tests/test_sweep_launch_scan.py`; arm 9 is the caller's glob and
    arm 10 is a false red this control accepts on purpose.
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


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals())),
                                  tmp_prefix='staticguards_'))
