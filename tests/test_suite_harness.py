"""Controls for tests/conftest.py, one per run_tests.py rule it carries.

Each control writes test files into its own directory and runs pytest on
them in a child process with the real conftest loaded as a plugin.
"""
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent
OUTCOME = re.compile(r'^(\S+::\S+) (PASSED|FAILED|SKIPPED|ERROR)', re.M)
VERDICT = re.compile(r'^(?:NO COVERAGE|NOT RUN HERE): .*$', re.M)


def run_pytest(tmp, files, *args, **env):
    """Exit code, output and verbose per-test outcomes of one pytest run."""
    for name, body in files.items():
        Path(tmp, name).write_text(textwrap.dedent(body), encoding='utf-8')
    # An empty pytest.ini makes `tmp` the rootdir, so no ancestor's
    # configuration reaches the child.
    Path(tmp, 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    environ = {key: value for key, value
               in _util.child_coverage('scrub').items()
               if not key.startswith('PYTEST_')}
    environ.update(env, PYTHONPATH=str(TESTS))
    done = subprocess.run(
        [sys.executable, '-m', 'pytest', '-p', 'conftest', '-v', *args,
         *files], cwd=tmp, env=environ, stdin=subprocess.DEVNULL,
        capture_output=True, encoding='utf-8', errors='replace',
        timeout=300)
    output = done.stdout + done.stderr
    return done.returncode, output, dict(OUTCOME.findall(output))


def test_tmp_is_a_private_resolved_directory_named_for_its_test(tmp):
    real, seen = os.path.join(tmp, 'real'), os.path.join(tmp, 'seen')
    os.mkdir(real)
    os.symlink(real, os.path.join(tmp, 'link'), target_is_directory=True)
    code, output, outcomes = run_pytest(tmp, {'test_dirs.py': f'''
        import os

        def record(name, path):
            assert type(path) is str and os.listdir(path) == []
            with open({seen!r}, 'a', encoding='utf-8') as log:
                log.write(name + '\\t' + path + '\\n')
            open(os.path.join(path, 'mine'), 'w').close()

        def test_first(tmp):
            record('test_first', tmp)

        def test_second(tmp):
            record('test_second', tmp)

        def test_alias(_tmp):
            record('test_alias', _tmp)
        ''', 'test_held.py': '''
        import contextlib
        import _teardown

        REAL, ATTEMPTS = _teardown.OwnedTemporaryDirectory.cleanup, 2
        held = []

        def refuse(self):
            held.append(self)
            raise PermissionError(13, 'held open', self.name)

        @contextlib.contextmanager
        def suite_scope():
            saved, _teardown.ATTEMPTS = _teardown.ATTEMPTS, ATTEMPTS
            _teardown.OwnedTemporaryDirectory.cleanup = refuse
            yield
            _teardown.OwnedTemporaryDirectory.cleanup = REAL
            _teardown.ATTEMPTS = saved
            assert len(held) == ATTEMPTS, f'cleanup tried {len(held)} times'
            REAL(held[0])

        def test_held(tmp):
            pass
        '''}, TMPDIR=os.path.join(tmp, 'link'))
    assert (code, set(outcomes.values())) == (0, {'PASSED'}), output
    assert 'UserWarning: temporary tree left behind: ' in output, output
    paths = dict(line.split('\t') for line in
                 Path(seen).read_text(encoding='utf-8').splitlines())
    assert sorted(paths) == ['test_alias', 'test_first', 'test_second']
    roots = {os.path.dirname(path) for path in paths.values()}
    assert all(os.path.basename(path) == name
               for name, path in paths.items()), paths
    assert len(roots) == 1 and os.path.dirname(*roots) == real, paths
    assert not os.path.exists(*roots), 'the module root outlived its tests'


def test_only_a_skip_in_allowed_skips_skips(tmp):
    code, output, outcomes = run_pytest(tmp, {'test_default.py': '''
        import unittest
        import pytest
        import _util

        def test_allowed():
            _util.skip('not here')

        def test_pytest_skip():
            pytest.skip('native')

        def test_unittest_skip():
            raise unittest.SkipTest('unittest')

        @pytest.mark.skip(reason='marker')
        def test_marked():
            pass
        ''', 'test_narrowed.py': '''
        import _util

        class Browser(_util.Skipped):
            pass

        ALLOWED_SKIPS = (Browser,)

        def test_browser():
            raise Browser('no browser')

        def test_component():
            _util.skip('excused')
        '''})
    assert code == 1, output
    assert outcomes == {
        'test_default.py::test_allowed': 'SKIPPED',
        'test_default.py::test_pytest_skip': 'FAILED',
        'test_default.py::test_unittest_skip': 'FAILED',
        'test_default.py::test_marked': 'ERROR',
        'test_narrowed.py::test_browser': 'SKIPPED',
        'test_narrowed.py::test_component': 'FAILED'}, output
    assert 'Skipped is not in ALLOWED_SKIPS: excused' in output, output
    assert VERDICT.findall(output) == [], output


def test_a_test_whose_body_does_not_run_to_completion_fails(tmp):
    code, output, outcomes = run_pytest(tmp, {'test_shapes.py': '''
        async def test_async():
            pass

        def test_returns_generator():
            return (n for n in ())

        def test_returns_value():
            return 1

        def test_exits_zero():
            raise SystemExit(0)
        '''})
    assert (code, len(outcomes), set(outcomes.values())) == (
        1, 4, {'FAILED'}), output


def test_a_file_that_passed_nothing_fails_unless_it_names_a_need(tmp):
    files = {'test_unrun.py': '''
        import _util
        REQUIRES = 'a browser'

        def test_one():
            _util.skip('no browser')

        def test_two():
            _util.skip('no browser')
        ''', 'test_hollow.py': '''
        import _util

        def test_one():
            _util.skip('nothing here')
        ''', 'test_ran.py': '''
        import _util

        def test_ran():
            pass

        def test_skipped():
            _util.skip('one of two')
        '''}
    unrun = 'NOT RUN HERE: test_unrun.py (2 skipped, needs a browser)'
    code, output, _ = run_pytest(tmp, files, '-n', '2')
    assert (code, VERDICT.findall(output)) == (1, [
        'NO COVERAGE: test_hollow.py (1 skipped)', unrun]), output
    code, output, _ = run_pytest(tmp, {'test_unrun.py': files.pop(
        'test_unrun.py')})
    assert (code, VERDICT.findall(output)) == (0, [unrun]), output


def test_a_module_that_collects_no_clean_test_fails_collection(tmp):
    code, output, _ = run_pytest(tmp, {'test_none.py': '''
        HELPER = 1
        ''', 'test_env_write.py': '''
        import os
        os.environ['HARNESS_IMPORT_WRITE'] = '1'

        def test_x():
            pass
        ''', 'test_cwd_write.py': '''
        import os
        os.chdir('..')

        def test_x():
            pass
        ''', 'test_skipped.py': '''
        import pytest
        pytest.importorskip('harness_absent_module')

        def test_x():
            pass
        ''', 'test_generator.py': '''
        def test_x():
            yield
        '''})
    errors = dict(re.findall(r'^_+ ERROR collecting (\S+) _+\n(.*)$', output,
                             re.M))
    # pytest itself refuses a generator test; the rest are conftest's.
    generator = errors.pop('test_generator.py', '')
    assert code == 2 and 'yield' in generator, output
    assert errors == {
        'test_none.py': 'it collected no test',
        'test_env_write.py':
            'importing it changed os.environ: HARNESS_IMPORT_WRITE',
        'test_cwd_write.py': 'importing it changed the working directory',
        'test_skipped.py': 'it skipped at collection'}, output


def test_narrowed_suite_deselects_every_test_it_does_not_name(tmp):
    code, output, outcomes = run_pytest(tmp, {'test_narrowed.py': '''
        def narrowed_suite():
            return ['test_kept']

        def test_kept():
            pass

        def test_dropped():
            raise AssertionError('deselected')
        ''', 'test_whole.py': '''
        def narrowed_suite():
            return None

        def test_kept():
            pass
        '''})
    assert (code, outcomes) == (0, {
        'test_narrowed.py::test_kept': 'PASSED',
        'test_whole.py::test_kept': 'PASSED'}), output
    assert ' 1 deselected' in output, output


def test_a_file_past_its_bound_fails_and_a_hang_ends_the_run(tmp):
    # test_spent moves the clock the bound is read on past the bound, so
    # test_after starts with no budget left whatever the machine's speed.
    code, output, outcomes = run_pytest(tmp, {'test_spent.py': '''
        import time

        def test_spent():
            time.monotonic = lambda clock=time.monotonic: clock() + 2

        def test_after():
            pass
        ''', 'test_hang.py': '''
        import threading

        def test_hang():
            threading.Event().wait()
        '''}, DAEDALUS_SUITE_TIMEOUT='2')
    assert (code, outcomes) == (1, {'test_spent.py::test_spent': 'PASSED',
                                    'test_spent.py::test_after': 'ERROR'}), (
        output)
    assert ('SUITE TIMED OUT after 2.0 s (returncode None) in '
            'test_hang.py::test_hang; its last lines are above') in output, (
        output)
    hangs = {'test_import_hang.py': ('-n', '2'), 'test_narrow_hang.py': ()}
    for name, args in hangs.items():
        code, output, _ = run_pytest(tmp, {name: '''
            import threading

            def narrowed_suite():
                threading.Event().wait()

            def test_x():
                pass
            ''' + ('threading.Event().wait()' if args else '')}, *args,
            DAEDALUS_SUITE_TIMEOUT='2')
        assert code != 0 and (f'SUITE TIMED OUT after 2.0 s (returncode '
                              f'None) in {name}; ') in output, output


def test_each_test_starts_at_root_with_process_state_restored(tmp):
    # Each loads the bridge's config with its own root, one at import and
    # one in narrowed_suite; the runner gave each file a process.
    load = '''
        import os, sys, _util
        from unittest import mock
        SOURCE = _util.ROOT / 'daedalus_bridge' / 'http_transport.py'

        def load(side):
            with mock.patch.dict(os.environ, DAEDALUS_DIR=side,
                                 DAEDALUS_PORT='0'):
                _util.load(SOURCE)

        def test_own_config():
            config = sys.modules['daedalus_bridge.config']
            assert str(config.BASE) == __name__[-1], config.BASE
        '''
    loads = {'test_a.py': load + "load('a')\n", 'test_b.py': load +
             "def narrowed_suite():\n            load('b')\n"}
    code, output, outcomes = run_pytest(tmp, {**loads, 'test_state.py': f'''
        import contextlib
        import os
        import sys

        @contextlib.contextmanager
        def suite_scope():
            cwd, os.environ['HARNESS_SCOPE'] = os.getcwd(), '1'
            yield
            assert os.getcwd() == cwd, 'cwd leaked past the tests'
            del os.environ['HARNESS_SCOPE']

        def test_leaks():
            os.environ['HARNESS_LEAK'] = '1'
            os.chdir(os.sep)
            sys.path.append('harness-leak')

        def test_sees_none():
            assert os.getcwd() == {str(_util.ROOT)!r}
            assert 'HARNESS_LEAK' not in os.environ
            assert 'harness-leak' not in sys.path
            assert os.environ['HARNESS_SCOPE'] == '1'
            assert 'daedalus_bridge.config' not in sys.modules
        '''})
    assert (code, outcomes) == (0, {
        'test_a.py::test_own_config': 'PASSED',
        'test_b.py::test_own_config': 'PASSED',
        'test_state.py::test_leaks': 'PASSED',
        'test_state.py::test_sees_none': 'PASSED'}), output


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
