#!/usr/bin/env python3
"""What a new tests module may not bind, and the three checks on that set.

Two halves decide the set and neither computes the other's. The
re-implementation half is every name a module under `tests/_*.py`
defines, in Python and in JavaScript; `test_wf_suite_boundaries.py`
owns the module-level bindings of `tests/_wffixtures.py` and nowhere
else. This suite derives their union in `tests/_reserved_names.py` and
holds THREE properties of it: that no reserved name is bound without a
row in its own table, that no row names a site which is not live, and
that no row excuses a declaration this branch itself wrote.

The generated artifact and the generator that writes it are a separate
subject with their own suite, `tests/test_reserved_names_artifact.py`.

Two controls here belong to neither half by subject and sit here for
want of a better home: the source-anchor preconditions the routes assume,
and the branch-boundary rows over the two allowance tables.
"""

# The owner-module fixtures are spelled out in both halves rather than
# shared. Giving the shared copy a `tests/_*.py` home makes it the OWNER
# of a reserved JavaScript name, which grows the reserved set for a
# synthetic string; these are plain data both suites derive against.
# pylint: disable=duplicate-code
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _reserved_names  # noqa: E402
import _util  # noqa: E402
from _helper_binds import definitions  # noqa: E402
from _branch_boundary import (IS_THE_BASE, UNREADABLE, introduced_rows,
                              js_digests, python_digests)  # noqa: E402
from _helper_reimplementation import (  # noqa: E402
    _entry_points, _live_sources)
from _source_anchors import (  # noqa: E402
    after_call, first_call_line, the_call_line)
from _unconsolidated_js_names import (  # noqa: E402
    UNCONSOLIDATED_JS_NAMES)
from _unconsolidated_names import UNCONSOLIDATED_NAMES  # noqa: E402

ROOT = _util.ROOT

# The residue table each limb's site needs a row in: the join the two
# guards cannot make, since the fixture rule has no table to excuse one.
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
    set-equal to `reimplementations` union `js_reimplementations`, each
    difference in both directions empty, and the reason is structural
    rather than incidental. `tests/_wffixtures.py` IS a `tests/_*.py`
    module, so the three fixture names that are definitions are already
    in the python limb, and the two that are not (`BLOCK_NEEDS`,
    `BLOCK_OUTPUTS`) are `Assign` binds, which `definitions` never
    reports; while it remains a shared helper the union adds nothing
    HERE.

    `len(residue_sites())` is not that set's size, and both are worth
    knowing. The tables are keyed `(path, name)` and two modules declare
    a reserved JavaScript name more than once, so the list carries three
    rows the set does not: `tests/_gm_harness.py::makeStorage` twice and
    `tests/test_gm_transfers.py::flushMessages` three times. The equality
    is about the set, because that is what the tables key on; the count
    is about the list, because that is what a refusal prints.
    """
    del tmp
    unallowed = sorted(
        f'{site.path}::{site.name} ({site.limb}) owned by {list(site.owners)}'
        for site in _reserved_names.residue_sites()
        if (site.path, site.name) not in RESIDUE_TABLES[site.limb])
    assert not unallowed, (
        'reserved names re-implemented with no row in the matching residue '
        'table:\n' + '\n'.join(unallowed))


def test_no_allowance_row_names_a_dead_site(tmp):
    """A row claims one LIVE site; both halves of that must hold.

    Both tables: a control that reads one of a pair is a half-control. A stale
    row is a refusal, and a row with no reason is worse, because nothing tells
    a reader which of the two it is looking at.
    """
    del tmp
    for limb, table in sorted(RESIDUE_TABLES.items()):
        live = {(s.path, s.name)
                for s in _reserved_names.residue_sites() if s.limb == limb}
        assert live, f'the {limb} residue reader derived no site at all'
        stale = sorted(k for k in table if k not in live)
        assert not stale, f'{limb} rows name no live site: {stale}'
        silent = sorted(k for k in table if not table[k].strip())
        assert not silent, f'{limb} rows carry no justification: {silent}'
    # The oracle is live: the reader finds a site planted in a synthetic
    # tree, so "no stale row" is not a green over a reader that read nothing.
    planted = _planted_tree({'tests/test_planted.py':
                             'def _placed_helper(value):\n    return value\n'})
    seen = [s.name for s in _reserved_names.residue_sites(planted)]
    assert '_placed_helper' in seen, seen


def test_an_allowance_row_may_not_name_a_branch_added_declaration(tmp):
    """A row may not legalise a duplication the branch itself wrote.

    Both tables: a control that reads one of a pair is a half-control.
    `UNREADABLE` FAILS and `IS_THE_BASE` SKIPS and prints, because a skip that
    asserted nothing is indistinguishable from a pass.
    """
    del tmp
    for label, table, read in (('py', UNCONSOLIDATED_NAMES, python_digests),
                               ('js', UNCONSOLIDATED_JS_NAMES, js_digests)):
        boundary = introduced_rows(table, read, ROOT)
        assert boundary.reason != UNREADABLE, boundary.reason
        if boundary.reason:
            print(f'[{label}] the branch boundary was NOT evaluated: '
                  f'{boundary.reason}')
            assert boundary.reason == IS_THE_BASE, boundary.reason
        assert not boundary.introduced, (
            'rows excuse a declaration the base tree does not carry, so the '
            f'branch wrote it: {boundary.introduced}')
    # The two reasons are answers this function OWES rather than answers it
    # happens to give today, and both are reachable from the public `bases=`
    # whatever this branch added: time-invariant by construction.
    for label, table, read in (('py', UNCONSOLIDATED_NAMES, python_digests),
                               ('js', UNCONSOLIDATED_JS_NAMES, js_digests)):
        refused = introduced_rows(table, read, ROOT, bases=('no-such-base',))
        assert refused.reason == UNREADABLE, (label, refused.reason)
        skipped = introduced_rows(table, read, ROOT, bases=('HEAD',))
        assert skipped.reason == IS_THE_BASE, (label, skipped.reason)


def test_no_workflow_fixture_name_is_bound_outside_its_module(tmp):
    """The fixture limb has no residue table, so nothing excuses a shadow.

    Every one is read from `tests/_wffixtures.py` rather than from names
    the boundary rule hardcodes, so a fixture added there is covered on the
    day it is added -- and a bind that rule's own walk cannot see is caught
    here, because `scan` reads the walrus, the `for` target and
    `except ... as` too.
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


def test_an_anchor_refuses_an_ambiguous_position(tmp):
    """A plant must name one place, or it is a coin toss.

    The anchors disagree about a second occurrence and that disagreement is
    the property: two refuse it, and `first_call_line` takes the EARLIER line,
    a promise about order the name alone does not make. Each carries its own
    `else`: accepting the position is a defect apart from a wrong message.
    """
    del tmp
    two = ('def _helper(tmp):\n    seed(tmp)\n    seed(tmp)\n'
           '    return tmp\n')
    try:
        the_call_line(two, 'seed')
    except AssertionError as error:
        assert 'the seed call is not unique' in str(error), error
    else:
        raise AssertionError('the call anchor took an ambiguous position')
    try:
        after_call(two, 'seed', '    seed(tmp)')
    except AssertionError as error:
        assert 'the seed anchor is not unique' in str(error), error
    else:
        raise AssertionError('the plant anchor took an ambiguous position')
    calls = ('_helper = None\ndef test_control(tmp):\n    del tmp\n'
             '    _helper(tmp)\n    _helper(tmp)\n')
    assert first_call_line(calls, '_helper') == 4


def test_this_suite_binds_no_reserved_name(tmp):
    """Self-application: the suite is measured by the union it derives.

    A `tests/` module is bound by the same rules as any other, so a name
    this file binds that a `tests/_*.py` module already owns is a collision
    it created -- and only the entry point is exempt, the guard the
    recogniser already applies.
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='reservednames_')


if __name__ == '__main__':
    raise SystemExit(main())
