#!/usr/bin/env python3
"""Suite for daedalus_cli — what the result printer shows the operator.

The channel labels, the marker a console that cannot encode them gets,
and the entry point's stdio rule for an explicit PYTHONIOENCODING. The
CLI is always run as a subprocess, the way a shell would run it.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_helpers import BRIDGE_ENV, CLI, TOK, cli_env  # noqa: E402


def run_python(code, env, timeout=60):
    return subprocess.run([sys.executable, '-c', code], cwd=str(_util.ROOT),
                          env=env, capture_output=True, text=True,
                          encoding='utf-8', timeout=timeout)


def test_result_printer_labels_eval_world_as_a_channel(tmp):
    del tmp
    code = (
        'from daedalus_cli.output import print_result\n'
        'base = {"id":"channel", "result":4, "error":None, "ts":1, '
        '"token":"tok"}\n'
        'print_result({**base, "world":"cdp"})\n'
        'print_result({**base, "world":"page-main"})\n'
        'print_result({**base, "world":"page:example.com"})\n'
        'print_result({**base, "world":"module-main"})\n')
    result = run_python(code, cli_env())
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    assert '@channel=cdp' in result.stdout, result.stdout
    assert '@channel=page-main' in result.stdout, result.stdout
    assert '@channel=page:example.com' in result.stdout, result.stdout
    assert '@channel=module-main' in result.stdout, result.stdout
    assert '[privileged]' not in result.stdout, result.stdout
    assert '[untrusted]' not in result.stdout, result.stdout


def test_the_printer_survives_a_console_that_cannot_encode_it(tmp):
    """A legacy code page degrades the output; it never aborts the command.

    Windows consoles default to one — cp1252 on the hosted runners — where
    `print` raises UnicodeEncodeError rather than degrading, and the arrow in
    the result header used to abort `daedalus result` with a traceback and no
    output at all. Two different things can be unencodable: the markers this
    module chooses, which fall back to ASCII, and caller data such as a tab
    title, which cannot be chosen and is replaced character by character.
    Both are exercised here, because fixing only the markers would leave the
    command still able to die on somebody's page title.
    """
    del tmp
    code = (
        'from daedalus_cli.output import print_result\n'
        'print_result({"id":"job7", "result":"caf\\u00e9 \\u4e16\\u754c", '
        '"error":None, "ts":1, "token":"tok", "world":"cdp"})\n')
    # Parent and child agree on the code page, the way a real console and the
    # process writing to it do; decoding this child as UTF-8 would fail in the
    # test rather than in the code under test.
    result = subprocess.run(
        [sys.executable, '-c', code], cwd=str(_util.ROOT),
        env=cli_env(PYTHONIOENCODING='cp1252'), capture_output=True,
        text=True, encoding='cp1252', timeout=60)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    assert 'UnicodeEncodeError' not in result.stderr, result.stderr
    assert '<- job7' in result.stdout, result.stdout
    assert '@channel=cdp' in result.stdout, result.stdout


def test_the_entry_point_leaves_an_explicit_encoding_alone(tmp):
    """An operator's PYTHONIOENCODING survives the command being run.

    The module's own stdio policy treats an explicit PYTHONIOENCODING as an
    operator decision and leaves it alone, and a test already covers that at
    import. The entry point then reconfigured both streams to UTF-8 a second
    time, unconditionally, so the policy held for anything that imported the
    module and not for anyone who actually ran the command — the bytes a
    caller received were UTF-8 whatever they asked for. Raw bytes, because
    decoding them here with the encoding under test would pass either way.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        _util.post_json(base + '/sync-tabs', {'token': TOK, 'tabs': [
            {'tabId': '11', 'url': 'https://example.com/a',
             'title': 'caf\u00e9'}]})
        result = subprocess.run(
            CLI + ['tabs'], cwd=str(_util.ROOT),
            env=cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK,
                        PYTHONIOENCODING='cp1252'),
            capture_output=True, timeout=60)
        assert result.returncode == 0, (result.returncode, result.stderr)
        assert b'caf\xe9' in result.stdout, result.stdout
        assert b'caf\xc3\xa9' not in result.stdout, result.stdout


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
