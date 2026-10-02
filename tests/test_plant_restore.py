#!/usr/bin/env python3
"""Contracts for the plant helper's save/restore pair.

The failure they close is silent: a path-scoped VCS restore is a statement
about the whole path, so it also sets that path to a committed state, and
the uncommitted work it carried is gone with nothing in the output to say
so. These run the helper against a real repository carrying a real
uncommitted edit, because a fixture without one cannot tell the two
restores apart.

A claim is made here only where a control would fail if it stopped being
true. A restore that re-read what it wrote and refused a mismatch is NOT
one: a mismatch needs a filesystem that lies, so no run reaches it, and a
check on the source's spelling is satisfied by `payload = written`.
"""
import contextlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ratchet_fixture import _git  # noqa: E402
from _plant_fixture import (  # noqa: E402
    PLANT, _COMMITTED, _FIXED, _PLANTED, _as_nobody, _committed_repo,
    _only_entry, _open_the_entry, _reported_state, _run_plant, _say,
    _unreadable_as_bytes)

SKILL_SOURCE = (_util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
                / 'SKILL.md')


def test_restore_returns_the_uncommitted_work_and_the_planted_bytes_are_gone(
        tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    # The uncommitted "fix": `_FIXED` differs from the committed byte, so
    # the equality below proves it survived.
    target.write_bytes(_FIXED)

    saved = _run_plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    target.write_bytes(_PLANTED)
    assert target.read_bytes() == _PLANTED

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.read_bytes() == _FIXED
    # No assertion on what the restore PRINTED: its own success wording
    # is what it would report if it had stopped doing the work.

    # The entry is gone, proven through the CLI: a second restore refuses.
    again = _run_plant('restore', str(target), '--store', str(store))
    assert again.returncode != 0, _say(again)
    assert target.read_bytes() == _FIXED


def test_restore_without_a_prior_save_refuses_and_changes_nothing(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    out = _run_plant('restore', str(target), '--store', str(store))
    assert out.returncode != 0, _say(out)
    assert str(target) in _say(out), _say(out)
    assert target.read_bytes() == _FIXED


def test_a_second_save_refuses_and_leaves_the_stored_bytes_alone(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    first = _run_plant('save', str(target), '--store', str(store))
    assert first.returncode == 0, _say(first)
    target.write_bytes(_PLANTED)
    second = _run_plant('save', str(target), '--store', str(store))
    assert second.returncode != 0, _say(second)
    assert str(target) in _say(second), _say(second)
    # The stored bytes are the first save's, shown by what restore writes.
    target.write_bytes(b'anything at all\n')
    out = _run_plant('restore', str(target), '--store', str(store))
    assert out.returncode == 0, _say(out)
    assert target.read_bytes() == _COMMITTED


def test_the_refusal_does_not_advise_overwriting_a_changed_file(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    first = _run_plant('save', str(target), '--store', str(store))
    assert first.returncode == 0, _say(first)
    target.write_bytes(_FIXED)
    again = _run_plant('save', str(target), '--store', str(store))
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
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    first = _run_plant('save', str(target), '--store', str(store))
    assert first.returncode == 0, _say(first)
    again = _run_plant('save', str(target), '--store', str(store))
    assert again.returncode != 0, _say(again)
    advice = _say(again).split('; ', 1)[-1]
    assert 'has changed since that copy was taken' not in advice, advice
    assert 'still matches that copy' in advice, advice
    assert 'plant.py restore' in advice, advice


def test_clear_discards_the_entry_it_names(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    first = _run_plant('save', str(target), '--store', str(store))
    assert first.returncode == 0, _say(first)
    cleared = _run_plant('clear', str(target), '--store', str(store))
    assert cleared.returncode == 0, _say(cleared)
    # Proven by what the tool does next, never by a status word.
    assert _run_plant('save', str(target), '--store',
                      str(store)).returncode == 0
    assert _run_plant('clear', str(target), '--store',
                      str(store)).returncode == 0
    refused = _run_plant('restore', str(target), '--store', str(store))
    assert refused.returncode != 0, _say(refused)
    assert 'no stored copy' in _say(refused), _say(refused)
    assert target.read_bytes() == _COMMITTED


def test_a_clear_of_an_entry_already_gone_is_a_discard(tmp):
    # The race the removal encodes is between this caller's `isdir` check
    # and the `rmtree` that follows it, so the removal below is the one
    # the check lost to: the entry is really taken, and the tool is told
    # it was already gone.
    plant = _util.load(PLANT, 'plant_clear_race')
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    with contextlib.redirect_stdout(io.StringIO()):
        assert plant.save(str(target), str(store)) == 0
    entry = _only_entry(store)

    class _RacedRemoval:
        @staticmethod
        def rmtree(path):
            shutil.rmtree(path)
            raise FileNotFoundError(2, 'No such file or directory', path)

    said = io.StringIO()
    real_shutil = plant.shutil
    try:
        plant.shutil = _RacedRemoval
        with contextlib.redirect_stdout(said):
            status = plant.clear(str(target), str(store))
    finally:
        plant.shutil = real_shutil
    assert status == 0, status
    assert not entry.exists(), 'the entry survived the removal'
    # The post-condition is the entry's absence, so a store entry that is
    # gone is a discard and never a refusal that names what is there.
    assert said.getvalue().startswith('discarded '), said.getvalue()


# Read-only on BOTH platforms - Windows has no execute bit and honours
# only this flag. The polarity below is pinned to it by its own test.
RECORDED_MODE = 0o400


def test_the_recorded_mode_is_the_polarity_this_suite_asserts(tmp):
    del tmp
    # A recorded mode with the write bit would contradict the assertion
    # below, and only CI would find that out.
    assert not RECORDED_MODE & stat.S_IWRITE, oct(RECORDED_MODE)


_PAYLOAD = b'PUBLISHED' * 600000


def _states_while_restoring(plant, target, store):
    """Every distinct state a reader observes while a restore publishes.

    What is sampled is the file's SIZE, one stat per turn: an in-place
    write is observable as a length between the two states, and a
    half-written file cannot reach the full length before it holds those
    bytes. Whether the sampler COULD see a third state is settled by the
    hard-link control, not by this loop's length.
    """
    root = _util.ROOT
    proc = subprocess.Popen(
        [sys.executable, str(plant), 'restore', str(target),
         '--store', str(store)], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, cwd=root,
        env=_util.child_coverage('keep', cwd=root))
    seen = set()
    while proc.poll() is None:
        try:
            seen.add(os.stat(target).st_size)
        except OSError as why:
            seen.add(f'unreadable: {type(why).__name__}')
    proc.wait(timeout=60)
    return seen


def _helper_that_grows_the_target_in_place(tmp):
    """A copy of the real helper whose _publish writes the target in
    place, chunked and flushed - the honest shape, since a real in-place
    write grows the file as the page cache drains."""
    source = PLANT.read_text(encoding='utf-8')
    old = '        os.replace(temp, target)'
    new = ("        with open(target, 'wb') as handle:\n"
           "            for start in range(0, len(payload), 1 << 14):\n"
           "                handle.write(payload[start:start + (1 << 14)])\n"
           "                handle.flush()\n"
           "        del temp\n"
           "        return")
    assert source.count(old) == 1, 'the publish anchor moved'
    broken = Path(tmp) / 'plant_in_place.py'
    broken.write_text(source.replace(old, new), encoding='utf-8')
    return broken


def _a_directory_on_another_device(store):
    """A directory on another device: os.replace is per-filesystem."""
    wanted = os.stat(store).st_dev
    for candidate in ('/dev/shm', tempfile.gettempdir()):
        try:
            probe = Path(candidate)
            if not probe.is_dir() or os.stat(probe).st_dev == wanted:
                continue
            made = probe / f'plantrestore-otherdev-{os.getpid()}'
            made.mkdir(exist_ok=True)
            # 0o700: the helper runs as the user that made the directory,
            # so the owner bits are all it needs.
            os.chmod(made, 0o700)
            return made
        except OSError:
            continue
    return None


def test_a_target_on_another_device_still_restores(tmp):
    store = Path(tmp) / 'store'
    store.mkdir(parents=True)
    elsewhere = _a_directory_on_another_device(store)
    if elsewhere is None:
        _util.skip('no writable directory on a second device here')
    try:
        target = elsewhere / 'target.py'
        target.write_bytes(_COMMITTED)
        assert _run_plant('save', str(target), '--store',
                          str(store)).returncode == 0
        target.write_bytes(_PLANTED)
        restored = _run_plant('restore', str(target), '--store', str(store))
        assert restored.returncode == 0, _say(restored)
        assert target.read_bytes() == _COMMITTED
    finally:
        for leftover in elsewhere.glob('*'):
            leftover.unlink()
        elsewhere.rmdir()


def test_a_racing_save_is_refused_and_not_traced(tmp):
    # The guard is check-then-act, so a second save can pass it and reach
    # an entry that now exists: a refusal, not a traceback.
    plant = _util.load(PLANT, 'plant_racing_save')
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    with contextlib.redirect_stdout(io.StringIO()):
        assert plant.save(str(target), str(store)) == 0
    entry = str(_only_entry(store))
    real_exists = os.path.exists
    state = {'raced': False}

    def racing(path):
        if str(path) == entry and not state['raced']:
            state['raced'] = True
            return False
        return real_exists(path)

    err, out = io.StringIO(), io.StringIO()
    try:
        os.path.exists = racing
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(out):
            status = plant.save(str(target), str(store))
    finally:
        os.path.exists = real_exists
    assert state['raced'], 'the race was never reached'
    assert status == 1, status
    assert str(target) in err.getvalue(), err.getvalue()


def _planted_publish_fixture(tmp):
    """A saved payload and a planted file, ready for one restore."""
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_PAYLOAD)
    assert _run_plant('save', str(target), '--store',
                      str(store)).returncode == 0
    target.write_bytes(_PLANTED)
    return target, store


def test_a_reader_never_sees_a_third_state_while_a_restore_publishes(tmp):
    target, store = _planted_publish_fixture(tmp)
    seen = _states_while_restoring(PLANT, target, store)
    assert seen, 'the sampler observed nothing, so this run asserts nothing'
    assert seen <= {len(_PAYLOAD), len(_PLANTED)}, sorted(seen)


def _link_sees_after_restore(plant, tmp):
    """Restore a target, and report what a hard link to it saw afterwards.

    A second name for one inode is a reader guaranteed to be there:
    `os.replace` installs a NEW inode and leaves every other name on the
    untouched old one, while a write in place mutates the inode they
    share. That separates the forms by mechanism, not by racing.
    """
    repo = Path(tmp) / 'repo'
    repo.mkdir(parents=True)
    root = _util.ROOT
    target = repo / 'target.py'
    target.write_bytes(_COMMITTED)
    link = repo / 'link.py'
    os.link(target, link)
    store = Path(tmp) / 'store'
    assert _run_plant('save', str(target), '--store',
                      str(store)).returncode == 0
    target.write_bytes(_PLANTED)
    out = subprocess.run(
        [sys.executable, str(plant), 'restore', str(target),
         '--store', str(store)], capture_output=True, text=True,
        cwd=root, env=_util.child_coverage('keep', cwd=root), timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    return link.read_bytes()


def test_the_publish_swaps_the_inode_rather_than_writing_through_it(tmp):
    # The link still holds the planted bytes: the restore published a new
    # inode, so a reader holding the old one cannot see a partial file.
    assert _link_sees_after_restore(PLANT, tmp) == _PLANTED


def test_the_control_catches_a_publish_that_is_not_atomic(tmp):
    # The same check against a helper that grows the target in place: the
    # link now holds the RESTORED bytes, written through the shared
    # inode. No window to win, so nothing passes this vacuously.
    broken = _helper_that_grows_the_target_in_place(tmp)
    assert _link_sees_after_restore(broken, tmp) == _COMMITTED


def test_restore_returns_the_mode_it_recorded(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.chmod(RECORDED_MODE)
    saved = _run_plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    target.chmod(0o600)
    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.read_bytes() == _COMMITTED
    recorded = stat.S_IMODE(target.stat().st_mode)
    assert not recorded & stat.S_IWRITE, recorded
    if os.name == 'posix':
        # Windows reports 0o444 for the same flag, so exactness is POSIX's
        # to claim; the read-only assertion above is the portable one.
        assert recorded == RECORDED_MODE, recorded


def test_restore_returns_a_target_that_was_saved_read_only(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.chmod(0o444)
    saved = _run_plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        try:
            _open_the_entry(store, target)
        except OSError as why:
            _util.skip(f'cannot hand the tree to a plain user: {why!r}')
        target.chmod(0o444)
        restored = _as_nobody(
            [sys.executable, str(PLANT), 'restore', str(target),
             '--store', str(store)])
    else:
        restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.read_bytes() == _COMMITTED
    # Windows refuses to delete a read-only file, and so does cleanup.
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
    saved = _run_plant('save', str(link), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    real.write_bytes(_PLANTED)
    restored = _run_plant('restore', str(link), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert link.is_symlink(), 'the link was replaced by a regular file'
    assert real.read_bytes() == _COMMITTED


def test_restore_refuses_a_store_whose_mode_record_is_gone(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    assert _run_plant('save', str(target), '--store',
                      str(store)).returncode == 0
    entry = _only_entry(store)
    (entry / 'mode').unlink()
    out = _run_plant('restore', str(target), '--store', str(store))
    assert out.returncode != 0, _say(out)
    # A designed refusal names the path; an unhandled crash does not.
    assert str(target) in _say(out), _say(out)
    assert target.read_bytes() == _FIXED, 'a refused restore wrote anyway'
    assert (entry / 'bytes').is_file(), 'a refused restore dropped the copy'


def test_a_restore_onto_a_directory_refuses_and_leaves_no_temp_behind(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    saved = _run_plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    # A path that has become a directory: `os.replace` cannot install a
    # file over one, so the publish fails with its temp already written.
    target.unlink()
    target.mkdir()

    refused = _run_plant('restore', str(target), '--store', str(store))
    assert refused.returncode != 0, _say(refused)
    said = _say(refused)
    assert 'Traceback' not in said, said
    assert refused.stdout == '', said
    assert f'cannot restore {target}' in said, said
    assert target.is_dir(), 'a refused restore published anyway'
    # The stored copy is what the refusal sends the operator back to.
    assert (_only_entry(store) / 'bytes').is_file(), said
    # The temp the failed publish wrote is gone with it: a temp left in
    # the target's directory is litter a plant cycle leaves behind.
    assert not list(target.parent.glob('.target.py.*.tmp')), (
        sorted(p.name for p in target.parent.iterdir()))


def test_restore_leaves_another_pending_plant_alone(tmp):
    one = _committed_repo(tmp, 'one')
    two = _committed_repo(tmp, 'two')
    store = Path(tmp) / 'store'
    for target in (one, two):
        assert _run_plant('save', str(target), '--store',
                          str(store)).returncode == 0
    one.write_bytes(_PLANTED)
    assert _run_plant('restore', str(one), '--store',
                      str(store)).returncode == 0
    assert one.read_bytes() == _COMMITTED
    # The other plant's copy is still in the store, to be restored from.
    assert len([i for i in Path(store).iterdir() if i.is_dir()]) == 1
    assert _run_plant('restore', str(two), '--store',
                      str(store)).returncode == 0
    assert two.read_bytes() == _COMMITTED


def test_the_two_spellings_of_a_path_do_not_share_an_entry(tmp):
    if sys.platform.startswith('win'):
        _util.skip('creating a symlink needs a privilege Windows withholds')
    repo = Path(tmp) / 'spelled'
    repo.mkdir(parents=True)
    real = repo / 'real.py'
    real.write_bytes(_COMMITTED)
    (repo / 'link.py').symlink_to(real.name)
    store = Path(tmp) / 'store'
    assert _run_plant('save', str(repo / 'link.py'), '--store',
                      str(store)).returncode == 0
    # The key is the path as spelled, so the other spelling holds no
    # entry. Keying on the resolved path would publish the link's plant
    # over the real file.
    refused = _run_plant('restore', str(real), '--store', str(store))
    assert refused.returncode != 0, _say(refused)
    assert str(real) in _say(refused), _say(refused)
    assert real.read_bytes() == _COMMITTED


def test_clear_leaves_another_pending_plant_alone(tmp):
    one = _committed_repo(tmp, 'one')
    two = _committed_repo(tmp, 'two')
    store = Path(tmp) / 'store'
    for target in (one, two):
        assert _run_plant('save', str(target), '--store',
                          str(store)).returncode == 0
    assert _run_plant('clear', str(one), '--store',
                      str(store)).returncode == 0
    # One left, not zero: clearing one plant takes no other's.
    assert len([i for i in Path(store).iterdir() if i.is_dir()]) == 1
    for target in (one, two):
        target.write_bytes(_PLANTED)
    assert _run_plant('restore', str(two), '--store',
                      str(store)).returncode == 0
    assert two.read_bytes() == _COMMITTED


def test_the_refusal_recommends_clear_when_the_copy_is_unreadable(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    assert _run_plant('save', str(target), '--store',
                      str(store)).returncode == 0
    _unreadable_as_bytes(_only_entry(store))
    again = _run_plant('save', str(target), '--store', str(store))
    assert again.returncode != 0, _say(again)
    advice = _say(again).split('; ', 1)[-1]
    assert f'plant.py clear {target}' in advice, advice
    assert 'plant.py restore' not in advice, advice
    # WHICH advice, not just that both branches name `clear`: a store
    # that cannot open its own bytes is not evidence that the file
    # moved on, and the "it has changed" wording claims exactly that.
    assert 'has changed since that copy was taken' not in advice, advice
    assert 'neither that copy nor the file could be read' in advice, advice


def test_a_write_that_dies_partway_leaves_the_target_untouched(tmp):
    if sys.platform.startswith('win'):
        _util.skip('a POSIX file-size limit is what truncates the write')
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(b'x' * 40000)
    saved = _run_plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    target.write_bytes(_PLANTED)
    # One 512-byte block per file, so the payload cannot be written
    # whole; however the child dies, the target keeps its planted bytes.
    subprocess.run(
        ['sh', '-c', f'ulimit -f 1; exec "{sys.executable}" "{PLANT}" '
                     f'restore "{target}" --store "{store}"'],
        capture_output=True, text=True, timeout=60,
        env=_util.child_coverage('scrub'))
    assert target.read_bytes() == _PLANTED


def test_save_reports_a_dirty_target_against_head(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    dirty = _run_plant('save', str(target), '--store', str(store))
    assert dirty.returncode == 0, _say(dirty)
    assert _reported_state(dirty.stdout) == 'dirty', _say(dirty)


def test_save_reports_unknown_outside_a_git_work_tree(tmp):
    if shutil.which('git') is None:
        _util.skip('git is not on PATH')
    outside = Path(tmp) / 'loose' / 'target.py'
    outside.parent.mkdir(parents=True)
    outside.write_bytes(_FIXED)
    try:
        # `_git` launches and discards; `check=True` raises exactly when
        # this path is not inside a work tree, which is the answer.
        _git(outside.parent, 'rev-parse', '--is-inside-work-tree')
        probe = 'true'
    except subprocess.CalledProcessError:
        probe = ''
    if probe == 'true':
        _util.skip('this temporary tree is inside a git work tree')
    store = Path(tmp) / 'store'
    other = _run_plant('save', str(outside), '--store', str(store))
    assert other.returncode == 0, _say(other)
    assert _reported_state(other.stdout) == 'unknown', _say(other)


_PARAGRAPH_ANCHORS = ('have not watched fail', 'plant the defect',
                      'Restore the way')


def _flatten(text):
    """Each block on one line, so no needle spans a line break."""
    return '\n\n'.join(' '.join(block.split())
                       for block in text.split('\n\n'))


def _plant_paragraph(text):
    """The three mutation paragraphs, each isolated by a unique anchor."""
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
