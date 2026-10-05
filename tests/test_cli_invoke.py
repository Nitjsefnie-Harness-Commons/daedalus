#!/usr/bin/env python3
"""Suite for daedalus_cli — how the command resolves its configuration.

The version banner, --help, the token and URL the environment hands the
command, and the import that must succeed without an ambient _settings
module. The CLI is always run as a subprocess, the way a shell would run
it, so what is checked is the configuration an operator's environment
actually produces.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_helpers import cli_env, run_cli  # noqa: E402

sys.path.insert(0, str(_util.ROOT))
from daedalus_cli import __version__  # noqa: E402


def run_python(code, env, timeout=60):
    return subprocess.run([sys.executable, '-c', code], cwd=str(_util.ROOT),
                          env=env, capture_output=True, text=True,
                          encoding='utf-8', timeout=timeout)


def test_version_flag(tmp):
    r = run_cli(['--version'], cli_env())
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert r.stdout.strip() == f'daedalus {__version__}', r.stdout


def test_help_exits_zero(tmp):
    r = run_cli(['--help'], cli_env())
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert 'usage' in r.stdout.lower()


def test_missing_token_is_an_error(tmp):
    # No TOKEN and no DAEDALUS_TOKEN: required() must refuse before any HTTP.
    r = run_cli(['tabs'], cli_env(DAEDALUS_URL='http://127.0.0.1:1'))
    assert r.returncode != 0, (r.returncode, r.stdout)
    assert 'DAEDALUS_TOKEN is not set' in r.stderr, r.stderr


def test_url_default_and_override(tmp):
    code = 'from daedalus_cli.transport import URL; print(URL)'
    r = run_python(code, cli_env())
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'http://127.0.0.1:8081', r.stdout
    r = run_python(code, cli_env(DAEDALUS_URL='http://127.0.0.1:9999/x'))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'http://127.0.0.1:9999/x', r.stdout


def test_imports_cleanly_without_settings_module(tmp):
    # A public install has no `_settings` module; the env-var fallback must be
    # the whole configuration path and must import cleanly.
    code = ('import importlib.util, sys\n'
            'if importlib.util.find_spec("_settings") is not None:\n'
            '    sys.exit("ambient _settings present")\n'
            'import daedalus_cli.cli\n'
            'print("clean-import-ok")\n')
    r = run_python(code, cli_env())
    if r.returncode != 0 and 'ambient _settings' in r.stderr:
        _util.skip('an ambient _settings module is installed here')
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert 'clean-import-ok' in r.stdout


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
