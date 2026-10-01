"""The composed-bound rule: a call-site deadline is DERIVED, never written.

Eight sites in `tests/` deliberately keep their bound at their own call
site rather than reaching `tests/_noderun.py`'s shared hang detector,
because routing them through it puts every module they call inside that
launcher's audited path. The cost of staying out is a rule nothing else
enforces: a deadline that is a number TYPED at the call site, rather than
composed from a recorded table and the shared multiple, is read by nothing
else in this tree.

Three properties, and each has a fabrication the other two accept — which
is why one control is not enough. The ALGEBRA: the deadline must be
`round()`ed from the table. The VALUE: the module must not rebind
`SITE_HANG_MULTIPLE`, because a deadline spelled with the NAME proves the
name is used and nothing about what it is worth. The TABLE: the recorded
samples must be large enough to compose a bound a child could reach,
because the algebra rule reads everything downstream of the table and a
table nobody measured satisfies it as long as someone did the arithmetic.

`tests/test_node_launch_routing.py` holds the three controls; this is the
rule they drive, split out so a reader of a deadline finds it beside the
deadline rather than inside a suite about another subject.
"""
import ast
from pathlib import Path

from _node_launch_routing import SITE_HANG_MULTIPLE

_TESTS_DIR = Path(__file__).resolve().parent

# `<stem>_DEADLINE_S`. This is the OTHER direction of the shared-detector
# rule — the sites that do NOT reach the shared detector still have to stop
# a wedged child, and nothing else holds them honest.
COMPOSED_BOUND_SITES = (
    ('_realbrowser.py', 'NODE_PROBE'),
    ('_realbrowser.py', 'MINIMAL_SPAWN'),
    ('_gm_harness.py', 'GM_CHILD'),
    ('test_real_browser_control_extension.py', 'CONTROL_CHILD'),
    ('test_real_browser_environment.py', 'REPO_PROBE'),
    ('test_real_browser_environment.py', 'WORKER_PROBE'),
    ('test_real_browser_harness.py', 'WORKER_CHECK'),
    ('test_real_browser_harness.py', 'CDP_HARNESS'),
)


def module_constants(tree):
    """`name -> value node` for a module's top-level bindings.

    Read from the parse rather than from a maintained list, so a constant
    the module adds is inside this rule's reach on the day it is added.
    """
    constants = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node.value is None:
            continue
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target])
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value
    return constants


def site_constants(module_name, root=None):
    """A module's module-level bindings as SOURCE, read by the shared reader.

    Source and not the parse tree, because the algebra a deadline is
    composed by is a spelling: `round(X_SLOWEST_S * SITE_HANG_MULTIPLE)` and
    a folded `round(X_SLOWEST_S * 5)` are the same figure and different
    rules, and only the first is what this repository documents.
    """
    source = (_TESTS_DIR if root is None else root) / module_name
    return {name: ast.unparse(value)
            for name, value in module_constants(
                ast.parse(source.read_text(encoding='utf-8'))).items()}


# Annotated, not inferred: a `{'table': None}` literal types its values as
# `None`, and the cache then refuses the dict it is built to hold.
_SIBLINGS: dict = {'table': None}


def _follow_reexports(exported):
    """Let a module's own exports include what it imported from a sibling.

    `test_cli.py` does not assign `CLI`; it imports it from
    `_cli_helpers.py`, and the CLI suites reach it as `test_cli.CLI`. One
    level is enough and one level is all that is claimed: a re-export chain
    deeper than that shows up as unresolved rather than as a guess.
    """
    trees = {}
    for stem in exported:
        path = _TESTS_DIR / f'{stem}.py'
        if path.is_file():
            trees[stem] = ast.parse(path.read_text(encoding='utf-8'))
    resolved = {}
    for stem, tree in trees.items():
        names = dict(exported.get(stem, {}))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level or node.module not in trees:
                continue
            for alias in node.names:
                source = exported.get(node.module, {}).get(alias.name)
                if source and alias.name not in names:
                    names[alias.name] = source
        resolved[stem] = names
    return resolved


def sibling_constants():
    """Every module-level constant under `tests/`, by module then name.

    Most suites spell a shared figure as a constant IMPORTED from a
    sibling, so a reader that looks only at the file in front of it reads
    every one of them as absent. Only module-level constants are followed:
    a helper FUNCTION's value is a question about its body, not its name.

    Read once and kept, because this is asked once per composed module and
    re-parsing every file in `tests/` each time is quadratic in a tree this
    size.
    """
    if _SIBLINGS.get('table') is not None:
        return _SIBLINGS['table']
    exported = {}
    for path in sorted(_TESTS_DIR.glob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        exported.setdefault(path.stem, {}).setdefault(
                            target.id, []).append(node.value)
    _SIBLINGS['table'] = _follow_reexports(exported)
    return _SIBLINGS['table']


def imported_constants(tree, exported):
    """Bind the constants this module imports from a sibling under `tests/`."""
    bound = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level or node.module not in exported:
            continue
        for alias in node.names:
            source = exported[node.module].get(alias.name)
            if source:
                bound.setdefault(alias.asname or alias.name, []).extend(source)
    return bound


def _resolved_constant(name, own, imported):
    """A constant as the module under test BINDS it, as source.

    A module-level assignment shadows the import it shares a name with, so
    the two are read in that order. This exists for `SITE_HANG_MULTIPLE`: a
    rule that checks a deadline is spelled `round(X * SITE_HANG_MULTIPLE)`
    has checked that the NAME is used and nothing about what the name is
    worth, and the name is rebindable at module scope. One line in the real
    `tests/_gm_harness.py` composing a 208-day bound left the shared
    detector, the audit and the control that reads the spelling all green.
    """
    found = own.get(name) or imported.get(name)
    if not found:
        return None
    # A module-level assignment arrives as the one node it bound; an import
    # arrives as a LIST of the sources it resolved to, because a name bound
    # more than once is what a shadowing import looks like.
    return ast.unparse(found[0] if isinstance(found, list) else found)


def shared_multiple(module_name, root=None):
    """`SITE_HANG_MULTIPLE` as the module under test binds it, by VALUE.

    Checking that a deadline is spelled with the shared multiple checks
    that the NAME is used; it says nothing about what the name is worth, and
    the name is rebindable at module scope. One line in the real
    `tests/_gm_harness.py` — `SITE_HANG_MULTIPLE = 10 ** 6` — composes a
    208-day hang detector, and every control that reads the spelling stayed
    green on it, because the thin-table control catches a fabricated TABLE
    and this is a fabricated MULTIPLE.

    So the value is resolved where the deadline is composed — the module's
    own assignment shadowing the import it shares a name with — and compared
    to the shared constant rather than to its name. A value this cannot fold
    is not the shared multiple, which is the fail-closed direction: a reader
    that cannot prove the figure is the one this repository documents must
    not certify it.
    """
    source = (_TESTS_DIR if root is None else root) / module_name
    tree = ast.parse(source.read_text(encoding='utf-8'))
    resolved = _resolved_constant(
        'SITE_HANG_MULTIPLE', dict(module_constants(tree)),
        imported_constants(tree, sibling_constants()))
    if resolved is None:
        return None
    try:
        return ast.literal_eval(resolved)
    except (ValueError, SyntaxError, TypeError):
        return None


def table_values(source):
    """The floats in a recorded sample table, read from its own source."""
    return [node.value for node in ast.walk(ast.parse('X = ' + source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)]


def is_composed(constants, stem, multiple):
    """Whether a stem's deadline is `round()`ed from a table and the multiple.

    Composed means all three: a recorded table of samples, a `max()` taken
    over it, and a deadline rounded from that by the shared multiple. A
    number written at the call site is none of them. `multiple` is that
    multiple resolved where the deadline is composed, so the last step is a
    comparison of VALUES rather than of names, and a caller that cannot
    resolve it passes `None` and is refused rather than believed.
    """
    def has(name):
        # `.get`, not `[...]`: a site that deleted one of the three must be
        # REFUSED by this rule and named by the caller, not raise a KeyError
        # naming a dictionary key at a reader who cannot act on it.
        return name in constants

    tables = [name for name in constants
              if name.startswith(stem) and name.endswith('_SAMPLES_S')]
    if not tables or not has(f'{stem}_SLOWEST_S'):
        return False
    taken = ast.parse('X = ' + constants[f'{stem}_SLOWEST_S']).body[0].value
    if not (isinstance(taken, ast.Call)
            and ast.unparse(taken.func) == 'max'):
        return False
    # `*TABLE` unparses with its star, so a substring test reads both
    # the one-table `max(TABLE)` and the two-table `max(*A, *B)`.
    sources = [ast.unparse(argument) for argument in taken.args]
    if not any(any(table in source for source in sources)
               for table in tables):
        return False
    if multiple != SITE_HANG_MULTIPLE:
        return False
    return has(f'{stem}_DEADLINE_S') and constants[f'{stem}_DEADLINE_S'] == (
        f'round({stem}_SLOWEST_S * SITE_HANG_MULTIPLE)')


def retyped_bound_sites(root=None):
    """The composed-bound sites that no longer compose their figure."""
    typed = []
    for module_name, stem in COMPOSED_BOUND_SITES:
        if not is_composed(site_constants(module_name, root), stem,
                           shared_multiple(module_name, root)):
            typed.append(f'{module_name}:{stem}')
    return typed


def planted_module_copy(root, module_name, plant):
    """A real module's own bytes with `plant` appended, written under `root`.

    The plants belong in a REAL target. A string handed to `ast.parse` shows
    what the rule thinks of a shape; it cannot show whether the rule and the
    module the rule is written about agree, which is the question a plant is
    for. A copy answers it without a test rewriting a tracked file while
    every other suite is reading it: the bytes are the module's own, the
    rule reads them off disk, and the plant is the one change under test.
    """
    source = (_TESTS_DIR / module_name).read_text(encoding='utf-8')
    root.mkdir(parents=True, exist_ok=True)
    (root / module_name).write_text(f'{source}\n\n{plant}\n', encoding='utf-8')


SHARED_LAUNCHER = '_noderun.py'


def module_level_constants(path):
    """`name -> source` for one module's top-level assignments."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return {target.id: ast.unparse(node.value)
            for node in tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)}


def derived_population(tests_dir=None):
    """The composed-bound sites the TREE carries, derived rather than listed.

    Two readings, both off the tree, so a table that drifts from what the
    modules hold is visible to a control rather than only to a reader:

    - every module-level `*_DEADLINE_S` outside the shared launcher, paired
      with the stem it derives from. The shared launcher is excluded because
      its two are composed inside it, which is the whole point of the other
      eight keeping their bounds at their own call sites instead;
    - every module-level `*_SAMPLES_S` together with whether some
      `*_SLOWEST_S` in the same module reads it. A table nothing composes
      from is a figure no deadline was built out of.
    """
    root = _TESTS_DIR if tests_dir is None else tests_dir
    sites = set()
    tables = set()
    unconsumed = []
    for path in sorted(root.glob('*.py')):
        constants = module_level_constants(path)
        slowest = {name: source for name, source in constants.items()
                   if name.endswith('_SLOWEST_S')}
        for name, source in constants.items():
            if name.endswith('_DEADLINE_S') and path.name != SHARED_LAUNCHER:
                sites.add((path.name, name[:-len('_DEADLINE_S')]))
            elif name.endswith('_SAMPLES_S'):
                tables.add((path.name, name))
                if not any(name in read for read in slowest.values()):
                    unconsumed.append(f'{path.name}:{name}')
    return sites, tables, unconsumed


def composed_population(root, module_name, plant):
    """Every composed-bound module, copied under `root`, one of them planted.

    The whole population, not the planted module alone: a control pointed
    at a directory holding one file answers a question about that file, and
    the question here is whether the control still ACCEPTS the seven
    unplanted ones.
    """
    for name, _ in COMPOSED_BOUND_SITES:
        planted_module_copy(
            root, name, plant if name == module_name else 'VALUE = 1')
    return root
