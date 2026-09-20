#!/usr/bin/env python3
"""The net-capture --max and -t/--timeout types, through the real parser.

A type that stops refusing fails here rather than by the comment above
its raise.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_parse import accepted, refused  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli.transport import NET_CAPTURE_MAX  # noqa: E402


def test_net_capture_refuses_a_max_outside_one_to_the_ceiling(tmp):
    del tmp
    for value in ('0', '-1', str(NET_CAPTURE_MAX + 1)):
        code, message = refused(['net-capture', '--max', value])
        assert code != 0, (value, code, message)
        assert 'usage:' in message, (value, message)
        assert 'argument --max:' in message, (value, message)
        assert f'got {int(value)}' in message, (value, message)
    for value in ('1', str(NET_CAPTURE_MAX)):
        args = accepted(['net-capture', '--max', value])
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
