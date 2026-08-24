#!/usr/bin/env python3
"""scripts/gen_gitignore.py, which decides what this repository publishes.

The ignore file denies by default and names back exactly what ships, so the
generator's postcondition — every tracked path is still named — is the whole
guard. These tests drive it against repositories where Git fails, times out,
answers something it cannot parse, or holds a path a byte cannot decode.
"""
import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


def test_gitignore_postcondition_detects_an_ignored_tracked_file(tmp):
    """The generator's final check must inspect paths already in the index."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_mutation')
    baseline = generator.main(repo)
    assert baseline == 0, (
        'the postcondition rejected the generated tracked-file allow rule')
    real_run = generator.subprocess.run

    def mutate_before_postcondition(args, **kwargs):
        if 'check-ignore' in args:
            (repo / '.gitignore').write_text('*\n', encoding='utf-8')
        return real_run(args, **kwargs)

    generator.subprocess.run = mutate_before_postcondition
    try:
        result = generator.main(repo)
    finally:
        generator.subprocess.run = real_run

    assert result == 1, (
        'the postcondition accepted a tracked file ignored by the mutation')


def test_gitignore_postcondition_rejects_a_fatal_check_error(tmp):
    """A failed Git check cannot report that every tracked file is named."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_fatal')
    real_run = generator.subprocess.run

    def inject_fatal_check_error(args, **kwargs):
        if 'check-ignore' in args:
            return subprocess.CompletedProcess(
                args, 128, stdout='',
                stderr='fatal: injected check failure')
        return real_run(args, **kwargs)

    generator.subprocess.run = inject_fatal_check_error
    error = io.StringIO()
    try:
        with contextlib.redirect_stderr(error):
            result = generator.main(repo)
    finally:
        generator.subprocess.run = real_run

    assert result != 0, 'a fatal check error was reported as release-safe'
    assert 'fatal: injected check failure' in error.getvalue(), (
        'the fatal check diagnostic was hidden from the operator')


def test_gitignore_rejects_match_status_without_matched_paths(tmp):
    """Git match status without paths is an inconsistent failed guard."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_empty_match')
    real_run = generator.subprocess.run

    def inject_empty_match(args, **kwargs):
        if 'check-ignore' in args:
            return subprocess.CompletedProcess(
                args, 0, stdout='', stderr='')
        return real_run(args, **kwargs)

    generator.subprocess.run = inject_empty_match
    error = io.StringIO()
    try:
        with contextlib.redirect_stderr(error):
            result = generator.main(repo)
    finally:
        generator.subprocess.run = real_run

    assert result == 1, 'Git match status without paths was release-safe'
    assert 'reported matches without naming paths' in error.getvalue(), (
        'the inconsistent Git result was hidden from the operator')


def _gitignore_exception_result(tmp, exception_type, failing_command):
    """Run the generator with one Git command raising an injected exception."""
    repo = Path(tmp) / f'{exception_type.__name__}-{failing_command}'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    module_name = f'gen_gitignore_{exception_type.__name__}_{failing_command}'
    generator = _util.load(ROOT / 'scripts' / 'gen_gitignore.py', module_name)
    real_run = generator.subprocess.run

    def inject_exception(args, **kwargs):
        if failing_command in args:
            if exception_type is FileNotFoundError:
                raise FileNotFoundError('injected launch failure')
            raise subprocess.TimeoutExpired(args, kwargs.get('timeout'))
        return real_run(args, **kwargs)

    generator.subprocess.run = inject_exception
    error = io.StringIO()
    result = None
    caught = None
    try:
        with contextlib.redirect_stderr(error):
            result = generator.main(repo)
    except (OSError, subprocess.SubprocessError) as failure:
        caught = failure
    finally:
        generator.subprocess.run = real_run
    return result, caught, error.getvalue()


def test_gitignore_reports_launch_failures_from_both_git_calls(tmp):
    """A Git launch failure at either boundary returns an operator failure."""
    for command in ('ls-files', 'check-ignore'):
        result, caught, error = _gitignore_exception_result(
            tmp, FileNotFoundError, command)
        assert caught is None, (command, caught)
        assert result == 1, command
        assert f'git {command} failed' in error, (command, error)


def test_gitignore_reports_timeouts_from_both_git_calls(tmp):
    """A Git timeout at either boundary returns an operator failure."""
    for command in ('ls-files', 'check-ignore'):
        result, caught, error = _gitignore_exception_result(
            tmp, subprocess.TimeoutExpired, command)
        assert caught is None, (command, caught)
        assert result == 1, command
        assert f'git {command} failed' in error, (command, error)


def _gitignore_invalid_output_result(tmp, failing_command):
    """Run one Git boundary against a child that writes invalid UTF-8."""
    repo = Path(tmp) / f'invalid-output-{failing_command}'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py',
        f'gen_gitignore_invalid_output_{failing_command}')
    real_run = generator.subprocess.run

    def emit_invalid_bytes(args, **kwargs):
        # Raise the decode failure rather than trying to provoke one from a
        # real child. subprocess decodes with the locale, and the byte that
        # is undecodable as UTF-8 is an ordinary character under the Windows
        # code page — so the previous spelling of this injection simulated
        # nothing there and the generator succeeded. What the generator
        # contracts is that a decode failure at either Git boundary becomes
        # an operator-shaped result, and that is exactly what this asks it.
        if failing_command in args:
            raise UnicodeDecodeError(
                'utf-8', b'\xff', 0, 1, 'invalid start byte')
        return real_run(args, **kwargs)

    generator.subprocess.run = emit_invalid_bytes
    error = io.StringIO()
    result = None
    caught = None
    try:
        with contextlib.redirect_stderr(error):
            result = generator.main(repo)
    except UnicodeDecodeError as failure:
        caught = failure
    finally:
        generator.subprocess.run = real_run
    return result, caught, error.getvalue()


def test_gitignore_reports_invalid_output_from_both_git_calls(tmp):
    """Invalid output at either Git boundary returns an operator failure."""
    for command in ('ls-files', 'check-ignore'):
        result, caught, error = _gitignore_invalid_output_result(tmp, command)
        assert caught is None, (command, caught)
        assert result == 1, command
        assert f'git {command} failed' in error, (command, error)


def test_gitignore_bounds_both_git_calls(tmp):
    """Both local Git operations receive an explicit finite timeout."""
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    tracked = repo / 'tracked.txt'
    tracked.write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'tracked.txt'], check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_timeouts')
    real_run = generator.subprocess.run
    timeouts = []

    def record_timeout(args, **kwargs):
        timeouts.append(kwargs.get('timeout'))
        return real_run(args, **kwargs)

    generator.subprocess.run = record_timeout
    try:
        result = generator.main(repo)
    finally:
        generator.subprocess.run = real_run

    assert result == 0, result
    assert len(timeouts) == 2, timeouts
    assert all(isinstance(item, (int, float)) and item > 0
               for item in timeouts), timeouts


def test_gitignore_names_a_tracked_path_containing_a_space(tmp):
    """A space in a tracked filename must survive enumeration as ONE path.

    `git ls-files` without `-z` separates paths on whitespace, so
    'has space.txt' became two tokens; the generator then named and
    postcondition-checked the FRAGMENTS and reported success while the real
    file stayed ignored — a fail-open postcondition on the most ordinary
    filename shape there is.
    """
    repo = Path(tmp) / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    (repo / 'plain.txt').write_text('published\n', encoding='utf-8')
    (repo / 'has space.txt').write_text('published\n', encoding='utf-8')
    subprocess.run(
        ['git', '-C', str(repo), 'add', '-f', 'plain.txt', 'has space.txt'],
        check=True)

    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_space')
    result = generator.main(repo)
    assert result == 0, 'the generator rejected its own space-named allow rule'
    rules = (repo / '.gitignore').read_text(encoding='utf-8').splitlines()
    assert '!/has space.txt' in rules, rules
    ignored = subprocess.run(
        ['git', '-C', str(repo), 'check-ignore', '--no-index', '--',
         'has space.txt'],
        capture_output=True, check=False)
    assert ignored.returncode != 0, (
        'the generated .gitignore still ignores the tracked space-named file')


def test_gitignore_survives_an_undecodable_byte_in_the_repo_path(tmp):
    """A repository path carrying a raw byte must not crash the generator.

    argv arrives via surrogateescape, so a byte 0xff in the path becomes
    U+DCFF in the value the diagnostics print; under
    PYTHONIOENCODING=utf-8:strict both the success line and the failure
    lines raised UnicodeEncodeError — the success one AFTER a correct
    .gitignore had already been written. Run as a subprocess because the
    strict encoding has to be in force before the interpreter starts.
    """
    _util.require_undecodable_names(tmp)
    repo = os.fsencode(tmp) + b'/\xffrepo'
    os.mkdir(repo)
    subprocess.run(['git', '-C', repo, 'init', '-q'], check=True)
    with open(repo + b'/tracked.txt', 'wb') as handle:
        handle.write(b'published\n')
    subprocess.run(['git', '-C', repo, 'add', '-f', 'tracked.txt'],
                   check=True)

    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8:strict'}
    script = str(ROOT / 'scripts' / 'gen_gitignore.py')
    ok = subprocess.run([sys.executable, script, os.fsdecode(repo)],
                        capture_output=True, text=True, env=env, check=False)
    assert ok.returncode == 0, (ok.returncode, ok.stdout, ok.stderr)

    not_a_repo = os.fsencode(tmp) + b'/\xffplain'
    os.mkdir(not_a_repo)
    fail = subprocess.run(
        [sys.executable, script, os.fsdecode(not_a_repo)],
        capture_output=True, text=True, env=env, check=False)
    assert fail.returncode == 1, (fail.returncode, fail.stdout, fail.stderr)
    assert 'UnicodeEncodeError' not in fail.stderr, fail.stderr
    assert 'FAIL' in fail.stderr, fail.stderr


def test_gitignore_log_safe_never_raises_and_stays_useful(tmp):
    """The generator's copy of the log safeguard must not itself raise.

    Same finding as the server copy: str(value) and the encode step ran
    outside any fallback, so a conversion-limited huge int, an exception
    object with a failing __str__, or a str subclass with a raising encode
    or a non-string decode turned the diagnostic into the failure. The
    copies cannot share an implementation (importing server.py requires its
    env and runs module-level config), so they are kept behavior-identical —
    this test holds the generator's copy to the same shared contract.
    """
    del tmp
    generator = _util.load(
        ROOT / 'scripts' / 'gen_gitignore.py', 'gen_gitignore_log_safe')

    for value, expected in _util.log_safe_cases():
        assert generator._log_safe(value) == expected, (
            f'_log_safe({type(value).__name__}) disagrees')
    # Ordinary values pass through in full, ASCII and non-ASCII alike.
    assert generator._log_safe('plain ascii') == 'plain ascii'
    assert generator._log_safe('héllo — 世界') == 'héllo — 世界'


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gitignoregen_')


if __name__ == '__main__':
    raise SystemExit(main())
