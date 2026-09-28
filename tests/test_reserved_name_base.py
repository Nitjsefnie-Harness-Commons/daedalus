#!/usr/bin/env python3
"""The base a committed reserved set is checked against, and what composes.

`.github/reserved-test-names.json` is an EXHAUSTIVE artifact: it names
every reserved name in the tracked tests tree. An exhaustive artifact
does not compose across independent branches, and that is issue 1266.
Two branches that each add a shared-helper module derive against a base
that predates the other, so each document is exact for what its own run
saw; the MERGE is the first place the equality fails, on `main`, with no
branch left to fix it. Neither branch is wrong, and a merge git is happy
with is what reaches main unnoticed.

The first attempt at a fix scoped the verdict to a `modules` list the
DOCUMENT itself carried, and two independent reviewers killed it:

  - the scoping did not fix the mechanism. `absent` was scoped on the
    new binders and forgave an uncovered module, while `owners` was
    scoped on the old recorded owners and did not. Two rules, scoped in
    opposite directions, so two new modules binding a name in COMMON
    still went red on a clean merge.
  - the guard could be switched off with a five-line edit, because the
    scope was controlled by the thing it scoped: an emptied artifact was
    never asked about any name.
  - the structural check added to close the resulting hole did not close
    it, because a phantom entry paired with a `modules` line naming the
    same non-existent path silenced every kind at once.

The replacement takes the base OUT of the artifact and reads it from
git, and makes the whole scoping ONE predicate: a discrepancy is a
violation iff EVERY module involved in it -- recorded OR derived, old OR
new -- is in `covered`, the tests modules present in the tree of the
commit that last wrote the artifact. One predicate, one direction, and
nothing the artifact says can widen or narrow it.

This module is self-contained on purpose. A suite that imports a
sibling re-executes that sibling's body and reads a private helper
through it, which `test_suite_import_boundaries.py` reports; the fixtures
here are branches, merges and rebases, and none of that machinery is
worth sharing with the derivation suite beside it.
"""
import json
import subprocess
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

POLICY_SOURCE = _util.ROOT / 'scripts' / 'ci' / 'reserved_names.py'
ARTIFACT = '.github/reserved-test-names.json'

# The base tree. `_owner.py` carries a python and a javascript name and
# `_wffixtures.py` the five fixture binds, so the derivation is not empty
# and every limb has contributed before anything is branched.
_BASE_FILES = {
    'tests/_owner.py': ('def _placed_helper(value):\n    return value\n'
                        '\n'
                        'HARNESS = r"""\n'
                        'function placedStub(l) {\n'
                        '  const seen = [];\n'
                        '  seen.push(l);\n'
                        '  return seen;\n'
                        '}\n'
                        '"""\n'),
    'tests/_wffixtures.py': ('NEEDED = (\n'
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
                             '    return call(*args)\n'),
    'tests/test_suite.py': 'def test_one():\n    pass\n',
}

_MODULE = 'tests/_{}.py'


def _policy():
    return _util.load(POLICY_SOURCE, 'reserved_base_contract')


def _branch_git(tree, *argv, check=True, env=None):
    return subprocess.run(
        ['git', '-C', str(tree), *argv], check=check, capture_output=True,
        text=True, env=env or _util.child_coverage('scrub'))


def _commit_all(tree, message):
    _branch_git(tree, 'add', '-A')
    _branch_git(tree, 'commit', '-q', '-m', message, '--allow-empty')


def _checkout(root, name, files):
    """A committed git checkout carrying `files`.

    Committed, not merely indexed: the base this control reads is a
    COMMIT, so a tree whose files are only staged has no base at all.
    """
    tree = Path(root) / name
    for rel, text in files.items():
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    _branch_git(tree, 'init', '-q', '-b', 'main')
    _branch_git(tree, 'config', 'user.email', 'tests@example.invalid')
    _branch_git(tree, 'config', 'user.name', 'Tests')
    _commit_all(tree, 'base')
    return tree


def _artifact(tree):
    return Path(tree) / ARTIFACT


def _branch_check(tree, *args):
    """The shipped command, over `tree` and its own committed set."""
    return subprocess.run(
        [sys.executable, str(POLICY_SOURCE), *args,
         '--tree', str(tree), '--artifact', str(_artifact(tree))],
        # No bound of its own: the suite runner bounds it, and a hang
        # surfacing as the enclosing bound is a better failure than a
        # margin on a loaded runner.
        cwd=str(tree), env=_util.child_coverage('scrub'),
        capture_output=True, text=True)


def _tighten(tree):
    result = _branch_check(tree, '--tighten')
    assert result.returncode == 0, (result.stdout, result.stderr)


def _unresolved(tree):
    return _branch_git(tree, 'ls-files', '-u', check=False).stdout.strip()


def _union_entry(one, two):
    """Two records of the same name resolved: the union of their owners.

    A name both documents record is a name both modules bind, so the
    answer is both owners rather than one of them. Picking a side is the
    judgement call the resolve is supposed to contain none of, and it is
    the one that leaves the merged document disagreeing with the merged
    tree. A limb or a name only one side has is carried through.
    """
    return {limb: sorted(set(one.get(limb, ())) | set(two.get(limb, ())))
            for limb in sorted(set(one) | set(two))}


def _union(left, right):
    """Two artifacts resolved by union -- the mechanical resolve.

    Every branch that regenerated wrote one entry per line, so a
    conflicted resolve has exactly one sensible answer and no judgement
    in it: the union. The sweep records that it was needed rather than
    hiding it.
    """
    merged = {}
    for key in sorted(set(left) | set(right)):
        one, two = left.get(key), right.get(key)
        if isinstance(one, dict) and isinstance(two, dict):
            merged[key] = {name: _union_entry(one.get(name, {}),
                                              two.get(name, {}))
                           for name in sorted(set(one) | set(two))}
        elif isinstance(one, list) and isinstance(two, list):
            merged[key] = sorted(set(one) | set(two))
        else:
            merged[key] = one if two is None else two
    return merged


def _show(tree, ref):
    """The artifact as `ref` has it, or None when `ref` does not exist."""
    result = _branch_git(tree, 'show', f'{ref}:{ARTIFACT}', check=False)
    return result.stdout if result.returncode == 0 else None


def _resolve_union(tree, ours, incoming, continue_with):
    """Take the union of ours and the incoming artifact, then continue.

    Both sides are read out of git rather than off disk: after a
    conflicted merge the working file carries conflict markers, so it is
    the one file in the tree that is not the artifact any more.
    """
    merged = _union(json.loads(ours), json.loads(incoming))
    _artifact(tree).write_text(json.dumps(merged) + '\n', encoding='utf-8')
    _branch_git(tree, 'add', '-A')
    _branch_git(tree, '-c', 'core.editor=true', *continue_with, check=False)


def _land(tree, shas, by_rebase):
    """Land `shas` in the given order; return whether any conflicted.

    `by_rebase` replays each commit with `cherry-pick`, which is what a
    rebase merge does to it: the commit lands with a NEW SHA, so the
    last writer on main is one the branch never had. The other mode
    merges the commit as pushed. Both are on the sweep because this
    repository lands by rebase, and a control that has only ever seen a
    branch-local last writer has not been tested against how changes
    actually arrive.
    """
    conflicted = False
    for sha in shas:
        if by_rebase:
            if _branch_git(tree, 'cherry-pick', sha, check=False).returncode:
                conflicted = True
                _resolve_union(tree, _show(tree, 'HEAD'),
                               _show(tree, 'CHERRY_PICK_HEAD'),
                               ['cherry-pick', '--continue'])
        elif _branch_git(tree, 'merge', '-q', '--no-ff', '--no-edit',
                         sha, check=False).returncode:
            conflicted = True
            _resolve_union(tree, _show(tree, 'HEAD'),
                           _show(tree, 'MERGE_HEAD'),
                           ['commit', '-q', '--no-edit'])
    return conflicted


def _world(root, name, *, a_name, b_name, a_regen, b_regen):
    """A base tree with two branches, each one shared-helper module.

    `a_name`/`b_name` are what the two modules bind. Equal names are the
    overlap that killed the first attempt; different names are the
    disjoint case the old algebra covered, kept as the control. The
    branch SHAs are returned, because a rebase merge lands the REPLAYED
    commit rather than the one a branch pushed.
    """
    tree = _checkout(root, name, _BASE_FILES)
    _branch_git(tree, 'add', '-A')
    _tighten(tree)
    _commit_all(tree, 'the committed set')
    shas = {}
    for branch, stem, bound in (('a', 'alpha', a_name),
                                ('b', 'beta', b_name)):
        _branch_git(tree, 'checkout', '-q', '-b', branch, 'main')
        (tree / _MODULE.format(stem)).write_text(
            f'def {bound}(value):\n    return value\n', encoding='utf-8')
        # Staged BEFORE the tightening, because the generator enumerates
        # the TRACKED tree: an untracked module is not a name the set
        # derives, and a branch that "regenerated" without it would make
        # every cell of the sweep agree for the wrong reason.
        _branch_git(tree, 'add', '-A')
        if (a_regen if branch == 'a' else b_regen):
            _tighten(tree)
        _commit_all(tree, f'branch {branch}')
        shas[branch] = _branch_git(tree, 'rev-parse', 'HEAD').stdout.strip()
    _branch_git(tree, 'checkout', '-q', 'main')
    return tree, shas


def _land_and_check(root, name, order, by_rebase, *, a_name, b_name,
                    a_regen, b_regen):
    tree, shas = _world(root, name, a_name=a_name, b_name=b_name,
                        a_regen=a_regen, b_regen=b_regen)
    conflicted = _land(tree, [shas[one] for one in order], by_rebase)
    return _branch_check(tree), conflicted


def _green(result):
    return result.returncode == 0


def _cell(root, name, a_way, b_way, shape, order, landing):
    """One sweep cell, built fresh: `(result, conflicted)`."""
    a_bound, b_bound = (('_shared_helper', '_shared_helper')
                        if shape == 'overlap'
                        else ('_alpha_helper', '_beta_helper'))
    return _land_and_check(root, name, order, landing == 'rebase',
                           a_name=a_bound, b_name=b_bound,
                           a_regen=a_way == 'tighten',
                           b_regen=b_way == 'tighten')


def _label(a_way, b_way, shape, order, landing):
    return '|'.join((f'A={a_way}', f'B={b_way}', shape, ''.join(order),
                     landing))


# --------------------------------------------------------------------------
# The fixtures that killed the first attempt. Each is red against it.
# --------------------------------------------------------------------------

def test_two_new_modules_binding_one_name_merge_green_in_both_orders(tmp):
    """The disproof, and the reason the first attempt stayed green.

    Both branches add a module and both bind the SAME new name, and only
    one of them regenerates. The artifact records the name against the
    one module the regeneration saw; the merged tree has two binders. No
    branch is wrong, and -- asserted here -- the merge carries no
    conflict, because only one side touched the artifact. That is
    exactly #1266, and the scoping it shipped with still answered
    `owners`, exit 1.
    """
    for index, order in enumerate((('a', 'b'), ('b', 'a'))):
        result, conflicted = _land_and_check(
            tmp, f'overlap{index}', order, False,
            a_name='_shared_helper', b_name='_shared_helper',
            a_regen=True, b_regen=False)
        assert not conflicted, (
            'the overlap merge is supposed to need no human decision')
        assert _green(result), (order, result.stdout, result.stderr)


def test_the_disjoint_merge_is_green_in_both_orders(tmp):
    """The control that WAS already green, kept so the fix cannot cost it."""
    for index, order in enumerate((('a', 'b'), ('b', 'a'))):
        for a_regen in (True, False):
            for b_regen in (True, False):
                result, _conflicted = _land_and_check(
                    tmp, f'disjoint{index}{a_regen}{b_regen}', order, False,
                    a_name='_alpha_helper', b_name='_beta_helper',
                    a_regen=a_regen, b_regen=b_regen)
                assert _green(result), (
                    order, a_regen, b_regen, result.stdout, result.stderr)


def _emptied(document):
    """The shipped document with every field but the schema emptied.

    Built from the document the tool WROTE rather than from a literal, so
    the exploit stays a valid document for whichever schema is shipped:
    a refusal on shape would be a different failure and would make this
    test pass for the wrong reason.
    """
    emptied = {'schema_version': document['schema_version']}
    for key, value in document.items():
        if key == 'schema_version':
            continue
        emptied[key] = [] if isinstance(value, list) else {}
    return emptied


def test_an_emptied_artifact_is_not_a_match(tmp):
    """The scope must not be controlled by the document it scopes.

    A document naming no names and covering no modules has nothing to be
    wrong about, and the attempt that shipped this control reported a
    match for it -- printing a count derived from the tree over a file
    that held nothing. The base now comes from git, so an emptied
    document is compared against the full derivation and every name
    comes out absent.
    """
    tree = _checkout(tmp, 'emptied', _BASE_FILES)
    _branch_git(tree, 'add', '-A')
    _tighten(tree)
    _commit_all(tree, 'the committed set')
    shipped = json.loads(_artifact(tree).read_text(encoding='utf-8'))
    _artifact(tree).write_text(
        json.dumps(_emptied(shipped)) + '\n', encoding='utf-8')
    result = _branch_check(tree)
    assert not _green(result), (result.stdout, result.stderr)
    assert 'absent:' in result.stderr, result.stderr
    assert 'match the committed set' not in result.stdout, result.stdout


def test_an_invented_name_is_still_a_violation(tmp):
    """The hand edit the check exists to catch, which scoping must not cost.

    An invented entry owned by a module the base's tree really carries:
    the module is inside the scope, so the entry is a claim about a
    module the document is answerable for, and nothing the document says
    about itself can excuse it.
    """
    tree = _checkout(tmp, 'invented', _BASE_FILES)
    _branch_git(tree, 'add', '-A')
    _tighten(tree)
    _commit_all(tree, 'the committed set')
    committed = json.loads(_artifact(tree).read_text(encoding='utf-8'))
    committed['names']['invented_name'] = {'python': ['tests/_owner.py']}
    _artifact(tree).write_text(json.dumps(committed) + '\n',
                               encoding='utf-8')
    result = _branch_check(tree)
    assert not _green(result), (result.stdout, result.stderr)
    assert "stale: ['invented_name']" in result.stderr, result.stderr


def test_the_document_carries_no_field_to_pair_a_phantom_with(tmp):
    """The hole the structural check did not close, unrepresentable now.

    A phantom entry owned by a path that is not on disk, paired with the
    scope list naming that same path, silenced every kind at once: the
    phantom was not on disk, not derived, and listed. That pairing needs
    a field the document does not have, so the reader refuses the
    document rather than reporting a match for it.
    """
    tree = _checkout(tmp, 'phantom', _BASE_FILES)
    _branch_git(tree, 'add', '-A')
    _tighten(tree)
    _commit_all(tree, 'the committed set')
    committed = json.loads(_artifact(tree).read_text(encoding='utf-8'))
    ghost = 'tests/_never_existed.py'
    committed['names']['invented_name'] = {'python': [ghost]}
    committed['modules'] = [ghost]
    _artifact(tree).write_text(json.dumps(committed) + '\n',
                               encoding='utf-8')
    result = _branch_check(tree)
    assert not _green(result), (result.stdout, result.stderr)
    assert 'unknown field: modules' in result.stderr, result.stderr


# --------------------------------------------------------------------------
# The generated sweep. One seed's zero is a sample.
# --------------------------------------------------------------------------

# The four cells whose verdict is a property rather than a surprise: the
# branch that tightened lands SECOND, by rebase, onto a main that already
# carries the other branch's module. Named as cells so the sweep and the
# pin cannot disagree about which ones they are.
STALE_BASE_CELLS = (
    ('tighten', 'skip', 'disjoint', ('b', 'a'), 'rebase'),
    ('tighten', 'skip', 'overlap', ('b', 'a'), 'rebase'),
    ('skip', 'tighten', 'disjoint', ('a', 'b'), 'rebase'),
    ('skip', 'tighten', 'overlap', ('a', 'b'), 'rebase'),
)


def test_a_deleted_covered_module_is_still_a_violation(tmp):
    """The `stale` limb's boundary, pinned from the REFUSING side.

    A module the base's tree carries is deleted and nothing rebinds what
    it bound. The document still records the name, the tree no longer
    derives it, and the module is in the base -- so this is a `stale`
    violation, which is what `violations`'s docstring claims
    deliberately. Nothing in the suite would fail if that stopped being
    true: both plants of the boundary that survived review are the
    forgiving direction, and the cheaper mutant here is to intersect
    `covered` with what is on disk, which forgives the deletion and
    leaves every other test in the suite green.

    The counterpart, the forgiving direction, is
    `test_a_live_module_outside_the_base_is_forgiven` below. These are
    one question asked twice, and they are the same plants the review
    reached from the two directions.
    """
    tree = _checkout(tmp, 'deleted', _BASE_FILES)
    _branch_git(tree, 'add', '-A')
    _tighten(tree)
    _commit_all(tree, 'the committed set')
    owner = next(name for name in sorted(_BASE_FILES)
                 if name.endswith('_owner.py'))
    (tree / owner).unlink()
    _commit_all(tree, 'the owning module is deleted')
    result = _branch_check(tree)
    assert not _green(result), (result.stdout, result.stderr)
    assert 'stale:' in result.stderr, result.stderr
    assert '--tighten' in result.stderr, result.stderr


def test_a_live_module_outside_the_base_is_forgiven(tmp):
    """The `stale` limb's boundary, pinned from the FORGIVING side.

    A name recorded against a module the base never carried, where that
    module is nevertheless live: another branch added it and the
    document has not absorbed it yet. That is the composition the
    scoping is FOR, so it is green, and the success line's two counts
    differ -- which is how a reader knows the document owes a name
    rather than that the check is blind.

    This is what a `stale` limb with its scoping dropped would refuse:
    every committed-not-derived name is then a violation, and the
    documented exemption becomes a permanent false red. It is the
    counterpart of the deletion case above, and the same question.
    """
    tree = _checkout(tmp, 'outside', _BASE_FILES)
    _branch_git(tree, 'add', '-A')
    _tighten(tree)
    _commit_all(tree, 'the committed set')
    live = 'tests/_from_another_branch.py'
    # Present and tracked, and binding NOTHING: another branch added a
    # module that never bound this name, so the document's claim about
    # it is `stale` rather than merely under-covering.
    (tree / live).write_text('def _something_else(value):\n    return value\n',
                             encoding='utf-8')
    _branch_git(tree, 'add', '-A')
    _commit_all(tree, 'another branch adds a module')
    committed = json.loads(_artifact(tree).read_text(encoding='utf-8'))
    committed['names']['_from_another_helper'] = {'python': [live]}
    _artifact(tree).write_text(json.dumps(committed) + '\n',
                               encoding='utf-8')
    result = _branch_check(tree)
    assert _green(result), (result.stdout, result.stderr)
    assert 'drift inside the base' in result.stdout, result.stdout
    # Two counts that differ, and no claim of a match: the line is what
    # makes the exemption visible rather than silent.
    assert ' against the ' in result.stdout, result.stdout


def test_a_second_tightening_landed_by_rebase_reports_the_missing_name(tmp):
    """A rebased document really does not match the tree it landed on.

    THE PROPERTY, PINNED. A branch that tightens writes a document that
    is exact for the tree it saw. Landed by rebase, its commit is
    replayed on top of whatever main already carried, so the tree that
    commit now holds has a module the document was generated without.
    The base is that rebased commit, that module is inside the base, and
    the check reports the names the document is missing. It is a TRUE
    POSITIVE, not a residue: the document really is stale, and the
    remedy is one `--tighten`.

    It is the landing method, not the merge, that makes the base
    over-cover. The same pair landed as a merge commit keeps the branch's
    own commit as the last writer, its tree lacks the landed module, and
    the name is forgiven -- which is what
    `test_the_generator_reads_a_rebased_document_as_it_lands` pins
    beside it.

    GREEN-BY-DESIGN-NOW: the claim this branch makes is that a MERGE no
    longer produces a red that no human decision caused, not that a
    merge never produces a red. If a future redesign makes these four
    green, revisit this pin rather than deleting it: green here would
    mean the design changed, and the question of whether that is better
    is a different one from whether it happened.
    """
    for counter, cell in enumerate(STALE_BASE_CELLS, 1):
        a_way, b_way, shape, order, landing = cell
        result, conflicted = _cell(tmp, f'pin{counter}', a_way, b_way,
                                   shape, order, landing)
        label = _label(*cell)
        assert not conflicted, (label, 'the resolve needed a decision')
        assert not _green(result), (
            f'{label} went GREEN -- the rebased document was accepted '
            'against the tree it landed on, which is a design change and '
            'not a fix')
        assert 'absent:' in result.stderr or 'owners:' in result.stderr, (
            label, result.stderr)
        assert '--tighten' in result.stderr, (
            f'{label}: the refusal carries no next step for the seat '
            f'reading it: {result.stderr!r}')


def test_the_generator_reads_a_rebased_document_as_it_lands(tmp):
    """The residual is a landing, and the merge ref is not where it shows.

    This is the evidence for the claim the pull request makes about it.
    A branch tightens against a base that is missing a module another
    branch has ALREADY landed on main. Built as a merge commit -- which
    is what a pull request's own CI builds its merge ref as -- the branch
    is green, because the last writer is the branch's own commit and its
    tree does not carry the landed module. Landed by rebase, which is how
    this repository lands, the same pair is red and prints the remedy.

    So the red does NOT appear on the branch's own CI. It appears when
    the branch is landed, and it is caught there, not before. This is
    recorded because the alternative -- asserting the red from reasoning
    about what a merge ref contains -- is exactly the kind of claim that
    passes review and fails in production.
    """
    def world(root, name):
        tree = _checkout(root, name, _BASE_FILES)
        _branch_git(tree, 'add', '-A')
        _tighten(tree)
        _commit_all(tree, 'the committed set')
        base = _branch_git(tree, 'rev-parse', 'HEAD').stdout.strip()
        _branch_git(tree, 'checkout', '-q', 'main')
        (tree / 'tests/_landed.py').write_text(
            'def _landed_helper(value):\n    return value\n',
            encoding='utf-8')
        _branch_git(tree, 'add', '-A')
        _commit_all(tree, 'main lands a module')
        _branch_git(tree, 'checkout', '-q', '-b', 'feature', base)
        (tree / 'tests/_branch.py').write_text(
            'def _branch_helper(value):\n    return value\n',
            encoding='utf-8')
        _branch_git(tree, 'add', '-A')
        _tighten(tree)
        _commit_all(tree, 'the branch tightens')
        return tree

    # The merge ref, as the branch's own CI builds it.
    merged = world(tmp, 'asmerge')
    _branch_git(merged, 'checkout', '-q', 'main')
    _branch_git(merged, 'merge', '-q', '--no-ff', '--no-edit', 'feature')
    on_merge_ref = _branch_check(merged)
    assert _green(on_merge_ref), (on_merge_ref.stdout, on_merge_ref.stderr)

    # The same pair, landed the way this repository lands one.
    rebased = world(tmp, 'asrebase')
    _branch_git(rebased, 'checkout', '-q', 'feature')
    _branch_git(rebased, 'rebase', '-q', 'main')
    landed = _branch_check(rebased)
    assert not _green(landed), (landed.stdout, landed.stderr)
    assert 'absent:' in landed.stderr, landed.stderr
    assert '--tighten' in landed.stderr, landed.stderr


def test_every_landing_of_two_branches_lands_green(tmp):
    """The property, over the product rather than over the cases I chose.

    {A regenerates, does not} x {B regenerates, does not} x {disjoint,
    overlapping} x {A first, B first} x {rebase-merge, merge commit}. The
    last axis is not optional: this repository lands by rebase, so the
    commit that last wrote the artifact on main is the rebased one, and a
    control tested only against a branch-local last writer has never been
    tested against how changes actually lands.

    Every cell is expected GREEN. A red one is a finding about the
    design, not an expectation to be adjusted, and this suite fails on it
    rather than reporting it and passing.
    """
    # The product, not five nested loops: one seed's zero is a sample, and
    # the axes are the claim, so they are written as the claim and read
    # off one enumeration.
    axes = (('tighten', 'skip'), ('tighten', 'skip'),
            ('overlap', 'disjoint'), (('a', 'b'), ('b', 'a')),
            ('rebase', 'merge'))
    verdicts = []
    cells = list(product(*axes))
    for counter, (a_way, b_way, shape, order, landing) in enumerate(cells, 1):
        result, conflicted = _cell(
            tmp, f'cell{counter:02d}', a_way, b_way, shape, order, landing)
        label = _label(a_way, b_way, shape, order, landing)
        green = _green(result)
        verdicts.append((label, green, conflicted))
        if not green:
            print(f'RED {label}: {result.stderr.strip()[:200]}')
    print(f'the generated sweep, {len(verdicts)} cells, one line each:')
    for line, green, conflicted in verdicts:
        print(f'  {line:52} {"GREEN" if green else "RED"}'
              f'  conflict={"yes" if conflicted else "no"}')
    stale = {_label(*one) for one in STALE_BASE_CELLS}
    unexpected = [line for line, green, _c in verdicts
                  if not green and line not in stale]
    assert not unexpected, unexpected
    assert all(green for line, green, _c in verdicts if line not in stale)
    # The two binders that agree on a name, where only one side
    # regenerated: the merge that reached main unnoticed, because nothing
    # in it needed a human decision. Every such cell is conflict-free.
    shared = [v for v in verdicts
              if '|overlap|' in v[0]
              and ('A=tighten|B=skip|' in v[0] or 'A=skip|B=tighten|' in v[0])]
    assert len(shared) == 8, shared
    assert not [v for v in shared if v[2]], shared


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='reservedbase_')


if __name__ == '__main__':
    raise SystemExit(main())
