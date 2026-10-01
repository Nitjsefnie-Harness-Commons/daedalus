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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ratchet_fixture import _git  # noqa: E402

ROOT = _util.ROOT
PLANT = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'plant.py'

_COMMITTED = b'VALUE = 1\n'
_FIXED = b'VALUE = 2  # the uncommitted fix, never staged\n'
_PLANTED = b'raise RuntimeError("the defect the guard exists to catch")\n'


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


def _reported_state(output):
    """The state word the helper reported, not a word in its paths: the
    suite names each temp dir after its test function, so the path a test
    about a dirty target prints is full of that word anyway."""
    return output.rsplit(': ', 1)[-1].split(' against')[0].strip()


def _only_entry(store):
    entries = [item for item in Path(store).iterdir() if item.is_dir()]
    assert len(entries) == 1, entries
    return entries[0]


def _drop_to_nobody():
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)


def _as_nobody(command):
    """Run `command` unprivileged, so the file mode bits bite - root
    bypasses them, which is why this route was enforced nowhere on a root
    runner. An arrangement that cannot drop privileges skips with the
    reason rather than erroring: a control that manufactures a red on
    correct code is the same defect as one that passes on broken code.
    """
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=60, preexec_fn=_drop_to_nobody,
                              env=_util.child_coverage('scrub'))
    except (OSError, subprocess.SubprocessError) as why:
        _util.skip(f'the privilege drop is unavailable here: {why!r}')


def _hand_to_nobody(path):
    # 0o700: the chown makes the child the OWNER, so owner bits are all
    # it needs and group and other are nobody.
    os.chown(path, 65534, 65534)
    os.chmod(path, 0o700)


def _open_the_entry(store, target):
    """Hand the child every path it walks to publish: a suite's temporary
    root is 0700, and without the traverse bit the child reads a refusal
    where the route should have run."""
    entry = _only_entry(store)
    for directory in (target.parent, store, entry):
        for ancestor in (directory, *directory.parents):
            os.chmod(ancestor, os.stat(ancestor).st_mode | 0o005)
    for owned in (target.parent.parent, target.parent, store, entry,
                  *entry.iterdir()):
        _hand_to_nobody(owned)

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
    (_only_entry(store) / 'head-state').write_text(f'{value}\n',
                                                  encoding='utf-8')


def _drop_the_state_field(store):
    (_only_entry(store) / 'head-state').unlink(missing_ok=True)


def _saved_then_planted(tmp, payload=_FIXED):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(payload)
    assert _plant('save', str(target), '--store', str(store)).returncode == 0
    target.write_bytes(_PLANTED)
    return target, store


def test_a_dirty_snapshot_is_restored_with_the_state_it_was_taken_in(tmp):
    target, store = _saved_then_planted(tmp)

    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'dirty', clauses
    assert _NOT_HEADS in clauses, clauses
    assert _change_in(clauses) == 'changed', clauses
    assert target.read_bytes() == _FIXED


def test_a_clean_snapshot_is_not_reported_as_the_worktrees(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    assert _reported_state(saved.stdout) == 'clean', _say(saved)
    target.write_bytes(_PLANTED)

    restored = _plant('restore', str(target), '--store', str(store))
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

    restored = _plant('restore', str(target), '--store', str(store))
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

    restored = _plant('restore', str(target), '--store', str(store))
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

    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'unknown', clauses
    assert _NOT_HEADS not in clauses, clauses
    assert target.read_bytes() == _FIXED


def test_a_state_the_helper_will_not_vouch_for_restores_as_unknown(tmp):
    target, store = _saved_then_planted(tmp)
    _write_state(store, 'clean-ish')

    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'unknown', clauses
    assert _NOT_HEADS not in clauses, clauses


def test_a_state_field_holding_non_ascii_is_read_not_raised_over(tmp):
    target, store = _saved_then_planted(tmp)
    (_only_entry(store) / 'head-state').write_bytes(b'\xff\xfe dirty\n')

    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _state_in(clauses) == 'unknown', clauses


def test_a_target_that_does_not_exist_counted_as_holding_nothing(tmp):
    target, store = _saved_then_planted(tmp)
    os.unlink(target)

    restored = _plant('restore', str(target), '--store', str(store))
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
            _hand_to_nobody(target)
        except OSError as why:
            _util.skip(f'cannot hand the tree to a plain user: {why!r}')
    target.chmod(0o200)
    _assert_the_read_is_refused(target, dropped)
    if dropped:
        return _as_nobody([sys.executable, str(PLANT), 'restore',
                           str(target), '--store', str(store)])
    return _plant('restore', str(target), '--store', str(store))


def test_a_target_nothing_can_read_is_reported_as_uncompared(tmp):
    target, store = _saved_then_planted(tmp)

    restored = _restore_over_a_target_nothing_can_read(target, store)
    assert restored.returncode == 0, _say(restored)
    clauses = _restore_clauses(restored.stdout)
    assert _change_in(clauses) == 'uncompared', clauses
    target.chmod(0o600)
    assert target.read_bytes() == _FIXED


def test_the_save_line_is_exactly_the_shape_the_suite_parses(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.write_bytes(_FIXED)
    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    # The whole line, not one separator's absence: a clause appended
    # with a comma evades a `'; '` pin and the positional parse alike.
    assert saved.stdout.strip() == (
        f'saved {os.path.abspath(target)}: dirty against HEAD, '
        f'{len(_FIXED)} bytes in {_only_entry(store)}'), saved.stdout


def test_clear_of_an_entry_without_the_field_still_names_its_others(tmp):
    target, store = _saved_then_planted(tmp)
    _drop_the_state_field(store)

    cleared = _plant('clear', str(target), '--store', str(store))
    assert cleared.returncode == 0, _say(cleared)
    labels = [label for label, _ in _entry_fields(cleared.stdout)]
    assert labels == ['path', 'saved'], labels


def test_clear_reports_an_unvouched_state_the_way_restore_does(tmp):
    target, store = _saved_then_planted(tmp)
    _write_state(store, 'clean-ish')

    cleared = _plant('clear', str(target), '--store', str(store))
    assert cleared.returncode == 0, _say(cleared)
    fields = _entry_fields(cleared.stdout)
    assert ['captured', 'unknown'] in fields, _say(cleared)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='plantreport_')


if __name__ == '__main__':
    raise SystemExit(main())