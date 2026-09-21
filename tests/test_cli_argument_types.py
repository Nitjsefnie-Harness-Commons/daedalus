#!/usr/bin/env python3
"""The parser's value types --max, -t/--timeout, --chrome-tab, --expires and
-p/--params, through the real parser.

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

SET_COOKIE = ['set-cookie', 'https://example.com/', 'sid', 'v']


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


MALFORMED_TAB_SHAPES = (
    (['close-tab', 'x'], 'chrome_tabs'),
    (['focus-tab', 'x'], 'chrome_tab'),
    (['ext-navigate', 'https://example.com/', '--chrome-tab', 'x'],
     '--chrome-tab'),
    (['ext-reload', '--chrome-tab', 'x'], '--chrome-tab'),
    (['inject-css', '--css', 'a{color:red}', '--chrome-tab', 'x'],
     '--chrome-tab'),
    (['remove-css', '--css', 'a{color:red}', '--chrome-tab', 'x'],
     '--chrome-tab'),
    (['block-requests', '*.example.com/*', '--chrome-tab', 'x'],
     '--chrome-tab'),
    (['net-capture', '--chrome-tab', 'x'], '--chrome-tab'),
    (['net-capture-stop', '--chrome-tab', 'x'], '--chrome-tab'),
    (['net-capture-get', '--chrome-tab', 'x'], '--chrome-tab'),
    (['screenshot', '--chrome-tab', 'abc'], '--chrome-tab'),
)


def test_a_malformed_chrome_tab_id_is_refused_at_parse_time(tmp):
    """Every chrome-tab spelling refuses before the handler runs."""
    del tmp
    for argv, argument in MALFORMED_TAB_SHAPES:
        code, message = refused(argv)
        assert code == 2, (argv, code, message)
        assert 'usage:' in message, (argv, message)
        assert (f'argument {argument}: {argv[-1]!r} is not an integer'
                in message), (argv, message)
        assert 'Traceback' not in message, (argv, message)


def test_a_malformed_expires_is_refused_at_parse_time(tmp):
    del tmp
    code, message = refused([*SET_COOKIE, '--expires', 'x'])
    assert code == 2, (code, message)
    assert 'usage:' in message, message
    assert "argument --expires: 'x' is not a number" in message, message
    assert 'Traceback' not in message, message


def test_malformed_params_are_refused_at_parse_time(tmp):
    del tmp
    code, message = refused(['cdp', 'Page.enable', '-p', '{bad'])
    assert code == 2, (code, message)
    assert 'usage:' in message, message
    assert "argument -p/--params: '{bad' is not valid JSON" in message, (
        message)
    assert 'Traceback' not in message, message


def test_well_formed_values_parse_to_their_typed_values(tmp):
    del tmp
    args = accepted(['screenshot', '--chrome-tab', '12'])
    assert args.chrome_tab == 12, args
    args = accepted(['focus-tab', '7'])
    assert args.chrome_tab == 7, args
    args = accepted(['close-tab', '5', '6'])
    assert args.chrome_tabs == [5, 6], args
    args = accepted([*SET_COOKIE, '--expires', '3.5'])
    assert args.expires == 3.5, args
    args = accepted(['cdp', 'Page.enable', '-p', '{"a":1}'])
    assert args.params == {'a': 1}, args
    args = accepted(['cdp', 'Page.enable'])
    assert args.params is None, args
    args = accepted(['cdp', 'Page.enable', '--chrome-tab', '0'])
    assert args.chrome_tab == 0, args


def test_expires_keeps_floats_whole_domain(tmp):
    """The fix moved the refusal to argparse; it did not tighten the domain."""
    del tmp
    args = accepted([*SET_COOKIE, '--expires', 'inf'])
    assert args.expires == float('inf'), args
    args = accepted([*SET_COOKIE, '--expires', 'nan'])
    assert args.expires != args.expires, args
    args = accepted([*SET_COOKIE, '--expires', '-3'])
    assert args.expires == -3.0, args


def test_params_keep_json_loads_whole_domain(tmp):
    """A JSON value that is not an object is still admitted."""
    del tmp
    for text, value in (('[1,2]', [1, 2]), ('"str"', 'str'), ('3', 3),
                        ('null', None)):
        args = accepted(['cdp', 'Page.enable', '-p', text])
        assert args.params == value, (text, args.params)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
