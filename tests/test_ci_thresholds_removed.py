#!/usr/bin/env python3
"""Exact removal declarations and tracked-subject exemptions."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wffixtures import _value_error  # noqa: E402
from test_ci_thresholds_guard import (  # noqa: E402
    FAMILIES, GUARD, _captured_main, _document, _git, _history, _judge,
    _raised)


def test_removals_require_exact_reason_and_preserve_siblings(tmp):
    for index, entry in enumerate(('tests_line_baseline', *FAMILIES)):
        for kind in ('', 'bug', 'definition'):
            base = _document()
            if entry in FAMILIES:
                base[entry] = {'backend/kept.py': 9, 'backend/second.py': 8}
            head = copy.deepcopy(base)
            del head[entry]
            trailer = f'\n\nThresholds-Removed: {entry}={kind}' if kind else ''
            repo, rev = _history(tmp, f'{index}-{kind}',
                                 ['fix: remove' + trailer], base, head)
            status, _out, err = _judge(repo, rev, base, head)
            assert status == (0 if kind else 1), err
            if not kind:
                assert 'removed without a reason' in err, err
            if entry in FAMILIES and kind:
                head[entry] = {'backend/second.py': 8}
                assert _judge(repo, rev, base, head)[0] == 0


def test_trailer_grammar_invalidates_one_line_only(tmp):
    entry = 'tests_line_baseline'
    cases = (
        ('{entry}=bug', True), ('{entry}=definition', True),
        ('{entry}.path=bug', False), ('{entry}=breach', False),
        ('{entry}=Bug', False), ('{entry}=bug, other=typo', False),
        ('other, {entry}=bug', False), ('{entry}=bug, =definition', False),
        ('{entry}=bug,', False), ('', False),
        (' {entry} = bug , unused = definition ', True),
        ('{entry}=bug\nThresholds-Removed: invalid', True),
        ('invalid\nThresholds-Removed: {entry}=definition', True),
    )
    for index, (value, allowed) in enumerate(cases):
        base, head = _raised()
        del head[entry]
        repo, rev = _history(tmp, str(index), [
            'fix: remove\n\nThresholds-Removed:\t'
            + value.format(entry=entry)],
            base, head)
        status, _out, err = _judge(repo, rev, base, head)
        assert status == (0 if allowed else 1), (value, err)
    prefixes = (' thresholds-removed:', 'Thresholds-Removed :',
                ' Thresholds-Removed:')
    for index, prefix in enumerate(prefixes):
        base, head = _raised()
        del head[entry]
        repo, rev = _history(tmp, f'prefix-{index}', [
            f'fix: remove\n\n{prefix} {entry}=bug'], base, head)
        assert _judge(repo, rev, base, head)[0] == 1


def test_path_tokens_expand_only_their_own_family(tmp):
    for index, family in enumerate(FAMILIES):
        base = _document()
        base[family] = {'backend/kept.py': 9, 'backend/second.py': 8}
        head = copy.deepcopy(base)
        del head[family]['backend/kept.py']
        repo, rev = _history(tmp, str(index), [
            f'fix: remove\nThresholds-Removed: {family}.backend/kept.py=bug'],
            base, head)
        assert _judge(repo, rev, base, head)[0] == 0
        removed = _util.load(GUARD.with_name('thresholds_removed.py'),
                             'removed_paths')
        declarations = removed.declared(rev, 'HEAD', repo)
        candidates = ['backend/kept.py', 'backend/new.py']
        assert declarations.paths(family, candidates) == {'backend/kept.py'}
        assert declarations.paths('tests_line_baseline', candidates) == set()
        assert removed.exempt_paths(rev, 'HEAD', repo, family,
                                    base[family], candidates) == set()
        head[family] = {}
        status, _out, err = _judge(repo, rev, base, head)
        assert status == 1 and f'{family}.backend/second.py' in err, err


def test_gone_subject_needs_no_declaration_but_read_failure_does(tmp):
    for index, family in enumerate(FAMILIES):
        base = _document()
        base[family] = {'backend/kept.py': 9}
        head = copy.deepcopy(base)
        del head[family]
        repo, rev = _history(tmp, str(index), ['fix: delete subject'],
                             base, head)
        _git(repo, 'rm', 'backend/kept.py')
        _git(repo, 'commit', '-qm', 'fix: delete file')
        assert _judge(repo, rev, base, head)[0] == 0
        real = subprocess.run
        for failure in (0, 128, OSError('cannot read index'),
                        subprocess.TimeoutExpired('git', 10)):
            def read(argv, failure=failure, real=real, **kwargs):
                if 'ls-files' in argv:
                    if isinstance(failure, Exception):
                        raise failure
                    return subprocess.CompletedProcess(argv, failure, b'', b'')
                return real(argv, **kwargs)

            with patch.object(subprocess, 'run', read):
                assert _judge(repo, rev, base, head)[0] == 1


def test_delete_and_looser_readd_is_a_raise_against_base(tmp):
    for index, family in enumerate(('tests_line_baseline', *FAMILIES)):
        base, head = _raised()
        if family in FAMILIES:
            base[family] = {'backend/kept.py': 9}
            head = copy.deepcopy(base)
            head[family]['backend/kept.py'] = 10
        repo, rev = _history(tmp, str(index), [], base)
        target = repo / '.github/ci-thresholds.json'
        intermediate = copy.deepcopy(base)
        del intermediate[family]
        target.write_text(json.dumps(intermediate, default=float))
        _git(repo, 'add', '.github/ci-thresholds.json')
        _git(repo, 'commit', '-qm',
             f'fix: remove\n\nThresholds-Removed: {family}=definition')
        target.write_text(json.dumps(head, default=float))
        _git(repo, 'add', '.github/ci-thresholds.json')
        _git(repo, 'commit', '-qm', 'fix: restore looser value')
        status, _out, err = _judge(repo, rev, base, head)
        assert status == 1 and 'removed without a reason' not in err, err


def test_map_gates_exempt_only_declared_actual_removals(tmp):
    cases = (('size_baseline', 'module_size_baseline', 'tracked_sizes', 600),
             ('line_lengths', 'long_line_baseline', 'tracked_long_lines', 2),
             ('type_error_baseline', 'type_error_baseline', 'analyse', 2),
             ('js_module_coverage', 'js_coverage_baseline',
              'tracked_uncovered_counts', 2))
    for name, family, reader, count in cases:
        base = _document()
        base[family] = {'backend/kept.py': count + 1}
        head = copy.deepcopy(base)
        head[family] = {}
        repo, rev = _history(tmp, name, [
            f'fix: remove\n\nThresholds-Removed: {family}=definition'],
            base, head)
        policy = _util.load(GUARD.with_name(name + '.py'), name + '_removal')
        counts = {'backend/kept.py': count}
        answer = (1, counts, '') if reader == 'analyse' else counts
        args = ['--thresholds', str(repo / '.github/ci-thresholds.json')]
        if name in ('type_error_baseline', 'js_module_coverage'):
            args += ['--root', str(repo)]
        if name == 'js_module_coverage':
            args += [str(repo)]
        with (
            patch.object(policy, reader, lambda *args, answer=answer: answer),
            patch.object(policy, 'ROOT', repo),
            patch.dict(os.environ, DAEDALUS_THRESHOLDS_BASE_REV=rev),
        ):
            status, _out, err = _captured_main(policy, args)
            assert status == 0, (name, err)
            counts['backend/second.py'] = count
            assert _captured_main(policy, args)[0] == 1
            del counts['backend/second.py']
            with patch.dict(os.environ,
                            DAEDALUS_THRESHOLDS_BASE_REV='missing'):
                assert _captured_main(policy, args)[0] == 1
            path = repo / '.github/ci-thresholds.json'
            for recorded in ({'backend/kept.py': 1}, {'backend/second.py': 1}):
                head[family] = recorded
                path.write_text(json.dumps(head, default=float))
                assert _captured_main(policy, args)[0] == 1


def test_exemptions_fail_closed_on_unreadable_base_document(tmp):
    family = 'module_size_baseline'
    repo, rev = _history(tmp, 'bad-base', [
        f'fix: remove\n\nThresholds-Removed: {family}=bug'])
    removed = _util.load(GUARD.with_name('thresholds_removed.py'), 'bad_base')
    for missing in (None, '', ' '):
        assert _value_error(lambda missing=missing: removed.declared(
            missing, 'HEAD', repo)) is not None
        assert removed.exempt_paths(missing, 'HEAD', repo, family,
                                    {}, ['backend/kept.py']) == set()
    real = subprocess.run
    for failure in (b'{', b'[]', b'{"schema_version":2}', 128,
                    OSError('unreadable'),
                    subprocess.TimeoutExpired('git', 10)):
        def read(argv, failure=failure, **kwargs):
            if 'show' not in argv:
                return real(argv, **kwargs)
            if isinstance(failure, Exception):
                raise failure
            return subprocess.CompletedProcess(
                argv, failure if isinstance(failure, int) else 0, failure)

        with patch.object(subprocess, 'run', read):
            assert removed.exempt_paths(rev, 'HEAD', repo, family,
                                        {}, ['backend/kept.py']) == set()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='thremoved_')


if __name__ == '__main__':
    raise SystemExit(main())
