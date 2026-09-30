"""The retirement's sites, derived from the guard's own source, and the
sweep that reverts each of them.

A retirement is a per-key fact on a tracked container, and the rule it
exists for is that a constant-key read of a retired key does not answer
from the recorded value there. The rule lives at a handful of places and
every one of them has to be right, so the enumeration of them belongs in
the tree: a report cannot be re-run, and a site added in the tree with no
decision taken about it is exactly the defect this module exists to
prevent.

**The universe is a property, not a filename.** A module is in it when it
builds a tracked container or calls one of the helpers that copies one --
the places a retirement can enter a value at all. Every `tests/` module is
classified, and a module that is not in the universe carries a recorded
reason in `MODULE_EXCLUSIONS`, so a module that has to be argued out of
the sweep has to be argued out of the sweep in the tree. The `_pyroute*`
naming coincides with the property today; a reviewer planted a real
retirement-carrying builder in `tests/_tabroute_keyset.py`, a module the
retirement suite already imports from, and the naming missed it while both
census assertions passed. Coincidence is what this branch's previous five
completeness claims rested on.

**A site is a ROLE, not a spelling.** It is a function that carries,
returns, filters, settles or consults the key set a retirement is recorded
in: a `stale=` argument to a container build, a read of a container's
marker, a set operation over its own arguments, or a call to another site.
The role is what a neutral name cannot hide -- a reviewer planted
`settle_into(marker, items) -> frozenset(marker) & set(items)`, which *is*
the intersection helper, and a `startswith` vocabulary could not see it.

**The sweep is here for the same reason.** `python3
tests/_retirement_sweep.py` reverts each site in turn on the real code,
runs the suites, and prints the controls that died. A control that
survives its own revert is not a control, and this branch has shipped
several; the driver is what catches the next. A revert must be the defect
the site guards and not something stronger: neutering a marker entirely
kills more than removing the intersection does, and a control that only
dies on the stronger mutation still passes on the real one.

It lives in a `_`-prefixed module rather than a suite because it runs the
suites: a suite importing a sibling suite re-executes that suite's whole
module body, and this one has to read the guard modules rather than import
any suite. `test_the_retirement_census_is_the_guard_own_list` in
`test_tab_routing_dict_retirement.py` is the half of this that runs on
every commit; this is the half that reverts the code.
"""
import ast
import subprocess
import sys
from pathlib import Path

import _util

HERE = Path(__file__).resolve().parent
SELF = Path(__file__).resolve().stem

# The constructors and helpers whose use makes a module part of the guard
# family. Keyed on the ROLE -- a module that builds a tracked container or
# copies one is where a retirement can enter a value -- not on a filename.
_CONSTRUCTORS = ('DeferredContainer', 'SpreadContainer')
_COPY_HELPERS = ('container_copy', 'replace_container',
                 'replace_deferred_storage', 'join_clean_occupancy')

# Derived sites with NO mutation, and why there is none to write. These are
# carries whose retirement is unobservable by construction, so any mutation
# of them is a no-op and no control could distinguish it. They stay in the
# census -- a real retirement planted in one of them makes it a site that
# needs a revert -- but the sweep reports them as decided rather than as
# survivors, because "a mutation killed nothing" and "this has nothing to
# mutate" are different facts and only one of them is a finding.
NO_MUTATION = {
    '_pyroute_mapping._apply_set_store': (
        "the carry IS container_copy's own default here: the call passes "
        "no stale, so container_copy takes `owner.stale` itself, and a "
        "dict operand never reaches this fold: set_operands refuses one. "
        'Nothing removes the carry at this call site except an explicit '
        'frozenset(), and that drops a carry no control observes. '
        'CONTROLLING CONTROL: test_a_set_fold_refuses_a_dict_operand. '
        'WHAT WOULD REFUTE IT: any path that delivers a dict operand to '
        '_apply_set_store; the control drives exactly that and the model '
        'answers with None'),
}

# Every `tests/` module the universe derivation does NOT put in, and why.
# An exclusion is a decision, so it is recorded here: a module has to be
# argued out of the sweep in the tree, not silently skipped by it.
MODULE_EXCLUSIONS = {
    '_retirement_sweep': 'the sweep tool itself; it reads the family',
}

# The suites a revert has to turn red in, and the test whose name must
# appear among the failures.
# What the branch's own verdicts were at the head this block was written
# for. The sweep prints these in its header beside its own result, because
# a reader who runs this module to see whether a control bites is exactly
# the reader who needs to know what the numbers were: they are recorded in
# the tree rather than only in a report, and CI certifies the head.
ACCEPTANCE = {
    'A - the three stale-recorded rows (#1154), 3 reads':
        'routed (1, 1), clean (0, 0) on all nine cells',
    'B - the four name-source rows (#1178), 3 reads':
        'routed (1, 1), clean (0, 0) on all twelve cells',
    'C - every _AXES row': 'routed (1, 1), clean (0, 0)',
    'C - every _ACCOUNTED row': '(0, 0) on both prefixes',
    "C - _SILENT": "one member, #1162's update-starred-source subscript",
    'D - the scaling budget': 'narrow=33 wide=53 budget=66',
    'E - a key written after the store retired it':
        'resolves to the recorded value, no new join',
    'F - the false-green census':
        '1 cell, byte-identical to base b39f850e',
}

_SUITE_TIMEOUT = 900
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
    # The DEFECT, not something stronger. `return frozenset()` drops the
    # marker entirely and kills four controls, but removing only the
    # intersection while still carrying is the over-carry this helper
    # exists to prevent, and it killed nothing until a control distinguished
    # the two forms. A revert must be the defect the site guards.
    '_pyroute_storage.retired_into': (
        '    return retired & set(items)', '    return retired'),
    # This was a NO_MUTATION decision -- "the carry is container_copy's
    # default and no control observes it" -- until the control written to
    # observe it contradicted the claim: it asserts the marker survives the
    # projection, so a `frozenset()` here DOES kill it. A site a control can
    # see is a site the sweep can test, so it moved from the decided set to
    # here rather than having its reason narrowed until it was true.
    '_pyroute_reads._readback_popitem': (
        '    replace_deferred_storage(state, owner, container_copy(owner, items))',
        '    replace_deferred_storage(state, owner, container_copy(\n'
        '        owner, items, False, frozenset()))'),
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


def _is_a_suite(tree):
    """Whether a module is a suite rather than a module of the guard.

    A suite's `main` returns the runner's result. That is the property,
    and it is structural: a fixture in a suite and a decision in the guard
    are both functions that build a container, and what separates them is
    which one the test runner drives.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'main':
            return any('runner' in ast.dump(child)
                       for child in ast.walk(node))
    return False


def _builds_a_container(tree):
    """Whether a module builds or copies a tracked container."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = getattr(node.func, 'id', None) or getattr(
            node.func, 'attr', None)
        if callee in _CONSTRUCTORS or callee in _COPY_HELPERS:
            return True
    return False


def module_universe():
    """The modules a retirement can enter a value through, and the reasons
    every other `tests/` module is not one of them.

    Derived from what each module DOES rather than from what it is called,
    so a retirement-carrying builder planted outside the `_pyroute*` family
    is found by the same rule that finds today's. Every module is
    classified: one that builds or copies a container is in, and one that
    does not is out with its reason recorded, so nothing is skipped
    silently and a module that has to be argued out is argued out in the
    tree.
    """
    in_universe, excluded = [], {}
    for path in sorted(HERE.glob('*.py')):
        stem = path.stem
        if stem == SELF:
            excluded[stem] = MODULE_EXCLUSIONS[stem]
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        if _is_a_suite(tree):
            excluded[stem] = ('a suite: its main returns the test runner, '
                              'so it holds controls, not guard decisions')
        elif _builds_a_container(tree):
            in_universe.append(stem)
        else:
            excluded[stem] = ('builds no tracked container and copies none, '
                              'so a retirement cannot enter a value here')
    return tuple(in_universe), excluded


_MARKER_PARAMETER = 'stale'


def _carries_positionally(functions, family=None):
    """The functions that hand a retirement to a helper which takes one.

    One level, not the whole call graph: `container_copy(owner, items,
    unknown, stale_after_store(...))` passes the marker as an argument, and
    a caller that merely forwards a container is not deciding anything.
    """
    carriers = set()
    for name, node in functions.items():
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            callee = getattr(child.func, 'id', None)
            known = dict(functions)
            known.update(family or {})
            target = known.get(callee) if callee else None
            if target is None:
                continue
            if any(isinstance(argument, ast.arg)
                   and argument.arg == _MARKER_PARAMETER
                   for argument in target.args.args) and child.args:
                carriers.add(name)
    return carriers


def _carries_or_consults(node):
    """Whether a function carries a marker in or reads one out."""
    for child in ast.walk(node):
        if isinstance(child, ast.keyword) and child.arg == 'stale':
            return True
        if isinstance(child, ast.Attribute) and child.attr == 'stale':
            return True
        if isinstance(child, ast.arg) and child.arg == 'stale':
            return True
    return False


def _settles_a_key_set(node):
    """Whether a function returns a set operation over its own arguments.

    This is the axis a neutral name cannot hide: it is the shape of the
    intersection helper, whatever the helper is called.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.BinOp) or not isinstance(
                child.op, (ast.BitAnd, ast.BitOr, ast.BitXor, ast.Sub)):
            continue
        if any(isinstance(side, ast.Call) and getattr(
                side.func, 'id', None) in ('set', 'frozenset')
                for side in (child.left, child.right)):
            return True
        if any(isinstance(side, (ast.Set, ast.List, ast.Tuple, ast.Dict))
               for side in (child.left, child.right)):
            return True
    return False


def _is_a_control(node):
    """Whether a function asserts rather than decides.

    A control is where the fact is checked, not where it is decided, and
    reverting one changes the check rather than the guard. This is a role
    read off the function's own body, not a name.
    """
    return any(isinstance(child, ast.Assert) for child in ast.walk(node))


def _family_tables():
    """Every function in the universe, by bare name, across the modules.

    Cross-module, because carrying a retirement is usually a forward: the
    read side hands the settled set to `container_copy`, which lives in the
    storage module.
    """
    tables = {}
    for name in module_universe()[0]:
        tree = ast.parse((HERE / (name + '.py')).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                tables.setdefault(node.name, node)
    return tables


def _site_functions(tree, family=None):
    """The functions that carry, consult or settle a retirement's key set.

    The three axes are structural, so a neutral name cannot hide the role:
    a `stale=` argument or a container's marker is carrying or consulting,
    and a set operation over the function's own arguments is settling. A
    function that merely CALLS a site is not one -- the caller did not
    decide anything -- and reading the call graph would pull in every
    dispatcher, which is not what a revert target is.
    """
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    sites = {name for name, node in functions.items()
             if not _is_a_control(node)
             and (_carries_or_consults(node) or _settles_a_key_set(node))}
    sites |= {name for name in _carries_positionally(functions, family or {})
              if not _is_a_control(functions[name])}
    return functions, sites


def retirement_sites():
    """The functions that propagate or consult a retirement.

    Read out of the guard's own source over the derived universe, so a
    site added without a decision taken about it is found rather than
    shipped."""
    family = _family_tables()
    sites = set()
    for name in module_universe()[0]:
        tree = ast.parse((HERE / (name + '.py')).read_text(encoding='utf-8'))
        sites.update(f'{name}.{function}'
                     for function in _site_functions(tree, family)[1])
    return frozenset(sites)


def undecided_sites():
    """The derived sites with neither a revert nor a recorded decision."""
    return retirement_sites() - set(REVERTS) - set(NO_MUTATION)


def _clear_bytecode():
    """Drop the compiled forms, before every child run.

    Two same-length reverts of one module inside a second reuse the
    earlier `.pyc`, and a byte-length-preserving revert is exactly the
    kind that survives one.
    """
    for cached in HERE.glob('__pycache__/*.pyc'):
        cached.unlink()


def _red_controls():
    """The failing test names across the control suites.

    A suite that times out is recorded as a timeout and the sweep carries
    on: one slow suite must not discard every verdict already collected,
    which is what an unhandled `TimeoutExpired` did.
    """
    red, timed_out = [], []
    for suite in CONTROL_SUITES:
        _clear_bytecode()
        try:
            finished = subprocess.run(
                [sys.executable, str(HERE / Path(suite).name)],
                cwd=HERE.parent, env=_util.child_coverage('scrub'),
                capture_output=True, text=True, timeout=_SUITE_TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out.append(Path(suite).name)
            continue
        for line in finished.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith(('FAIL', 'ERROR')):
                red.append(f'{Path(suite).stem}.'
                           f'{stripped.split()[1].split(":")[0]}')
    return red, timed_out


def revert_sites():
    """Revert every derived site in turn and report what died.

    Returns the survivors -- the sites whose revert left every suite
    green, which is the finding this driver exists to name."""
    missing = undecided_sites()
    if missing:
        raise SystemExit(f'no revert written for: {sorted(missing)}')
    survivors, unmutated = [], []
    for question, answer in ACCEPTANCE.items():
        print(f'  {question[:44]:46s} {answer}')
    print(f'{"site":46s} {"controls that die":44s} survivors')
    for stale in HERE.glob('__pycache__'):
        for cached in stale.glob('*.pyc'):
            cached.unlink()
    for site in sorted(retirement_sites()):
        module = site.rpartition('.')[0]
        path = HERE / (module + '.py')
        original = path.read_text(encoding='utf-8')
        if site in NO_MUTATION:
            unmutated.append(site)
            print(f'{site:46s} NOT TESTED: {NO_MUTATION[site][:46]}')
            continue
        old, new = REVERTS[site]
        if old not in original:
            print(f'{site:46s} ANCHOR-MISSING')
            survivors.append(site + ' (anchor missing)')
            continue
        mutant = original.replace(old, new, 1)
        try:
            ast.parse(mutant)
        except SyntaxError as error:
            # A revert that does not parse would blank the module and every
            # suite would fail for that reason, which reads exactly like a
            # control that died. Say so instead.
            print(f'{site:46s} MUTANT-DOES-NOT-PARSE: {error}')
            survivors.append(site + ' (mutant does not parse)')
            continue
        path.write_text(mutant, encoding='utf-8')
        try:
            red, timed_out = _red_controls()
        finally:
            path.write_text(original, encoding='utf-8')
        if timed_out:
            print(f'{site:46s} TIMED OUT: {timed_out}')
            continue
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
