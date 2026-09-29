"""The retirement's sites, derived from the guard's own source, and the
sweep that reverts each of them.

Named for what it is rather than for the family it reads: the universe is
the guard family, identified by the `_pyroute*` naming, and a tool that
reads that family is not itself a member of it. The earlier name made
the driver the twenty-first site in its own census.

A retirement is a per-key fact on a tracked container, and the rule it
exists for is that a constant-key read of a retired key does not answer
from the recorded value there. The rule lives at a handful of places and
every one of them has to be right, so the enumeration of them belongs in
the tree: a report cannot be re-run, and a site added in the tree with
no decision taken about it is exactly the defect this module exists to
prevent.

**The enumeration is keyed on a PROPERTY, not on a spelling.** A site is
a guard function that propagates or consults a retirement, which is to
say one that mentions `stale` -- as a name, as an attribute, or as a
keyword argument. A fold spelled with a different helper, or a retirement
forwarded positionally, is still found; a fold that never touches one is
not in the set whatever it is called. Keying on the callee name
`_fold_items` would find today's four folds and miss the rest of the
rule, which is the shape of the finding this module answers.

**The sweep is here for the same reason.** `python3
tests/_pyroute_retirement_sites.py` reverts each site in turn on the real
code, runs the suites, and prints the controls that died. A control that
survives its own revert is not a control, and this branch has shipped
three of them; the driver is what catches the fourth.

It lives in a `_`-prefixed module rather than a suite because it runs the
suites: a suite importing a sibling suite re-executes that suite's whole
module body, and this one has to import the guard modules rather than any
suite. `test_the_retirement_census_is_the_guard_own_list` in
`test_tab_routing_dict_retirement.py` is the half of this that runs on
every commit; this is the half that reverts the code.
"""
import ast
import subprocess
import sys
from pathlib import Path

import _util

HERE = Path(__file__).resolve().parent
_RETIREMENT_VOCABULARY = ('stale', 'retired')
GUARD_MODULES = tuple(sorted(
    path.stem for path in HERE.glob('_pyroute*.py')))

# Every guard module, globbed rather than listed. A hand list is the same
# defect as a hand site list: a module added to the family would be invisible
# to the derivation, which is the one claim this branch has no business
# making by hand. Filled in after HERE, further down.

# The suites a revert has to turn red in, and the test whose name must
# appear among the failures.
CONTROL_SUITES = ('tests/test_tab_routing_dict_retirement.py',
                  'tests/test_tab_routing_dict_keyset.py',
                  'tests/test_tab_routing_positions.py',
                  'tests/test_tab_routing_negative_stores.py',
                  'tests/test_tab_routing_unmodelled_mutation.py',
                  'tests/test_tab_routing_unprovable.py',
                  'tests/test_tab_routing_dict_stores.py')

# One revert per derived site: the function, the text to remove, and what
# replaces it. A site with no entry here is a site with no decision
# taken about it, and `revert_sites` refuses to run until it has one.
REVERTS = {
    '_pyroute_reads._dict_value': (
        "            stale = ((stale - set(value.items))\n"
        "                     | retired_into(value.stale, items))\n", ""),
    '_pyroute_reads._merge_or_value': (
        "            stale = ((stale - set(known.items))\n"
        "                     | retired_into(known.stale, items))\n", ""),
    '_pyroute_reads._dict_call_value': (
        "            stale = ((stale - set(known[0]))\n"
        "                     | retired_into(known[2], items))\n", ""),
    '_pyroute_reads._mapping_lookup': (
        "    if key in owner.stale:\n"
        "        return merge_yielded((owner.items.get(key), UNPROVABLE_SENDER"
        ",\n"
        "                              default))\n", ""),
    '_pyroute_reads._readback_copy': (
        "        items, dict_length(items, owner.length is not None), 'dict', "
        "node,\n"
        "        stale=owner.stale)",
        "        items, dict_length(\n"
        "            items, owner.length is not None), 'dict', node)"),
    '_pyroute_reads._source_items': (
        "            return known.items, known.length is not None, known.stale"
        "",
        "            return known.items, known.length is not None, "
        "frozenset()"),
    '_pyroute_reads._apply_pop': (
        "        owner, items, key is _UNRESOLVED_KEY, stale_after_store(\n"
        "            owner, () if key is _UNRESOLVED_KEY else (key,))))",
        "        owner, items, key is _UNRESOLVED_KEY))"),
    '_pyroute_mapping._mark_unprovable': (
        "                          stale=stale_after_store(owner, unreadable=T"
        "rue))",
        "                          )"),
    '_pyroute_mapping._apply_mapping_store': (
        "                                 | (carried - set(keywords))))",
        "                                 | carried))"),
    '_pyroute_mapping._apply_setdefault': (
        "            replace_container(state, owner_name, owner, items,\n"
        "                              stale=stale_after_store(owner, (literal"
        ",)))",
        "            replace_container(state, owner_name, owner, items)"),
    '_pyroute_stores.replace_container': (
        "    copied = container_copy(owner, items, unknown_length, stale)",
        "    copied = container_copy(owner, items, unknown_length)"),
    '_pyroute_stores._seed_receiver': (
        "                {**owner.items, cast(Hashable, key): value},\n"
        "                stale=stale_after_store(owner, (key,)))",
        "                {**owner.items, cast(Hashable, key): value})"),
    '_pyroute_stores._subscript_store': (
        "        stale=stale_after_store(\n"
        "            owner, () if dynamic else (literal,)))", "        )"),
    '_pyroute_storage.retired_into': (
        '    return retired & set(items)', '    return frozenset()'),
    '_pyroute_storage.stale_after_store': (
        "    if unreadable:\n"
        "        return (owner.stale | (set(owner.items) - {DYNAMIC_KEY})) \\"
        "\n"
        "            - set(written)\n"
        "    return owner.stale - set(written)",
        "    return owner.stale"),
    '_pyroute_storage.container_copy': (
        "    current = owner.stale if stale is None else stale\n"
        "    return DeferredContainer(items, length, owner.kind, owner.identit"
        "y,\n"
        "                             owner.star_display,\n"
        "                             frozenset(current) - {DYNAMIC_KEY})",
        "    return DeferredContainer(items, length, owner.kind,\n"
        "                             owner.identity, owner.star_display)"),
    '_pyroute_storage.join_clean_occupancy': (
        "            items, length, owner.kind, owner.identity, star, owner.st"
        "ale))",
        "            items, length, owner.kind, owner.identity, star))"),
    '_pyroute_positions.at_position': (
        "        if index in container.stale:\n"
        "            # A store the model could not read may have put something"
        " else\n"
        "            # at this key, so the recorded value is one more candidat"
        "e.\n"
        "            return [items.get(index), UNPROVABLE_SENDER,\n"
        "                    items.get(DYNAMIC_KEY)]\n", ""),
    '_pyroute_match._bind_mapping': (
        "            rests.append(DeferredContainer(\n"
        "                rest, len(rest), 'dict', branch.identity,\n"
        "                stale=retired_into(branch.stale, rest)))",
        "            rests.append(DeferredContainer(\n"
        "                rest, len(rest), 'dict', branch.identity))"),
    '_pyroute_values.stored_signature': (
        "                         value.star_display, value.stale)",
        "                         value.star_display)"),
}


def _mentions_a_retirement(node):
    """Whether a function propagates or consults one, by property.

    The vocabulary is the concept's two spellings in this tree: `stale` for
    the marker itself and `retired` for what a fold inherits. Keying on one
    of the two is what let `retired_into` -- the function that decides
    what a fold inherits -- sit outside the sweep entirely.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Name) \
                and child.id.startswith(_RETIREMENT_VOCABULARY):
            return True
        if isinstance(child, ast.Attribute) \
                and child.attr.startswith(_RETIREMENT_VOCABULARY):
            return True
        if isinstance(child, ast.keyword) and child.arg \
                and child.arg.startswith(_RETIREMENT_VOCABULARY):
            return True
    return False


def retirement_sites():
    """The guard functions that propagate or consult a retirement.

    Read out of the guard's own source, so a site added without a
    decision taken about it is found rather than shipped."""
    sites = set()
    for name in GUARD_MODULES:
        tree = ast.parse((HERE / (name + '.py')).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) \
                    and _mentions_a_retirement(node):
                sites.add(f'{name}.{node.name}')
    return frozenset(sites)


def undecided_sites():
    """The derived sites no revert has been written for."""
    return retirement_sites() - set(REVERTS)


def _red_controls():
    """The failing test names across the control suites."""
    red = []
    for suite in CONTROL_SUITES:
        finished = subprocess.run(
            [sys.executable, str(HERE / Path(suite).name)], cwd=HERE.parent,
            env=_util.child_coverage('scrub'), capture_output=True,
            text=True, timeout=900)
        for line in finished.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith(('FAIL', 'ERROR')):
                red.append(f'{Path(suite).stem}.'
                           f'{stripped.split()[1].split(":")[0]}')
    return red


def revert_sites():
    """Revert every derived site in turn and report what died.

    Returns the survivors -- the sites whose revert left every suite
    green, which is the finding this driver exists to name."""
    missing = undecided_sites()
    if missing:
        raise SystemExit(f'no revert written for: {sorted(missing)}')
    survivors = []
    print(f'{"site":46s} {"controls that die":44s} survivors')
    for stale in HERE.glob('__pycache__'):
        for cached in stale.glob('*.pyc'):
            cached.unlink()
    for site in sorted(retirement_sites()):
        module = site.rpartition('.')[0]
        path = HERE / (module + '.py')
        original = path.read_text(encoding='utf-8')
        old, new = REVERTS[site]
        if old not in original:
            print(f'{site:46s} ANCHOR-MISSING')
            survivors.append(site + ' (anchor missing)')
            continue
        path.write_text(original.replace(old, new, 1), encoding='utf-8')
        try:
            red = _red_controls()
        finally:
            path.write_text(original, encoding='utf-8')
        print(f'{site:46s} {len(red):2d} red  {", ".join(red[:2])[:42]:44s} '
              f'{"NONE" if red else "*** SURVIVES ***"}')
        if not red:
            survivors.append(site)
    return survivors


def main():
    print(f'derived sites: {len(retirement_sites())}  '
          f'reverts written: {len(REVERTS)}  '
          f'undecided: {sorted(undecided_sites()) or "none"}')
    survivors = revert_sites()
    print(f'SURVIVORS: {survivors or "none"}')
    return 1 if survivors else 0


if __name__ == '__main__':
    raise SystemExit(main())
