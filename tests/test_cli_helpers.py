#!/usr/bin/env python3
"""What the one shared `cli_env` strips and sets.

Every suite that drives the CLI builds the subcommand's environment from the
`cli_env` in tests/_cli_helpers.py, so what it promises is what makes their
results mean anything: the subcommand must not inherit the runner's own
bridge configuration, token or stream encoding, and an override is the
caller's last word. A failure names the name it is about; the environment
itself is not one to print.
"""
import contextlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_helpers import cli_env  # noqa: E402

# The names a subcommand must not read from us: the DAEDALUS_ configuration
# vars, the bare TOKEN the CLI resolves as a fallback, and the stream encoding
# it applies its own rule to. The first group is a sample, not the rule — the
# rule is the prefix, and the last case below is what pins it.
BRIDGE_NAMES = ('DAEDALUS_TOKEN', 'DAEDALUS_URL', 'DAEDALUS_MCP_PORT')
RUNNER_NAMES = ('TOKEN', 'ID', 'PYTHONIOENCODING')
# A real CLI var the sample never names: a helper that filtered these three
# by name instead of by prefix is caught here rather than passing every case.
OFF_LIST_NAME = 'DAEDALUS_DIR'
NEUTRAL = 'CLIHELPER_SUITE_NEUTRAL'


@contextlib.contextmanager
def _runner_environment():
    """Stand in for the runner that launches the suite.

    A name this process already carries is captured and put back, so the pin
    reads the same on a developer machine and on a bare CI leg.
    """
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
        env = cli_env(DAEDALUS_TOKEN='clitok', TOKEN='clitok',
                      PYTHONIOENCODING='utf-8')
    applied = {name: env.get(name) for name in
               ('DAEDALUS_TOKEN', 'TOKEN', 'PYTHONIOENCODING', 'ID')}
    assert applied == {'DAEDALUS_TOKEN': 'clitok', 'TOKEN': 'clitok',
                       'PYTHONIOENCODING': 'utf-8', 'ID': None}, applied


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='clihelpers_'))
