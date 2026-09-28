#!/usr/bin/env python3
"""Contracts for the plant helper's save/restore pair.

The failure they close is silent: a path-scoped VCS restore is a statement
about the whole path, so it also sets that path to a committed state, and
the uncommitted work it carried is gone with nothing in the output to say
so. These run the helper against a real repository carrying a real
uncommitted edit, because a fixture without one cannot tell the two
restores apart. One claim has no end-to-end route - a re-read that
refuses a mismatch needs a filesystem that lies - so it is controlled
against the parsed source instead, and mutation-proved both ways.
"""
import ast
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


def _restrictive_mode():
    """The mode these tests record: POSIX has an execute bit and Windows has
    only the read-only flag, so each platform is asked in its own terms."""
    return 0o400 if os.name == 'posix' else 0o600


def test_restore_returns_the_mode_it_recorded(tmp):
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.chmod(_restrictive_mode())
    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    target.chmod(0o600)
    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.read_bytes() == _COMMITTED
    recorded = stat.S_IMODE(target.stat().st_mode)
    if os.name == 'posix':
        assert recorded == 0o400, recorded
    else:
        assert not recorded & stat.S_IWRITE, recorded


def test_restore_returns_a_target_that_was_saved_read_only(tmp):
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        _util.skip('root bypasses the file mode bits, so this route cannot '
                   'fail here; it bites on any unprivileged run')
    target = _repo(tmp)
    store = Path(tmp) / 'store'
    target.chmod(0o444)
    saved = _plant('save', str(target), '--store', str(store))
    assert saved.returncode == 0, _say(saved)
    # Truncate-then-write cannot open this; os.replace can.
    restored = _plant('restore', str(target), '--store', str(store))
    assert restored.returncode == 0, _say(restored)
    assert target.read_bytes() == _COMMITTED
    # Windows refuses to delete a read-only file, and so does the runner's
    # cleanup, so give the tree its permissions back before this returns.
    target.chmod(0o600)


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


def _restore_function(source):
    tree = ast.parse(source)
    return next((node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == 'restore'), None)


def _is_the_target_readback(node):
    """The `with open(path, ...)` that reads the target back."""
    if not isinstance(node, ast.With):
        return False
    opened = node.items[0].context_expr
    return (isinstance(opened, ast.Call)
            and isinstance(opened.func, ast.Name)
            and opened.func.id == 'open'
            and getattr(opened.args[0], 'id', '') == 'path')


def _read_names(function):
    """The names a `with open(...)` block assigns - the handle binding is
    not them, so the bytes are named by an assignment inside the block."""
    names = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.With) or not node.items:
            continue
        opened = node.items[0].context_expr
        if not (isinstance(opened, ast.Call)
                and isinstance(opened.func, ast.Name)
                and opened.func.id == 'open'):
            continue
        for inner in ast.walk(node):
            # Assign carries `targets`; AugAssign and AnnAssign carry a
            # single `target`.
            targets = list(getattr(inner, 'targets', None) or [])
            single = getattr(inner, 'target', None)
            if isinstance(single, ast.Name):
                targets.append(single)
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _refuses_a_mismatch(source):
    """Whether restore compares two of its own reads and returns on it.
    Deliberately shape-sensitive: no run reaches the re-read's failure
    condition, so this is the only control that fails if it is deleted,
    and a refactor that reshapes the body should fail here loudly."""
    function = _restore_function(source)
    assert function is not None, 'plant.py has no restore()'
    reads = _read_names(function)
    for node in ast.walk(function):
        if not (isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)):
            continue
        test = node.test
        if not any(isinstance(op, ast.NotEq) for op in test.ops):
            continue
        if not (isinstance(test.left, ast.Name)
                and isinstance(test.comparators[0], ast.Name)):
            continue
        left, right = test.left.id, test.comparators[0].id
        if left == right or left not in reads or right not in reads:
            continue
        return any(isinstance(inner, ast.Return) for inner in node.body)
    return False


def _is_the_mismatch_guard(node):
    return (isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and any(isinstance(op, ast.NotEq) for op in node.test.ops))


def _prune(body, drop):
    """Drop the named elements from a statement list, recursing: the
    read-back and the guard sit inside the restore's `try`."""
    kept = []
    for node in body:
        if _is_the_target_readback(node) and 'readback' in drop:
            continue
        if _is_the_mismatch_guard(node) and 'guard' in drop:
            continue
        if _is_the_mismatch_guard(node) and 'return' in drop:
            node.body = [ast.Expr(ast.Constant('REMOVED BY MUTATION'))]
        for field in ('body', 'orelse', 'finalbody'):
            inner = getattr(node, field, None)
            if isinstance(inner, list) and inner and isinstance(inner[0],
                                                                ast.stmt):
                setattr(node, field, _prune(inner, drop))
        kept.append(node)
    return kept


def _weakened(source, drop):
    """Restore with one element of the re-read claim removed at a time.

    `drop` names what goes: `readback` the block that re-reads the
    target, `guard` the comparison and its refusal, `return` only the
    refusal. Done on the parsed tree, so a reformat does not hide it.
    """
    tree = ast.parse(source)
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and node.name == 'restore')
    function.body = _prune(function.body, drop)
    return ast.unparse(tree)


def test_restore_re_reads_what_it_wrote_and_refuses_a_mismatch(tmp):
    del tmp
    assert _refuses_a_mismatch(PLANT.read_text(encoding='utf-8'))


def test_the_mismatch_control_is_load_bearing(tmp):
    source = PLANT.read_text(encoding='utf-8')
    copied = Path(tmp) / 'plant.py'
    for label, drop in (('read-back and refusal', ('readback', 'guard')),
                        ('the refusal alone', ('return',)),
                        ('the read-back alone', ('readback',))):
        mutated = _weakened(source, drop)
        assert mutated != source, (label, 'the mutation changed nothing')
        copied.write_text(mutated, encoding='utf-8')
        assert not _refuses_a_mismatch(mutated), label


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='plantrestore_')


if __name__ == '__main__':
    raise SystemExit(main())
