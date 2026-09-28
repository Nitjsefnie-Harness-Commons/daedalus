#!/usr/bin/env python3
"""Contracts for the plant helper's save/restore pair.

The failure they close is silent: a path-scoped VCS restore is a statement
about the whole path, so it also sets that path to a committed state, and
the uncommitted work it carried is gone with nothing in the output to say
so. These run the helper against a real repository carrying a real
uncommitted edit, because a fixture without one cannot tell the two
restores apart.

A claim is only made here where a control would fail if it stopped being
true, and each of those controls is mutation-proved. A restore that re-read
what it wrote and refused a mismatch is NOT one of them: a mismatch needs
a filesystem that lies, so no run reaches it, and a check on the source's
spelling is satisfied by `payload = written`. That check is gone rather
than kept, and the claim it guarded went with it.
"""
import os
import shutil
import stat
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


def _only_entry(store):
    entries = [item for item in Path(store).iterdir() if item.is_dir()]
    assert len(entries) == 1, entries
    return entries[0]


def _unreadable_as_bytes(entry):
    """Make a stored copy unreadable as bytes, on every platform.

    A directory where a file is expected raises IsADirectoryError on
    every platform, so this needs no privilege drop and no Windows
    carve-out - and it is a real corrupted store, not a contrivance.
    """
    payload = entry / 'bytes'
    payload.unlink()
    payload.mkdir()


def _drop_to_nobody():
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)


def _as_nobody(command):
    """Run `command` unprivileged, so the file mode bits actually bite.

    Root bypasses them, which is why this route was enforced nowhere on
    a root runner. The child owns what it publishes over, so the caller
    hands the tree to it first.
    """
    return subprocess.run(command, capture_output=True, text=True,
                          timeout=60, preexec_fn=_drop_to_nobody,
                          env=_util.child_coverage('scrub'))


def _hand_to_nobody(path):
    os.chown(path, 65534, 65534)
    os.chmod(path, 0o777)


def _open_the_entry(store, target):
    """Hand the child every directory and file it walks to publish.

    A suite's own temporary root is created 0700, so without the traverse
    bit the child reads a refusal where the route should have run.
    """
    entry = _only_entry(store)
    for directory in (target.parent, store, entry):
        for ancestor in (directory, *directory.parents):
            os.chmod(ancestor, os.stat(ancestor).st_mode | 0o005)
    for owned in (target.parent, store, entry, *entry.iterdir()):
        _hand_to_nobody(owned)


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
    # No assertion on what the restore PRINTED: its own success wording
    # survives the read-back being deleted, so such an assertion passes
    # exactly when the program has stopped doing the work.

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


def test_the_refusal_does_not_advise_overwriting_a_changed_file(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    first = _plant('save', str(target), '--store', str(store))
    assert first.returncode == 0, _say(first)
    target.write_bytes(_FIXED)
    again = _plant('save', str(target), '--store', str(store))
    assert again.returncode != 0, _say(again)
    said = _say(again)
    # The advice, not the whole line: the paths carry the store name.
    advice = said.split('; ', 1)[-1]
    assert 'has changed since that copy was taken' in advice, said
    assert f'plant.py clear {target}' in advice, said
    # The verb it must not name, not the word: this suite's own tmp
    # prefix contains "restore".
    assert 'plant.py restore' not in advice, said
    assert target.read_bytes() == _FIXED


def test_the_refusal_offers_a_restore_when_the_file_has_not_moved_on(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    first = _plant('save', str(target), '--store', str(store))
    assert first.returncode == 0, _say(first)
    again = _plant('save', str(target), '--store', str(store))
    assert again.returncode != 0, _say(again)
    advice = _say(again).split('; ', 1)[-1]
    assert 'has changed since that copy was taken' not in advice, advice
    assert 'still matches that copy' in advice, advice
    assert 'plant.py restore' in advice, advice


def test_clear_discards_the_entry_it_names(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    first = _plant('save', str(target), '--store', str(store))
    assert first.returncode == 0, _say(first)
    cleared = _plant('clear', str(target), '--store', str(store))
    assert cleared.returncode == 0, _say(cleared)
    # Proven by what the tool does next, never by a status word.
    assert _plant('save', str(target), '--store',
                  str(store)).returncode == 0
    assert _plant('clear', str(target), '--store',
                  str(store)).returncode == 0
    refused = _plant('restore', str(target), '--store', str(store))
    assert refused.returncode != 0, _say(refused)
    assert 'no stored copy' in _say(refused), _say(refused)
    assert target.read_bytes() == _COMMITTED


# Read-only on BOTH platforms: Windows has no execute bit and honours only
# this flag. `test_the_recorded_mode_is_the_polarity_this_suite_asserts`
# keeps this and the polarity the restore assertion below uses in step.
RECORDED_MODE = 0o400


def test_the_recorded_mode_is_the_polarity_this_suite_asserts(tmp):
    del tmp
    # A recorded mode with the write bit would contradict the assertion
    # below, and a platform that cannot report an execute bit would only
    # find that out in CI.
    assert not RECORDED_MODE & stat.S_IWRITE, oct(RECORDED_MODE)


def test_restore_returns_the_mode_it_recorded(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.chmod(RECORDED_MODE)
    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    target.chmod(0o600)
    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.read_bytes() == _COMMITTED
    recorded = stat.S_IMODE(target.stat().st_mode)
    assert not recorded & stat.S_IWRITE, recorded
    if os.name == 'posix':
        # Windows reports 0o444 for the same flag, so exactness is POSIX's
        # to claim; the read-only assertion above is the portable one.
        assert recorded == RECORDED_MODE, recorded


def test_restore_returns_a_target_that_was_saved_read_only(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.chmod(0o444)
    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    # Truncate-then-write cannot open this; os.replace can.
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        _open_the_entry(store, target)
        _hand_to_nobody(target)
        target.chmod(0o444)
        restored = _as_nobody(
            [sys.executable, str(PLANT), 'restore', str(target),
             '--store', str(store)])
    else:
        restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.read_bytes() == _COMMITTED
    # Windows refuses to delete a read-only file, and so does the runner's
    # cleanup, so give the tree its permissions back before this returns.
    target.chmod(0o600)


def test_restore_writes_through_a_symlinked_target(tmp):
    if sys.platform.startswith('win'):
        _util.skip('creating a symlink needs a privilege Windows withholds')
    repo = Path(tmp) / 'linked'
    repo.mkdir(parents=True)
    real = repo / 'real.py'
    real.write_bytes(_COMMITTED)
    link = repo / 'link.py'
    link.symlink_to(real.name)
    store = Path(tmp) / 'store'
    saved = _plant('save', str(link), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    real.write_bytes(_PLANTED)
    restored = _plant('restore', str(link), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert link.is_symlink(), 'the link was replaced by a regular file'
    assert real.read_bytes() == _COMMITTED


def test_restore_refuses_a_store_it_cannot_read(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    assert _plant('save', str(target), '--store',
                  str(store)).returncode == 0
    entry = _only_entry(store)
    _unreadable_as_bytes(entry)
    out = _plant('restore', str(target), '--store', str(store))
    assert out.returncode != 0, _say(out)
    assert target.read_bytes() == _FIXED, 'a refused restore wrote anyway'
    assert entry.is_dir(), 'a refused restore dropped the stored copy'


def test_clear_leaves_another_pending_plant_alone(tmp):
    one = _repo(tmp, 'one')
    two = _repo(tmp, 'two')
    store = Path(tmp) / 'store'
    for target in (one, two):
        assert _plant('save', str(target), '--store',
                      str(store)).returncode == 0
    assert _plant('clear', str(one), '--store', str(store)).returncode == 0
    # One entry left, not zero: clearing one plant must not take another's.
    assert len([i for i in Path(store).iterdir() if i.is_dir()]) == 1
    for target in (one, two):
        target.write_bytes(_PLANTED)
    assert _plant('restore', str(two), '--store', str(store)).returncode == 0
    assert two.read_bytes() == _COMMITTED


def test_the_refusal_recommends_clear_when_the_copy_is_unreadable(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    assert _plant('save', str(target), '--store',
                  str(store)).returncode == 0
    _unreadable_as_bytes(_only_entry(store))
    again = _plant('save', str(target), '--store', str(store))
    assert again.returncode != 0, _say(again)
    advice = _say(again).split('; ', 1)[-1]
    assert f'plant.py clear {target}' in advice, advice
    assert 'plant.py restore' not in advice, advice


def test_a_write_that_dies_partway_leaves_the_target_untouched(tmp):
    if sys.platform.startswith('win'):
        _util.skip('a POSIX file-size limit is what truncates the write')
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(b'x' * 40000)
    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    target.write_bytes(_PLANTED)
    # One 512-byte block per file, so the payload cannot be written
    # whole. However the child dies, the target keeps the planted bytes.
    subprocess.run(
        ['sh', '-c', f'ulimit -f 1; exec "{sys.executable}" "{PLANT}" '
                     f'restore "{target}" --store "{store}"'],
        capture_output=True, text=True, timeout=60,
        env=_util.child_coverage('scrub'))
    assert target.read_bytes() == _PLANTED


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


def test_the_isolation_refuses_a_relocated_phrase(tmp):
    blocks = _flatten(SKILL_SOURCE.read_text(encoding='utf-8')).split('\n\n')
    name, needle = 'whole_tree', _SKILL_DECISIONS['whole_tree']
    assert _skill_decisions()[name], 'the control must bite on the real file'
    copied = Path(tmp) / 'SKILL.md'
    at = next(i for i, block in enumerate(blocks) if needle in block)
    own = list(blocks)
    own[at] = own[at].replace(needle, 'REMOVED FROM ITS OWN PARAGRAPH')
    borrowed = [f'A neighbouring paragraph that happens to say {needle}.']
    borrowed.extend(own)
    copied.write_text('\n\n'.join(borrowed), encoding='utf-8')
    assert not _skill_decisions(copied)[name], name


def test_the_isolation_refuses_a_duplicated_anchor(tmp):
    blocks = _flatten(SKILL_SOURCE.read_text(encoding='utf-8')).split('\n\n')
    copied = Path(tmp) / 'SKILL.md'
    copied.write_text('\n\n'.join(
        blocks + [next(b for b in blocks if 'plant the defect' in b)]),
        encoding='utf-8')
    refused = False
    try:
        _skill_decisions(copied)
    except AssertionError:
        refused = True
    assert refused, 'a paragraph carrying an anchor twice was not refused'




def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='plantrestore_')


if __name__ == '__main__':
    raise SystemExit(main())
