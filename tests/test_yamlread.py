#!/usr/bin/env python3
"""The job-scalar reader itself, pinned apart from the policy it serves.

`test_ci_workflows.py` asks whether the speed job's environment is the
right expression; that answer is only as good as the reader that finds
the key. A reader that matched the first `environment:` anywhere in the
file, or one outside `jobs` entirely, would report a gate that is not
there — so the reader gets its own suite, the same way `_workflows.py`
has `test_workflow_parsing.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _yamlread import job_scalar  # noqa: E402


def test_a_decoy_job_cannot_supply_the_speed_environment(tmp):
    """The environment above answers for `speed`, not for whoever is first.

    Read as raw text this was `workflow.partition('    environment:')`, which
    matches the first four-space-indented `environment:` anywhere in the
    file — so hanging the key on a job declared above `speed` and stripping
    it off `speed` left the gate green with forks running unreviewed.
    """
    del tmp
    workflow = ('jobs:\n'
                '  decoy:\n'
                '    environment: benchmark\n'
                '  speed:\n'
                '    runs-on: ubuntu-latest\n')
    assert job_scalar(workflow, 'decoy', 'environment') == 'benchmark'
    assert job_scalar(workflow, 'speed', 'environment') is None


def test_job_scalar_stays_inside_the_jobs_mapping(tmp):
    """A scalar outside `jobs` cannot impersonate the speed job."""
    del tmp
    workflow = (
        'name: speed\n'
        'run-name: |\n'
        '  speed:\n'
        '    environment: >-\n'
        "      ${{ github.event_name == 'pull_request'\n"
        '      && github.event.pull_request.head.repo.full_name != '
        'github.repository\n'
        "      && 'fork-benchmark' || 'benchmark' }}\n"
        'jobs:\n'
        '  speed:\n'
        '    runs-on: ubuntu-latest\n')
    assert job_scalar(workflow, 'speed', 'environment') is None


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
