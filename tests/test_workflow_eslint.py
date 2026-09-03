#!/usr/bin/env python3
"""The eslint job's pins, install shape, currency gate, and flat config.

There is deliberately no package.json: the versions are pinned in the
workflow and installed with --no-save. Nothing else keeps the three pins on
one install command, the currency gate watching every pin, and the flat
config carrying the options whose flip silently changes the verdict surface.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _job_section, _tests_yml  # noqa: E402
from _yamlread import job_mapping, step_scalar  # noqa: E402

ROOT = _util.ROOT
JOB = 'eslint'
INSTALL_NAME = 'Install eslint (pinned, no package.json, no build step)'
CURRENCY_NAME = "Check the pins against the registries' latest majors"
LINT_NAME = 'eslint'
# Each pin's env var and the package it installs, in install order.
PINS = (
    ('ESLINT_VERSION', 'eslint'),
    ('ESLINT_JS_VERSION', '@eslint/js'),
    ('GLOBALS_VERSION', 'globals'),
)
EXACT_VERSION = re.compile(r'\d+\.\d+\.\d+\Z')


def _run(name):
    """Return one named step's run block, read by the bounded reader."""
    run = step_scalar(_tests_yml(), JOB, name, 'run')
    assert run is not None, f'{JOB}: no step named {name!r}'
    return run


def _positions(workflow):
    """Map each step name to its line position in the job's own section."""
    section = _job_section(workflow, JOB)
    found = {}
    for offset, line in enumerate(section):
        match = re.match(r'      - name: (.+)$', line)
        if match:
            found[match.group(1)] = offset
    return found


def test_the_job_pins_three_exact_versions(tmp):
    """One exact x.y.z pin per package: no range, no latest, none missing."""
    del tmp
    env = job_mapping(_tests_yml(), JOB, 'env')
    assert sorted(env) == sorted(var for var, _ in PINS), sorted(env)
    for var, package in PINS:
        assert EXACT_VERSION.fullmatch(env[var]), (var, package, env[var])


def test_one_install_command_carries_every_pin(tmp):
    """A second --no-save install prunes the first one's packages, so the
    three specs have to ride the one command."""
    del tmp
    section = _job_section(_tests_yml(), JOB)
    installs = [
        line for line in section if 'npm install' in line
        and 'npx --no-install' not in line
    ]
    assert len(installs) == 1, installs
    run = _run(INSTALL_NAME)
    for var, package in PINS:
        assert f'"{package}@${{{var}}}"' in run, (package, var, run)
    assert '--no-save' in run, run
    assert '--no-package-lock' in run, run


def test_the_currency_gate_watches_every_pin(tmp):
    """A pin that is never compared is a pin that rots: every env var and
    package pair is in the gate's table, the table drives the query, and
    the query reads the pin it compares."""
    del tmp
    run = _run(CURRENCY_NAME)
    for var, package in PINS:
        assert f'"{var}:{package}"' in run, (package, var, run)
    assert 'npm view "${package}" version' in run, run
    assert 'pinned="${!var}"' in run, run


def test_the_gate_fails_closed_when_a_pin_drifts(tmp):
    """A renamed or deleted env pin resolves empty, and an empty pin makes
    the integer comparison error inside its own if -- so the gate has to
    check for that itself, name the variable, and fail the job."""
    del tmp
    run = _run(CURRENCY_NAME)
    assert 'if [ -z "${pinned}" ]' in run, run
    assert 'echo "${var} is unset or empty' in run, run


def test_the_currency_gate_sits_between_install_and_lint(tmp):
    del tmp
    workflow = _tests_yml()
    found = _positions(workflow)
    for name in (INSTALL_NAME, CURRENCY_NAME, LINT_NAME):
        assert name in found, name
    order = (found[INSTALL_NAME], found[CURRENCY_NAME], found[LINT_NAME])
    assert order == tuple(sorted(order)), order
    assert 'npm install' not in _run(LINT_NAME), 'the lint step installs'


def test_the_lint_step_lints_the_tracked_javascript(tmp):
    del tmp
    run = _run(LINT_NAME)
    assert "git ls-files '*.js' ':!:examples/*'" in run, run
    assert 'npx --no-install eslint "${sources[@]}"' in run, run


def test_the_flat_config_carries_the_carried_rule_options(tmp):
    """The four customizations, and caughtErrors pinned where eslint 9+ no
    longer defaults to what the previous config inherited."""
    del tmp
    config = (ROOT / 'eslint.config.js').read_text(encoding='utf-8')
    expected = (
        "'no-unused-vars': ['error', {",
        "args: 'none',",
        "caughtErrors: 'none',",
        "varsIgnorePattern: '^_',",
        "'no-empty': ['error', { allowEmptyCatch: true }],",
        "'no-console': 'off',",
        "'no-constant-condition': ['error', { checkLoops: false }],",
    )
    missing = [line for line in expected if line not in config]
    assert not missing, missing


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
