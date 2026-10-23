"""What the one shared `cli_env` strips and sets."""
import contextlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_helpers import cli_env  # noqa: E402

BRIDGE_NAMES = ('DAEDALUS_TOKEN', 'DAEDALUS_URL', 'DAEDALUS_MCP_PORT',
                'DAEDALUS_TOKEN_OVERRIDE', 'DAEDALUS_ID')
RUNNER_NAMES = ('TOKEN', 'ID', 'PYTHONIOENCODING')
OFF_LIST_NAME = 'DAEDALUS_DIR'
NEUTRAL = 'CLIHELPER_SUITE_NEUTRAL'


@contextlib.contextmanager
def _runner_environment():
    """Stand in for the runner that launches the suite."""
    seeded: dict[str, str] = dict.fromkeys(
        BRIDGE_NAMES + RUNNER_NAMES + (OFF_LIST_NAME, NEUTRAL), 'seeded')
    saved = {name: os.environ[name] for name in seeded if name in os.environ}
    os.environ.update(seeded)
    try:
        yield
    finally:
        for name in seeded:
            os.environ.pop(name, None)
        os.environ.update(saved)


def test_the_cli_environment_carries_no_bridge_configuration_of_our_own(tmp):
    del tmp
    with _runner_environment():
        env = cli_env()
    leaked = [name for name in BRIDGE_NAMES + RUNNER_NAMES if name in env]
    assert leaked == [], leaked


def test_a_bridge_name_the_sample_never_names_is_stripped_too(tmp):
    del tmp
    with _runner_environment():
        env = cli_env()
    assert OFF_LIST_NAME not in env, OFF_LIST_NAME


def test_the_cli_environment_keeps_the_rest_of_our_environment(tmp):
    del tmp
    with _runner_environment():
        env = cli_env()
    assert env.get(NEUTRAL) == 'seeded', NEUTRAL


def test_the_cli_environment_never_writes_bytecode(tmp):
    del tmp
    with _runner_environment():
        env = cli_env()
    assert env.get('PYTHONDONTWRITEBYTECODE') == '1', (
        'PYTHONDONTWRITEBYTECODE', env.get('PYTHONDONTWRITEBYTECODE'))


def test_an_override_is_applied_over_the_stripped_environment(tmp):
    del tmp
    with _runner_environment():
        env = cli_env(DAEDALUS_TOKEN='clitok',
                      DAEDALUS_TOKEN_OVERRIDE='clitok',
                      PYTHONIOENCODING='utf-8')
    applied = {name: env.get(name) for name in
               ('DAEDALUS_TOKEN', 'DAEDALUS_TOKEN_OVERRIDE',
                   'PYTHONIOENCODING', 'DAEDALUS_ID')}
    assert applied == {'DAEDALUS_TOKEN': 'clitok',
                       'DAEDALUS_TOKEN_OVERRIDE': 'clitok',
                       'PYTHONIOENCODING': 'utf-8',
                       'DAEDALUS_ID': None}, applied


def test_prefixed_token_and_tab_precede_legacy_environment(tmp):
    from unittest.mock import patch
    transport = _util.load(_util.ROOT / 'daedalus_cli/transport.py')
    del tmp
    for primary, legacy, want in (('new', 'old', 'new'),
                                  (None, 'old', 'old'),
                                  ('', 'old', 'old'),
                                  ('new', None, 'new'),
                                  (None, None, '')):
        env = {'DAEDALUS_TOKEN': 'durable'}
        for name, value in (('DAEDALUS_TOKEN_OVERRIDE', primary),
                            ('DAEDALUS_ID', primary), ('TOKEN', legacy),
                            ('ID', legacy)):
            if value is not None: env[name] = value
        with patch.dict(os.environ, env, clear=True):
            assert transport.token() == (want or 'durable')
            assert transport.tab() == want


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='clihelpers_'))
