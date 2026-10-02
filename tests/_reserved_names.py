"""The union of the owner sets the two name-bound controls each read alone.

The re-implementation limb is every name a module under `tests/_*.py`
binds at module-execution scope as a `def`, an `async def` or a `class`,
plus every `function` it declares inside a string constant at or above
`JS_FLOOR`. `tests/test_wf_suite_boundaries.py` owns the
module-level bindings of `tests/_wffixtures.py`, five names it hardcodes
because the rule is stated over them rather than derived.

Nothing joins the two, so a name a shared helper owns is invisible to the
workflow-fixture rule and a fixture name is invisible to the
re-implementation rule. This module is the join, computed from the
recognisers both controls already use rather than from a list of their
own: the same `_helper_binds` scope, the same entry-point exclusion, the
same `JS_FLOOR`, the same owner test, and the same import limb that
settles a name taken from a sibling.

THE JAVASCRIPT LIMB CARRIES THE FLOOR. `js_declarations` reports every
declaration a helper module makes, including one- and two-line ones, and
the JavaScript limb owns a name only at or above `JS_FLOOR`. The set
stated here is the set the rule enforces, so the floor decides both
sides: a one-line `function f` in a helper is not a name a module may
not bind, and how many helper declarations sit below the floor is a
figure to measure rather than a number to repeat here.

THE FIXTURE LIMB IS A BIND, NOT A DEFINITION. Two of the five
(`BLOCK_NEEDS`, `BLOCK_OUTPUTS`) are assignments, so the re-implementation
rule cannot own them however they are read; the fixture rule can, and
this module reads them with `scan` for that reason.
"""
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _branch_boundary import _parsed  # noqa: E402
from _helper_binds import definitions, scan  # noqa: E402
from _helper_reimplementation import (  # noqa: E402
    JS_FLOOR, _entry_points, _imports_the_name, _in_tests, _is_shared_helper,
    _is_the_owner, _live_sources, js_declarations)

PYTHON = 'python'
JAVASCRIPT = 'javascript'
FIXTURE = 'fixture'
LIMBS = (PYTHON, JAVASCRIPT, FIXTURE)
FIXTURE_MODULE = 'tests/_wffixtures.py'

ResidueSite = namedtuple('ResidueSite', 'limb path name owners')
FixtureSite = namedtuple('FixtureSite', 'path name')

_LIVE = None


def _parsed_modules(sources):
    """(imports, binds, definitions) per path, the entry point excluded.

    The entry point is excluded the way `_helper_reimplementation`
    excludes it, so the suites that each call a `main` under a
    `__main__` guard are not copies of the one helper module that also
    has one.
    """
    parsed = {}
    for path in sorted(sources):
        tree = _parsed(path, sources[path])
        imports, binds = scan(tree)
        entry = _entry_points(tree)
        defined = {name: lines for name, lines in definitions(tree).items()
                   if name not in entry}
        parsed[path] = (imports, binds, defined)
    return parsed


def _derive():
    """(sources, parsed) for the tracked tests tree, read once.

    Four derivations read every module, and parsing the tree once per
    call measured minutes against seconds; one machine, warm cache. A
    caller with its own sources map never reaches this cache.
    """
    global _LIVE
    if _LIVE is None:
        sources = _live_sources()
        _LIVE = (sources, _parsed_modules(sources))
    return _LIVE


def reserved(sources=None):
    """{name: {limb: (owner, ...)}} for the union of the three owner sets.

    Every name carries the module or modules that own it, so a refusal
    names the binding a re-implementation should have imported instead.
    """
    if sources is None:
        sources, parsed = _derive()
    else:
        parsed = _parsed_modules(sources)
    out = {}
    for path in sorted(sources):
        if not _is_shared_helper(path):
            continue
        _imports, _binds, defined = parsed[path]
        for name in defined:
            out.setdefault(name, {}).setdefault(PYTHON, set()).add(path)
    helpers = {path: sources[path] for path in sources
               if _is_shared_helper(path)}
    for path, items in sorted(js_declarations(helpers).items()):
        for item in items:
            if item.body_lines >= JS_FLOOR:
                out.setdefault(item.name, {}).setdefault(
                    JAVASCRIPT, set()).add(path)
    if FIXTURE_MODULE not in sources:
        raise AssertionError(
            'the workflow fixture module is not in the tree: '
            f'{FIXTURE_MODULE}')
    for name in parsed[FIXTURE_MODULE][1]:  # binds, not definitions
        out.setdefault(name, {}).setdefault(FIXTURE, set()).add(
            FIXTURE_MODULE)
    return {name: {limb: tuple(sorted(owners))
                   for limb, owners in sorted(entry.items())}
            for name, entry in sorted(out.items())}


def _owner_maps(derived):
    """The owner set per limb, in the shape `_is_the_owner` reads."""
    maps = {limb: {} for limb in LIMBS}
    for name, entry in derived.items():
        for limb, owners in entry.items():
            maps[limb][name] = set(owners)
    return maps


def residue_sites(sources=None, derived=None):
    """Every site binding a reserved name outside the module that owns it.

    A Python site is a definition the import limb does not settle and the
    JavaScript one a declaration at or above `JS_FLOOR`. `limb` names
    which of the two residue tables the site would need a row in, and a
    site is reported over the union rather than over one limb's half.
    """
    if sources is None:
        sources, parsed = _derive()
    else:
        parsed = _parsed_modules(sources)
    if derived is None:
        derived = reserved(sources)
    owners = _owner_maps(derived)
    stems = {Path(path).stem for path in sources}
    sites = []
    for path in sorted(sources):
        if not _in_tests(path):
            continue
        imports, _binds, defined = parsed[path]
        for name in sorted(defined):
            if (name not in owners[PYTHON]
                    or _is_the_owner(path, name, owners[PYTHON])
                    or _imports_the_name(imports, name, stems)):
                continue
            sites.append(ResidueSite(
                PYTHON, path, name, tuple(sorted(owners[PYTHON][name]))))
    for path, items in sorted(js_declarations(sources).items()):
        if not _in_tests(path):
            continue
        for item in items:
            if (item.body_lines < JS_FLOOR
                    or item.name not in owners[JAVASCRIPT]
                    or _is_the_owner(path, item.name, owners[JAVASCRIPT])):
                continue
            sites.append(ResidueSite(
                JAVASCRIPT, path, item.name,
                tuple(sorted(owners[JAVASCRIPT][item.name]))))
    return sites


def fixture_sites(sources=None):
    """Every bind of a workflow-fixture name outside `tests/_wffixtures.py`.

    A bind, not a definition: the fixture rule refuses a shadow outright,
    because it has no residue table to excuse one, and a suite that took
    the name by import is a bind the import limb settles, which is why
    `scan` is read here and not a definition walk.
    """
    if sources is None:
        sources, parsed = _derive()
    else:
        parsed = _parsed_modules(sources)
    fixture = {name for name, entry in reserved(sources).items()
               if FIXTURE in entry}
    sites = []
    for path in sorted(sources):
        if path == FIXTURE_MODULE or not _in_tests(path):
            continue
        for name in sorted(fixture & set(parsed[path][1])):
            sites.append(FixtureSite(path, name))
    return sites
