#!/usr/bin/env python3
"""The classifier callers preserve unsupported-pattern refusals.

These controls keep a caller from converting a matcher refusal into a safe
looking answer. Each table combines a supported pattern with an unsupported
one, including both orders so a nonmatching path reaches the refusal.
"""
import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


def _classifier():
    return _util.load(ROOT / 'scripts' / 'ci' / 'classify_changes.py',
                      'classify_callers_mod')


def _event(name='pull_request', repository='octo/daedalus',
           sha='a' * 40, pull_request='248', before='b' * 40):
    return {'name': name, 'repository': repository, 'sha': sha,
            'pull_request': pull_request, 'before': before}


def _recorder(stdout):
    calls = []

    def run(argv):
        calls.append(argv)
        return stdout

    return calls, run


def _assert_value_error(call, message):
    try:
        call()
    except ValueError:
        return
    raise AssertionError(message)


def test_is_documentation_refuses_ordered_unsupported_patterns(tmp):
    del tmp
    mod = _classifier()
    cases = (
        (('docs?', '**/*.md'), 'README.md'),
        (('**/*.md', 'docs?'), 'src/server.py'),
    )
    for patterns, path in cases:
        with mock.patch.object(mod, 'DOCUMENTATION_PATTERNS', patterns):
            _assert_value_error(
                lambda path=path: mod.is_documentation(path),
                f'is_documentation accepted {patterns!r} for {path!r}')


def test_documentation_only_refuses_unsupported_patterns_in_mixed_paths(tmp):
    del tmp
    mod = _classifier()
    paths = ['README.md', 'src/server.py']
    cases = (
        ('docs?', '**/*.md'),
        ('**/*.md', 'docs?'),
    )
    for patterns in cases:
        with mock.patch.object(mod, 'DOCUMENTATION_PATTERNS', patterns):
            _assert_value_error(
                lambda: mod.documentation_only(paths),
                f'documentation_only accepted {patterns!r}')


def test_workflows_changed_refuses_unsupported_patterns_in_mixed_paths(tmp):
    del tmp
    mod = _classifier()
    paths = ['README.md', '.github/workflows/check.yml']
    cases = (
        ('.github/workflows/**', 'workflow?'),
        ('workflow?', '.github/workflows/**'),
    )
    for patterns in cases:
        with mock.patch.object(mod, 'WORKFLOW_PATTERNS', patterns):
            _assert_value_error(
                lambda: mod.workflows_changed(paths),
                f'workflows_changed accepted {patterns!r}')


def test_classify_refuses_unsupported_documentation_pattern_in_mixed_paths(
        tmp):
    del tmp
    mod = _classifier()
    _calls, run = _recorder('README.md\nsrc/server.py\n')
    patterns = ('docs?', '**/*.md')
    with mock.patch.object(mod, 'DOCUMENTATION_PATTERNS', patterns):
        _assert_value_error(
            lambda: mod.classify(_event(), run),
            'classify accepted an unsupported documentation pattern')


def test_classify_reports_mixed_paths_through_outside_fallback(tmp):
    del tmp
    mod = _classifier()
    _calls, run = _recorder('README.md\nsrc/server.py\n')
    docs_only, matrix, workflows, reason = mod.classify(_event(), run)
    assert docs_only is False, reason
    assert matrix == mod.FULL_MATRIX, matrix
    assert workflows is False
    assert reason == '2 paths changed, 1 outside documentation', reason


def test_main_propagates_classify_value_error(tmp):
    mod = _classifier()
    out = Path(tmp) / 'github_output'
    saved = dict(os.environ)
    completed = subprocess.CompletedProcess(
        ['gh'], 0, stdout='README.md\n', stderr='')
    try:
        os.environ.clear()
        os.environ.update({
            'GITHUB_EVENT_NAME': 'pull_request',
            'GITHUB_REPOSITORY': 'octo/daedalus',
            'GITHUB_SHA': 'a' * 40,
            'PR_NUMBER': '248',
            'BEFORE_SHA': 'b' * 40,
            'GITHUB_OUTPUT': str(out),
        })
        with mock.patch.object(mod, 'DOCUMENTATION_PATTERNS',
                               ('docs?', '**/*.md')):
            with mock.patch.object(mod.subprocess, 'run',
                                   return_value=completed):
                with contextlib.redirect_stdout(io.StringIO()):
                    _assert_value_error(
                        mod.main,
                        'main caught classify ValueError and emitted fallback')
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert not out.exists(), 'main wrote fallback outputs after refusal'


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='changeclassifiercallers_')


if __name__ == '__main__':
    raise SystemExit(main())
