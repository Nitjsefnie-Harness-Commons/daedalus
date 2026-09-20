#!/usr/bin/env python3
"""Execute the tests workflow's pip-cache invariants that GitHub
otherwise fails silently.

These tests parse the tests workflow's pip cache steps: what the six
cached jobs restore and save, gated on which events, and pinned to one
reviewed actions/cache release across the workflow, with comments naming
its tag.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfgraph import _job_section, _tests_yml  # noqa: E402
from _ghexpr import evaluate_if  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from test_workflow_cache_boundary import REVIEWED_CACHE_RELEASES  # noqa: E402


_CACHE_JOBS = (
    # (job, the key's python component, the step a save must follow, the
    # step the restore must precede). Rows carrying a literal 3.13 fix it.
    ('suites', '${{ matrix.python }}', 'Run every suite',
     'Install the test dependencies and project'),
    ('coverage-matrix', '${{ matrix.python }}', 'Measure',
     'Install the coverage toolchain and the project'),
    ('coverage', '3.13', 'Install the coverage toolchain and the project',
     'Install the coverage toolchain and the project'),
    ('pycodestyle', '3.13', 'pycodestyle', 'Install linters'),
    ('pylint', '3.13', 'pylint', 'Install linters and import dependencies'),
    ('pyright', '3.13', 'pyright',
     'Install the type checker and the dependencies it resolves'),
)

# Every platform pip cache directory, so one spelling warms all three OSes;
# actions/cache ignores the two that do not exist on the running platform.
_PIP_CACHE_PATHS = (
    '~/.cache/pip', '~/Library/Caches/pip', '~\\AppData\\Local\\pip\\Cache')


def _cache_step(steps, action):
    """Return (index, step) for the job's one actions/cache/<action> step."""
    matches = [(index, step) for index, step in enumerate(steps)
               if step.get('uses', '').startswith(f'actions/cache/{action}@')]
    assert len(matches) == 1, f'expected one cache/{action} step: {matches}'
    return matches[0]


def _uses_version_comment(workflow, job, action):
    """The version comment on the job's one cache step's `uses:` line."""
    lines = [line.strip() for line in _job_section(workflow, job)
             if f'actions/cache/{action}@' in line]
    assert len(lines) == 1, (job, action, lines)
    _uses, _marker, comment = lines[0].partition('# ')
    return comment.strip()


def _named_step_index(steps, name):
    """Return the index of the job's one step carrying this name."""
    matches = [index for index, step in enumerate(steps)
               if step.get('name') == name]
    assert len(matches) == 1, f'expected one {name!r} step: {matches}'
    return matches[0]


def test_the_cached_jobs_declare_no_pip_cache_on_setup_python(tmp):
    """`cache: pip` saves in a post-job step that runs after untrusted code.

    That post step is the cache-poisoning shape issue #166 took out of the
    speed cells: a pull_request run would write what a later main run
    restores. These six jobs take the cache as two steps instead.
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

    The key names the platform, the interpreter and the requirements hash.
    An exact match restores what a same-platform run of the same
    dependency set wrote; missing it, the restore-keys prefix falls back
    to the newest cache sharing the prefix, possibly an older set's.
    Where the interpreter is 3.13 that is six jobs on one namespace
    rather than one apiece — the cache holds fetched packages any such
    install reuses. Key and restore-keys prefix are pinned exactly:
    neither a deleted fallback nor a renamed prefix can silently
    fragment it. Gating a restore cannot make it safer — a gate there
    would be the save gate wearing the wrong step's name.
    """
    del tmp
    workflow = _tests_yml()
    for job, python, _save_after, install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        restore_index, restore = _cache_step(steps, 'restore')
        assert re.fullmatch(r'actions/cache/restore@[0-9a-f]{40}',
                            restore['uses']), restore['uses']
        comment = _uses_version_comment(workflow, job, 'restore')
        sha = restore['uses'].split('@')[1]
        assert sha in REVIEWED_CACHE_RELEASES, (job, 'restore', sha)
        expected_tag = REVIEWED_CACHE_RELEASES[sha]
        assert comment == expected_tag, (job, comment, expected_tag)
        assert 'if' not in restore, (job, restore.get('if'))
        assert set(restore['with']['path'].splitlines()) == set(
            _PIP_CACHE_PATHS), (job, restore['with']['path'])
        expected = 'pip-${{ runner.os }}-' + python
        key = restore['with']['key']
        assert key == expected + "-${{ hashFiles('requirements-*.txt') }}", (
            job, key)
        assert restore['with'].get('restore-keys') == expected + '-\n', (
            job, restore['with'].get('restore-keys'))
        assert restore_index < _named_step_index(steps, install), (
            job, restore_index, install)


def test_the_cached_jobs_save_the_pip_cache_only_from_a_push_of_main(tmp):
    """The event gate between restore and save is the whole answer.

    A pull_request or workflow_dispatch run, or a push that is not main,
    restores and never writes; only a push of main, whose checkout the
    repository itself produced, may. Evaluated as Actions would evaluate it
    rather than substring-matched, so an `||` or a widened ref reads as the
    defect it is, and a cancelled run writes nothing either way. The save
    follows the per-row recorded step: suites, measurement or lint — but
    the install itself for `coverage`.
    """
    del tmp
    workflow = _tests_yml()
    for job, _python, save_after, _install in _CACHE_JOBS:
        steps = complete_job_mapping(workflow, job)['steps']
        restore_index, restore = _cache_step(steps, 'restore')
        save_index, save = _cache_step(steps, 'save')
        assert re.fullmatch(r'actions/cache/save@[0-9a-f]{40}',
                            save['uses']), save['uses']
        comment = _uses_version_comment(workflow, job, 'save')
        sha = save['uses'].split('@')[1]
        assert sha in REVIEWED_CACHE_RELEASES, (job, 'save', sha)
        expected_tag = REVIEWED_CACHE_RELEASES[sha]
        assert comment == expected_tag, (job, comment, expected_tag)
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
