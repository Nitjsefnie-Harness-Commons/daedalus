#!/usr/bin/env python3
"""The scope the committed reserved set is checked inside, and why it has one.

`.github/reserved-test-names.json` is an EXHAUSTIVE artifact: it names
every reserved name in the tracked tests tree, so the control that reads
it asserts equality with a derivation over that whole tree. An exhaustive
artifact does not compose across independent branches. Two branches that
each add a shared-helper module derive against a base that predates the
other, so each document is exact for what its own run saw and neither is
the derivation over the merged tree -- the MERGE is the first place the
equality fails, on `main`, with no branch left to fix it (issue 1266, and
`git log` shows main red on it three times on 2026-09-27, each cleared by
a regeneration-only commit).

So the document carries the tests modules it was derived over
(`modules`), and the verdict `scripts/ci/reserved_names.py::violations`
is asked only about those. This suite holds the property that scoping has
to have, and the integrity of the scope list itself. The refusal kinds
and the document shape are `test_reserved_test_names.py`; the fixtures
and the real-generator driver are shared with it rather than copied.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _helper_reimplementation import _live_sources  # noqa: E402
from test_reserved_test_names import (  # noqa: E402
    _FIXTURES, _OWNER, _contract, _fixture_checkout, _live_sources_of,
    _run_generator)

# One shared-helper module each, so a branch that adds one contributes a
# name the other branch cannot have derived.
_ALPHA = 'def _alpha_helper(value):\n    return value\n'
_BETA = 'def _beta_helper(value):\n    return value\n'


def _merged_documents(*documents):
    """Two artifacts resolved the way a merge resolves them: union by key.

    The field names are read off the documents rather than assumed, so
    this states the merge and not one schema's shape: two branches that
    each add a module and each tighten produce disjoint insertions, and
    a union is what a resolve of two such files leaves.
    """
    merged = {}
    for key in sorted(set().union(*(set(one) for one in documents))):
        values = [one[key] for one in documents if key in one]
        if all(isinstance(one, dict) for one in values):
            merged[key] = {name: entry for one in values
                           for name, entry in one.items()}
        elif all(isinstance(one, list) for one in values):
            merged[key] = sorted({item for one in values for item in one})
        else:
            merged[key] = values[0]
    return merged


def _tightened(tmp, files, name):
    """The artifact one branch's own tightening writes, and that tree."""
    tree = _fixture_checkout(tmp, files, name)
    artifact = tree / '.github' / 'reserved-test-names.json'
    result = _run_generator(tree, artifact, '--tighten')
    assert result.returncode == 0, (result.stdout, result.stderr)
    return tree, json.loads(artifact.read_text(encoding='utf-8'))


def test_two_branches_tightening_merge_green_in_either_order(tmp):
    """The occurrence: neither branch is wrong and the merge is red.

    Both merge orders are covered, because a scoping that only forgave
    the branch which did not tighten would be a different and much
    weaker control: with both branches tightening, every name in the
    merged document was recorded by a branch that derived it, and a
    scoping that refused that would be refusing an exact document.
    """
    base = {'tests/_owner.py': _OWNER,
            'tests/_wffixtures.py': _FIXTURES}
    _base_tree, base_set = _tightened(tmp, base, 'base')
    _a_tree, from_a = _tightened(tmp, {**base, 'tests/_alpha.py': _ALPHA},
                                 'brancha')
    _b_tree, from_b = _tightened(tmp, {**base, 'tests/_beta.py': _BETA},
                                 'branchb')
    merged = _fixture_checkout(tmp, {**base, 'tests/_alpha.py': _ALPHA,
                                     'tests/_beta.py': _BETA}, 'merged')
    artifact = Path(tmp) / 'merged-reserved.json'

    # Branch B did not regenerate, so the merged document never covered
    # the module it added and the name it brings is simply not there.
    artifact.write_text(json.dumps(from_a), encoding='utf-8')
    result = _run_generator(merged, artifact)
    assert result.returncode == 0, (result.stdout, result.stderr)

    # Both regenerated: the resolve unions two disjoint insertions, and
    # the merged document is then the derivation over the merged scope.
    both = _merged_documents(from_a, from_b)
    artifact.write_text(json.dumps(both), encoding='utf-8')
    result = _run_generator(merged, artifact)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert both != base_set, 'a branch that tightened recorded nothing'


def test_the_committed_scope_is_exactly_the_modules_that_bind_a_name(tmp):
    """`modules` and the recorded owners are two derivations of one scope.

    `modules` is what the verdict is scoped to and the recorded owners
    are what it is checked against, so a hand edit that decoupled them
    would scope the control to a set nothing is recorded in, or exempt a
    module the document owns names for. `--tighten` writes both from the
    same map, so they can only come apart by hand.
    """
    del tmp
    policy = _contract()
    committed = policy.load()
    owners = {owner for entry in committed['names'].values()
              for one in entry.values() for owner in one}
    assert committed['modules'] == sorted(owners), (
        sorted(set(committed['modules']) ^ owners))
    assert set(committed['modules']) <= set(_live_sources())


def test_a_module_outside_the_committed_scope_brings_no_violation(tmp):
    """The other side of the coin, pinned so the exemption is not a bug.

    A module the document does not list contributed a name, and the
    document is not asked about it -- that is the whole mechanism. It
    is worth a statement in its own right, because a change that made
    the exemption reach INSIDE the listed scope would leave every other
    test here green.
    """
    policy = _contract()
    base = {'tests/_owner.py': _OWNER,
            'tests/_wffixtures.py': _FIXTURES}
    _tree, base_set = _tightened(tmp, base, 'unscoped')
    grown = _fixture_checkout(tmp, {**base, 'tests/_alpha.py': _ALPHA},
                              'unscopedgrown')
    artifact = Path(tmp) / 'unscoped-reserved.json'
    artifact.write_text(json.dumps(base_set), encoding='utf-8')
    found = policy.violations(policy.load(artifact),
                              policy.document(_live_sources_of(grown)),
                              set(_live_sources_of(grown)))
    assert not any(found.values()), found
    # And the same tree with the document regenerated is exact, so the
    # module is recognised -- the verdict forgives it, it does not miss
    # that it is there.
    grown_artifact = grown / '.github' / 'reserved-test-names.json'
    assert _run_generator(grown, grown_artifact,
                          '--tighten').returncode == 0
    exact = json.loads(grown_artifact.read_text(encoding='utf-8'))
    assert '_alpha_helper' in exact['names'], sorted(exact['names'])
    assert 'tests/_alpha.py' in exact['modules']
    assert not any(policy.violations(exact, exact,
                                     set(_live_sources_of(grown))).values())


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='reservedscope_')


if __name__ == '__main__':
    raise SystemExit(main())
