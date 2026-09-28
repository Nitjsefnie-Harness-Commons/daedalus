#!/usr/bin/env python3
"""The shared runner refuses a test whose body it did not run."""
import asyncio
import contextlib
import gc
import io
import json
import os
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ASYNC_DEF_DETAIL = (
    'an async def or generator test is not awaited or iterated '
    'by the runner; drive the coroutine with asyncio.run inside '
    'a plain def test')
RETURNED_DETAIL = ('returned an awaitable or generator '
                   'the runner does not run: ')

PROBE_SUITE = '''\
import sys
from pathlib import Path

sys.path.insert(0, {tests!r})
import _util  # noqa: E402


async def test_async_body_never_runs(tmp):
    raise AssertionError('the body ran, so the runner awaited it')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
'''


def _run(tests):
    out = io.StringIO()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with contextlib.redirect_stdout(out):
            code = _util.runner(tests)
        gc.collect()
    return code, out.getvalue(), [str(w.message) for w in caught]


def _never_awaited(messages):
    return [m for m in messages if 'never awaited' in m]


class _Awaitable:
    def __await__(self):
        yield


def test_an_async_def_test_is_refused_without_being_called(tmp):
    del tmp

    async def test_x(tmp):
        del tmp

    code, text, messages = _run([test_x])
    assert code == 1, text
    assert f'  FAIL  test_x: {ASYNC_DEF_DETAIL}\n' in text, text
    assert '  PASS' not in text, text
    assert not _never_awaited(messages), messages


def test_an_async_generator_test_is_refused_without_being_called(tmp):
    del tmp

    async def test_x(tmp):
        yield tmp

    code, text, messages = _run([test_x])
    assert code == 1, text
    assert f'  FAIL  test_x: {ASYNC_DEF_DETAIL}\n' in text, text
    assert '  PASS' not in text, text
    assert not _never_awaited(messages), messages


def test_a_generator_function_test_is_refused_without_being_called(tmp):
    del tmp

    def test_x(tmp):
        raise AssertionError('this body executed')
        yield  # pylint: disable=unreachable

    code, text, messages = _run([test_x])
    assert code == 1, text
    assert f'  FAIL  test_x: {ASYNC_DEF_DETAIL}\n' in text, text
    assert '  PASS' not in text, text
    assert '\n0/1 passed' in text, text
    assert not _never_awaited(messages), messages


def test_a_returned_coroutine_is_refused_and_closed(tmp):
    del tmp

    async def body():
        raise AssertionError('the coroutine ran')

    def test_x(tmp):
        del tmp
        return body()

    code, text, messages = _run([test_x])
    assert code == 1, text
    assert f'  FAIL  test_x: {RETURNED_DETAIL}coroutine\n' in text, text
    assert '\n0/1 passed' in text, text
    assert not _never_awaited(messages), messages


def test_a_returned_awaitable_object_is_refused(tmp):
    del tmp

    def test_x(tmp):
        del tmp
        return _Awaitable()

    code, text, messages = _run([test_x])
    assert code == 1, text
    assert f'  FAIL  test_x: {RETURNED_DETAIL}_Awaitable\n' in text, text
    assert '\n0/1 passed' in text, text
    assert not _never_awaited(messages), messages


def test_a_returned_async_generator_is_refused(tmp):
    del tmp

    async def body():
        yield 1

    def test_x(tmp):
        del tmp
        return body()

    code, text, messages = _run([test_x])
    assert code == 1, text
    assert (f'  FAIL  test_x: {RETURNED_DETAIL}async_generator\n'
            in text), text
    assert '\n0/1 passed' in text, text
    assert not _never_awaited(messages), messages


def test_a_returned_generator_is_refused(tmp):
    del tmp

    def test_x(tmp):
        del tmp
        return (x for x in ())

    code, text, messages = _run([test_x])
    assert code == 1, text
    assert f'  FAIL  test_x: {RETURNED_DETAIL}generator\n' in text, text
    assert '\n0/1 passed' in text, text
    assert not _never_awaited(messages), messages


def test_a_refused_test_counts_as_failed_in_the_summary(tmp):
    async def test_x(tmp):
        del tmp

    summary = os.path.join(tmp, 'summary.json')
    with mock.patch.dict(os.environ, {'DAEDALUS_TEST_SUMMARY': summary}):
        code, text, _messages = _run([test_x])
    assert code == 1, text
    assert '\n0/1 passed\n' in text, text
    with open(summary, encoding='utf-8') as handle:
        counts = json.load(handle)
    assert counts == {'total': 1, 'passed': 0, 'skipped': 0,
                      'failed': 1, 'requires': None}, counts


def test_a_plain_test_returning_none_passes(tmp):
    del tmp

    def test_x(tmp):
        del tmp

    code, text, messages = _run([test_x])
    assert code == 0, text
    assert '  PASS  test_x\n' in text, text
    assert '\n1/1 passed\n' in text, text
    assert not _never_awaited(messages), messages


def test_a_plain_test_returning_a_value_passes(tmp):
    del tmp

    def test_x(tmp):
        del tmp
        return 'a string'

    def test_y(tmp):
        del tmp
        return 42

    code, text, _messages = _run([test_x, test_y])
    assert code == 0, text
    assert '  PASS  test_x\n' in text, text
    assert '  PASS  test_y\n' in text, text
    assert '\n2/2 passed\n' in text, text


def test_a_test_driving_its_coroutine_with_asyncio_run_passes(tmp):
    del tmp
    ran = []

    async def body():
        ran.append(True)
        return 'awaited'

    def test_x(tmp):
        del tmp
        return asyncio.run(body())

    code, text, messages = _run([test_x])
    assert code == 0, text
    assert '  PASS  test_x\n' in text, text
    assert ran == [True], ran
    assert not _never_awaited(messages), messages


def test_a_raised_assertion_error_still_reports_fail(tmp):
    del tmp

    def test_x(tmp):
        del tmp
        raise AssertionError('the message')

    code, text, _messages = _run([test_x])
    assert code == 1, text
    assert '  FAIL  test_x: the message\n' in text, text
    assert '\n0/1 passed\n' in text, text


def test_the_issue_probe_suite_fails_without_a_never_awaited_warning(tmp):
    probe = Path(tmp) / 'test_probe.py'
    tests_dir = str(Path(__file__).resolve().parent)
    probe.write_text(PROBE_SUITE.format(tests=tests_dir), encoding='utf-8')
    env = dict(os.environ)
    env.pop('DAEDALUS_TEST_SUMMARY', None)
    result = subprocess.run(
        [sys.executable, str(probe)], cwd=str(_util.ROOT), env=env,
        capture_output=True, text=True, timeout=60, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert (f'  FAIL  test_async_body_never_runs: {ASYNC_DEF_DETAIL}\n'
            in result.stdout), output
    assert 'never awaited' not in output, output


ENDING_SUITE = '''\
import sys

sys.path.insert(0, {tests!r})
import _util  # noqa: E402


def test_a_the_subject_ends_the_run(tmp):
    del tmp
    {ending}


def test_b_a_plain_failure(tmp):
    del tmp
    assert False, 'a plain failure'


def test_c_the_last_test_runs(tmp):
    del tmp


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
'''

# A subject whose exit code is an object that refuses to be rendered. It
# overrides `__repr__` alone, so `repr()` and `str()` both reach the raising
# method: the report renders a subject's exit code one way and its exception
# message the other, and this shape defeats both.
EVIL_REPR_EXIT = '''\
class EvilRepr:
    def __repr__(self):
        raise RuntimeError('evil repr')


sys.exit(EvilRepr())
'''


def _run_probe(tmp, source, summary):
    """Launch one fixture suite the way the repository launches a suite.

    The escape is only observable across a process boundary: in-process the
    runner could hand the exception back to a caller, but a suite that ends
    the interpreter takes its own status and its whole stdout with it, and
    both are what the aggregate reads.
    """
    probe = Path(tmp) / 'test_probe.py'
    probe.write_text(source, encoding='utf-8')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               DAEDALUS_TEST_SUMMARY=str(summary))
    return subprocess.run(
        [sys.executable, str(probe)], cwd=str(_util.ROOT), env=env,
        capture_output=True, text=True, timeout=60, check=False)


def _reported(text):
    """(outcome, test name) per per-test line the runner printed.

    Every outcome but PASS appends `: detail`, so the name the timing parser
    reads off that line carries the colon with it; a test name is an
    identifier and never holds one, so it comes off here.
    """
    tags = ('  PASS  ', '  SKIP  ', '  FAIL  ', '  ERROR ')
    return [(line.split()[0], line.split()[1].rstrip(':'))
            for line in text.splitlines() if line.startswith(tags)]


def _line_for(text, name):
    return next(line for line in text.splitlines() if name in line)


def _ending_run(tmp, ending):
    summary = Path(tmp) / 'ending.json'
    source = ENDING_SUITE.format(
        tests=str(Path(__file__).resolve().parent),
        ending=textwrap.indent(ending, '    ').lstrip())
    return _run_probe(tmp, source, summary), summary


def test_a_subject_that_exits_is_a_failure_and_the_run_continues(tmp):
    """The whole chain of a run the subject tried to end, asserted at once.

    `sys.exit(0)` is the dangerous code: the process reported success having
    printed nothing. Any single link — a status, a per-test line, the summary,
    the JSON — could be restored while the others stay broken, so all four.
    """
    result, summary = _ending_run(tmp, 'sys.exit(0)')
    output = result.stdout + result.stderr
    assert result.returncode != 0, (result.returncode, output)
    assert _reported(result.stdout) == [
        ('FAIL', 'test_a_the_subject_ends_the_run'),
        ('FAIL', 'test_b_a_plain_failure'),
        ('PASS', 'test_c_the_last_test_runs'),
    ], output
    assert 'SystemExit(0)' in _line_for(
        result.stdout, 'test_a_the_subject_ends_the_run'), output
    assert '\n1/3 passed\n' in result.stdout, output
    with open(summary, encoding='utf-8') as handle:
        counts = json.load(handle)
    assert counts == {'total': 3, 'passed': 1, 'skipped': 0,
                      'failed': 2, 'requires': None}, counts


def test_the_reported_status_is_the_runners_not_the_subjects(tmp):
    """A refusal code must not become the suite's exit status.

    `argparse` refuses with 3, and 3 is where the runner's own 1 belongs; the
    chosen code belongs in the report, where 0 and 3 read as different events.
    """
    result, _summary = _ending_run(tmp, 'sys.exit(3)')
    output = result.stdout + result.stderr
    assert result.returncode == 1, (result.returncode, output)
    assert 'SystemExit(3)' in _line_for(
        result.stdout, 'test_a_the_subject_ends_the_run'), output


def test_a_keyboard_interrupt_still_ends_the_run(tmp):
    """The escape is caught by name, so the arms either side of it still hold.

    Reporting an interrupt as a FAIL and carrying on would leave a half-run
    file looking like a completed one, so nothing is reported at all.

    Silence alone cannot tell that apart from an arm that crashed reporting
    it, so this asserts the interrupt positively instead: the traceback ends
    at `KeyboardInterrupt`, which only a pass-through arm can produce, and it
    runs through the runner's own frame, which proves the runner reached the
    test rather than the absences below holding because nothing ran.
    """
    result, summary = _ending_run(tmp, 'raise KeyboardInterrupt()')
    output = result.stdout + result.stderr
    assert result.returncode != 0, (result.returncode, output)
    assert result.stderr.rstrip().endswith('KeyboardInterrupt'), output
    assert 'in runner' in result.stderr, output
    assert not _reported(result.stdout), output
    assert not summary.exists(), output


def test_a_string_exit_code_is_reported_verbatim(tmp):
    """A refusal message is the code `argparse` and a RefusingParser choose.

    A message is the one code shape a subject picks deliberately to be read,
    so the report has to carry it through unchanged rather than reducing every
    code to a number.
    """
    result, summary = _ending_run(tmp, "sys.exit('a refusal message')")
    output = result.stdout + result.stderr
    assert result.returncode == 1, (result.returncode, output)
    assert "SystemExit('a refusal message')" in _line_for(
        result.stdout, 'test_a_the_subject_ends_the_run'), output
    assert _reported(result.stdout)[0] == (
        'FAIL', 'test_a_the_subject_ends_the_run'), output
    with open(summary, encoding='utf-8') as handle:
        counts = json.load(handle)
    assert counts['failed'] == 2, counts


def test_a_code_that_refuses_to_render_is_named_not_rendered(tmp):
    """The report line must not be able to end the run it is reporting on.

    `SystemExit(obj)` where `obj.__repr__` raises reaches the arm's own
    interpolation, and an f-string that raises while formatting takes the
    remaining tests with it — the exact failure this change removes, reached
    by the line added to remove it. So the code is rendered under a guard: the
    run continues, and the report names the type instead of the value.
    """
    result, summary = _ending_run(tmp, EVIL_REPR_EXIT)
    output = result.stdout + result.stderr
    assert _reported(result.stdout) == [
        ('FAIL', 'test_a_the_subject_ends_the_run'),
        ('FAIL', 'test_b_a_plain_failure'),
        ('PASS', 'test_c_the_last_test_runs'),
    ], output
    assert 'EvilRepr' in _line_for(
        result.stdout, 'test_a_the_subject_ends_the_run'), output
    assert '\n1/3 passed\n' in result.stdout, output
    with open(summary, encoding='utf-8') as handle:
        counts = json.load(handle)
    assert counts == {'total': 3, 'passed': 1, 'skipped': 0,
                      'failed': 2, 'requires': None}, counts


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
