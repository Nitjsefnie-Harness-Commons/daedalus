#!/usr/bin/env python3
"""Which jobs may reach the Actions cache, as one set rather than a list.

The per-job pins in test_ci_workflows.py each look at the job they pin, so
none of them can see a cache step appear on a job none of them names. This
suite holds the boundary: the exact set of jobs allowed to carry a
setup-python `cache:` input, or a step using any member of the actions/cache
family — the combined `actions/cache@`, whose save runs as a post-job step,
as well as the split `actions/cache/restore@` and `actions/cache/save@`. The
set is recorded in full, so a new member is a red test rather than a silent
widening.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfgraph import _job_names, _tests_yml  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402


# The jobs allowed to reach the Actions cache at all: the three this
# workflow caches through separate restore and save steps, plus the
# pycodestyle, pylint and pyright jobs whose `cache: pip` is issue #611's
# recorded known exception. A job outside this set is a cache write path
# nobody recorded.
_CACHE_WRITE_JOBS = frozenset((
    'suites', 'coverage-matrix', 'coverage',
    'pycodestyle', 'pylint', 'pyright',
))


# Both spellings of the action, matched by the delimiter that ends the name:
# the combined `actions/cache@`, which restores on the step and saves in a
# post-job step, and the split `actions/cache/save@` and
# `actions/cache/restore@`. A prefix over the family rather than a set of
# the three names known today, so a further sub-action is matched as well.
_CACHE_ACTIONS = ('actions/cache@', 'actions/cache/')


def _cache_write_jobs(workflow):
    """Jobs carrying a setup-python `cache:` input or an actions/cache step.

    A job declaring no steps of its own carries neither: a `uses:` job's
    caching belongs to the workflow it calls.
    """
    carriers = set()
    for job in _job_names(workflow):
        steps = complete_job_mapping(workflow, job).get('steps', [])
        setup = [step for step in steps
                 if step.get('uses', '').startswith('actions/setup-python')]
        if (any('cache' in step.get('with', {}) for step in setup)
                or any(step.get('uses', '').startswith(_CACHE_ACTIONS)
                       for step in steps)):
            carriers.add(job)
    return carriers


def test_only_the_recorded_jobs_carry_a_cache_write_path(tmp):
    """The set of jobs that reach the Actions cache is exactly this six.

    A pin that iterates the three jobs this branch changed cannot see a
    cache step appear on any other job: `cache: pip` planted on the wheel
    job, and a combined `actions/cache@` planted on the timed job, each left
    every one of them green. This is the boundary, so it names the whole
    allowed set rather than iterating it — the three cache jobs plus the
    lint trio whose `cache: pip` is issue #611's recorded known exception —
    and it goes red when either spelling appears on a job nobody recorded,
    or when a recorded job's own cache path disappears.
    """
    del tmp
    carrying = _cache_write_jobs(_tests_yml())
    assert carrying == _CACHE_WRITE_JOBS, (
        'unrecorded cache write path on '
        f'{sorted(carrying - _CACHE_WRITE_JOBS)}; recorded jobs gone quiet: '
        f'{sorted(_CACHE_WRITE_JOBS - carrying)}')


def test_a_steps_less_job_carries_no_cache_path(tmp):
    """A steps-less job carries nothing, and reading it raises nothing.

    A `uses:` job's caching belongs to the workflow it calls. tests.yml
    holds no such job to plant on, so this one reads a synthetic workflow.
    """
    del tmp
    workflow = ('jobs:\n'
                '  called:\n'
                '    uses: ./.github/workflows/other.yml\n'
                '  local:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/cache@v4\n')
    assert _cache_write_jobs(workflow) == {'local'}


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cacheboundary_'))
