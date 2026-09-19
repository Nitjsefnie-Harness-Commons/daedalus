#!/usr/bin/env python3
"""Path containment and equality under degraded resolution."""
import contextlib
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
sys.path.insert(0, str(_util.ROOT))
from daedalus_bridge import path_safety  # noqa: E402


def test_containment_rechecks_a_degraded_resolution_before_refusing(tmp):
    """One transient spelling mismatch is not proof of an escape."""
    root = str(Path(tmp) / 'root')
    resolved_root = os.path.join(root, 'canonical')
    inside = os.path.join(resolved_root, 'inside.json')
    degraded = os.path.join(root, 'CANONI~1', 'inside.json')
    outside = os.path.join(root, 'outside', 'inside.json')
    candidate_call = os.path.join(resolved_root, 'inside.json')
    expected_calls = [
        root, candidate_call, root, candidate_call,
        root, candidate_call, root, candidate_call,
    ]
    answers = iter([
        resolved_root, degraded, resolved_root, inside,
        resolved_root, outside, resolved_root, outside,
    ])
    calls = []
    realpath = path_safety.os.path.realpath

    def resolving_stub(path):
        calls.append(os.fspath(path))
        return next(answers)

    path_safety.os.path.realpath = resolving_stub
    try:
        try:
            contained = path_safety.under(root, 'inside.json')
        except ValueError as failure:
            contained = f'REFUSED: {failure}'
        try:
            path_safety.under(root, 'inside.json')
        except ValueError:
            escape = 'refused'
        else:
            escape = 'ALLOWED'
    finally:
        path_safety.os.path.realpath = realpath
    assert contained == Path(inside), contained
    assert escape == 'refused', escape
    assert calls == expected_calls, calls


def test_path_refusal_logs_both_attempts_and_success_is_quiet(tmp):
    root = str(Path(tmp) / 'root')
    first_root = str(Path(tmp) / 'FIRST~1')
    first_candidate = str(Path(tmp) / 'outside-1' / 'inside.json')
    second_root = str(Path(tmp) / 'canonical')
    second_candidate = str(Path(tmp) / 'outside-2' / 'inside.json')
    answers = iter((first_root, first_candidate, second_root, second_candidate,
                    second_root, os.path.join(second_root, 'inside.json')))
    realpath = path_safety.os.path.realpath

    def resolving_stub(_path):
        return next(answers)
    path_safety.os.path.realpath = resolving_stub
    refusal_log, success_log = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(refusal_log):
            try:
                path_safety.under(root, 'inside.json')
            except ValueError:
                pass
            else:
                raise AssertionError('persistent mismatch was allowed')
        with contextlib.redirect_stdout(success_log):
            path_safety.under(root, 'inside.json')
    finally:
        path_safety.os.path.realpath = realpath
    assert len(lines := refusal_log.getvalue().splitlines()) == 1, lines
    assert lines[0].startswith('[PATH-REFUSAL] kind=containment '), lines
    assert f'root={root!r}' in lines[0], lines
    assert "parts=('inside.json',)" in lines[0], lines
    for spelling in (first_root, first_candidate,
                     second_root, second_candidate):
        assert repr(spelling) in lines[0], lines
    assert success_log.getvalue() == '', success_log.getvalue()


def test_path_equality_rechecks_a_degraded_resolution_before_refusing(tmp):
    """A transient spelling mismatch is not a stable alias verdict."""
    left = str(Path(tmp) / 'left')
    right = str(Path(tmp) / 'right')
    resolved = str(Path(tmp) / 'canonical')
    degraded = str(Path(tmp) / 'CANONI~1')
    alias = str(Path(tmp) / 'other-target')
    expected_calls = [left, right, left, right] * 2
    answers = iter([
        resolved, degraded, resolved, resolved,
        resolved, alias, resolved, alias,
    ])
    calls = []
    realpath = path_safety.os.path.realpath

    def resolving_stub(path):
        calls.append(os.fspath(path))
        return next(answers)

    comparer = getattr(path_safety, 'same_path', None)
    assert comparer is not None, 'missing same_path comparer'
    path_safety.os.path.realpath = resolving_stub
    try:
        same = comparer(left, right)
        different = comparer(left, right)
    finally:
        path_safety.os.path.realpath = realpath
    assert same is True, same
    assert different is False, different
    assert calls == expected_calls, calls


def main():
    return _util.runner(_util.collect(globals()))


if __name__ == '__main__':
    raise SystemExit(main())
