#!/usr/bin/env python3
"""A pin: no tests.yml job does nothing but wait.

Refused: a job whose every ``run:`` script is nothing but a wait — ``sleep``
or ``Start-Sleep`` with one or more duration operands (digits with an
optional s/m/h/d suffix), a bare ``wait``, or ``timeout <duration>
sleep <duration>`` — behind blank lines, comments, trailing comments and
set-option preambles. Issue 461 closed the waiting-job shape; this pin keeps
it closed by test. The polling half (waiting on check runs instead of the
``needs:`` dependency) is pinned by the check-runs ban in
test_the_speed_gate_depends_on_the_exact_aggregate_job. Out of scope, by
design: poll loops, unrecognized commands, and jobs with no ``run:`` steps.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfgraph import _job_names, _tests_yml  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

# A duration is one or more digit groups, each with an optional unit.
# Linear: (\d+[smhd]?)+ backtracks catastrophically on a failing match.
_DURATION = r'\d+(?:[smhd]\d*)*'

_WAIT = (re.compile(rf'sleep(?: {_DURATION})+'),
         re.compile(rf'Start-Sleep -(?:S|Seconds)(?: {_DURATION})+'),
         re.compile(r'wait'),
         re.compile(rf'timeout {_DURATION} sleep {_DURATION}'))

# Clusters plus one operand; a second command is work, not a preamble.
_OPTION_LINE = re.compile(r'set (?:-\w+ )*-\w+(?: \S+)?')


def _wait_only(script):
    for raw in script.splitlines():
        # bash starts a comment only at a word-initial '#'.
        line = re.sub(r'(^|\s)#.*$', '', raw).strip()
        if not line or _OPTION_LINE.fullmatch(line):
            continue
        if not any(each.fullmatch(line) for each in _WAIT):
            return False
    return True


def wait_only_jobs(workflow):
    """Return the jobs whose every ``run:`` script is nothing but a wait."""
    waiters = []
    for job in _job_names(workflow):
        runs = [step['run'] for step in
                complete_job_mapping(workflow, job).get('steps') or []
                if 'run' in step]
        if runs and all(_wait_only(script) for script in runs):
            waiters.append(job)
    return waiters


def test_a_job_whose_only_work_is_waiting_cannot_join_the_workflow(tmp):
    """Every real job's ``run:`` scripts must not all be pure waits."""
    del tmp
    assert wait_only_jobs(_tests_yml()) == [], wait_only_jobs(_tests_yml())


_SYNTHETIC = """\
jobs:
  compile:
    steps:
      - uses: actions/checkout@v4
  stall:
    steps:
      - run: |
          set -euo pipefail

          # wait for the service
          sleep 300
  mixed:
    steps:
      - run: echo doing-real-work
      - run: sleep 300
"""

_WAIT_SCRIPTS = (
    'sleep 300',
    'sleep 300s',
    'sleep 2m30s',
    'sleep 5m 30s',
    'Start-Sleep -S 60s',
    'Start-Sleep -Seconds 90',
    'wait',
    'timeout 300 sleep 60',
)

_DROPPED_LINES = (
    '',
    '# just a note',
    'set -e',
    'set -ux',
    'set -euo pipefail',
    'set -o pipefail',
    'set -e -u -o pipefail',
    'sleep 300 # wait for the service',
    'sleep 300  # wait for the service',
    'sleep 300   ',
    'set -euo pipefail # strict',
)

_WORK_SCRIPTS = (
    'set -e; sleep 300',
    'set -euo pipefail && sleep 300',
    'echo doing-real-work',
    'sleep',
    'timeout 5 sleep $(touch marker)',
    'timeout 5m 30s sleep 300',
    'sleep 300#touch marker',
    'sleep ' + '0' * 28 + '!',
)


def test_the_pin_refuses_a_synthetic_waiting_job_end_to_end(tmp):
    """The machinery must name the waiting job, not the uses-only job.

    ``mixed`` is the work-plus-wait control pinning the universal
    quantifier: such a job is never classified.
    """
    del tmp
    assert wait_only_jobs(_SYNTHETIC) == ['stall'], wait_only_jobs(_SYNTHETIC)
    # Second refusal over the real tree, so losing either test's own line
    # leaves the other standing.
    clean = wait_only_jobs(_tests_yml())
    assert clean == [], clean


def test_wait_scripts_are_recognized(tmp):
    """One committed case per wait family."""
    del tmp
    for script in _WAIT_SCRIPTS:
        assert _wait_only(script), script


def test_dropped_lines_are_skipped(tmp):
    """Blank, comment-only, set-option and trailing-comment lines skip."""
    del tmp
    for script in _DROPPED_LINES:
        assert _wait_only(script), repr(script)


def test_work_scripts_are_refused(tmp):
    """Anything unrecognized, or a second command, is work."""
    del tmp
    for script in _WORK_SCRIPTS:
        assert not _wait_only(script), script


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='speedgate_jobs_')


if __name__ == '__main__':
    raise SystemExit(main())
