#!/usr/bin/env python3
"""The jobs recognised as reaching the Actions cache, as one set.

The per-job pins in test_ci_workflows.py each look at the job they pin, so
none of them can see a cache step appear on a job none of them names. This
suite holds that boundary as a set, over the two spellings a `uses:`/`with:`
reading can recognise: a member of the actions/cache family, and a
`/setup-` action carrying a `cache` input. Those are mechanisms rather than
a roster of the action names in use today, and the set is recorded in full,
so either spelling arriving on an unrecorded job is a red test rather than
a silent widening.

Three kinds of carrier are spelled a way no such reading can recognise,
and this suite stays green on all three: a setup action that caches with no
input at all (actions/setup-go's `cache` and actions/setup-node's
`package-manager-cache` are both `default: true`), a setup action naming
its cache input something else (astral-sh/setup-uv's `enable-cache`), and a
cache action outside the actions/cache family (Swatinem/rust-cache, or a
buildx `cache-to: type=gha`). Each needs per-action knowledge this module
does not hold.
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
# recorded known exception. A job outside this set reaches the Actions
# cache by a route nobody recorded.
_CACHE_REACHING_JOBS = frozenset((
    'suites', 'coverage-matrix', 'coverage',
    'pycodestyle', 'pylint', 'pyright',
))


# The two names each mechanism goes by, matched with the delimiters that
# bound them so a near miss is not swallowed: the actions/cache family,
# combined (`actions/cache@`, whose save runs as a post-job step) or split
# (`actions/cache/<sub-action>@`), and the setup-action family, whose
# members are named `<owner>/setup-<tool>` — the leading `/` is an owner
# boundary, so `pnpm/action-setup-x` is not a member.
_CACHE_ACTIONS = ('actions/cache@', 'actions/cache/')
_SETUP_ACTION = '/setup-'


def _reaches_the_actions_cache(step):
    """Whether one step is recognised as reaching the Actions cache.

    Case-insensitively on both names: neither an action name nor an input
    name becomes something else when respelled. The input is read for
    presence, not value, so `cache: false` is recognised too; the family
    does not agree on what the input means, and a false red is visible
    where a false green is not.
    """
    uses = step.get('uses', '').casefold()
    if uses.startswith(_CACHE_ACTIONS):
        return True
    inputs = {name.casefold() for name in step.get('with', {})}
    return _SETUP_ACTION in uses and 'cache' in inputs


def _cache_reaching_jobs(workflow):
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


def test_only_the_recorded_jobs_reach_the_actions_cache(tmp):
    """The recognised carriers are exactly the six recorded jobs.

    A pin that iterates named jobs cannot see a cache step appear on a job
    it does not name, so this one asserts on the whole set: it goes red
    when a recognised step appears on a job nobody recorded, and when a
    recorded job's own recognised step disappears. A carrier spelled a way
    the rule cannot recognise leaves it green — the module docstring lists
    which spellings those are.
    """
    del tmp
    carrying = _cache_reaching_jobs(_tests_yml())
    assert carrying == _CACHE_REACHING_JOBS, (
        'unrecorded path to the Actions cache on '
        f'{sorted(carrying - _CACHE_REACHING_JOBS)}; recorded jobs gone '
        f'quiet: {sorted(_CACHE_REACHING_JOBS - carrying)}')


def test_a_steps_less_job_reaches_no_cache(tmp):
    """A steps-less job reaches nothing, and reading it raises nothing.

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
    assert _cache_reaching_jobs(workflow) == {'local'}


def test_a_cache_input_on_any_setup_action_reaches_the_cache(tmp):
    """A `cache` input on any setup action is recognised as a carrier.

    The rule is the mechanism, not the roster: a setup action nobody has
    written here yet is recognised the first time one arrives carrying that
    input, and both names are matched case-insensitively. The `quiet` job
    is the exemplar for the other side: actions/setup-python gives its
    `cache` input no default, so a step omitting it caches no package
    manager directory, and the rule reads it as no carrier. That agreement
    does not hold for every setup action — see the module docstring.
    tests.yml holds no such action to plant on.
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
                '  mixed-case-input:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/setup-node@v7\n'
                '        with:\n'
                '          Cache: npm\n'
                '  quiet:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/setup-python@v6\n')
    assert _cache_reaching_jobs(workflow) == {
        'unseen-setup', 'mixed-case', 'mixed-case-input'}


def test_a_near_miss_action_name_reaches_no_cache(tmp):
    """Each mechanism's name is matched only up to the delimiters bounding it.

    Drop one and the match widens to names that are not that mechanism at
    all — `actions/cache-warmer` for the family, anything called `setupfoo`
    past the setup limb's trailing `-`, and `pnpm/action-setup-x` past its
    leading owner boundary. tests.yml contains no such near miss, so the
    recorded-set assertion cannot see the loosening and this fixture is
    what fails on it.
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
                '  infix-setup:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: pnpm/action-setup-x@v1\n'
                '        with:\n'
                '          cache: pnpm\n'
                '  real:\n'
                '    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: actions/cache/save@v4\n')
    assert _cache_reaching_jobs(workflow) == {'real'}


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cacheboundary_'))
