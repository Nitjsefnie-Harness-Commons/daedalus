#!/usr/bin/env python3
"""Contracts for the plant helper's save/restore pair.

The failure they close is silent: a path-scoped VCS restore is a statement
about the whole path, so it also sets that path to a committed state, and
the uncommitted work it carried is gone with nothing in the output to say
so. These run the helper against a real repository carrying a real
uncommitted edit, because a fixture without one cannot tell the two
restores apart.
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ratchet_fixture import _git  # noqa: E402

ROOT = _util.ROOT
PLANT = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'plant.py'
SKILL_SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'SKILL.md'

_COMMITTED = b'VALUE = 1\n'
_FIXED = b'VALUE = 2  # the uncommitted fix, never staged\n'
_PLANTED = b'raise RuntimeError("the defect the guard exists to catch")\n'


def _out(result):
    return result.stdout.decode('utf-8', 'replace')


def _plant(*args):
    return subprocess.run([sys.executable, str(PLANT), *args],
                          capture_output=True, text=True, timeout=60,
                          env=_util.child_coverage('scrub'))


def _repo(tmp, name='plantrepo'):
    """A committed repository holding one committed target file."""
    if shutil.which('git') is None:
        _util.skip('git is not on PATH')
    repo = Path(tmp) / name
    repo.mkdir(parents=True)
    target = repo / 'target.py'
    target.write_bytes(_COMMITTED)
    _git(repo, '-c', 'init.defaultBranch=main', 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    _git(repo, 'add', 'target.py')
    _git(repo, 'commit', '-qm', 'base')
    return target


def _say(result):
    return result.stdout + result.stderr


def test_restore_returns_the_uncommitted_work_and_the_planted_bytes_are_gone(
        tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    # The uncommitted "fix". `_FIXED` differs from the committed
    # `_COMMITTED`, so the equality below is what proves it survived.
    target.write_bytes(_FIXED)

    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    target.write_bytes(_PLANTED)
    assert target.read_bytes() == _PLANTED

    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.read_bytes() == _FIXED
    assert 'match' in restored.stdout.lower(), _say(restored)

    # The entry is gone, proven through the CLI: a second restore refuses.
    again = _plant('restore', str(target), '--store', str(store))
    assert again.returncode != 0, _say(again)
    assert target.read_bytes() == _FIXED


def test_restore_without_a_prior_save_refuses_and_changes_nothing(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    out = _plant('restore', str(target), '--store', str(store))
    assert out.returncode != 0, _say(out)
    assert str(target) in _say(out), _say(out)
    assert target.read_bytes() == _FIXED


def test_a_second_save_refuses_and_leaves_the_stored_bytes_alone(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    first = _plant('save', str(target), '--store', str(store))
    assert first.returncode == 0, _say(first)
    target.write_bytes(_PLANTED)
    second = _plant('save', str(target), '--store', str(store))
    assert second.returncode != 0, _say(second)
    assert str(target) in _say(second), _say(second)
    # The stored bytes are the first save's, shown by what restore writes.
    target.write_bytes(b'anything at all\n')
    out = _plant('restore', str(target), '--store', str(store))
    assert out.returncode == 0, _say(out)
    assert target.read_bytes() == _COMMITTED


def test_restore_returns_the_file_mode_it_recorded(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.chmod(0o640)
    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    target.chmod(0o600)
    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.stat().st_mode & 0o777 == 0o640


def test_save_reports_a_dirty_target_against_head(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    dirty = _plant('save', str(target), '--store', str(store))
    assert dirty.returncode == 0, _say(dirty)
    assert 'dirty' in dirty.stdout.lower(), _say(dirty)


def test_save_reports_unknown_outside_a_git_work_tree(tmp):
    if shutil.which('git') is None:
        _util.skip('git is not on PATH')
    outside = Path(tmp) / 'loose' / 'target.py'
    outside.parent.mkdir(parents=True)
    outside.write_bytes(_FIXED)
    try:
        probe = _out(_git(outside.parent, 'rev-parse',
                          '--is-inside-work-tree')).strip()
    except subprocess.CalledProcessError:
        probe = ''
    if probe == 'true':
        _util.skip('this temporary tree is inside a git work tree')
    store = Path(tmp) / 'store'
    other = _plant('save', str(outside), '--store', str(store))
    assert other.returncode == 0, _say(other)
    assert 'unknown' in other.stdout.lower(), _say(other)


_PARAGRAPH_ANCHORS = ('have not watched fail', 'plant the defect',
                      'Restore the way')


def _flatten(text):
    """Each block on one line, so no needle spans a line break."""
    return '\n\n'.join(' '.join(block.split())
                       for block in text.split('\n\n'))


def _plant_paragraph(text):
    """The three mutation paragraphs, each isolated by a unique anchor, so a
    phrase a neighbouring ratchet paragraph shares cannot satisfy one of the
    decisions below."""
    blocks = _flatten(text).split('\n\n')
    found = []
    for anchor in _PARAGRAPH_ANCHORS:
        hits = [part for part in blocks if anchor in part]
        assert len(hits) == 1, (anchor, len(hits))
        found.append(hits[0])
    return ' '.join(' '.join(found).split())


_SKILL_DECISIONS = {
    'helper': 'plant.py save',
    'helper_restore': 'plant.py restore',
    'never_checkout': 'never `git checkout --`',
    'commit_first': 'commit before you plant',
    'whole_tree': 'whole tree',
    'green_signal': 'signal that the restore did nothing',
}


def _skill_decisions(path=SKILL_SOURCE):
    paragraph = _plant_paragraph(path.read_text(encoding='utf-8'))
    return {name: needle in paragraph
            for name, needle in _SKILL_DECISIONS.items()}


def test_skill_names_the_helper_and_the_plant_restore_rule(tmp):
    del tmp
    decisions = _skill_decisions()
    for name, satisfied in decisions.items():
        assert satisfied, (name, sorted(decisions.items()))


def test_skill_mutations_are_caught_independently(tmp):
    original = _flatten(SKILL_SOURCE.read_text(encoding='utf-8'))
    copied = Path(tmp) / 'SKILL.md'
    for name, needle in _SKILL_DECISIONS.items():
        assert needle in _plant_paragraph(original), name
        mutated = original.replace(needle, 'REMOVED BY MUTATION')
        assert mutated != original, (name, 'the mutation changed nothing')
        copied.write_text(mutated, encoding='utf-8')
        assert not _skill_decisions(copied)[name], name


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='plantrestore_')


if __name__ == '__main__':
    raise SystemExit(main())
