#!/usr/bin/env python3
"""The fetch-timings -n count, against the real parser and handler.

`-n` says how many of the timing ring buffer's last entries to display, so
a value below 1 has no meaning: the tail slice reads a zero as the whole
buffer and a negative as a longer tail than the one asked for. Every case
drives the real parser the way cli.main does, and the selection cases
dispatch the real handler with the extension call faked to record it.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_parse import refused  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_browser  # noqa: E402
from daedalus_cli.cli import DISPATCH  # noqa: E402
from daedalus_cli.parser import build_parser  # noqa: E402

TIMINGS = (
    {'method': 'GET', 'status': 200, 'bodySize': 1000, 'ms_bodyDecode': 1,
     'ms_fetch': 11, 'ms_encode': 2, 'ms_total': 14,
     'url': 'https://cdn.example.com/first.ts'},
    {'method': 'GET', 'status': 206, 'bodySize': 2000, 'ms_bodyDecode': 2,
     'ms_fetch': 22, 'ms_encode': 3, 'ms_total': 27,
     'url': 'https://cdn.example.com/second.ts'},
    {'method': 'GET', 'status': 200, 'bodySize': 3000, 'ms_bodyDecode': 3,
     'ms_fetch': 33, 'ms_encode': 4, 'ms_total': 40,
     'url': 'https://cdn.example.com/third.ts'},
)
CANNED = {'timings': list(TIMINGS), 'count': len(TIMINGS),
          'hasNativeToBase64': False}


class RecordingExtCmd:
    """Records each ext_cmd call and replays canned answers in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, cmd_id, cmd_type, timeout=10, **fields):
        self.calls.append((cmd_id, cmd_type, fields))
        return self.answers.pop(0)


def run_cli(argv, answers):
    """Parse argv with the real parser, dispatch, return (calls, stdout)."""
    recorded = RecordingExtCmd(answers)
    args = build_parser().parse_args(argv)
    out = io.StringIO()
    original = commands_browser.ext_cmd
    commands_browser.ext_cmd = recorded
    try:
        with contextlib.redirect_stdout(out):
            DISPATCH[args.cmd](args)
    finally:
        commands_browser.ext_cmd = original
    return recorded, out.getvalue()


def test_fetch_timings_refuses_a_count_below_one(tmp):
    del tmp
    for value in ('0', '-1', '-5'):
        code, message = refused(['fetch-timings', '-n', value])
        assert code != 0, (value, code, message)
        assert 'usage:' in message, (value, message)
        assert 'argument -n:' in message, (value, message)
        assert value in message, (value, message)


def test_fetch_timings_still_refuses_a_non_integer_count(tmp):
    del tmp
    code, message = refused(['fetch-timings', '-n', 'soon'])
    assert code != 0, (code, message)
    assert 'argument -n:' in message, message
    assert 'count must be a whole number' in message, message
    assert "'soon'" in message, message


def test_fetch_timings_one_selects_exactly_the_last_entry(tmp):
    del tmp
    recorded, out = run_cli(['fetch-timings', '-n', '1'], [CANNED])
    assert recorded.calls == [
        ('_fetch_timings', 'fetch-timings', {})], recorded.calls
    assert TIMINGS[0]['url'] not in out, out
    assert TIMINGS[1]['url'] not in out, out
    assert TIMINGS[2]['url'] in out, out


def test_fetch_timings_larger_counts_keep_the_selection(tmp):
    del tmp
    cases = (
        (['fetch-timings', '-n', '2'], (False, True, True)),
        (['fetch-timings', '-n', '3'], (True, True, True)),
        (['fetch-timings', '-n', '30'], (True, True, True)),
        (['fetch-timings'], (True, True, True)),
    )
    for argv, shown in cases:
        recorded, out = run_cli(argv, [CANNED])
        assert recorded.calls == [
            ('_fetch_timings', 'fetch-timings', {})], (argv, recorded.calls)
        for entry, visible in zip(TIMINGS, shown):
            present = entry['url'] in out
            assert present is visible, (argv, entry['url'], out)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
