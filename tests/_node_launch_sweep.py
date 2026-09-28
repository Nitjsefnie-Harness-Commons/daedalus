"""The walk: which launches a module holds, and what the tree fails on.

`tests/_node_launch_routing.py` holds the RULE — the tables that close its
population and the readers that decide whether one launch is bounded. This
holds the part that goes looking: the population a sweep reads, the
findings one module contributes, and the plants that put a real module's
own bytes in front of both.

The seam is the one a reader meets first. A question about a launch —
"is this child bounded", "may this row excuse it" — is about the module
in front of you; a question about the TREE — "which modules did the walk
read", "does a carve-out hide a site" — is not, and answering it from the
same file is what let a walk narrow its own population in silence.

A shared helper rather than a suite, for the reason the rule's own module
states: two suites need both halves and a sibling SUITE import is a seam
this repository refuses.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
from _node_launch_routing import (  # noqa: E402
    CLASSIFYING_MODULES, NOT_FIXED_WORK, NOT_SITES, UNRESOLVED_LAUNCHES,
    VERDICT_NODE, VERDICT_UNRESOLVED, _assignments_in, _bounds_its_own_child,
    _deadline, _executable_verdict, _exempt, _imported_constants,
    _imported_stems, _sibling_constants, _TESTS_DIR)
from _command_type_readers import _parents  # noqa: E402



def _launches(tree, exported=None):
    """Every launch in a module, with the shape the exemption table keys on.

    The population is every call the repository's own predicate says places
    a child. Nothing is filtered out before that, so a module the walk
    cannot parse, or a launcher spelled a way this does not follow, shows up
    as an unclassified site rather than as a clean tree.

    Each row carries the `scope` it was found in — the container's own
    statements, the same node the walk entered — because the questions
    asked ABOUT a launch are questions about the code around it, and a
    reader that reaches outside the launch's own scope can be answered by a
    sibling it has nothing to do with. That is a false green, which is the
    direction that loses a site, so the scope travels with the row rather
    than being re-derived per question.
    """
    if exported is None:
        exported = _sibling_constants()
    receivers = census._subprocess_receivers(tree)
    direct = census._from_import_launches(tree)
    aliases = census._member_aliases(tree, receivers, direct)
    module_constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module_constants.setdefault(
                        target.id, []).append(node.value)
    found = []
    shared = {
        'exported': exported,
        'stems': _imported_stems(tree, exported),
    }
    scopes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append((node.name, _own_statements(node.body)))
    # A launch at module scope, in a class body, or in a nested function is
    # a site too, and a walk that only entered top-level functions would
    # miss every one of them. Each is built the same way: the container's
    # own statements, minus the definitions that are scopes of their own —
    # so a method is counted once under its own name and a class-body
    # statement once under the class, rather than twice or not at all.
    scopes.append(('<module>', _own_statements(tree.body)))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            scopes.append((node.name, _own_statements(node.body)))
    for name, scope in scopes:
        scope_context = dict(shared)
        scope_context['bound'] = dict(module_constants)
        scope_context['bound'].update(_imported_constants(tree, exported))
        if name != '<module>':
            scope_context['bound'].update(_assignments_in(scope))
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            if not census._is_launch(node, receivers, direct, aliases):
                continue
            argv = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg in ('args', 'argv')),
                None)
            found.append({
                'line': node.lineno,
                'callee': ast.unparse(node.func),
                'function': name,
                'verdict': (
                    _executable_verdict(argv, scope_context)
                    if argv is not None else (VERDICT_UNRESOLVED, None)),
                'deadline': _deadline(node),
                'node': node,
                'scope': scope,
            })
    return sorted(found, key=lambda row: row['line'])

def _own_statements(body):
    """A container's own statements: its body minus every nested definition.

    A method, a nested function and a nested class are each a scope the walk
    enters on its own, so leaving one in here would report its launches a
    second time under the enclosing name — and a table row keyed on the
    inner name would then silence both, which is wider than the key reads.
    """
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return ast.Module(
        body=[statement for statement in body
              if not isinstance(statement, definitions)],
        type_ignores=[])

def _planted_module_copy(root, module_name, plant):
    """A real module's own bytes with `plant` appended, written under `root`.

    The plants belong in a REAL target. A string handed to `ast.parse` shows
    what the walk thinks of a shape; it cannot show whether the walk and the
    module the rule is written about agree, which is the question a plant is
    for. A copy answers it without a test rewriting a tracked file while
    every other suite is reading it: the bytes are the module's own, the
    walk reads them off disk, and the plant is the one change under test.
    """
    source = (_TESTS_DIR / module_name).read_text(encoding='utf-8')
    root.mkdir(parents=True, exist_ok=True)
    (root / module_name).write_text(f'{source}\n\n{plant}\n', encoding='utf-8')

def _module_findings(name, tree, used):
    """The three site classes one module contributes.

    Split out of `_routing_sweep` so `_carve_outs` asks the same question
    the sweep asks. A skip list whose members are exempt from a decision is
    only honest if something still makes that decision about them, and one
    reader of the launch rules is what makes it true.
    """
    parents = _parents(tree)
    unrouted, unbounded, unclassified = [], [], []
    for launch in _launches(tree):
        shape = (name, launch['function'], launch['callee'])
        verdict = launch['verdict'][0]
        if verdict == VERDICT_UNRESOLVED:
            if shape in UNRESOLVED_LAUNCHES:
                used.add(shape)
            else:
                unclassified.append(f'{name}:{launch["line"]} in '
                                    f'{launch["function"]}()')
            continue
        if verdict != VERDICT_NODE:
            continue
        if name in CLASSIFYING_MODULES:
            if not _bounds_its_own_child(launch, parents):
                unbounded.append(f'{name}:{launch["line"]}')
            continue
        if _exempt(shape, launch):
            used.add(shape)
            continue
        # A `Popen` carries no `timeout=` to print, and that is the most
        # likely real finding this control exists to report, so the
        # message names the child rather than raising on the way there.
        shown = launch['deadline']
        unrouted.append(
            f'{name}:{launch["line"]} (timeout='
            f'{ast.unparse(shown) if shown is not None else "none"})')
    return unrouted, unbounded, unclassified

def _carve_outs(root):
    """`NOT_SITES` members the walk would have reported on.

    A member is skipped whole, so a module added here hides every launch in
    it, and nothing in the rest of the table would notice: the row it silences
    was never read. This asks each member the question the sweep asks and
    requires the answer to be empty, so the exemption is a no-op on the tree
    as it stands and a real module added to the list fails here. Only the two
    classes a launch rule decides are asked — an unresolvable executable is
    `UNRESOLVED_LAUNCHES`' business, and a member that needed a row there
    would be a member in two tables.
    """
    offenders = []
    for name in sorted(NOT_SITES):
        path = root / name
        if not path.is_file():
            continue
        used = set()
        unrouted, unbounded, _ = _module_findings(
            name, ast.parse(path.read_text(encoding='utf-8')), used)
        offenders.extend(f'{name} {site}' for site in unrouted + unbounded)
    return offenders

def _population(root=None):
    """The modules one sweep reads, in order — the population, not a sample.

    A separate function rather than a loop inside `_routing_sweep` because
    a population nothing can inspect is a population that can be narrowed
    in silence: skipping `tests/_gm_harness.py` out of the walk and nothing
    else left every control green, and the `unused`-row check does not
    close that direction, because a module with no allowance row of its own
    takes no row with it when it stops being read.
    `tests/test_node_launch_routing.py` reads this against what the sweep
    says it actually walked, so the two cannot drift.
    """
    root = _TESTS_DIR if root is None else root
    return [path for path in sorted(root.glob('*.py'))
            if path.name not in NOT_SITES]

def _routing_sweep(root=None, walked=None):
    """The five failure lists the tree produces.

    `root` is the directory the walk reads, and it exists so a control can
    run the walk over a COPY of a real module with a defect planted in it —
    the evidence that the rule fires on the module it is written about,
    without a test rewriting a tracked file while other suites read it.
    `walked` is filled with the names the sweep actually read, so a control
    measures this walk rather than re-deriving what it should have read.
    """
    root = _TESTS_DIR if root is None else root
    unrouted, unbounded, unclassified, used = [], [], [], set()
    for path in _population(root):
        if walked is not None:
            walked.append(path.name)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        found = _module_findings(path.name, tree, used)
        unrouted += found[0]
        unbounded += found[1]
        unclassified += found[2]
    unused = [(table, row) for table, rows in (
        ('UNRESOLVED_LAUNCHES', UNRESOLVED_LAUNCHES),
        ('NOT_FIXED_WORK', NOT_FIXED_WORK))
        for row in sorted(set(rows) - used)]
    return unrouted, unbounded, unclassified, _carve_outs(root), unused
