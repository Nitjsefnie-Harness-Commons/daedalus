#!/usr/bin/env python3
"""The screenshot -q/--quality value, against the real parser and handler.

`-q` is a JPEG quality, documented 1-100, but the bare int conversion
admitted anything: 0 fell through the worker's `quality || 80` to the
default, and values past 100 reached Chrome's capture call unvalidated.
Every case drives the real parser the way cli.main does, and the accepted
cases dispatch the real handler with the bridge call faked to record the
payload it sends.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_parse import refused  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_media  # noqa: E402
from daedalus_cli.cli import DISPATCH  # noqa: E402
from daedalus_cli.parser import build_parser  # noqa: E402


class RecordingApi:
    """Records each api call and replays canned answers in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, path, body=None, timeout=30, headers=None):
        del timeout, headers
        self.calls.append((method, path, body))
        return self.answers.pop(0)


def run_cli(argv, answers):
    """Parse argv with the real parser, dispatch, return (calls, stdout)."""
    recorded = RecordingApi(answers)
    args = build_parser().parse_args(argv)
    out = io.StringIO()
    original = (commands_media.api, commands_media.token,
                commands_media.wait_for_result)
    commands_media.api = recorded
    commands_media.token = lambda: 'tok'
    commands_media.wait_for_result = lambda *a, **k: {
        'result': {'path': 'tok/x/t.png', 'size': 3}}
    try:
        with contextlib.redirect_stdout(out):
            DISPATCH[args.cmd](args)
    finally:
        commands_media.api, commands_media.token = original[0], original[1]
        commands_media.wait_for_result = original[2]
    return recorded, out.getvalue()


def put_quality(recorded):
    """The quality field of the one command the handler sent."""
    method, path, body = recorded.calls[0]
    assert (method, path) == ('PUT', '/command'), recorded.calls
    return body.get('quality')


def test_screenshot_refuses_a_quality_outside_one_to_hundred(tmp):
    del tmp
    for value in ('0', '101', '-5', '500'):
        code, message = refused(['screenshot', '-q', value])
        assert code != 0, (value, code, message)
        assert 'usage:' in message, (value, message)
        assert 'argument -q/--quality:' in message, (value, message)
        assert value in message, (value, message)


def test_screenshot_still_refuses_a_non_integer_quality(tmp):
    del tmp
    code, message = refused(['screenshot', '-q', 'soon'])
    assert code != 0, (code, message)
    assert 'argument -q/--quality:' in message, message
    assert 'quality must be a whole number' in message, message
    assert "'soon'" in message, message


def test_screenshot_accepts_the_documented_range_ends(tmp):
    del tmp
    for value in ('1', '100'):
        recorded, out = run_cli(
            ['screenshot', '-q', value],
            [{'ok': True, 'did': 'd1', 'target': 'tab=extension'}])
        assert out != '', out
        assert put_quality(recorded) == int(value), recorded.calls


def test_screenshot_without_q_sends_no_quality(tmp):
    del tmp
    recorded, out = run_cli(
        ['screenshot'],
        [{'ok': True, 'did': 'd1', 'target': 'tab=extension'}])
    assert out != '', out
    method, path, body = recorded.calls[0]
    assert (method, path) == ('PUT', '/command'), recorded.calls
    assert 'quality' not in body, recorded.calls


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
