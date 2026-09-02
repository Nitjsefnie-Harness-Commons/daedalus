#!/usr/bin/env python3
"""The overlap harness's one-retry-on-a-silent-stall pins."""
import contextlib
import io
import subprocess
import sys
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _util  # noqa: E402


def _scripted_stall(platform, communicates, drains):
    """Drive the harness with every attempt's outcome scripted.

    Returns the result, the verdict, the stderr the harness wrote and the
    Popen stub, so a pin asserts on the record the harness actually kept.
    """
    process = mock.Mock(pid=4713, returncode=0, stdout=None, stderr=None)
    process.communicate.side_effect = communicates
    popen = mock.Mock(return_value=process)
    captured = io.StringIO()
    with (
            mock.patch.object(_overlap.shutil, 'which',
                              return_value='node.exe'),
            mock.patch.object(_overlap.subprocess, 'Popen', popen),
            mock.patch.object(_overlap.sys, 'platform', platform),
            mock.patch.object(
                _overlap._drain, 'kill_and_drain', side_effect=drains),
            contextlib.redirect_stderr(captured),
    ):
        result = message = None
        try:
            result = _overlap.run_background_overlap('background', [], [])
        except AssertionError as failure:
            message = str(failure)
    return result, message, captured.getvalue(), popen


def test_windows_silent_stall_recovers_on_second_attempt(tmp):
    """A silent Windows stall is retried once with doubled bounds."""
    del tmp
    result, message, note, popen = _scripted_stall(
        'win32',
        [subprocess.TimeoutExpired('node', 60), ('[]', '')],
        [(False, '', '')])
    assert result == [], result
    assert message is None, message
    assert popen.call_count == 2, popen.call_args_list
    second_argv = popen.call_args_list[1].args[0]
    assert second_argv[-1] == '30000', second_argv
    assert ('overlap harness recovered after outer timeout: '
            'attempt 1 (pid ' in note), note


def test_windows_silent_stall_with_timed_out_drain_declines(tmp):
    """A drain that did not finish declines the retry and is named."""
    del tmp
    result, message, _, popen = _scripted_stall(
        'win32',
        [subprocess.TimeoutExpired('node', 60)],
        [(True, None, None)])
    assert result is None, result
    assert message == (
        "overlap harness outer backstop timed out after 60s; "
        "last step: none recorded; stdout: ''; stderr: ''\n"
        'retry declined: the post-kill drain did not complete '
        '(drain outcome: timed out)'), message
    assert popen.call_count == 1, popen.call_args_list


def test_windows_silent_stall_twice_names_both_attempts(tmp):
    """A second silent stall keeps the first attempt's evidence."""
    del tmp
    result, message, _, popen = _scripted_stall(
        'win32',
        [subprocess.TimeoutExpired('node', 60),
         subprocess.TimeoutExpired('node', 120)],
        [(False, '', ''), (False, '', '')])
    assert result is None, result
    assert 'after 120s' in message, message
    record = 'attempt 1 (pid 4713): last step: none recorded'
    assert record in message, message
    assert 'retry declined' not in message, message
    assert popen.call_count == 2, popen.call_args_list


def test_windows_second_stall_names_the_timed_out_drain(tmp):
    """A last-attempt drain that did not finish is named, not silent."""
    del tmp
    result, message, _, popen = _scripted_stall(
        'win32',
        [subprocess.TimeoutExpired('node', 60),
         subprocess.TimeoutExpired('node', 120)],
        [(False, '', ''), (True, None, None)])
    assert result is None, result
    assert popen.call_count == 2, popen.call_args_list
    assert message.splitlines()[0] == (
        'overlap harness outer backstop timed out after 120s; '
        "last step: none recorded; stdout: ''; stderr: ''"), message
    assert ('retry declined: the post-kill drain did not complete '
            '(drain outcome: timed out)' in message), message
    record = 'attempt 1 (pid 4713): last step: none recorded'
    assert record in message, message
    assert 'drain timed out: no' in message, message


def test_non_windows_silent_stall_recovers_on_second_attempt(tmp):
    """A silent non-Windows stall is retried once with doubled bounds."""
    del tmp
    result, message, note, popen = _scripted_stall(
        'linux',
        [subprocess.TimeoutExpired('node', 60), ('[]', '')],
        [(False, '', '')])
    assert result == [], result
    assert message is None, message
    assert popen.call_count == 2, popen.call_args_list
    second_argv = popen.call_args_list[1].args[0]
    assert second_argv[-1] == '30000', second_argv
    assert ('overlap harness recovered after outer timeout: '
            'attempt 1 (pid ' in note), note


def test_non_windows_declined_retry_is_named_in_the_verdict(tmp):
    """A non-Windows drain that did not finish declines the retry."""
    del tmp
    result, message, _, popen = _scripted_stall(
        'linux', [subprocess.TimeoutExpired('node', 60)],
        [(True, None, None)])
    assert result is None, result
    assert message == (
        "overlap harness outer backstop timed out after 60s; "
        "last step: none recorded; stdout: ''; stderr: ''\n"
        'retry declined: the post-kill drain did not complete '
        '(drain outcome: timed out)'), message
    assert popen.call_count == 1, popen.call_args_list


def test_windows_stall_with_output_does_not_retry(tmp):
    """Recorded output is the diagnosis; a retry would only spend a child."""
    del tmp
    result, message, note, popen = _scripted_stall(
        'win32', [subprocess.TimeoutExpired('node', 60)],
        [(False, 'partial stdout', '[step] something')])
    assert result is None, result
    assert message == (
        'overlap harness outer backstop timed out after 60s; '
        "last step: something; stdout: 'partial stdout'; "
        "stderr: '[step] something'"), message
    assert popen.call_count == 1, popen.call_args_list
    assert note == '', note


def test_a_stall_with_only_stderr_recorded_does_not_retry(tmp):
    """Recorded stderr alone is the diagnosis; a retry would spend a child."""
    del tmp
    result, message, note, popen = _scripted_stall(
        'linux',
        [subprocess.TimeoutExpired('node', 60), ('[]', '')],
        [(False, '', '[step] something')])
    assert result is None, result
    assert message == (
        'overlap harness outer backstop timed out after 60s; '
        "last step: something; stdout: ''; "
        "stderr: '[step] something'"), message
    assert popen.call_count == 1, popen.call_args_list
    assert note == '', note


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='overlapstallretry_')


if __name__ == '__main__':
    raise SystemExit(main())
