#!/usr/bin/env python3
"""Which jobs may reach the Actions cache, as one set rather than a list.

The per-job pins in test_ci_workflows.py each look at the job they pin, so
none of them can see a cache step appear on a job none of them names. This
suite holds the boundary: the exact set of jobs allowed to hold a step that
reaches the Actions cache. A step does that two ways, and the rule below is
those two mechanisms rather than a roster of the action names in use today —
by using a member of the actions/cache family, or by passing a `cache:`
input to a setup action. The set is recorded in full, so a new carrier is a
red test rather than a silent widening.
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


# The two names each mechanism goes by, matched with the delimiter that ends
# them so a near miss is not swallowed: the actions/cache family, combined
# (`actions/cache@`, whose save runs as a post-job step) or split
# (`actions/cache/<sub-action>@`), and the setup-action family, whose members
# are named `<owner>/setup-<tool>` and share the `cache:` input.
_CACHE_ACTIONS = ('actions/cache@', 'actions/cache/')
_SETUP_ACTION = '/setup-'


def _reaches_the_actions_cache(step):
    """Whether one step reaches the Actions cache, by either mechanism.

    Case-insensitively: an action name is not a case-sensitive identifier,
    and neither mechanism becomes something else when respelled.
    """
    uses = step.get('uses', '').casefold()
    if uses.startswith(_CACHE_ACTIONS):
        return True
    return _SETUP_ACTION in uses and 'cache' in step.get('with', {})


def _cache_write_jobs(workflow):
    """Jobs holding at least one step that reaches the Actions cache.

    A job declaring no steps of its own holds none: a `uses:` job's caching
    belongs to the workflow it calls.
    """
    carriers = set()
    for job in _job_names(workflow):
        steps = complete_job_mapping(workflow, job).get('steps', [])
        if any(_reaches_the_actions_cache(step) for step in steps):
            carriers.add(job)
    return carriers


def test_only_the_recorded_jobs_carry_a_cache_write_path(tmp):
    """The set of jobs that reach the Actions cache is exactly this six.

    A pin that iterates named jobs cannot see a cache step appear on a job
    it does not name, so this one asserts on the whole set: it goes red when
    a step reaching the cache by either mechanism appears on a job nobody
    recorded, and when a recorded job's own cache path disappears.
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


def test_a_cache_input_on_any_setup_action_is_a_cache_path(tmp):
    """An unseen setup action carrying `cache:` is a cache write path.

    The rule is the mechanism, not the roster: `cache:` is the setup-action
    family's shared toolchain-cache input, so a setup action nobody has
    written here yet is a carrier the first time one is added. The name is
    matched case-insensitively, and the input is required — a setup action
    without it reaches no cache. tests.yml holds no such action to plant on.
    """
    del tmp
    workflow = ('jobs:\n'
                '  unseen-setup:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/setup-go@v6\n'
                '        with:\n'
                '          cache: true\n'
                '  mixed-case:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: Actions/Cache@v4\n'
                '  quiet:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/setup-go@v6\n')
    assert _cache_write_jobs(workflow) == {'unseen-setup', 'mixed-case'}


def test_a_near_miss_action_name_reaches_no_cache(tmp):
    """Each mechanism's name is matched only up to its own delimiter.

    Drop the delimiter from either and the match widens to names that are
    not that mechanism at all — `actions/cache-warmer` for the family,
    anything called `setupfoo` for the setup limb. tests.yml contains no
    such near miss, so the recorded-set assertion cannot see the loosening
    and this fixture is what fails on it.
    """
    del tmp
    workflow = ('jobs:\n'
                '  near-cache:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/cache-warmer@v1\n'
                '  near-setup:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/setupfoo@v1\n'
                '        with:\n'
                '          cache: pip\n'
                '  real:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/cache/save@v4\n')
    assert _cache_write_jobs(workflow) == {'real'}


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cacheboundary_'))
