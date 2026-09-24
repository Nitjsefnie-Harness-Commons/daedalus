#!/usr/bin/env python3
"""Unit contract for result slots and delivery-id retention."""
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _case_fold  # noqa: E402
import _util  # noqa: E402


_RESULT_STORE_PROBE = r'''
import json
import os
from pathlib import Path

from daedalus_bridge import result_store

slot = Path(os.environ['RESULT_SLOT'])
payload = {
    'id': 'unit-result',
    'result': {'value': 7},
    'resultGeneration': 'generation-1',
    'deliveryId': 'delivery-1',
}
result_store.atomic_result_write(
    slot, json.dumps(payload, ensure_ascii=False).encode('utf-8'))
peek, delivery = result_store.read_result_file(slot, False, '')
mismatch, mismatch_delivery = result_store.read_result_file(
    slot, True, 'generation-other')
mismatch_kept = slot.is_file()
consumed, consumed_delivery = result_store.read_result_file(
    slot, True, 'generation-1')
consumed_missing = not slot.exists()
before = result_store.delivery_recorded('delivery-1')
with result_store.result_lock:
    result_store.record_delivery('delivery-1')
after = result_store.delivery_recorded('delivery-1')
print(json.dumps({
    'peek': peek,
    'delivery': delivery,
    'mismatch': mismatch,
    'mismatch_delivery': mismatch_delivery,
    'mismatch_kept': mismatch_kept,
    'consumed': consumed,
    'consumed_delivery': consumed_delivery,
    'consumed_missing': consumed_missing,
    'recorded_before': before,
    'recorded_after': after,
}, sort_keys=True))
'''


_DELIVERY_PATH_PROBE = r'''
import contextlib
import io
import json
import os
from pathlib import Path

from daedalus_bridge import path_safety, result_store

res_dir = Path(os.environ['DAEDALUS_DIR']) / 'results'
delivery_root = result_store.delivery_root(res_dir)
key = result_store.result_key('tok', 'extension')
delivery_dir = delivery_root / key
delivery_file = delivery_dir / '123_1.json'
degraded_root = Path(os.environ['DAEDALUS_DIR']) / 'RESULT~1' / 'deliveries'
wrong_root = Path(os.environ['DAEDALUS_DIR']) / 'wrong' / 'deliveries'
realpath = path_safety.os.path.realpath


def call_with(answers):
    calls = []
    answers = iter(str(answer) for answer in answers)

    def resolving_stub(path):
        calls.append(os.fspath(path))
        return next(answers)

    path_safety.os.path.realpath = resolving_stub
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            paths = result_store.delivery_result_paths(
                res_dir, 'tok', 'extension', '123_1')
    finally:
        path_safety.os.path.realpath = realpath
    return paths, calls, output.getvalue()


paths, transient_calls, transient_log = call_with([
    delivery_root, delivery_dir,
    degraded_root, delivery_root,
    delivery_root, delivery_root,
    delivery_dir, delivery_file,
])
stable_calls = []
stable_log = io.StringIO()
answers = iter(str(answer) for answer in [
    delivery_root, delivery_dir,
    wrong_root, delivery_root,
    wrong_root, delivery_root,
])


def stable_stub(path):
    stable_calls.append(os.fspath(path))
    return next(answers)


path_safety.os.path.realpath = stable_stub
try:
    with contextlib.redirect_stdout(stable_log):
        try:
            result_store.delivery_result_paths(
                res_dir, 'tok', 'extension', '123_1')
        except ValueError:
            stable = 'refused'
        else:
            stable = 'allowed'
finally:
    path_safety.os.path.realpath = realpath

print('DELIVERY_PATH ' + json.dumps({
    'paths': [str(path) for path in paths],
    'transient_calls': transient_calls,
    'transient_log': transient_log,
    'stable': stable,
    'stable_calls': stable_calls,
    'stable_log': stable_log.getvalue(),
}))
'''


_UNCONFIGURED_PROBE = r'''
import json
import os
from pathlib import Path

from daedalus_bridge import result_store

root = Path(os.environ['THROWAWAY_ROOT'])
delivery_dir, delivery_file = result_store.delivery_result_paths(
    root, 'tok', 'extension', '123_1')
print('UNCONFIGURED ' + json.dumps({
    'daedalus_env': sorted(
        name for name in os.environ if name.startswith('DAEDALUS_')),
    'dir': str(delivery_dir),
    'file': str(delivery_file),
}))
'''


def test_the_alias_refusal_redacts_the_credential(tmp):
    """The alias guard's own refusal print carries the secret too.

    This is the one place a route's path refusal is printed without going
    through `under`, so threading the credential into `under` alone would
    still leave the token fully spelled in this line — in the derived
    target component and in the resolved attempts alike.
    """
    store = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_store.py',
        'fixture_alias_secret')
    root = Path(tmp) / 'results' / 'deliveries'
    try:
        (root / 'aliascredential_real').mkdir(parents=True)
        (root / 'aliascredential_ext').symlink_to(
            root / 'aliascredential_real', target_is_directory=True)
    except (OSError, NotImplementedError) as why:
        _util.skip(f'this filesystem will not hold a symlink: {why}')
    output = io.StringIO()
    refused = False
    with contextlib.redirect_stdout(output):
        try:
            store.delivery_result_paths(
                Path(tmp) / 'results', 'aliascredential', 'ext', '123_1')
        except ValueError:
            refused = True
    assert refused, output.getvalue()
    line = output.getvalue()
    assert 'aliascredential' not in line, line
    assert "parts=('aliascre…_ext',)" in line, line


def test_result_store_owns_atomic_slots_and_delivery_dedup(tmp):
    root = Path(tmp) / 'result-store-root'
    slot = root / 'results' / 'unit.json'
    slot.parent.mkdir(parents=True)
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(root),
        'DAEDALUS_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
        'RESULT_SLOT': str(slot),
    })
    proc = subprocess.run(
        [sys.executable, '-c', _RESULT_STORE_PROBE],
        cwd=_util.ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    answer = json.loads(proc.stdout)
    assert answer == {
        'peek': {
            'deliveryId': 'delivery-1',
            'id': 'unit-result',
            'result': {'value': 7},
            'resultGeneration': 'generation-1',
        },
        'delivery': '',
        'mismatch': {'consumed': False},
        'mismatch_delivery': '',
        'mismatch_kept': True,
        'consumed': {
            'consumed': True,
            'resultGeneration': 'generation-1',
        },
        'consumed_delivery': 'delivery-1',
        'consumed_missing': True,
        'recorded_before': False,
        'recorded_after': True,
    }, answer


def test_delivery_paths_use_the_retrying_parent_comparison(tmp):
    """The real delivery caller retries a degraded parent spelling."""
    docroot = Path(tmp) / 'docroot'
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(docroot),
        'DAEDALUS_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    proc = subprocess.run(
        [sys.executable, '-c', _DELIVERY_PATH_PROBE],
        cwd=_util.ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    marked = [line for line in proc.stdout.splitlines()
              if line.startswith('DELIVERY_PATH ')]
    assert len(marked) == 1, (proc.stdout, proc.stderr)
    answer = json.loads(marked[0][len('DELIVERY_PATH '):])
    delivery_root = docroot / 'results' / 'deliveries'
    delivery_dir = delivery_root / 'tok_extension'
    delivery_file = delivery_dir / '123_1.json'
    wrong_root = docroot / 'wrong' / 'deliveries'
    assert answer['paths'] == [str(delivery_dir), str(delivery_file)], answer
    assert answer['transient_calls'] == [
        str(delivery_root), str(delivery_dir),
        str(delivery_root), str(delivery_root),
        str(delivery_root), str(delivery_root),
        str(delivery_dir), str(delivery_file),
    ], answer
    assert answer['transient_log'] == '', answer
    assert answer['stable'] == 'refused', answer
    assert answer['stable_calls'] == [
        str(delivery_root), str(delivery_dir),
        str(delivery_root), str(delivery_root),
        str(delivery_root), str(delivery_root),
    ], answer
    stable_lines = answer['stable_log'].splitlines()
    assert len(stable_lines) == 1, answer
    assert stable_lines[0].startswith('[PATH-REFUSAL] kind=alias '), answer
    assert f'root={str(delivery_root)!r}' in stable_lines[0], answer
    assert "parts=('tok…_extension',)" in stable_lines[0], answer
    stable_attempts = (
        (str(wrong_root), str(delivery_root)),
        (str(wrong_root), str(delivery_root)),
    )
    assert f'attempts={stable_attempts!r}' in stable_lines[0], answer


def test_the_store_needs_no_bridge_configuration(tmp):
    """A results root is a parameter, so no `DAEDALUS_*` value is needed.

    Importing `daedalus_bridge.config` with those stripped raises
    `SystemExit`, so a store that still read it could not run here at all.
    """
    root = Path(tmp) / 'unconfigured' / 'results'
    root.mkdir(parents=True)
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    env.update({
        'PYTHONDONTWRITEBYTECODE': '1',
        'THROWAWAY_ROOT': str(root),
    })
    proc = subprocess.run(
        [sys.executable, '-c', _UNCONFIGURED_PROBE],
        cwd=_util.ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    marked = [line for line in proc.stdout.splitlines()
              if line.startswith('UNCONFIGURED ')]
    assert len(marked) == 1, (proc.stdout, proc.stderr)
    answer = json.loads(marked[0][len('UNCONFIGURED '):])
    assert answer['daedalus_env'] == [], answer
    # Resolved on both sides: `under` returns the path it checked,
    # and the runner happens to resolve the temp root it hands
    # over, so this does not depend on that staying true.
    expected_dir = os.path.realpath(root / 'deliveries' / 'tok_extension')
    assert answer['dir'] == expected_dir, (answer, expected_dir)
    expected_file = os.path.realpath(
        root / 'deliveries' / 'tok_extension' / '123_1.json')
    assert answer['file'] == expected_file, (answer, expected_file)


def _folded_and_exact(store, res_dir):
    """The two spellings' delivery paths, the way `paths` ordered them."""
    folded, _folded_file = store.delivery_result_paths(
        res_dir, 'foldtoken', 'foo', '123_1')
    exact, _exact_file = store.delivery_result_paths(
        res_dir, 'foldtoken', 'Foo', '123_1')
    return folded, exact


def test_a_folded_spelling_names_the_same_target(tmp):
    """A name the parent folds onto another spelling is that target.

    The directory exists under the spelling `Foo`, and the parent resolves
    `foo` to the same entry. `realpath` answers with the caller's spelling
    either way -- only `ntpath` canonicalises case -- so what makes them one
    target is the parent, and the only way to see that is to ask it.
    """
    store = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_store.py',
        'fixture_folded_target')
    res_dir = Path(tmp) / 'results'
    on_disk = res_dir / 'deliveries' / 'foldtoken_Foo'
    on_disk.mkdir(parents=True)
    with _case_fold.case_folding(res_dir) as root:
        folded, exact = _folded_and_exact(store, res_dir)
        one_entry = os.path.samefile(folded, exact)
        listed = sorted(p.name for p in (res_dir / 'deliveries').iterdir())
        # The two paths are spelled differently, which is what POSIX
        # `realpath` does here, and they are one entry, which is what the
        # parent does. Both answers are read inside the emulation, because
        # outside it the host's own filesystem is the one being asked.
        assert folded.name == 'foldtoken_foo', folded.name
        assert exact.name == 'foldtoken_Foo', exact.name
        assert one_entry, (folded, exact, root)
    assert listed == ['foldtoken_Foo'], listed


def test_on_a_case_sensitive_parent_the_two_spellings_are_two_targets(tmp):
    """The control: where the parent folds nothing, `Foo` and `foo` differ.

    Same fixture, no emulated parent, and the guard answers the same way for
    both -- it refuses an alias, and a target that does not exist is not an
    alias -- while the two names are two entries that the parent will keep
    apart. This is the half that makes the folded verdict above a property
    of the parent rather than of the assertion.
    """
    store = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_store.py',
        'fixture_case_sensitive_target')
    res_dir = Path(tmp) / 'results'
    (res_dir / 'deliveries' / 'foldtoken_Foo').mkdir(parents=True)
    folded, exact = _folded_and_exact(store, res_dir)
    assert folded.name == 'foldtoken_foo', folded.name
    assert exact.name == 'foldtoken_Foo', exact.name
    assert folded != exact, (folded, exact)
    # The parent holds one entry and no spelling of it, which is the whole
    # difference: the other spelling is not a name this parent resolves.
    assert not os.path.lexists(folded), folded
    assert sorted(p.name for p in (res_dir / 'deliveries').iterdir()) == [
        'foldtoken_Foo']


def _symlinked_alias(tmp, name):
    """One real directory and a symlink standing in for it, as the tree."""
    res_dir = Path(tmp) / 'results'
    real = res_dir / 'deliveries' / f'{name}_real'
    real.mkdir(parents=True)
    alias = res_dir / 'deliveries' / f'{name}_ext'
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError) as why:
        _util.skip(f'this filesystem will not hold a symlink: {why}')
    return res_dir, real, alias


def _refused_alias(store, res_dir, token, tab):
    """Whether the store refused, and what its one refusal line said."""
    output = io.StringIO()
    refused = False
    with contextlib.redirect_stdout(output):
        try:
            store.delivery_result_paths(res_dir, token, tab, '123_1')
        except ValueError:
            refused = True
    return refused, output.getvalue()


def test_a_folded_spelling_does_not_admit_a_symlinked_alias(tmp):
    """The accepted case must not become an accepted symlink.

    The refusal is the guard the folded case relaxes, so the alias it exists
    to catch is pinned under the relaxation too: `samefile` follows a
    symlink, and a name that stands in for a sibling directory is not the
    entry it names, whichever spelling the parent would resolve it to.
    """
    store = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_store.py',
        'fixture_folded_alias')
    res_dir, real, _alias = _symlinked_alias(tmp, 'aliascredential')
    with _case_fold.case_folding(res_dir):
        refused, output = _refused_alias(
            store, res_dir, 'aliascredential', 'ext')
    assert refused, output
    assert output.count('kind=alias') == 1, output
    assert not list(real.iterdir()), list(real.iterdir())


def test_a_symlinked_alias_is_refused_without_a_folding_parent(tmp):
    """The control: the same symlink, on a parent that folds nothing.

    The exclusion is not a consequence of the folded case, and this is the
    half of the pair that says so: identical fixture, no emulated parent,
    identical verdict.
    """
    store = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_store.py',
        'fixture_case_sensitive_alias')
    res_dir, real, _alias = _symlinked_alias(tmp, 'aliascredential')
    refused, output = _refused_alias(
        store, res_dir, 'aliascredential', 'ext')
    assert refused, output
    assert output.count('kind=alias') == 1, output
    assert not list(real.iterdir()), list(real.iterdir())


def test_the_guard_accepts_a_canonicalizing_resolver(tmp):
    """The relaxed branch, reached by injecting the Windows resolver.

    `realpath` is `posixpath.realpath` on every POSIX platform: it resolves
    symlinks and never learns an entry's on-disk case, so
    `delivery_dir.name` is the caller's spelling and the exact-case
    comparison passes by itself. On Windows `ntpath.realpath` answers
    through `_getfinalpathname` with the on-disk spelling, and that is the
    case the relaxation exists for, and no POSIX host can produce it: the
    resolver is injected here, over a parent that folds case, which is the
    pair a Windows or vfat parent is.

    What is asserted is the guard's verdict given that pair, not what
    POSIX's `realpath` does: the host's own answers are pinned by the two
    fixtures above, and the folding half is pinned against a real vfat in
    `test_result_routes`. The symlink half of this branch needs no injection
    — the host's resolver does return a different name for it.
    """
    store = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_store.py',
        'fixture_canonical_resolver')
    res_dir = Path(tmp) / 'results'
    on_disk = res_dir / 'deliveries' / 'canontok_Foo'
    on_disk.mkdir(parents=True)
    real_realpath = os.path.realpath
    stored = {}

    def canonical_realpath(path):
        """Answer with the on-disk spelling, as `ntpath.realpath` would."""
        resolved = real_realpath(path)
        name = os.path.basename(resolved)
        if name == 'canontok_foo':
            stored['injected'] = True
            return resolved[:-len('canontok_foo')] + 'canontok_Foo'
        return resolved

    output = io.StringIO()
    os.path.realpath = canonical_realpath
    try:
        with _case_fold.case_folding(res_dir), \
                contextlib.redirect_stdout(output):
            paths = store.delivery_result_paths(
                res_dir, 'canontok', 'foo', '123_1')
    except ValueError:
        accepted = False
    else:
        accepted = True
    finally:
        os.path.realpath = real_realpath
    assert stored.get('injected'), 'the resolver was never asked the case'
    assert accepted, output.getvalue()
    assert output.getvalue() == '', output.getvalue()
    assert paths[0].name == 'canontok_Foo', paths[0].name


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='result_store_')


if __name__ == '__main__':
    raise SystemExit(main())
