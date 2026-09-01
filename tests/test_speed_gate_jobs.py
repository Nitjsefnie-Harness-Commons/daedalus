#!/usr/bin/env python3
"""A pin: no tests.yml job does nothing but wait."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfgraph import _job_names, _tests_yml  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

_WAIT = (re.compile(r'sleep \d+'),
         re.compile(r'Start-Sleep -(?:S|Seconds) \d+'),
         re.compile(r'wait'),
         re.compile(r'timeout \d+ sleep(?: \S+)*'))

# Clusters plus one operand; a second command is work, not a preamble.
_OPTION_LINE = re.compile(r'set (?:-\w+ )*-\w+(?: \S+)?')


def _wait_only(script):
    for line in script.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or _OPTION_LINE.fullmatch(line):
            continue
        if not any(each.fullmatch(line) for each in _WAIT):
            return False
    return True


def test_a_job_whose_only_work_is_waiting_cannot_join_the_workflow(tmp):
    """Every `run:` script must be wait-only; combined `set` lines drop."""
    del tmp
    workflow = _tests_yml()
    waiters = []
    for job in _job_names(workflow):
        runs = [step['run'] for step in
                complete_job_mapping(workflow, job).get('steps') or []
                if 'run' in step]
        if runs and all(_wait_only(script) for script in runs):
            waiters.append(job)
    assert waiters == [], waiters


def test_the_option_line_recognition_drops_only_pure_preambles(tmp):
    del tmp
    for line in ('set -e', 'set -ux', 'set -euo pipefail', 'set -o pipefail',
                 'set -e -u -o pipefail'):
        assert _wait_only(f'{line}\nsleep 300'), line
    assert not _wait_only('set -e; sleep 300')
    assert not _wait_only('set -euo pipefail && sleep 300')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='speedgate_jobs_')


if __name__ == '__main__':
    raise SystemExit(main())
