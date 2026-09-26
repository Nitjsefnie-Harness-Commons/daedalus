#!/usr/bin/env python3
"""No tests module may re-implement a shared helper's name, not import it.

A helper is defined once in a shared helper module and imported by every
user. A module that needs a shared helper's behaviour and defines a
same-named local instead reads its own copy, so the one definition
nobody has to keep correct is the one the suite runs. The rule is the
complement of `test_helper_shadow_boundaries.py`: that control finds a
name bound both by an import out of the tests tree and by a local
binder, and this one finds the same collision with the import deleted.

A shared-helper module is a module under `tests/` whose file stem
starts with `_`. A name is owned by one when that module binds it at
module-execution scope as a `def`, an `async def` or a `class`. A
tests module is a re-implementation when it also binds that name in one
of those three forms, is not itself the owner, and imports that name
from no sibling under `tests/`. An owner is not a re-implementation of
itself, but the owner set is read as a set rather than per owner: where
two helper modules bind one name, each is a re-implementation of the
other and both are reported. An import binds the LOCAL name it brings
in, so `from X import _Y` and `import X as _Y` both suppress the
report, `import X` does not bind `_Y` at all and so does not suppress
it, and an import from outside the tests tree never suppresses it.

The scope rules are the shadow control's, read from `_helper_binds` so
there is one copy of them rather than two. Only the defining forms
count: a walrus, a `for` target, a `with ... as`, an `except E as`, a
`match` capture and a bind in an `else` or `finally` body all bind
during module execution, and none of them is a second DEFINITION of a
name. A name its own module calls inside `if __name__ == '__main__':`
is that module's script entry point, excluded on either side of the
comparison, so the hundred and seventy suites that each call a `main` are
not copies of the one helper module that also has one.

What this control does not see, by design: a local spelled without the
owner's leading underscore, which is a spelling difference and not a
shared-helper one; a `from X import *`, whose names the rule cannot
enumerate; a helper reached through a name computed at run time; and a
same-named local that genuinely re-implements nothing, which is what
every row of UNCONSOLIDATED_NAMES is.

The residue ships as that table, and the boundary on it is the BRANCH'S
OWN DIFF, not "the table may only shrink". A shrink-only rule is
unlandable on a base that moves: every new tests module `main` lands can
surface a collision nobody introduced, and the control would go red for
code the branch never touched. The enforceable boundary is instead that
a row may name any pre-existing site, and may NOT name a file this
branch adds or edits — so the table can absorb what `main` lands
underneath it while still being unable to excuse one line of what the
branch itself wrote. That is a rule over a derived set of paths rather
than a promise about the author's intent, which is why it can be a
control at all.

THE SAME RULE IN JAVASCRIPT. A `function` inside a Python string
literal is not a Python binding, and this repository's duplicate-code
checker is the only thing that looks at one: it compares parsed Python
ASTs, so it sees JavaScript only as the characters between two string
quotes, and its `min-similarity-lines=15` is a Python function-body
threshold that no five-line JavaScript block reaches. So the rule is
read a second time, in the other language, by `js_reimplementations`
below over the reader in `_js_functions`. A shared helper module owns a
JavaScript name when it defines `function <name>` inside a string
constant; a tests module re-implements it when it does the same, is not
the owner, and the block is at least `JS_FLOOR` body lines.

What the JavaScript rule does NOT carry over, stated rather than
assumed: the import limb. There is no `from X import eventTarget` in
JavaScript, so nothing records that a module took the name from a
shared helper rather than writing its own; a module that both splices a
shared stub in and still writes the name is a finding either way, which
is the conservative direction, but a module that ONLY splices one in is
invisible here and must be right by construction.

`JS_FLOOR` is this rule's own size floor, and it is scoped to it. The
claim it rests on is a PROPERTY, not a count, and
`test_the_size_floor_excludes_no_copy_of_the_class` asserts it: nothing
in the residue sits at exactly two body lines, so a floor of two and a
floor of three select the same declarations, and every copy of the
shared class in the tree is at or above the floor, so the floor excludes
no copy of the thing it exists for. Three is also the shortest copy of
that class — `function eventTarget() { return { addListener() {} }; }`
is three lines from brace to brace — and the band below it is one-line
wrappers no harness copies.

The population and the counts are `js_residue_histogram`, which the
control prints on every run. They are deliberately not written here: a
count in a docstring is a claim somebody has to reproduce, and this one
has been measured three ways with three answers, because a population
described in prose is resolved by each reader slightly differently.
Lowering `.pylintrc`'s `min-similarity-lines` instead would fire on
ordinary Python, which is why the floor lives here. The class's
shortest copy is a three-line body, the `function eventTarget() { return
{ addListener() {} }; }` spelling, so three is the floor: it excludes the
one-liner band and catches every spelling of the class, where one would
catch trivial wrappers and fifteen, the Python value, would catch none
of them. Two measures identically, so nothing rides on that choice;
three is the value the class's own shortest copy sets. Lowering
`.pylintrc`'s `min-similarity-lines` instead would fire on ordinary
Python, which is why the floor lives here.
"""
import ast
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _js_functions  # noqa: E402
import _util  # noqa: E402
from _branch_boundary import (  # noqa: E402
    BRANCH_BASES, _parsed, introduced_rows, js_digests, python_digests)
from _helper_binds import definitions, scan  # noqa: E402
from _unconsolidated_js_names import (  # noqa: E402
    UNCONSOLIDATED_JS_NAMES)
from _unconsolidated_names import UNCONSOLIDATED_NAMES  # noqa: E402

ROOT = _util.ROOT

Reimplementation = namedtuple('Reimplementation', 'path name lines owners')
JsReimplementation = namedtuple(
    'JsReimplementation', 'path name line body_lines owners')
JsDeclaration = namedtuple('JsDeclaration', 'name line body_lines')

JS_FLOOR = 3

_LIVE_SOURCES = None


def _mod(*lines):
    return ''.join(line + '\n' for line in lines)


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
    """The tracked tests modules, memoised: four tests read the whole tree
    and the two readers are the expensive part, so recomputing them per
    test cost this suite 45 seconds where the shared scans cost 13."""
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


def test_no_tests_module_reimplements_a_shared_helper_name(tmp):
    del tmp
    sources, findings = _live()
    assert sources, 'the tests tree enumerated no module'
    unallowed = sorted(
        f'{item.path}::{item.name} owned by {item.owners}'
        for item in findings
        if (item.path, item.name) not in UNCONSOLIDATED_NAMES)
    assert not unallowed, (
        'tests modules re-implement a shared helper name with no row in '
        'UNCONSOLIDATED_NAMES:\n' + '\n'.join(unallowed))


def test_an_allowance_row_naming_no_live_site_fails(tmp):
    del tmp
    _, findings = _live()
    live = {(item.path, item.name) for item in findings}
    for key in sorted(UNCONSOLIDATED_NAMES):
        assert key in live, (
            f'UNCONSOLIDATED_NAMES row {key} has no live '
            're-implementation; a stale allowance is a refusal')
        assert UNCONSOLIDATED_NAMES[key].strip(), (
            f'UNCONSOLIDATED_NAMES row {key} carries no justification')


def test_a_row_may_not_name_a_declaration_this_branch_added(tmp):
    del tmp
    introduced = introduced_rows(UNCONSOLIDATED_NAMES, python_digests, ROOT)
    assert introduced is not None, (
        'the branch boundary could not be evaluated: this checkout '
        'resolves neither ' + ' nor '.join(BRANCH_BASES) + ', so nothing '
        'here says a row is not excusing a definition the branch wrote. '
        'That is a refusal, not a pass — fetch the base and re-run.')
    assert not introduced, (
        'UNCONSOLIDATED_NAMES rows excuse a definition the base tree does '
        f'not carry, so the branch wrote it: {introduced}')


def test_no_tests_module_reimplements_a_shared_javascript_name(tmp):
    del tmp
    sources, findings = _live_js()
    assert sources, 'the tests tree enumerated no module'
    # This reader's one hole, sized: see the count above.
    dropped = []
    js_declarations(sources, dropped)
    cut = [(path, count) for path, count in dropped if count]
    print(f'[js] {sum(count for _p, count in dropped)} JavaScript bodies '
          f'were dropped as truncated, in {len(cut)} of {len(dropped)} '
          f'documents read')
    if cut:
        print('[js] first few: ' + ', '.join(
            f'{path}:{count}' for path, count in cut[:5]))
    unallowed = sorted(
        f'{item.path}::{item.name} at line {item.line} owned by '
        f'{item.owners}'
        for item in findings
        if (item.path, item.name) not in UNCONSOLIDATED_JS_NAMES)
    assert not unallowed, (
        'tests modules re-implement a shared helper\'s JavaScript name '
        'with no row in UNCONSOLIDATED_JS_NAMES:\n' + '\n'.join(unallowed))


def test_an_allowance_row_naming_no_live_javascript_site_fails(tmp):
    del tmp
    _, findings = _live_js()
    live = {(item.path, item.name) for item in findings}
    for key in sorted(UNCONSOLIDATED_JS_NAMES):
        assert key in live, (
            f'UNCONSOLIDATED_JS_NAMES row {key} has no live JavaScript '
            're-implementation; a stale allowance is a refusal')
        assert UNCONSOLIDATED_JS_NAMES[key].strip(), (
            f'UNCONSOLIDATED_JS_NAMES row {key} carries no justification')


def test_the_boundary_says_which_declaration_the_branch_wrote(tmp):
    """The boundary bites on a real repository, on both sides, in both
    languages, and on a SECOND copy as well as on a new name.

    Four cases, and the third is the one a name-keyed or set-keyed rule
    misses: a second declaration whose body the base ALREADY carries
    adds nothing to a set of digests, so a set-based comparison reports
    the file as clean. The base carries one `twin`; the head carries
    two, byte-identical — the issue this branch exists for, reproduced
    inside the control that is supposed to catch it.
    """
    repo = Path(tmp) / 'branch'
    repo.mkdir()
    for argv in (['git', 'init', '-q'],
                 ['git', 'config', 'user.email', 't@example.invalid'],
                 ['git', 'config', 'user.name', 'T']):
        subprocess.run(argv, cwd=repo, check=True,
                       env=_util.child_coverage('scrub'))
    (repo / 'tests').mkdir()
    (repo / 'tests' / '_owner.py').write_text(
        _mod('def kept(value):', '    return 1'), encoding='utf-8')
    base = _mod(
        'def twin(value):', '    return 1', '',
        'def pair(value):', '    return 1', '',
        'HARNESS = r"""', 'function carried(l) {',
        '  const seen = [];', '  return seen;', '}', '"""')
    (repo / 'tests' / 'test_base.py').write_text(base, encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))
    subprocess.run(['git', 'commit', '-qm', 'base'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))
    subprocess.run(['git', 'branch', 'main'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))
    # A new name, a second BYTE-IDENTICAL copy of a name the base
    # already carries, and a name nothing touched.
    (repo / 'tests' / 'test_base.py').write_text(_mod(
        'def twin(value):', '    return 1', '',
        'def twin(value):', '    return 1', '',
        'def pair(value):', '    return 1', '',
        'def added(value):', '    return 2', '',
        'HARNESS = r"""', 'function carried(l) {',
        '  const seen = [];', '  return seen;', '}',
        'function carried(l) {', '  const seen = [];', '  return seen;',
        '}', '"""'), encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))
    subprocess.run(['git', 'commit', '-qm', 'branch'], cwd=repo, check=True,
                   env=_util.child_coverage('scrub'))

    table = {('tests/test_base.py', 'carried'): 'this one predates',
             ('tests/test_base.py', 'twin'): 'this one is a second copy',
             ('tests/test_base.py', 'pair'): 'this one is untouched',
             ('tests/test_base.py', 'added'): 'this one is a new name'}
    assert introduced_rows(table, python_digests, repo, bases=('main',)) == [
        ('tests/test_base.py', 'added'),
        ('tests/test_base.py', 'twin')], 'the second copy is not free'
    assert introduced_rows(table, js_digests, repo, bases=('main',)) == [
        ('tests/test_base.py', 'carried')], 'and neither is a second copy'
    # A checkout carrying neither base, and one whose base IS the head,
    # cannot answer the question, and both are refusals.
    assert introduced_rows(table, python_digests, repo,
                           bases=('origin/main',)) is None
    assert introduced_rows(table, python_digests, repo,
                           bases=('HEAD',)) is None


def test_the_detector_names_the_module_and_the_name(tmp):
    del tmp
    sources = {}
    cases = [
        # The owning module defines three names: a helper two suites
        # re-implement, one nothing else touches, and one whose spelling
        # starts `test_`, which is not a boundary in either direction.
        ('tests/_owner.py', _mod('def _trim(mask, left, right):',
                                 '    return 1',
                                 'def _solo():',
                                 '    return 1',
                                 'def test_collects():',
                                 '    return 1'), ['test_collects']),
        ('tests/_names.py', _mod('def test_collects():', '    return 1'),
         ['test_collects']),
        # A helper-looking module outside the tests tree neither owns a
        # name nor is reported for one.
        ('scripts/_outside.py', _mod('def _trim(mask, left, right):',
                                     '    return 1'), []),
        # The three defining forms, each of them a re-implementation.
        ('tests/test_def.py', _mod('def _trim(mask, left, right):',
                                   '    return 1'), ['_trim']),
        ('tests/test_asyncdef.py', _mod('async def _trim(mask, left, '
                                        'right):', '    return 1'),
         ['_trim']),
        ('tests/test_class.py', _mod('class _trim:', '    pass'),
         ['_trim']),
        # A definition nested under `if True:` still binds during module
        # execution, so it is still a second definition.
        ('tests/test_nested.py', _mod('if True:', '    def _trim(mask):',
                                      '        return 1'), ['_trim']),
        # A `test_`-prefixed local, and a name no helper owns.
        ('tests/test_prefix.py', _mod('def test_collects():', '    return 1'),
         ['test_collects']),
        ('tests/test_unowned.py', _mod('def _nobody_defines():', '    pass'),
         []),
        # Every module-execution bind that is not a definition.
        ('tests/test_walrus.py', _mod('(_trim := 1)'), []),
        ('tests/test_for.py', _mod('for _trim in [1]:', '    pass'), []),
        ('tests/test_with.py', _mod('with open(__file__) as _trim:',
                                    '    pass'), []),
        ('tests/test_except.py', _mod('try:', '    pass',
                                      'except OSError as _trim:',
                                      '    pass'), []),
        ('tests/test_match.py', _mod('match 1:', '    case _trim:',
                                     '        pass'), []),
        ('tests/test_orelse.py', _mod('if True:', '    pass', 'else:',
                                      '    _trim = 1'), []),
        ('tests/test_finalbody.py', _mod('try:', '    pass', 'finally:',
                                         '    _trim = 1'), []),
        # The forms that bind no module name at all.
        ('tests/test_attr.py', _mod('holder._trim = 1'), []),
        ('tests/test_subscript.py', _mod("holder['trim'] = 1"), []),
        ('tests/test_augassign.py', _mod('_trim = 1', '_trim += 1'), []),
        ('tests/test_comp.py', _mod('[_trim for _trim in [1]]'), []),
        ('tests/test_lambda.py', _mod('f = lambda: (_trim := 1)'), []),
        # Limb three, over the three import spellings, the one that binds
        # no such name, and an import from outside the tests tree.
        ('tests/test_from.py', _mod('from _owner import _trim',
                                    'def _trim(mask, left, right):',
                                    '    return 1'), []),
        ('tests/test_import_as.py', _mod('import _owner as _trim',
                                         'def _trim(mask, left, right):',
                                         '    return 1'), []),
        ('tests/test_plain_import.py', _mod('import _owner',
                                            'def _trim(mask, left, right):',
                                            '    return 1'), ['_trim']),
        ('tests/test_from_os.py', _mod('from os import _trim',
                                       'def _trim(mask, left, right):',
                                       '    return 1'), ['_trim']),
        # Two owners: each is a re-implementation of the other, and a
        # third module binding the name is a third report.
        ('tests/_pair.py', _mod('def _twice():', '    return 1'),
         ['_twice']),
        ('tests/_second.py', _mod('def _twice():', '    return 1'),
         ['_twice']),
        ('tests/test_pair.py', _mod('def _twice():', '    return 1'),
         ['_twice']),
    ]
    expected = set()
    for path, text, names in cases:
        # A fabricated case is a program a tests module could contain,
        # so it must compile; ast.parse accepts source compile() rejects.
        compile(text, path, 'exec')
        sources[path] = text
        expected.update((path, name) for name in names)
    findings = reimplementations(sources)
    found = {(item.path, item.name) for item in findings}
    assert found == expected, sorted(found ^ expected)
    assert not any(item.name == '_solo' for item in findings), findings
    twice = next(item for item in findings if item.path == 'tests/_pair.py')
    assert twice.lines == [1], twice
    assert twice.owners == ['tests/_pair.py', 'tests/_second.py'], twice


def test_a_script_entry_point_is_not_a_shared_helper_name(tmp):
    del tmp
    sources = {
        'tests/_entry.py': _mod('def main(argv):', '    return 0', '', '',
                                "if __name__ == '__main__':", '    main([])'),
        'tests/test_entry.py': _mod('def main(argv):', '    return 0', '',
                                    '', "if __name__ == '__main__':",
                                    '    main([])'),
        'tests/_plain.py': _mod('def main(argv):', '    return 0'),
        'tests/test_plain.py': _mod('def main(argv):', '    return 0'),
        'tests/test_guarded.py': _mod('def main(argv):', '    return 0',
                                      '', '', "if '__main__' == __name__:",
                                      '    main([])'),
    }
    for path, text in sources.items():
        compile(text, path, 'exec')
    found = {(item.path, item.name) for item in reimplementations(sources)}
    # The unguarded helper owns `main` and the unguarded suite that
    # defines it is a re-implementation; the guarded suite is running
    # itself and the guarded helper is doing the same, so the exclusion
    # is the guard on either side, not the spelling.
    assert found == {('tests/test_plain.py', 'main')}, sorted(found)


def test_a_guard_excludes_a_name_it_CALLS_and_nothing_it_mentions(tmp):
    """A `__main__` guard excludes the entry point it invokes.

    A guard is a statement, not a scope, and this exclusion is the one
    place a module's own code hides a name from BOTH sides of the
    comparison. It used to collect every `ast.Name` the guard touched,
    so a module that merely printed, assigned or tested the truthiness
    of a shared helper's name lost that name from its own definitions
    and from the owner set, and each of the two re-implementations
    below went unreported. The unit is a CALL.
    """
    del tmp
    sources = {
        # The owner tests its own helper's name in the guard and never
        # calls it, so it keeps ownership and the offender is reported.
        'tests/_owner.py': _mod(
            'def _trim(mask, left, right):', '    return 1', '', '',
            "if __name__ == '__main__':",
            '    import sys', '    sys.exit(0 if _trim else 1)'),
        'tests/test_offender.py': _mod(
            'def _trim(mask, left, right):', '    return 1'),
        # The offender mentions the name in its guard and does not call
        # it, so it is still a re-implementation.
        'tests/test_mentions.py': _mod(
            'def _trim(mask, left, right):', '    return 1', '', '',
            "if __name__ == '__main__':",
            '    import sys', '    sys.exit(0 if _trim else 1)'),
        # A guard that DOES call the name is a script running itself.
        'tests/test_calls.py': _mod(
            'def _solver(value):', '    return 1', '', '',
            "if __name__ == '__main__':", '    _solver(1)'),
    }
    for path, text in sources.items():
        compile(text, path, 'exec')
    found = {(item.path, item.name) for item in reimplementations(sources)}
    assert found == {('tests/test_offender.py', '_trim'),
                     ('tests/test_mentions.py', '_trim')}, sorted(found)


def test_the_detector_refuses_a_module_it_cannot_parse(tmp):
    del tmp
    sources = {
        'tests/_owner.py': _mod('def _trim(mask, left, right):',
                                '    return 1'),
        'tests/test_broken.py': _mod('def broken(:'),
    }
    try:
        reimplementations(sources)
    except AssertionError as exc:
        assert 'tests/test_broken.py' in str(exc), exc
    else:
        raise AssertionError('the detector accepted an unparseable module')


def test_each_limb_decides_a_site_of_its_own(tmp):
    """Dropping a limb changes the verdict on the site only that limb
    decides, so the allowance table cannot be what detects: it allows
    findings, and it cannot produce one.
    """
    del tmp
    sources = {
        'tests/_owner.py': _mod('def _trim(mask, left, right):',
                                '    return 1'),
        'tests/_second.py': _mod('def _twice():', '    return 1'),
        'tests/_third.py': _mod('def _twice():', '    return 1'),
        'tests/test_owner.py': _mod('def _trim(mask, left, right):',
                                    '    return 1'),
        'tests/test_twice.py': _mod('from _second import _twice',
                                    'def _twice():', '    return 1'),
    }

    # Each call names the limb it drops rather than unpacking a mapping:
    # a `**`-unpacked call reads as a bounded launch whose head the launch
    # audit cannot prove, and this file is itself audited.
    def report(items):
        return {(item.path, item.name) for item in items}

    intact = report(reimplementations(sources))
    assert intact == {('tests/test_owner.py', '_trim'),
                      ('tests/_second.py', '_twice'),
                      ('tests/_third.py', '_twice')}, sorted(intact)
    # Limb two dropped: the sole owner stops being the definition.
    without_owner = report(reimplementations(
        sources, owner_is_the_definition=lambda path, name, o: False))
    assert ('tests/_owner.py', '_trim') in without_owner, without_owner
    # Limb three dropped: a module that imports the name and defines it
    # too is this control's finding, and the shadow control's.
    without_import = report(reimplementations(
        sources, an_import_settles_it=lambda i, n, s: False))
    assert ('tests/test_twice.py', '_twice') in without_import, without_import
    # The owner set read per owner rather than as a set: both `_twice`
    # owners become clean and the pair goes unreported.
    per_owner = report(reimplementations(
        sources, owner_is_the_definition=lambda path, name, o:
        path in o.get(name, ())))
    assert not [item for item in per_owner if item[1] == '_twice'], per_owner


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
