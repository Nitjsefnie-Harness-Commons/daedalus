#!/usr/bin/env python3
"""Execute the tests workflow's pip-cache invariants that GitHub
otherwise fails silently."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pip_cache import _CACHE_JOBS  # noqa: E402
from _wfgraph import _tests_yml  # noqa: E402
from _ghexpr import evaluate_if  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402


def _cache_step(steps, action):
    matches = [(index, step) for index, step in enumerate(steps)
               if step.get('uses', '').startswith(f'actions/cache/{action}@')]
    assert len(matches) == 1, f'expected one cache/{action} step: {matches}'
    return matches[0]


def _named_step_index(steps, name):
    matches = [index for index, step in enumerate(steps)
               if step.get('name') == name]
    assert len(matches) == 1, f'expected one {name!r} step: {matches}'
    return matches[0]


def test_the_cached_jobs_declare_no_pip_cache_on_setup_python(tmp):
    """`cache: pip` saves in a post-job step that runs after untrusted code.

    That post step is the cache-poisoning shape issue #166 recorded: a
    pull_request run would write what a later main run restores.
    """
    del tmp
    workflow = _tests_yml()
    for job, _python, _save_after, _install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        setup = [step for step in steps
                 if step.get('uses', '').startswith('actions/setup-python')]
        assert len(setup) == 1, (job, setup)
        declared = {'cache', 'cache-dependency-path'} & set(
            setup[0].get('with', {}))
        assert declared == set(), (job, sorted(declared))


def test_the_cached_jobs_restore_the_pip_cache_before_they_install(tmp):
    """Restore is safe on every event: a pull request reads what main wrote.

    A prefix fallback can reuse an older dependency set's fetched packages,
    so the restore must precede the install and carry no `if` gate. The
    action's contract inputs (path, key, restore-keys) are deliberately
    unread — fleet-rules "Merging and CI" with:-inputs ruling: a legitimate
    reconfiguration of the cache layout must pass.
    """
    del tmp
    workflow = _tests_yml()
    for job, _python, _save_after, install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        restore_index, restore = _cache_step(steps, 'restore')
        assert 'if' not in restore, (job, restore.get('if'))
        assert restore_index < _named_step_index(steps, install), (
            job, restore_index, install)


def test_the_cached_jobs_save_the_pip_cache_only_from_a_push_of_main(tmp):
    """Only main pushes may save: the repository produced their checkout.

    Evaluate the gate: substring matches alone miss `||` or a widened ref.
    """
    del tmp
    workflow = _tests_yml()
    for job, _python, save_after, _install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        restore_index, restore = _cache_step(steps, 'restore')
        save_index, save = _cache_step(steps, 'save')
        assert save['with']['key'] == restore['with']['key'], (job, save)
        assert save['with']['path'] == restore['with']['path'], (job, save)
        gate = save.get('if')
        assert gate is not None, (job, save)
        for conjunct in ('!cancelled()',
                         "github.event_name == 'push'",
                         "github.ref == 'refs/heads/main'"):
            assert conjunct in gate, (job, conjunct, gate)
        for github, cancelled, expected in (
                ({'event_name': 'push', 'ref': 'refs/heads/main'},
                 False, True),
                ({'event_name': 'push', 'ref': 'refs/heads/main'},
                 True, False),
                ({'event_name': 'push', 'ref': 'refs/heads/other'},
                 False, False),
                ({'event_name': 'pull_request',
                  'ref': 'refs/pull/185/merge'}, False, False),
                ({'event_name': 'workflow_dispatch',
                  'ref': 'refs/heads/main'}, False, False),
        ):
            verdict = evaluate_if(gate, {
                'github': github,
                'status': {'success': True, 'failure': False,
                           'cancelled': cancelled},
            })
            assert verdict is expected, (job, github, cancelled, gate, verdict)
        assert save_index > _named_step_index(steps, save_after), (
            job, save_index, save_after)
        assert save_index > restore_index, (job, save_index, restore_index)


def test_the_cached_jobs_pin_one_cache_release(tmp):
    del tmp
    workflow = _tests_yml()
    shas = set()
    for job, _python, _save_after, _install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        for action in ('restore', 'save'):
            _index, step = _cache_step(steps, action)
            shas.add(step['uses'].split('@')[1])
    assert len(shas) == 1, f'distinct actions/cache SHAs: {sorted(shas)}'


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cipipcache_')


if __name__ == '__main__':
    raise SystemExit(main())
