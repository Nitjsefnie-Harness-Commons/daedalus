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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
