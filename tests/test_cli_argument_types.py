#!/usr/bin/env python3
"""The net-capture --max and the -t/--timeout types, against the real parser.

Each case drives `build_parser` the way cli.main does and reads the refusal
argparse writes, so a type that stops refusing is caught here rather than
by the comment above its raise.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli.parser import build_parser  # noqa: E402
from daedalus_cli.transport import NET_CAPTURE_MAX  # noqa: E402


def refused(argv):
    """(exit code, stderr): argparse refused argv at parse time."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            build_parser().parse_args(argv)
    except SystemExit as exit_request:
        return exit_request.code, err.getvalue()
    raise AssertionError(f'{argv} parsed instead of being refused')


def test_net_capture_refuses_a_max_outside_one_to_the_ceiling(tmp):
    del tmp
    for value in ('0', '-1', str(NET_CAPTURE_MAX + 1)):
        code, message = refused(['net-capture', '--max', value])
        assert code != 0, (value, code, message)
        assert 'usage:' in message, (value, message)
        assert 'argument --max:' in message, (value, message)
        assert value in message, (value, message)
    for value in ('1', str(NET_CAPTURE_MAX)):
        args = build_parser().parse_args(['net-capture', '--max', value])
        assert args.max == int(value), (value, args.max)


def test_net_capture_still_refuses_a_non_integer_max(tmp):
    del tmp
    code, message = refused(['net-capture', '--max', 'ten'])
    assert code != 0, (code, message)
    assert 'argument --max:' in message, message
    assert '--max must be an integer from 1 to' in message, message
    assert "'ten'" in message, message


def test_a_non_integer_timeout_is_refused(tmp):
    del tmp
    for value in ('1.5', 'soon'):
        code, message = refused(['screenshot', '--timeout', value])
        assert code != 0, (value, code, message)
        assert 'argument -t/--timeout:' in message, (value, message)
        assert 'timeout must be a whole number of seconds' in message, (
            value, message)
        assert repr(value) in message, (value, message)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
