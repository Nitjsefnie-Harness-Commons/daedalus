#!/usr/bin/env python3
"""What the plant helper's restore report has to say.

A restore that republished identical bytes and one that recovered the
work printed the same line and exited the same way, and nothing marked
a snapshot taken from a dirty path as one. The report is the only
signal a reader has, so every claim below is parsed by its own label:
this suite names each temp dir after its test function, so a substring
pin over a whole line is satisfied by the path.
"""
import contextlib
import io
import os
import shutil
import stat
import sys
from pathlib import Path
from unittest import mock

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
    return _plant('restore', target, store, dropped)


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
    the same plain user the helper will be - and the entry must survive
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
    removal that follows them is refused.
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
    # here is the refusal, and it has to be one line on stderr with nothing
    # on stdout: a traceback over the whole call chain, or a line
    # announcing work the run then failed to finish, is what this replaces.
    assert restored.returncode != 0, _say(restored)
    said = _say(restored)
    assert 'Traceback' not in said, said
    assert len(restored.stderr.strip().splitlines()) == 1, said
    assert restored.stdout == '', said
    refusal = restored.stderr.strip()
    # Which of the two post-publish refusals fired. The chmod one leaves
    # the mode unapplied and says so; this one leaves the entry behind,
    # and a line that cannot tell them apart is not one a reader can act
    # on.
    assert 'could not be removed' in refusal, refusal
    # A refusal that reads as a save to redo sends the operator back into
    # a store that already holds the copy.
    assert f'{target} was restored' in refusal, refusal
    assert 'still there' in refusal, refusal
    # Our own sentence, not a path substring: the OS error's rendering
    # carries the entry path too, so `str(entry) in refusal` is satisfied
    # by the reason clause and proves nothing about ours.
    assert f'the stored copy at {entry} could not be removed' in refusal, (
        refusal)
    # The recorded mode, pinned as a VALUE. `_publish` ends in
    # `os.replace`, so the target is the fresh temp inode at the umask
    # default - 0o644 here, which is also what this target records - and
    # this cannot tell whether `os.chmod` ran at all. Whether it ran is
    # `test_plant_restore.py`'s control, over a recorded 0o400.
    assert stat.S_IMODE(target.stat().st_mode) == int(
        (entry / 'mode').read_text().strip(), 8), 'the recorded mode'
    assert target.read_bytes() == _FIXED

    # `clear` reaches the same removal, so this is what makes one shared
    # guard a tested property rather than a claim - and it is why the
    # refusal above can be said to cover issue 1434 as well.
    cleared = _plant('clear', target, store, dropped)
    assert cleared.returncode != 0, _say(cleared)
    said = _say(cleared)
    assert 'Traceback' not in said, said
    assert len(cleared.stderr.strip().splitlines()) == 1, said
    # Nothing announced a discard that did not happen.
    assert cleared.stdout == '', said
    refusal = cleared.stderr.strip()
    assert 'could not be removed' in refusal, refusal
    # `clear` published nothing, so its line must not say it restored.
    assert f'{target} was restored' not in refusal, refusal
    assert 'still there' in refusal, refusal
    assert f'the stored copy at {entry} could not be removed' in refusal, (
        refusal)
    assert entry.is_dir()


def _an_environment_holding_no_git(directory):
    """This process's environment, with a PATH that has no `git` in it.

    A child launched with it cannot exec `git` at all, and that is a
    `FileNotFoundError` out of the launch rather than a non-zero exit.
    """
    without = Path(directory) / 'no-git'
    without.mkdir()
    environment = dict(os.environ)
    environment['PATH'] = str(without)
    return environment


def test_save_reports_unknown_when_git_cannot_be_launched(tmp):
    target = _committed_repo(tmp)
    target.write_bytes(_FIXED)
    real = _run_plant('save', str(target), '--store', str(Path(tmp) / 'a'))
    assert real.returncode == 0, _say(real)
    # The control: this path with the real PATH is dirty, so the
    # 'unknown' below is the PATH and not the path itself.
    assert _reported_state(real.stdout) == 'dirty', _say(real)

    blind = _run_plant('save', str(target), '--store', str(Path(tmp) / 'b'),
                       environment=_an_environment_holding_no_git(tmp))
    assert blind.returncode == 0, _say(blind)
    assert _reported_state(blind.stdout) == 'unknown', _say(blind)


def _a_git_that_answers_rev_parse_and_refuses_status(directory):
    """A `git` on PATH that answers one subcommand and fails the other.

    The log is what proves which of the two refusals the report came
    from: without it an `unknown` reads the same whether the work tree
    could not be entered or its status could not be read.
    """
    bin_dir = Path(directory) / 'bin'
    bin_dir.mkdir()
    log = Path(directory) / 'git.log'
    script = bin_dir / 'git'
    script.write_text(
        '#!/bin/sh\n'
        'case "$*" in\n'
        '*rev-parse*)\n'
        '  echo "rev-parse answered true" >> "$DAEDALUS_FAKE_GIT_LOG"\n'
        '  echo true; exit 0 ;;\n'
        '*status*)\n'
        '  echo "status refused" >> "$DAEDALUS_FAKE_GIT_LOG"\n'
        '  exit 1 ;;\n'
        'esac\n'
        'echo "unexpected call: $*" >> "$DAEDALUS_FAKE_GIT_LOG"\n'
        'exit 2\n', encoding='utf-8')
    script.chmod(0o755)
    environment = dict(os.environ)
    environment['PATH'] = str(bin_dir) + os.pathsep + environment['PATH']
    environment['DAEDALUS_FAKE_GIT_LOG'] = str(log)
    return environment, log


def test_save_reports_unknown_when_git_cannot_report_a_status(tmp):
    if sys.platform.startswith('win'):
        _util.skip('a POSIX shell script is what stands in for git here')
    target = _committed_repo(tmp)
    target.write_bytes(_FIXED)
    store = Path(tmp) / 'store'
    real = _run_plant('save', str(target), '--store', str(Path(tmp) / 'a'))
    assert real.returncode == 0, _say(real)
    assert _reported_state(real.stdout) == 'dirty', _say(real)

    environment, log = _a_git_that_answers_rev_parse_and_refuses_status(tmp)
    saved = _run_plant('save', str(target), '--store', str(store),
                       environment=environment)
    assert saved.returncode == 0, _say(saved)
    assert _reported_state(saved.stdout) == 'unknown', _say(saved)
    # Both calls, in that order, and the one that failed is the status:
    # an 'unknown' from a refused `rev-parse` is a different line, and
    # this is what tells the two apart.
    assert log.read_text(encoding='utf-8') == (
        'rev-parse answered true\nstatus refused\n'), log.read_text()


def _a_target_the_plain_user_may_replace_but_not_chmod(target, store):
    """Root's inode in a directory the plain user owns: `os.replace` needs
    the directory and lands, `os.chmod` needs the inode and is refused.
    One process over one static tree - nothing here races."""
    if os.name != 'posix':
        _util.skip('POSIX ownership is what refuses the chmod')
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        _util.skip('only root can hand an inode away')
    try:
        _open_the_entry(store, target)
    except OSError as why:
        _util.skip(f'cannot hand the tree to a plain user: {why!r}')
    # Root's again and owner-only: nobody holds any mode on it at all, so
    # the chmod has no path to success for the user running the publish.
    os.chown(target, 0, 0)
    os.chmod(target, 0o600)
    probe = ('import os, sys\n'
             'try:\n'
             '    os.chmod(sys.argv[1], 0o600)\n'
             'except PermissionError:\n'
             '    sys.exit(0 if os.access(sys.argv[2], os.W_OK) else 8)\n'
             'sys.exit(9)\n')
    out = _as_nobody([sys.executable, '-c', probe, str(target),
                      str(target.parent)])
    # Both halves, or a green proves one arrangement and not the other:
    # a refusal with an unwritable directory would fail the publish first.
    assert out.returncode == 0, (out.returncode, _say(out))


def test_a_target_it_may_not_chmod_is_restored_not_refused(tmp):
    target, store = _saved_then_planted(tmp)
    _a_target_the_plain_user_may_replace_but_not_chmod(target, store)

    restored = _plant('restore', target, store, True)
    # The arm's own reasoning is that a file we may not chmod is not
    # necessarily one we may not replace. A refusal here would be the
    # error about a flag standing where the report belongs.
    assert restored.returncode == 0, _say(restored)
    assert restored.stdout.startswith('restored '), restored.stdout
    assert target.read_bytes() == _FIXED


def _a_git_that_never_answers(directory):
    """A `git` on PATH that is launched and never comes back.

    `exec` so the timeout's kill lands on the sleeper rather than on a
    shell that has a child of its own. The log is what proves the fake
    was reached: a hang nobody entered proves nothing.
    """
    bin_dir = Path(directory) / 'bin'
    bin_dir.mkdir()
    log = Path(directory) / 'git.log'
    script = bin_dir / 'git'
    script.write_text(
        '#!/bin/sh\n'
        'echo "hung: $*" >> "$DAEDALUS_FAKE_GIT_LOG"\n'
        'exec sleep 30\n', encoding='utf-8')
    script.chmod(0o755)
    return bin_dir, log


def test_save_reports_unknown_when_git_never_answers(tmp):
    if sys.platform.startswith('win'):
        _util.skip('a POSIX shell script is what stands in for git here')
    target = _committed_repo(tmp)
    target.write_bytes(_FIXED)
    bin_dir, log = _a_git_that_never_answers(tmp)
    # In-process, because the arm is the timeout `subprocess.run` raises
    # and only this process sets the deadline: `GIT_TIMEOUT` is a
    # constant, and a suite that waits it out costs half a minute.
    plant = _util.load(PLANT, 'plant_hung_git')
    environment = {
        'PATH': f'{bin_dir}{os.pathsep}{os.environ["PATH"]}',
        'DAEDALUS_FAKE_GIT_LOG': str(log),
    }
    said = io.StringIO()
    with mock.patch.object(plant, 'GIT_TIMEOUT', 3), \
            mock.patch.dict(os.environ, environment), \
            contextlib.redirect_stdout(said):
        status = plant.save(str(target), str(Path(tmp) / 'store'))
    assert status == 0, said.getvalue()
    assert _reported_state(said.getvalue()) == 'unknown', said.getvalue()
    assert log.read_text(encoding='utf-8').startswith('hung: '), (
        said.getvalue())


def test_a_publish_that_cannot_clean_up_its_temp_reports_the_publish(tmp):
    # In-process, because only the code under test can be handed two
    # refusals at once: `os.replace` fails the publish and `os.unlink`
    # fails the cleanup that follows it, and the operator acts on the
    # first of the two. The cleanup's own refusal must not become the
    # one reported.
    plant = _util.load(PLANT, 'plant_publish_cleanup')
    target = Path(tmp) / 'target.py'
    target.write_bytes(_FIXED)
    store = Path(tmp) / 'store'
    with contextlib.redirect_stdout(io.StringIO()):
        assert plant.save(str(target), str(store)) == 0
    target.write_bytes(_PLANTED)

    publish_error = 'the publish was refused'
    cleanup_error = 'the temp could not be removed'
    cleaned = []

    def refusing_replace(*args, **kwargs):
        raise OSError(publish_error)

    def refusing_unlink(path, *args, **kwargs):
        cleaned.append(path)
        raise OSError(cleanup_error)

    said = io.StringIO()
    real_replace, real_unlink = os.replace, os.unlink
    try:
        os.replace = refusing_replace
        os.unlink = refusing_unlink
        with contextlib.redirect_stderr(said):
            status = plant.restore(str(target), str(store))
    finally:
        os.replace, os.unlink = real_replace, real_unlink
    assert status == 1, status
    refusal = said.getvalue()
    assert publish_error in refusal, refusal
    assert cleanup_error not in refusal, refusal
    # And the cleanup did run, once: a pass over a refusal that was
    # never attempted would satisfy the two assertions above.
    assert len(cleaned) == 1, cleaned


def test_clear_of_a_path_with_no_stored_copy_says_there_is_nothing(tmp):
    target = _committed_repo(tmp)
    store = Path(tmp) / 'store'
    refused = _run_plant('clear', str(target), '--store', str(store))
    assert refused.returncode != 0, _say(refused)
    said = _say(refused)
    assert 'Traceback' not in said, said
    # Nothing announced a discard that did not happen.
    assert refused.stdout == '', said
    assert f'no stored copy of {target}' in said, said
    assert 'nothing to clear' in said, said
    # And the refusal left nothing in the store for the next save.
    assert _run_plant('save', str(target), '--store',
                      str(store)).returncode == 0


def _entry_replaced_by_a_symlink(store):
    """The entry's own name, pointing at the directory it used to be."""
    entry = _only_entry(store)
    elsewhere = entry.parent / 'moved'
    shutil.move(str(entry), str(elsewhere))
    os.symlink(str(elsewhere), str(entry))
    return entry, elsewhere


def test_a_clear_whose_entry_is_a_symlink_says_it_is_still_there(tmp):
    if sys.platform.startswith('win'):
        _util.skip('creating a symlink needs a privilege Windows withholds')
    target, store = _saved_then_planted(tmp)
    entry, elsewhere = _entry_replaced_by_a_symlink(store)

    cleared = _run_plant('clear', str(target), '--store', str(store))
    assert cleared.returncode != 0, _say(cleared)
    said = _say(cleared)
    assert 'Traceback' not in said, said
    assert cleared.stdout == '', said
    refusal = cleared.stderr.strip()
    assert f'the stored copy at {entry} could not be removed' in refusal, (
        refusal)
    assert 'still there' in refusal, refusal
    # What the refusal protects: the directory behind the link is not
    # the entry, and a removal that followed the name would take it.
    assert entry.is_symlink(), entry
    assert (elsewhere / 'bytes').is_file(), elsewhere


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
