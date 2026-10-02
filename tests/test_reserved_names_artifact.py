#!/usr/bin/env python3
"""The generated reserved-name record and the generator that writes it.

`tests/test_reserved_test_names.py` derives the set and holds the three
checks on it. This suite holds the fourth: that the committed artifact is
what the tree derives, and that `--tighten` and the refusal paths behave
when they are asked for. The script under test is
`scripts/ci/reserved_names.py`; the derivation it composes is
`tests/_reserved_names.py`.

The owner-module fixtures are spelled out again here rather than
imported from the sibling suite: a suite that reaches a sibling suite's
body re-executes that whole module inside itself.
"""

# The owner-module fixtures are spelled out in both halves rather than
# shared. Giving the shared copy a `tests/_*.py` home makes it the OWNER
# of a reserved JavaScript name, which grows the reserved set for a
# synthetic string; these are plain data both suites derive against.
# pylint: disable=duplicate-code
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
POLICY_SOURCE = ROOT / 'scripts' / 'ci' / 'reserved_names.py'
ARTIFACT = ROOT / '.github' / 'reserved-test-names.json'


_OWNER = (
    'def _placed_helper(value):\n'
    '    return value\n'
    '\n'
    'HARNESS = r"""\n'
    'function placedStub(l) {\n'
    '  const seen = [];\n'
    '  seen.push(l);\n'
    '  return seen;\n'
    '}\n'
    '"""\n')

_FIXTURES = (
    'NEEDED = (\n'
    '    "    needs:\\n")\n'
    '\n'
    '\n'
    'def _placed(tmp, source, name="tests.yml"):\n'
    '    return source\n'
    '\n'
    '\n'
    'def _swapped(old, new, name="tests.yml"):\n'
    '    return old, new\n'
    '\n'
    '\n'
    'def _refusal(call, *args, contains=None):\n'
    '    return call(*args)\n')

# The two owner modules, the mapping both readers take; read-only to both.
_OWNER_TREE = {'tests/_owner.py': _OWNER,
               'tests/_wffixtures.py': _FIXTURES}


def _planted_tree(modules):
    """A source map carrying the two owner modules, plus what is planted.

    A planted site is the liveness every absence assertion here needs: a
    recogniser that stopped reading reports nothing collides, and nothing
    collides is what a healthy tree looks like. The extras arrive as one
    mapping rather than unpacked, because a `**`-unpacked call is a launch
    the launch audit cannot place.
    """
    sources = dict(_OWNER_TREE)
    sources.update(modules)
    return sources


def _contract():
    return _util.load(POLICY_SOURCE, 'reserved_contract')


def _fixture_checkout(tmp, files, name):
    """A git-indexed tree carrying `files`, for the real generator to read.

    A checkout rather than a directory, because the generator enumerates
    the TRACKED tree: an index populated by `git add` is what `git
    ls-files` reads, so no commit is made or needed.
    """
    tree = Path(tmp) / name
    for rel, text in files.items():
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    for argv in (['git', 'init', '-q'],
                 ['git', 'config', 'user.email', 'tests@example.invalid'],
                 ['git', 'config', 'user.name', 'Tests'],
                 ['git', 'add', '-A']):
        subprocess.run(argv, cwd=tree, check=True,
                       env=_util.child_coverage('scrub'))
    return tree


def _run_generator(tree, artifact, *args):
    return subprocess.run(
        [sys.executable, str(POLICY_SOURCE), *args,
         '--tree', str(tree), '--artifact', str(artifact)],
        cwd=str(tree), env=_util.child_coverage('scrub'),
        capture_output=True, text=True, timeout=180)


def test_the_committed_set_is_what_the_rules_derive(tmp):
    """The staleness gate: the committed form is generated, never typed.

    A name added to or dropped from a shared helper changes what a module
    may bind, and nothing reads the artifact until a control does.
    """
    del tmp
    policy = _contract()
    found = policy.violations(policy.load(), policy.document())
    detail = '\n'.join(f'{kind}: {rows}' for kind, rows in found.items()
                       if rows)
    assert not any(found.values()), (
        f'the committed reserved set is not what the tree derives:\n{detail}'
        f'\n{policy.STALE_REMEDY}')


def test_the_generator_writes_exactly_a_fresh_derivation_gives(tmp):
    """`--tighten` regenerates; it never edits what it finds.

    The committed bytes are the renderer's over a tree the generator read
    itself, so a hand edit in the file it is tightening cannot survive,
    and a missing one is created rather than refused.
    """
    policy = _contract()
    extra = {'tests/test_suite.py': 'def test_one():\n    pass\n'}
    tree = _fixture_checkout(tmp, {**_OWNER_TREE, **extra}, 'generated')
    artifact = tree / '.github' / 'reserved-test-names.json'
    result = _run_generator(tree, artifact, '--tighten')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == '', result.stderr
    expected = policy.render(policy.document(_live_sources_of(tree)))
    assert artifact.read_bytes() == expected
    assert str(len(policy.load(artifact)['names'])) in result.stdout

    # A hand edit is overwritten rather than kept: the file is an output.
    artifact.write_text('{"schema_version": 1, "names": {"made_up": {}}}\n',
                        encoding='utf-8')
    result = _run_generator(tree, artifact)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert 'STALE' not in result.stderr, result.stderr
    result = _run_generator(tree, artifact, '--tighten')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert artifact.read_bytes() == expected


def _live_sources_of(tree):
    listed = subprocess.run(
        ['git', '-C', str(tree), 'ls-files', 'tests/*.py'], check=True,
        capture_output=True, text=True,
        env=_util.child_coverage('scrub')).stdout.splitlines()
    return {name: (tree / name).read_text(encoding='utf-8')
            for name in listed}


def test_a_noop_tighten_writes_nothing(tmp):
    policy = _contract()
    tree = _fixture_checkout(tmp, _OWNER_TREE, 'noop')
    artifact = tree / '.github' / 'reserved-test-names.json'
    first = _run_generator(tree, artifact, '--tighten')
    assert first.returncode == 0, (first.stdout, first.stderr)
    untouched = artifact.stat()
    second = _run_generator(tree, artifact, '--tighten')
    assert second.returncode == 0, (second.stdout, second.stderr)
    assert second.stdout == 'the reserved set is already current\n'
    assert second.stderr == ''
    after = artifact.stat()
    assert after.st_ino == untouched.st_ino
    assert after.st_mtime_ns == untouched.st_mtime_ns
    # And the check is green on what tightening just wrote.
    checked = _run_generator(tree, artifact)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)
    assert checked.stderr == '', checked.stderr
    assert len(policy.violations(policy.load(artifact),
                                 policy.document(_live_sources_of(tree)))) == 3


def test_a_tighten_that_cannot_publish_leaves_the_committed_set(tmp):
    """The artifact is replaced or left whole, never truncated in place.

    The sibling ratchets publish through `thresholds.py`'s
    temp-plus-`os.replace` and this generator used `write_bytes`, so a
    `--tighten` killed between the open and the close left a committed
    document cut in half. The plant is a real destination and a real
    `main` call, not a fixture agreeing with itself.
    """
    policy = _contract()
    import thresholds  # the contract put scripts/ci on the path
    tree = _fixture_checkout(tmp, _OWNER_TREE, 'atomic')
    target = Path(tmp) / 'reserved.json'
    modes = ['--tree', str(tree), '--artifact', str(target)]
    # Drifted, so `--tighten` takes the write path rather than reporting
    # the set already current and returning before any of this.
    target.write_text('{"schema_version": 1, "names": {"made_up": {}}}\n',
                      encoding='utf-8')
    stale = target.read_bytes()
    with mock.patch.object(thresholds.os, 'replace',
                           side_effect=OSError('publish refused')):
        status, stdout, stderr = _generator(policy, ['--tighten', *modes])
    assert status == 1, (stdout, stderr)
    assert stderr.strip() == 'publish refused', stderr
    assert target.read_bytes() == stale
    assert not list(target.parent.glob(f'.{target.name}.*')), sorted(
        target.parent.iterdir())


def test_the_check_refuses_each_drift_kind_and_names_the_command(tmp):
    policy = _contract()
    tree = _fixture_checkout(tmp, _OWNER_TREE, 'drift')
    artifact = tree / '.github' / 'reserved-test-names.json'
    assert _run_generator(tree, artifact, '--tighten').returncode == 0
    derived = policy.load(artifact)
    planted = {'schema_version': derived['schema_version'], 'names': {}}
    for name, entry in derived['names'].items():
        planted['names'][name] = entry
    dropped = sorted(planted['names'])[0]
    del planted['names'][dropped]
    moved = sorted(planted['names'])[0]
    planted['names'][moved] = {limb: ['tests/test_planted.py']
                               for limb in planted['names'][moved]}
    planted['names']['invented_name'] = {'python': ['tests/_owner.py']}
    artifact.write_text(json.dumps(planted) + '\n', encoding='utf-8')
    result = _run_generator(tree, artifact)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == '', result.stdout
    # Three kinds, each its own line: a name the tree derives and the
    # committed set lacks, one it no longer derives, and one whose owners
    # moved -- which is what a renamed module or a second owner looks like.
    assert f'absent: [{dropped!r}]' in result.stderr, result.stderr
    assert "stale: ['invented_name']" in result.stderr, result.stderr
    assert f'owners: [{moved!r}]' in result.stderr, result.stderr
    assert result.stderr.rstrip().endswith(policy.STALE_REMEDY), result.stderr
    assert 'Traceback' not in result.stderr


def test_a_document_that_is_not_the_generated_form_is_refused_by_name(tmp):
    policy = _contract()
    failures = (
        ('{', 'invalid reserved names JSON'),
        ('[]', 'reserved names must be an object'),
        ('{"schema_version": 2, "names": {}}', 'unsupported schema_version'),
        ('{"schema_version": 1}', 'missing field: names'),
        ('{"schema_version": 1, "names": [], "extra": 1}', 'unknown field'),
        ('{"schema_version": 1, "names": []}', 'names must be an object'),
        ('{"schema_version": 1, "names": {"": ["x"]}}',
         'a name must be a nonempty string'),
        ('{"schema_version": 1, "names": {"a": []}}',
         'a name must map to an object of limbs'),
        ('{"schema_version": 1, "names": {"a": {"python": "x"}}}',
         'owners must be module paths'),
        ('{"schema_version": 1, "names": {"a": {"cobol": ["x"]}}}',
         'unknown limb: cobol'),
        ('{"schema_version": 1, "names": {"a": {"python": [1]}}}',
         'owners must be module paths'),
    )
    for text, marker in failures:
        path = Path(tmp) / 'reserved.json'
        path.write_text(text, encoding='utf-8')
        try:
            policy.load(path)
        except ValueError as error:
            assert marker in str(error), (text, error)
        else:
            raise AssertionError(f'the reader accepted {text!r}')
    result = _run_generator(Path(tmp), Path(tmp) / 'absent.json')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert 'Traceback' not in result.stderr
    # A tree that is not a checkout, and a committed set that is not
    # there: both refuse with one line rather than a traceback.
    tree = _fixture_checkout(tmp, _OWNER_TREE, 'absent')
    result = _run_generator(tree, tree / '.github' / 'absent.json')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr.startswith(
        'cannot read reserved names: '), result.stderr


def test_a_tree_carrying_the_script_alone_refuses_by_name(tmp):
    """The one refusal path the import direction creates, pinned.

    The script reaches into its own directory for the derivation, so a
    checkout carrying the script and its tests but not
    `tests/_reserved_names.py` is a shape an operator makes by copying one
    file. A module-scope import would traceback before `main` could refuse,
    leaving the other four refusals here the only ones refusing.
    """
    bare = _fixture_checkout(tmp, {
        'scripts/ci/reserved_names.py': POLICY_SOURCE.read_text(
            encoding='utf-8'),
        'tests/_owner.py': _OWNER,
        'tests/_wffixtures.py': _FIXTURES,
    }, 'bare')
    result = subprocess.run(
        [sys.executable, 'scripts/ci/reserved_names.py'], cwd=bare,
        env=_util.child_coverage('scrub'), capture_output=True, text=True,
        timeout=180)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == '', result.stdout
    assert result.stderr.strip() == (
        "No module named '_reserved_names'"), result.stderr


def test_a_derivation_that_raises_on_import_refuses_by_name(tmp):
    """Which `ImportError` the refusal tuple means, stated rather than left.

    The tuple catches `ImportError`, not only the absent module, so a
    `tests/_reserved_names.py` raising on its own import is a one-line
    refusal naming the error rather than a traceback. That is the wider
    reading this pins; a narrower one would be `ModuleNotFoundError`, and
    the two differ by whether the file is there at all.
    """
    broken = _fixture_checkout(tmp, {
        'scripts/ci/reserved_names.py': POLICY_SOURCE.read_text(
            encoding='utf-8'),
        'tests/_reserved_names.py': "raise ImportError('inner fault')\n",
        'tests/_owner.py': _OWNER,
        'tests/_wffixtures.py': _FIXTURES,
    }, 'broken')
    result = subprocess.run(
        [sys.executable, 'scripts/ci/reserved_names.py'], cwd=broken,
        env=_util.child_coverage('scrub'), capture_output=True, text=True,
        timeout=180)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == '', result.stdout
    assert result.stderr.strip() == 'inner fault', result.stderr
    assert 'Traceback' not in result.stderr


def _generator(policy, argv):
    """(status, stdout, stderr) for one `main` run, captured in-process.

    The real-CLI tests below prove the shipped command; this reaches
    `main`'s own body, which a coverage-recording child leaves dark.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = policy.main(argv)
    return status, out.getvalue(), err.getvalue()


def test_main_reports_a_matching_set_then_refuses_and_tightens_it(tmp):
    policy = _contract()
    # A planted tree the generator reads for itself, so `main` is reached
    # without a reader swapped underneath it.
    tree = _fixture_checkout(tmp, _planted_tree({}), 'modes')
    target = Path(tmp) / 'reserved.json'
    modes = ['--tree', str(tree), '--artifact', str(target)]
    fresh = policy.render(policy.document(_live_sources_of(tree)))
    count = len(policy.document(_live_sources_of(tree))['names'])
    target.write_bytes(fresh)
    assert _generator(policy, modes) == (
        0, f'{count} reserved names match the committed set\n', '')

    drifted = json.loads(fresh.decode('utf-8'))
    del drifted['names'][sorted(drifted['names'])[0]]
    target.write_text(json.dumps(drifted), encoding='utf-8')
    status, stdout, stderr = _generator(policy, modes)
    assert status == 1
    assert stdout == ''
    assert stderr.endswith(policy.STALE_REMEDY + '\n'), stderr
    assert 'absent: [' in stderr, stderr

    status, stdout, stderr = _generator(policy, ['--tighten', *modes])
    assert (status, stderr) == (0, ''), stderr
    assert f'tightened the reserved set: {count} names' in stdout
    assert target.read_bytes() == fresh
    assert _generator(policy, ['--tighten', *modes]) == (
        0, 'the reserved set is already current\n', '')
    # A committed set that is not there is one line, not a traceback.
    status, stdout, stderr = _generator(
        policy, [*modes[:-1], str(Path(tmp) / 'absent.json')])
    assert (status, stdout) == (1, ''), (status, stdout)
    assert stderr.startswith('cannot read reserved names: '), stderr


def test_the_generator_reads_the_tracked_tree_it_is_pointed_at(tmp):
    """`--tree` names the checkout, and a file it does not track is not
    read: a scratch module left in the working tree by another run is
    not part of the set the committed document states.
    """
    policy = _contract()
    tree = _fixture_checkout(tmp, _OWNER_TREE, 'tracked')
    (tree / 'tests' / 'test_scratch.py').write_text(
        'def test_scratch():\n    pass\n', encoding='utf-8')
    derived = policy.document(policy.tracked_sources(tree))
    assert sorted(policy.tracked_sources(tree)) == [
        'tests/_owner.py', 'tests/_wffixtures.py']
    assert sorted(derived['names']) == [
        'NEEDED', '_placed', '_placed_helper', '_refusal', '_swapped',
        'placedStub'], sorted(derived['names'])
    bare = Path(tmp) / 'bare'
    bare.mkdir()
    status, _stdout, stderr = _generator(
        policy, ['--tree', str(bare), '--artifact', str(tree / 'none.json')])
    assert status == 1, stderr
    assert 'Traceback' not in stderr
    # A checkout carrying no tests module is a refusal, not an empty set:
    # a derivation that read nothing derives nothing.
    empty = _fixture_checkout(tmp, {'README.md': 'nothing\n'}, 'empty')
    status, _stdout, stderr = _generator(
        policy, ['--tree', str(empty), '--artifact', str(empty / 'none.json')])
    assert status == 1, stderr
    assert stderr.startswith('git ls-files named no tests module under '), (
        stderr)
    assert 'Traceback' not in stderr


def test_the_script_docstring_carries_the_printed_remedy(tmp):
    del tmp
    policy = _contract()
    # Flattened, because the docstring wraps the remedy to 79 columns and
    # the remedy is one line: a reader meets the same sentence either way.
    doc = ' '.join((policy.__doc__ or '').split())
    for phrase in ('never edited by hand', '--tighten',
                   'tests/test_reserved_test_names.py',
                   '.github/reserved-test-names.json'):
        assert phrase in doc, phrase
    assert ' '.join(policy.STALE_REMEDY.split()) in doc, policy.STALE_REMEDY
    assert sorted(policy.violations({'names': {}}, {'names': {}})) == [
        'absent', 'owners', 'stale']


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='reservednames_')


if __name__ == '__main__':
    raise SystemExit(main())
