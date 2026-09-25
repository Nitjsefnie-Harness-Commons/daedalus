#!/usr/bin/env python3
"""No tests module may shadow a shared-helper import it also binds locally.

Many shared helper modules live under `tests/`. A re-paste of any of
them — the same function body pasted directly below the line that imports
it — binds one module-level name twice. The local definition wins, the
suite reads its own copy, and the shared defect the helper exists to
exercise stays invisible. The owned names are derived from each importing
file's own import statement, so no maintained list of names or per-module
table can drift out of step with the tree.

The boundary is a name bound during MODULE EXECUTION, not one that merely
sits as a direct child of the module body: a re-paste nested under `if
True:`, a walrus rebind, a `for` target, a `with ... as`, an `except E
as`, a `try`/`except ImportError` fallback and a `match` capture all bind
at module execution and are all reported. A name is a shadow when the file
also imports it from a module in the same tests tree, whether by
`from X import name` or by binding a module import, `import X` or
`import X as Z`, whose target is that module.

The rule is deliberately narrow and complete only over the static forms.
A def, class, comprehension or lambda body is its own namespace, so a
rebinding there is not a module-scope shadow and a walrus inside a
comprehension or lambda is not collected. A walrus in a comprehension's
outermost iterable would bind in the enclosing scope, but CPython rejects
that source at compile time, so no committed module carries the form and
it is not collected. An import binds the name it brings INTO the module,
so an aliased import `X as _Y` is shadowed only by a rebind of `_Y`, never
of `X`. A name the file binds with no import from a sibling tests module,
or an import from outside the tests tree, is not a shadow. A module the
detector cannot parse fails the control, naming the file, rather than
being silently dropped.

What this control does not see, by design: a re-paste whose import was
deleted along with it is a duplicate body, not a shadow; a `from X
import *`, whose names the rule cannot enumerate; a `def` that
re-implements a shared helper's name without importing it at all; a suite
that imports another suite whole and reads its privates; and a dynamic
rebind through `globals()[...] = ...`, `exec` or `importlib`. The rule is
complete over the static rebinding forms, and the difference is the honest
thing to publish.
"""
import ast
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT

_DEFN = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_COMPREHENSION = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

Shadow = namedtuple(
    'Shadow', 'path name import_lines bind_lines sources')


def _target_names(target):
    """Names a target binds, unpacking tuples, lists and starred names.

    An attribute or subscript target binds no module name, so `x.name = 1`
    and `d['k'] = 1` contribute nothing.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for element in target.elts:
            names.extend(_target_names(element))
        return names
    return []


def _scan(tree):
    """Return (imports, binds) for what module execution establishes.

    imports maps a name to {import line: set of source module stems};
    binds maps a name to the set of lines that bind it. Only statements
    that execute in module scope are collected, so a def, class,
    comprehension or lambda body — which has its own namespace — is never
    descended into.
    """
    imports = {}
    binds = {}

    def bind(name, lineno):
        binds.setdefault(name, set()).add(lineno)

    def imported(name, lineno, source):
        imports.setdefault(name, {}).setdefault(lineno, set()).add(source)

    def collect_walrus(node):
        stack = list(ast.iter_child_nodes(node))
        while stack:
            current = stack.pop()
            if isinstance(current, ast.NamedExpr):
                if isinstance(current.target, ast.Name):
                    bind(current.target.id, current.lineno)
                stack.extend(ast.iter_child_nodes(current))
            elif isinstance(current, _DEFN):
                continue
            elif isinstance(current, ast.Lambda):
                continue
            elif isinstance(current, _COMPREHENSION):
                continue
            else:
                stack.extend(ast.iter_child_nodes(current))

    def record(node):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported(alias.asname or alias.name.split('.')[0],
                         node.lineno, alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != '*':
                    imported(alias.asname or alias.name, node.lineno,
                             node.module or '')
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _target_names(target):
                    bind(name, node.lineno)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            for name in _target_names(node.target):
                bind(name, node.lineno)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for name in _target_names(node.target):
                bind(name, node.lineno)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for name in _target_names(item.optional_vars):
                        bind(name, node.lineno)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind(node.name, node.lineno)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for sub in ast.walk(case.pattern):
                    if isinstance(sub, (ast.MatchAs, ast.MatchStar)):
                        if sub.name:
                            bind(sub.name, sub.lineno)
                    elif isinstance(sub, ast.MatchMapping) and sub.rest:
                        bind(sub.rest, sub.lineno)

    def statement(node):
        if isinstance(node, _DEFN):
            bind(node.name, node.lineno)
            return
        collect_walrus(node)
        record(node)
        for field in ('body', 'orelse', 'finalbody'):
            children = getattr(node, field, None)
            if (isinstance(children, list) and children and all(
                    isinstance(child, ast.stmt) for child in children)):
                block(children)
        for handler in getattr(node, 'handlers', None) or []:
            statement(handler)
        if isinstance(node, ast.Match):
            for case in node.cases:
                block(case.body)

    def block(body):
        for child in body:
            statement(child)

    block(tree.body)
    return imports, binds


def _shadow_findings(sources):
    """Names a tests module imports from a sibling and also binds itself.

    `sources` maps a path to its source text. A shadow is a name the file
    binds during module execution that it also imports from a module in
    the same tests tree. Returns one Shadow per shadowing name, each
    carrying the import and binding lines. A module that does not parse
    fails the control, naming the file, rather than being dropped.
    """
    stems = {Path(path).stem for path in sources}
    findings = []
    for path in sorted(sources):
        try:
            tree = ast.parse(sources[path])
        except SyntaxError as exc:
            raise AssertionError(
                f'tests module does not parse: {path}: {exc}') from exc
        imports, binds = _scan(tree)
        for name in sorted(set(imports) & set(binds)):
            lines = imports[name]
            in_tree = {src for srcs in lines.values() for src in srcs
                       if src in stems}
            if not in_tree:
                continue
            import_lines = sorted(
                line for line, srcs in lines.items() if srcs & stems)
            findings.append(Shadow(path, name, import_lines,
                                   sorted(binds[name]), sorted(in_tree)))
    return findings


def _mod(*lines):
    return ''.join(line + '\n' for line in lines)


def test_no_tests_module_shadows_a_shared_helper_import(tmp):
    del tmp
    listed = subprocess.run(
        ['git', 'ls-files', 'tests/*.py'], cwd=ROOT, capture_output=True,
        text=True, check=True).stdout.splitlines()
    assert listed, 'git ls-files named no tests module'
    sources = {name: (ROOT / name).read_text(encoding='utf-8')
               for name in listed}
    findings = _shadow_findings(sources)
    assert not findings, (
        'tests modules bind a name a shared-helper import also binds: '
        f'{findings}')


def test_the_detector_names_the_shadowing_file_and_name(tmp):
    shared = Path(tmp) / 'tests'
    shared.mkdir()
    sources = {}

    def write(relpath, text):
        target = shared.parent / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        sources[relpath] = target.read_text(encoding='utf-8')

    def suite(*body):
        return _mod('from _command_candidates import _load_queue', *body)

    write('tests/_command_candidates.py',
          _mod('def _load_queue(name):', '    return 1'))
    write('tests/_cmdqueue.py', _mod(
        'POLL_DELAY = 0.5', '', '',
        'def _poll_queue_reads():', '    return 1'))
    write('tests/_pyroute_core.py',
          _mod('def dict_assignments():', '    return 1'))
    # The design-change discriminating set: a re-paste nested under if, a
    # walrus, a for target, a try/except fallback (reported) and a
    # class-body rebinding (not reported), each isolating one form. The
    # third element of each case is the name it must be reported under, or
    # None when the rule must not report it.
    alias = _mod(
        'from _cmdqueue import (POLL_DELAY as _SHARED_POLL_DELAY,',
        '                       _poll_queue_reads)')
    cases = [
        ('test_a.py', suite(
            'if True:', '    def _load_queue():', '        pass'),
         '_load_queue'),
        ('test_b.py', suite('(_load_queue := 1)'), '_load_queue'),
        ('test_c.py', suite(
            'for _load_queue in [1]:', '    pass'), '_load_queue'),
        ('test_d.py', _mod(
            'try:',
            '    from _command_candidates import _load_queue',
            'except ImportError:',
            '    def _load_queue(name):',
            '        return 1'), '_load_queue'),
        ('test_e.py', suite(
            'class K:', '    _load_queue = 1'), None),
        # One case per remaining binder, each the sole reason it fires.
        ('test_asyncdef.py', suite(
            'async def _load_queue():', '    pass'), '_load_queue'),
        ('test_class.py', suite(
            'class _load_queue:', '    pass'), '_load_queue'),
        ('test_assign.py', suite('_load_queue = 1'), '_load_queue'),
        ('test_annassign.py', suite('_load_queue: int = 1'), '_load_queue'),
        ('test_augassign.py', suite('_load_queue += 1'), '_load_queue'),
        ('test_unpack.py', suite('_, _load_queue = 1, 2'), '_load_queue'),
        ('test_with_as.py', suite(
            'with open(__file__) as _load_queue:', '    pass'),
         '_load_queue'),
        ('test_except_as.py', suite(
            'try:', '    pass',
            'except Exception as _load_queue:', '    pass'), '_load_queue'),
        # A bind in an else body or a finally body is reached only through
        # that field of `statement`, so each case fires for its own alone.
        ('test_orelse.py', suite(
            'if True:', '    pass',
            'else:', '    _load_queue = 1'), '_load_queue'),
        ('test_finalbody.py', suite(
            'try:', '    pass',
            'finally:', '    _load_queue = 1'), '_load_queue'),
        ('test_match.py', suite(
            'match 1:', '    case _load_queue:', '        pass'),
         '_load_queue'),
        # A module import, not only a from-import, is a shadow source.
        ('test_import_as.py', _mod(
            'import _command_candidates as _load_queue',
            '_load_queue = 1'), '_load_queue'),
        # An import binds the LOCAL name it brings in, so rebinding the
        # pre-as name is clean — this is the live tests/_queueread.py and
        # tests/_pyroute.py shape. The genuinely imported local name
        # (_poll_queue_reads) rebound here must still be reported.
        ('test_alias_clean.py',
         alias + 'POLL_DELAY = _SHARED_POLL_DELAY\n', None),
        ('test_alias_rebind.py',
         alias + '_poll_queue_reads = None\n', '_poll_queue_reads'),
        ('test_pyroute_clean.py', _mod(
            'from _pyroute_core import dict_assignments as _dict_assignments',
            'dict_assignments = _dict_assignments'), None),
        # Near-misses: wrong scope, wrong source, or no source at all.
        ('test_from_os.py', _mod(
            'from os import _load_queue', '_load_queue = 1'), None),
        ('test_plain_import.py', _mod(
            'import json as _load_queue', '_load_queue = 1'), None),
        ('test_no_import.py', _mod(
            'def _load_queue():', '    pass'), None),
        ('test_comp_inner.py', suite(
            '[(_load_queue := i) for i in range(3)]'), None),
        ('test_lambda.py', suite(
            'f = lambda: (_load_queue := 1)'), None),
        ('test_assign_attr.py', suite('x._load_queue = 1'), None),
        ('test_annassign_attr.py', suite('x._load_queue: int = 1'), None),
    ]
    expected = set()
    for filename, text, name in cases:
        # A fabricated case is a program a tests module could contain,
        # so it must compile; ast.parse accepts source compile() rejects.
        compile(text, filename, 'exec')
        write('tests/' + filename, text)
        if name is not None:
            expected.add(('tests/' + filename, name))
    findings = _shadow_findings(sources)
    found = {(item.path, item.name) for item in findings}
    assert found == expected, sorted(found ^ expected)
    # The report names the import and binding lines; the set comparison
    # above holds with every lineno collapsed to 0, so pin them here.
    walrus = next(item for item in findings
                  if item.path == 'tests/test_b.py')
    assert walrus.import_lines == [1], walrus
    assert walrus.bind_lines == [2], walrus


def test_the_detector_refuses_a_module_it_cannot_parse(tmp):
    del tmp
    sources = {
        'tests/_cand.py': _mod('def helper():', '    return 1'),
        'tests/test_broken.py': _mod('def broken(:'),
    }
    try:
        _shadow_findings(sources)
    except AssertionError as exc:
        assert 'tests/test_broken.py' in str(exc), exc
    else:
        raise AssertionError('the detector accepted an unparseable module')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
