#!/usr/bin/env python3
"""What the plant helper's restore report has to say.

A restore that republished identical bytes and one that recovered the
work printed the same line and exited the same way, and nothing marked
a snapshot taken from a dirty path as one. The report is the only
signal a reader has, so every claim below is parsed by its own label:
this suite names each temp dir after its test function, so a substring
pin over a whole line is satisfied by the path.
"""
import os
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _plant_fixture import (  # noqa: E402
    PLANT, _FIXED, _PLANTED, _as_nobody, _committed_repo, _only_entry,
    _open_the_entry, _reported_state, _run_plant, _say)

_NOT_HEADS = "the published bytes are the worktree's, not what HEAD holds"

_CHANGE_CLAUSES = {
    'the file did not already hold these bytes': 'changed',
    'the file already held these bytes': 'unchanged',
    'the comparison with the file could not be made': 'uncompared',
}


def _restore_clauses(output):
    """The restore report split into the clauses the helper labels."""
    line = output.strip().splitlines()[-1]
    assert line.startswith('restored '), line
    return line.split('; ')


def _state_in(clauses):
    found = [c for c in clauses if c.endswith(' against HEAD')]
    assert len(found) == 1, clauses
    return found[0].split(' ', 1)[1].split(' against')[0]


def _change_in(clauses):
    found = [c for c in clauses if c in _CHANGE_CLAUSES]
    assert len(found) == 1, clauses
    return _CHANGE_CLAUSES[found[0]]


def _entry_fields(output):
    """The labelled fields `_entry_detail` printed, parsed by label."""
    detail = output.strip().splitlines()[-1].rsplit(' (', 1)[-1]
    return [item.split(' ', 1) for item in detail.rstrip(')').split(', ')]


def _write_state(store, value):
    """Put `value` where a save put its own: the field is a file, and a
    hand-edited one is a shape a store can genuinely hold."""
    (_only_entry(store) / 'head-state').write_text(
        f'{value}\n', encoding='utf-8')


def _drop_the_state_field(store):
    (_only_entry(store) / 'head-state').unlink(missing_ok=True)


def _saved_then_planted(tmp, payload=_FIXED):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(payload)
    assert _run_plant('save', str(target), '--store',
                      str(store)).returncode == 0
    target.write_bytes(_PLANTED)
    return target, store


def test_a_dirty_snapshot_is_restored_with_the_state_it_was_taken_in(tmp):
    target, store = _saved_then_planted(tmp)

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'dirty', clauses
    assert _NOT_HEADS in clauses, clauses
    assert _change_in(clauses) == 'changed', clauses
    assert target.read_bytes() == _FIXED


def test_a_clean_snapshot_is_not_reported_as_the_worktrees(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    saved = _run_plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    assert _reported_state(saved.stdout) == 'clean', _say(saved)
    target.write_bytes(_PLANTED)

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'clean', clauses
    assert _NOT_HEADS not in clauses, clauses
    assert _change_in(clauses) == 'changed', clauses


def test_a_restore_over_identical_bytes_says_it_held_them_already(tmp):
    # The recovery the recorded recurrence reaches for: re-save a path
    # that still carries the plant, then restore it. Nothing changes,
    # and the report has to say so - a count cannot.
    target, store = _saved_then_planted(tmp, payload=_PLANTED)

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _change_in(clauses) == 'unchanged', clauses
    assert _state_in(clauses) == 'dirty', clauses
    assert target.read_bytes() == _PLANTED


def test_an_entry_saved_before_the_state_field_restores_as_unknown(tmp):
    target, store = _saved_then_planted(tmp)
    # Absent either way: the entry shape a save from before this field
    # existed has, and one whose save never wrote it.
    _drop_the_state_field(store)

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'unknown', clauses
    assert _change_in(clauses) == 'changed', clauses
    assert target.read_bytes() == _FIXED


def test_an_unknown_state_never_says_the_bytes_are_not_heads(tmp):
    # `unknown` is also what a save inside a work tree whose status
    # could not be read records, and there the bytes may be exactly
    # HEAD's. Only `dirty` carries the claim.
    target, store = _saved_then_planted(tmp)
    _write_state(store, 'unknown')

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'unknown', clauses
    assert _NOT_HEADS not in clauses, clauses
    assert target.read_bytes() == _FIXED


def test_a_state_the_helper_will_not_vouch_for_restores_as_unknown(tmp):
    target, store = _saved_then_planted(tmp)
    _write_state(store, 'clean-ish')

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'unknown', clauses
    assert _NOT_HEADS not in clauses, clauses


def test_a_state_field_holding_non_ascii_is_read_not_raised_over(tmp):
    target, store = _saved_then_planted(tmp)
    (_only_entry(store) / 'head-state').write_bytes(b'\xff\xfe dirty\n')

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'unknown', clauses


def test_a_target_that_does_not_exist_counted_as_holding_nothing(tmp):
    target, store = _saved_then_planted(tmp)
    os.unlink(target)

    restored = _run_plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _change_in(clauses) == 'changed', clauses
    assert target.read_bytes() == _FIXED


def _assert_the_read_is_refused(target, as_nobody):
    """Prove the arrangement before the tool runs, so a green run proves
    something. Root cannot be refused by mode bits, so there the probe
    is the same plain user the restore will be."""
    if not as_nobody:
        refused = False
        try:
            with open(target, 'rb') as handle:
                handle.read()
        except PermissionError:
            refused = True
        assert refused, 'the target was still readable; nothing was proved'
        return
    probe = ('import sys\n'
             'try:\n'
             '    open(sys.argv[1], "rb").read()\n'
             'except PermissionError:\n'
             '    sys.exit(7)\n'
             'sys.exit(0)\n')
    out = _as_nobody([sys.executable, '-c', probe, str(target)])
    assert out.returncode == 7, (out.returncode, _say(out))


def _restore_over_a_target_nothing_can_read(target, store):
    """A real write-only mode, not a mock: `os.replace` needs the
    directory, which the child owns, so the publish lands while the read
    before it cannot.
    """
    if os.name != 'posix':
        _util.skip('POSIX mode bits are what refuse the read')
    dropped = hasattr(os, 'geteuid') and os.geteuid() == 0
    if dropped:
        try:
            _open_the_entry(store, target)
        except OSError as why:
            _util.skip(f'cannot hand the tree to a plain user: {why!r}')
    target.chmod(0o200)
    _assert_the_read_is_refused(target, dropped)
    if dropped:
        return _as_nobody([sys.executable, str(PLANT), 'restore',
                           str(target), '--store', str(store)])
    return _run_plant('restore', str(target), '--store', str(store))


def test_a_target_nothing_can_read_is_reported_as_uncompared(tmp):
    target, store = _saved_then_planted(tmp)

    restored = _restore_over_a_target_nothing_can_read(target, store)
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _change_in(clauses) == 'uncompared', clauses
    target.chmod(0o600)
    assert target.read_bytes() == _FIXED


def _assert_the_removal_is_refused(entry, as_nobody):
    """Prove the arrangement before the helper runs, so a green run proves
    something. Root cannot be refused by mode bits, so there the probe is
    the same plain user the restore will be - and the entry must survive
    it, or the refusal the helper meets is a different one."""
    if not as_nobody:
        refused = False
        try:
            shutil.rmtree(entry)
        except PermissionError:
            refused = True
        assert refused, 'the entry was still removable; nothing was proved'
        assert entry.is_dir(), 'the probe took the entry with it'
        return
    probe = ('import shutil, sys\n'
             'try:\n'
             '    shutil.rmtree(sys.argv[1])\n'
             'except PermissionError:\n'
             '    sys.exit(7)\n'
             'sys.exit(0)\n')
    out = _as_nobody([sys.executable, '-c', probe, str(entry)])
    assert out.returncode == 7, (out.returncode, _say(out))
    assert entry.is_dir(), 'the probe took the entry with it'


def _unremovable_entry(target, store):
    """A real unwritable entry directory, not a mock: `bytes` and `mode`
    still open, so the publish and the chmod both land, and only the
    removal that follows them is refused. Returns whether the helper has
    to run as a plain user, so both commands take the same route.
    """
    if os.name != 'posix':
        _util.skip('POSIX mode bits are what refuse the removal')
    dropped = hasattr(os, 'geteuid') and os.geteuid() == 0
    if dropped:
        try:
            _open_the_entry(store, target)
        except OSError as why:
            _util.skip(f'cannot hand the tree to a plain user: {why!r}')
    entry = _only_entry(store)
    # Read and traverse but not write: every unlink inside it is refused,
    # and none of the reads the commands make before them.
    entry.chmod(0o500)
    _assert_the_removal_is_refused(entry, dropped)
    return dropped


def _plant(action, target, store, as_nobody):
    if as_nobody:
        return _as_nobody([sys.executable, str(PLANT), action,
                           str(target), '--store', str(store)])
    return _run_plant(action, str(target), '--store', str(store))


def test_a_restore_that_cannot_remove_the_entry_says_it_is_still_there(
        tmp):
    target, store = _saved_then_planted(tmp)
    entry = _only_entry(store)
    dropped = _unremovable_entry(target, store)

    restored = _plant('restore', target, store, dropped)
    # The publish landed before the removal was reached, so a nonzero exit
    # here is the refusal, and it has to be the one line the helper's
    # other failures print rather than a traceback over the whole call
    # chain.
    assert restored.returncode != 0, _say(restored)
    said = _say(restored)
    assert 'Traceback' not in said, said
    assert len(restored.stderr.strip().splitlines()) == 1, said
    refusal = restored.stderr.strip()
    # Which of the two post-publish refusals fired. The chmod one leaves
    # the mode unapplied and says so; this one leaves the entry behind,
    # and a line that cannot tell them apart is not one a reader can act
    # on.
    assert 'could not be removed' in refusal, refusal
    # Say the restore happened, and that the entry is still there: a
    # refusal that reads as a save to redo sends the operator back into a
    # store that already holds the copy.
    assert f'{target} was restored' in refusal, refusal
    assert 'still there' in refusal, refusal
    assert str(entry) in refusal, refusal
    # `_open_the_entry` left the target at 0700, so this is the recorded
    # mode and not the one it already had: the chmod landed, and the
    # restore is complete apart from the entry still being on disk.
    assert stat.S_IMODE(target.stat().st_mode) == int(
        (entry / 'mode').read_text().strip(), 8), 'the chmod landed'
    assert target.read_bytes() == _FIXED

    # The other command, against the same entry that survived the first.
    # `clear` reaches the same removal, so this is what makes one shared
    # guard a tested property rather than a claim - and it is why the
    # refusal above can be said to cover issue 1434 as well.
    cleared = _plant('clear', target, store, dropped)
    assert cleared.returncode != 0, _say(cleared)
    said = _say(cleared)
    assert 'Traceback' not in said, said
    assert len(cleared.stderr.strip().splitlines()) == 1, said
    # Nothing announced a discard that did not happen: the line the
    # refusal used to arrive behind.
    assert cleared.stdout == '', said
    refusal = cleared.stderr.strip()
    assert 'could not be removed' in refusal, refusal
    # `clear` published nothing, so its line must not say it restored.
    assert f'{target} was restored' not in refusal, refusal
    assert 'still there' in refusal, refusal
    assert str(entry) in refusal, refusal
    assert entry.is_dir()


def test_the_save_line_is_exactly_the_shape_the_suite_parses(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    saved = _run_plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    # The whole line, not one separator's absence: a clause appended
    # with a comma evades a `'; '` pin and the positional parse alike.
    assert saved.stdout.strip() == (
        f'saved {os.path.abspath(target)}: dirty against HEAD, '
        f'{len(_FIXED)} bytes in {_only_entry(store)}'), saved.stdout


def test_clear_of_an_entry_without_the_field_still_names_its_others(tmp):
    target, store = _saved_then_planted(tmp)
    _drop_the_state_field(store)

    cleared = _run_plant('clear', str(target), '--store', str(store))
    assert cleared.returncode == 0, _say(cleared)
    labels = [label for label, _ in _entry_fields(cleared.stdout)]
    assert labels == ['path', 'saved'], labels


def test_clear_reports_an_unvouched_state_the_way_restore_does(tmp):
    target, store = _saved_then_planted(tmp)
    _write_state(store, 'clean-ish')

    cleared = _run_plant('clear', str(target), '--store', str(store))
    assert cleared.returncode == 0, _say(cleared)
    fields = _entry_fields(cleared.stdout)
    assert ['captured', 'unknown'] in fields, _say(cleared)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='plantreport_')


if __name__ == '__main__':
    raise SystemExit(main())
