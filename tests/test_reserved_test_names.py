#!/usr/bin/env python3
"""The names a new tests module may not bind, derived and stated once.

Two controls decide that set and neither computes the other's half.
`test_helper_reimplementation.py` owns every name a module under
`tests/_*.py` defines, in Python and in JavaScript;
`test_wf_suite_boundaries.py` owns the module-level bindings of
`tests/_wffixtures.py` and nowhere else. A module colliding with a name
from the other half is caught only by that other control running, so the
set is discovered by collision rather than known in advance.

This suite derives the union from the recognisers those two controls
already use, states it in a generated artifact, and asserts against it.
The derivation is `tests/_reserved_names.py`, beside the recognisers it
composes; `scripts/ci/reserved_names.py` writes the artifact and this
suite fails when it drifts from a fresh derivation, naming the command
that fixes it. An entry is added or dropped by tightening, never by hand.
"""
import ast
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _reserved_names  # noqa: E402
import _util  # noqa: E402
from _helper_binds import definitions  # noqa: E402
from _helper_reimplementation import (  # noqa: E402
    _entry_points, _live_sources)
from _unconsolidated_js_names import (  # noqa: E402
    UNCONSOLIDATED_JS_NAMES)
from _unconsolidated_names import UNCONSOLIDATED_NAMES  # noqa: E402

ROOT = _util.ROOT
POLICY_SOURCE = ROOT / 'scripts' / 'ci' / 'reserved_names.py'
ARTIFACT = ROOT / '.github' / 'reserved-test-names.json'

# The residue table each limb's site would need a row in. This is the
# join the two guards cannot make: the workflow-fixture rule has no table
# at all, so a fixture name is excused by neither.
RESIDUE_TABLES = {
    _reserved_names.PYTHON: UNCONSOLIDATED_NAMES,
    _reserved_names.JAVASCRIPT: UNCONSOLIDATED_JS_NAMES,
}

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


def _planted_tree(modules):
    """A source map carrying the two owner modules, plus what is planted.

    A planted site is the liveness every absence assertion in this suite
    needs: a recogniser that stopped reading the tree reports nothing
    collides, and nothing collides is what a healthy tree looks like.
    The extras arrive as one mapping rather than unpacked, because a
    `**`-unpacked call is a launch the launch audit cannot place.
    """
    sources = {'tests/_owner.py': _OWNER,
               'tests/_wffixtures.py': _FIXTURES}
    sources.update(modules)
    return sources


def test_the_union_is_not_empty_and_every_limb_contributed(tmp):
    """A derivation that read nothing is empty, and an empty one is a
    green every absence assertion below would also have reported, so the
    set is checked for content and each limb is checked separately.
    """
    del tmp
    derived = _reserved_names.reserved()
    assert derived, 'the union derived no name at all'
    contributed = {limb for entry in derived.values() for limb in entry}
    assert contributed == set(_reserved_names.LIMBS), sorted(contributed)


def test_every_name_carries_sorted_owners_within_the_tests_tree(tmp):
    del tmp
    sources = _live_sources()
    derived = _reserved_names.reserved(sources)
    assert derived
    for name, entry in sorted(derived.items()):
        assert name, name
        for limb, owners in sorted(entry.items()):
            assert owners, (name, limb)
            assert list(owners) == sorted(owners), (name, limb, owners)
            assert len(set(owners)) == len(owners), (name, limb, owners)
    unknown = sorted({owner for entry in derived.values()
                      for owners in entry.values() for owner in owners
                      if owner not in sources})
    assert not unknown, (
        f'names owned by a module the tree does not carry: {unknown}')


def test_no_reserved_name_is_reimplemented_without_a_residue_row(tmp):
    """No reserved name is re-implemented without a row in its own table.

    WHAT THIS ADDS, MEASURED AND STATED. Against this tree its sites are
    set-equal to `reimplementations` union `js_reimplementations` -- a
    set of 192, and each difference in both directions empty -- and the
    reason is structural rather than incidental.
    `tests/_wffixtures.py` IS a `tests/_*.py` module, so the three
    fixture names that are definitions are already in the python limb,
    and the two that are not (`BLOCK_NEEDS`, `BLOCK_OUTPUTS`) are
    `Assign` binds, which `definitions` never reports. While the fixture
    module remains a shared helper, the union therefore adds nothing
    HERE.

    `len(residue_sites())` is 196, not 192, and both are worth knowing. The
    residue tables are keyed `(path, name)`, and three modules declare a
    reserved JavaScript name more than once, so the list carries four rows
    the set does not: `tests/_gm_harness.py::makeStorage` twice,
    `tests/test_gm_transfers.py::flushMessages` three times and
    `tests/test_tab_routing_js_operations.py::run` twice. The equality is
    about the set, because that is what the tables key on; the count is
    about the list, because that is what a refusal prints.

    It is kept rather than deleted for the day that stops being true: a
    fixture module that is not a shared helper puts its names in the
    union and nowhere else, and this is the statement that would notice.
    The fixture limb beside it is the half that is not a restatement
    today, and the single entry point both of them make is the other
    half of the issue's ask.
    """
    del tmp
    unallowed = sorted(
        f'{site.path}::{site.name} ({site.limb}) owned by {list(site.owners)}'
        for site in _reserved_names.residue_sites()
        if (site.path, site.name) not in RESIDUE_TABLES[site.limb])
    assert not unallowed, (
        'reserved names re-implemented with no row in the matching residue '
        'table:\n' + '\n'.join(unallowed))


def test_no_workflow_fixture_name_is_bound_outside_its_module(tmp):
    """The fixture limb has no residue table, so nothing excuses a shadow.

    Every one of them is read from `tests/_wffixtures.py` rather than from
    the names the boundary rule hardcodes, so a fixture added there is
    covered by this statement on the day it is added — and a bind the
    boundary rule's own walk cannot see is caught here, because `scan`
    reads the walrus, the `for` target and the `except ... as` too.
    """
    del tmp
    found = [f'{site.path}::{site.name}'
             for site in _reserved_names.fixture_sites()]
    assert not found, (
        'shared workflow fixtures bound outside tests/_wffixtures.py:\n'
        + '\n'.join(sorted(found)))


def test_a_planted_python_collision_is_reported(tmp):
    del tmp
    sources = _planted_tree({
        'tests/test_planted.py':
            'def _placed_helper(value):\n    return value\n',
        'tests/test_taken.py':
            'from _owner import _placed_helper\n'
            'def _placed_helper(value):\n    return value\n',
    })
    sites = _reserved_names.residue_sites(sources)
    reported = {(site.limb, site.path, site.name) for site in sites}
    assert (_reserved_names.PYTHON, 'tests/test_planted.py',
            '_placed_helper') in reported, sorted(reported)
    # The import limb still settles the name, so the second module is not
    # a site however it spells the binding.
    assert not [site for site in sites
                if site.path == 'tests/test_taken.py'], sorted(sites)


def test_a_planted_javascript_collision_is_reported(tmp):
    del tmp
    sources = _planted_tree({
        'tests/test_planted.py': (
            'HARNESS = r"""\n'
            'function placedStub(l) {\n'
            '  const seen = [];\n'
            '  seen.push(l);\n'
            '  return seen;\n'
            '}\n'
            '"""\n'
            'HARNESS = r"""\n'
            'function tiny(l) {\n'
            '  return l;\n'
            '}\n'
            '"""\n'),
    })
    reported = {(site.limb, site.path, site.name)
                for site in _reserved_names.residue_sites(sources)}
    assert (_reserved_names.JAVASCRIPT, 'tests/test_planted.py',
            'placedStub') in reported, sorted(reported)
    # A body under the floor is not a re-implementation, which is the one
    # place the JavaScript limb could be wider than the rule it states.
    assert not [name for _limb, _path, name in reported
                if name == 'tiny'], sorted(reported)


def test_a_planted_fixture_collision_is_reported(tmp):
    del tmp
    for name in ('NEEDED', '_placed', '_swapped', '_refusal'):
        sources = _planted_tree({
            f'tests/test_planted_{name.strip("_")}.py':
                f'def {name}(*args):\n    return None\n'})
        found = [(site.path, site.name)
                 for site in _reserved_names.fixture_sites(sources)]
        assert found == [(f'tests/test_planted_{name.strip("_")}.py', name)], (
            name, found)
    # The owner module itself is not a shadow of its own names.
    assert not _reserved_names.fixture_sites(_planted_tree({}))


def test_this_suite_binds_no_reserved_name(tmp):
    """Self-application: the suite is measured by the union it derives.

    A `tests/` module is bound by the same rules as any other, so a name
    this file binds that a `tests/_*.py` module already owns is a
    collision this suite created. The entry point and no other name is
    exempt, the guard the recogniser already applies.
    """
    del tmp
    path = 'tests/test_reserved_test_names.py'
    tree = ast.parse(Path(__file__).read_text(encoding='utf-8'), path)
    entry = _entry_points(tree)
    bound = {name for name in definitions(tree) if name not in entry}
    derived = _reserved_names.reserved()
    assert bound, 'the suite binds nothing, so it proves nothing'
    collisions = sorted(bound & set(derived))
    assert not collisions, (
        f'{path} binds names the reserved set owns: {collisions}')


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
    may bind, and nothing reads the artifact until a control does, so a
    hand-typed one is a rule nobody enforces.
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
    tree = _fixture_checkout(tmp, {
        'tests/_owner.py': _OWNER,
        'tests/_wffixtures.py': _FIXTURES,
        'tests/test_suite.py': 'def test_one():\n    pass\n',
    }, 'generated')
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
    tree = _fixture_checkout(tmp, {
        'tests/_owner.py': _OWNER,
        'tests/_wffixtures.py': _FIXTURES,
    }, 'noop')
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

    The three sibling ratchets publish through `thresholds.py`'s
    temp-plus-`os.replace`; this generator used `write_bytes`, so a
    `--tighten` killed between the open and the close left a committed
    document cut in half. The plant is a real destination and a real
    `main` call with the publish step refusing, so a pass is the shape
    agreeing with the runtime rather than a fixture agreeing with
    itself, and the temporary it would have left is checked for too.
    """
    policy = _contract()
    import thresholds  # the contract put scripts/ci on the path
    tree = _fixture_checkout(tmp, {
        'tests/_owner.py': _OWNER,
        'tests/_wffixtures.py': _FIXTURES,
    }, 'atomic')
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
    tree = _fixture_checkout(tmp, {
        'tests/_owner.py': _OWNER,
        'tests/_wffixtures.py': _FIXTURES,
    }, 'drift')
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
    tree = _fixture_checkout(tmp, {
        'tests/_owner.py': _OWNER,
        'tests/_wffixtures.py': _FIXTURES,
    }, 'absent')
    result = _run_generator(tree, tree / '.github' / 'absent.json')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr.startswith(
        'cannot read reserved names: '), result.stderr


def test_a_tree_carrying_the_script_alone_refuses_by_name(tmp):
    """The one refusal path the import direction creates, pinned.

    The script reaches out of its own directory for the derivation, so a
    checkout carrying the script and its tests but not
    `tests/_reserved_names.py` is a shape an operator produces by copying
    one file. An import at module scope would traceback before `main`
    could refuse, so the other four refusals this suite pins would have
    been the only ones that refused.
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

    The tuple catches `ImportError`, not only the absence of the module,
    so a `tests/_reserved_names.py` that raises on its own import is a
    one-line refusal naming the error rather than a traceback. That is
    the wider reading this pins as intended; a narrower one would be
    `ModuleNotFoundError`, and the two are told apart by whether the
    file is there at all.
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
    `main`'s own body, which a child process recording no coverage would
    otherwise leave dark.
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
    tree = _fixture_checkout(tmp, {
        'tests/_owner.py': _OWNER,
        'tests/_wffixtures.py': _FIXTURES,
    }, 'tracked')
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
