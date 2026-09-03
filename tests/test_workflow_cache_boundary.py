#!/usr/bin/env python3
"""Which jobs may reach the Actions cache, as one set rather than a list.

The per-job pins in test_ci_workflows.py each look at the job they pin, so
none of them can see a cache write path appear on a job none of them names.
This suite holds the boundary: the exact set of jobs allowed to carry a
setup-python `cache:` input or an actions/cache step, recorded in full so a
new one is a red test instead of a silent widening.
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


def _cache_write_jobs(workflow):
    """Jobs carrying a setup-python `cache:` input or a cache action step."""
    carriers = set()
    for job in _job_names(workflow):
        steps = complete_job_mapping(workflow, job)['steps']
        setup = [step for step in steps
                 if step.get('uses', '').startswith('actions/setup-python')]
        if (any('cache' in step.get('with', {}) for step in setup)
                or any(step.get('uses', '').startswith('actions/cache/')
                       for step in steps)):
            carriers.add(job)
    return carriers


def test_only_the_recorded_jobs_carry_a_cache_write_path(tmp):
    """The set of jobs that reach the Actions cache is exactly this six.

    A pin that iterates the three jobs this branch changed cannot see a
    cache write path appear on any other job: `cache: pip` planted on the
    wheel job left every one of them green. This is the boundary, so it
    names the whole allowed set rather than iterating it — the three cache
    jobs plus the lint trio whose `cache: pip` is issue #611's recorded
    known exception — and it goes red only when a write path appears on a
    job nobody recorded.
    """
    del tmp
    carrying = _cache_write_jobs(_tests_yml())
    assert carrying == _CACHE_WRITE_JOBS, (
        'unrecorded cache write path on '
        f'{sorted(carrying - _CACHE_WRITE_JOBS)}; recorded jobs gone quiet: '
        f'{sorted(_CACHE_WRITE_JOBS - carrying)}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cacheboundary_'))
