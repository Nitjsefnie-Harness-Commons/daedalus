"""The re-implementation recogniser, in both languages, over a source map.

The rule it reads is stated in `tests/test_reserved_test_names.py`, over
the union `tests/_reserved_names.py` joins. This is the machinery both run,
so neither imports the other: `run_tests.py` gives every suite its own
process, and a suite that reaches a sibling suite's body re-executes that
whole module inside itself.
"""
import ast
import subprocess
from collections import namedtuple
from pathlib import Path

import _js_functions
import _util
from _branch_boundary import _parsed
from _helper_binds import definitions, scan

ROOT = _util.ROOT

Reimplementation = namedtuple('Reimplementation', 'path name lines owners')
JsReimplementation = namedtuple(
    'JsReimplementation', 'path name line body_lines owners')
JsDeclaration = namedtuple('JsDeclaration', 'name line body_lines')

JS_FLOOR = 3

_LIVE_SOURCES = None


def _in_tests(path):
    return Path(path).parent == Path('tests')


def _is_shared_helper(path):
    return _in_tests(path) and Path(path).stem.startswith('_')


def _entry_points(tree):
    """The names a `__main__` guard CALLS, and nothing else it mentions.

    A script's own entry point is not a shared helper's name: every
    module that runs itself under that guard has one, and none of them
    is a copy of any other. Both operand orders of the comparison are
    the same guard.

    The unit is a CALL, not a mention. Collecting every `ast.Name` the
    guard touches dropped a name from the offender's definitions AND from
    the owner set, so a module that merely printed, assigned or tested
    the truthiness of a shared helper's name lost that name twice over —
    a false green in both directions, and a false green that grows
    silently as a helper's guard gains a line. What is excluded is a
    name the guard never invokes.
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if not _is_a_main_guard(node.test):
            continue
        for statement in node.body:
            names.update(_called_names(statement))
    return names


def _is_a_main_guard(test):
    if (not isinstance(test, ast.Compare) or not test.ops
            or not isinstance(test.ops[0], ast.Eq)
            or len(test.comparators) != 1):
        return False
    left, right = test.left, test.comparators[0]
    if isinstance(left, ast.Name) and left.id == '__name__':
        guarded = right
    elif isinstance(right, ast.Name) and right.id == '__name__':
        guarded = left
    else:
        return False
    return isinstance(guarded, ast.Constant) and guarded.value == '__main__'


def _called_names(node):
    """The names a statement INVOKES, at its own level or a nested one."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            names.add(sub.func.id)
    return names


def _is_the_owner(path, name, owners):
    """Limb two, read as a set: the module that owns a name alone is the
    definition, while two owners leave each of them a re-implementation
    of the other.
    """
    held = owners.get(name, ())
    return len(held) == 1 and path in held


def _imports_the_name(imports, name, stems):
    """Limb three: the name arrives from a module inside the tests tree,
    by either import spelling that binds the name itself.
    """
    return bool({source for lines in imports.get(name, {}).values()
                 for source in lines} & stems)


def reimplementations(sources, owner_is_the_definition=_is_the_owner,
                      an_import_settles_it=_imports_the_name):
    """Every re-implementation over a {path: text} map of modules.

    The two parameters are the limbs the recogniser is built from, so a
    caller can drop one and watch the site only that limb decides. A
    module the recogniser cannot parse fails the control, naming the
    file, rather than being dropped.
    """
    stems = {Path(path).stem for path in sources}
    owners = {}
    parsed = {}
    for path in sorted(sources):
        tree = _parsed(path, sources[path])
        imports, _ = scan(tree)
        defined = definitions(tree)
        entry = _entry_points(tree)
        parsed[path] = (imports, {name: lines for name, lines
                                  in defined.items() if name not in entry},
                        entry)
        if _is_shared_helper(path):
            for name in defined:
                if name not in entry:
                    owners.setdefault(name, set()).add(path)

    findings = []
    for path in sorted(sources):
        if not _in_tests(path):
            continue
        imports, defined, entry = parsed[path]
        for name in sorted(defined):
            if name not in owners or owner_is_the_definition(path, name,
                                                             owners):
                continue
            if an_import_settles_it(imports, name, stems):
                continue
            findings.append(Reimplementation(
                path, name, sorted(defined[name]), sorted(owners[name])))
    return findings


def _live():
    listed = subprocess.run(
        ['git', 'ls-files', 'tests/*.py'], cwd=ROOT, capture_output=True,
        text=True, check=True, env=_util.child_coverage('scrub')
    ).stdout.splitlines()
    assert listed, 'git ls-files named no tests module'
    sources = {name: (ROOT / name).read_text(encoding='utf-8')
               for name in listed}
    return sources, reimplementations(sources)


def js_declarations(sources, truncated=None):
    """{path: [JsDeclaration]} for every JavaScript a module declares.

    The reader raises on a bracket that closes the wrong thing, naming
    the constant — the same fail-closed posture the Python side takes on
    a module that does not parse. A body that merely runs out is a
    fragment and is dropped, and `truncated`, when given, collects how
    many: that is the one hole in this scan, and a hole nobody counts is
    a hole nobody sees.
    """
    declared = {}
    for path in sorted(sources):
        found = []
        for text, starts in _js_functions.documents(sources[path], path):
            dropped = []
            for item in _js_functions.declarations(text, path, dropped):
                found.append(JsDeclaration(
                    item.name, _js_functions.lineno_at(starts, item.offset),
                    item.body_lines))
            if truncated is not None:
                truncated.append((path, dropped[0]))
        if found:
            declared[path] = found
    return declared


def js_reimplementations(sources, owner_is_the_definition=_is_the_owner,
                         minimum=JS_FLOOR):
    """Every re-implementation of a shared helper's JavaScript name.

    The same rule `reimplementations` applies, read in the other
    language: a shared-helper module owns a name when it defines
    `function <name>` inside a string constant, and a tests module
    re-implements it when it does the same, is not the owner, and the
    block is at least `minimum` body lines. The owner set is read as a
    set, so two helpers defining one name leave each of them a
    re-implementation of the other.
    """
    declared = js_declarations(sources)
    owners = {}
    for path, items in declared.items():
        if not _is_shared_helper(path):
            continue
        for item in items:
            if item.body_lines >= minimum:
                owners.setdefault(item.name, set()).add(path)
    findings = []
    for path in sorted(declared):
        if not _in_tests(path):
            continue
        for item in declared[path]:
            if (item.body_lines < minimum or item.name not in owners
                    or owner_is_the_definition(path, item.name, owners)):
                continue
            findings.append(JsReimplementation(
                path, item.name, item.line, item.body_lines,
                sorted(owners[item.name])))
    return findings


def _live_js():
    sources, _ = _live()
    return sources, js_reimplementations(sources)


def _live_sources():
    """The tracked tests modules, memoised: four tests read the whole
    tree, and this suite measured 45 s recomputing the readers per test
    against 13 s sharing them. One machine, warm cache.
    """
    global _LIVE_SOURCES
    if _LIVE_SOURCES is None:
        listed = subprocess.run(
            ['git', 'ls-files', 'tests/*.py'], cwd=ROOT,
            capture_output=True, text=True, check=True,
            env=_util.child_coverage('scrub')).stdout.splitlines()
        assert listed, 'git ls-files named no tests module'
        _LIVE_SOURCES = {name: (ROOT / name).read_text(encoding='utf-8')
                         for name in listed}
    return _LIVE_SOURCES
