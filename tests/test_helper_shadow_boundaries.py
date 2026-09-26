#!/usr/bin/env python3
"""No tests module may shadow a shared-helper import it also binds locally.

A re-paste — the same function body pasted directly below the line that
imports it — binds one module-level name twice. The local definition
wins, the suite reads its own copy, and the shared defect the helper
exists to exercise stays invisible. The owned names are derived from
each importing file's own import statement, so no maintained list of
names or per-module table can drift out of step with the tree.

The boundary is a name bound during MODULE EXECUTION, not one that merely
sits as a direct child of the module body: a re-paste nested under `if
True:`, a walrus rebind, a `for` target, a `with ... as`, an `except E
as`, a `try`/`except ImportError` fallback and a `match` capture all bind
at module execution and are all reported. A name is a shadow when the file
also imports it from a module in the same tests tree, whether by
`from X import name` or by binding a module import, `import X` or
`import X as Z`, whose target is that module.

The rule is deliberately narrow and complete only over the static forms.
A def, class or lambda BODY is its own namespace, so a rebinding there is
not a module-scope shadow. A comprehension and a generator expression are
NOT namespaces for an assignment expression: a walrus anywhere inside
one binds the containing scope, and a `def` and a lambda evaluate their
DEFAULTS, ANNOTATIONS and a `def`'s decorators where they are written,
so a walrus in those binds it too. `test_the_binder_agrees_with_cpython`
holds this walker to that list against the interpreter itself, so the
narrowness is a measured boundary rather than an assumption. A walrus in
a comprehension's OUTERMOST iterable would bind the containing scope, but
CPython rejects that source outright, so there is no case to compare.
An import binds the
name it brings INTO the module, so an aliased import `X as _Y` is
shadowed only by a rebind of `_Y`. A name the file binds with no import
from a sibling tests module, or an import from outside the tests tree, is
not a shadow. A module the detector cannot parse fails the control, naming
the file, rather than being silently dropped.

What this control does not see, by design: a re-paste whose import was
deleted along with it is a duplicate body, not a shadow; a `def` that
re-implements a shared helper's name without importing it at all, which
`test_helper_reimplementation.py` reports; a `from X import *`, whose
names the rule cannot enumerate; a suite that imports another suite whole
and reads its privates; and a dynamic rebind through `globals()[...] =
...`, `exec` or `importlib`.
"""
import ast
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _helper_binds import scan as _scan  # noqa: E402

ROOT = _util.ROOT

Shadow = namedtuple(
    'Shadow', 'path name import_lines bind_lines sources')


def _shadow_findings(sources):
    """A module that does not parse fails the control, naming the file,
    rather than being dropped.
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
        ('test_from_os.py', _mod(
            'from os import _load_queue', '_load_queue = 1'), None),
        ('test_plain_import.py', _mod(
            'import json as _load_queue', '_load_queue = 1'), None),
        ('test_plain_in_tree.py', _mod(
            'import _command_candidates', '_command_candidates = 1'),
         '_command_candidates'),
        ('test_no_import.py', _mod(
            'def _load_queue():', '    pass'), None),
        # A comprehension and a generator expression are NOT scopes for
        # an assignment expression: a walrus anywhere inside one binds
        # the CONTAINING scope, which is what CPython does and what
        # this control used to deny.
        ('test_comp_inner.py', suite(
            '[(_load_queue := i) for i in range(3)]'), '_load_queue'),
        ('test_comp_condition.py', suite(
            '[i for i in range(3) if (_load_queue := i)]'), '_load_queue'),
        ('test_genexp.py', suite(
            'list(i for i in range(3) if (_load_queue := i))'),
         '_load_queue'),
        ('test_comp_nested.py', suite(
            '[[j for i in range(3)] for _ in range(3) '
            'if (_load_queue := 1)]'), '_load_queue'),
        # A lambda's BODY is its own scope, so nothing in it binds the
        # module — not even a comprehension inside it.
        ('test_lambda.py', suite(
            'f = lambda: (_load_queue := 1)'), None),
        ('test_lambda_comp_body.py', suite(
            'f = lambda: [i for i in range(3) if (_load_queue := i)]'), None),
        # A lambda's and a def's DEFAULTS are evaluated where they are
        # written, so a walrus in one binds the containing scope, and so
        # does one in a decorator.
        ('test_lambda_default.py', suite(
            'f = lambda q=(_load_queue := 1): q'), '_load_queue'),
        ('test_def_default.py', suite(
            'def g(q=(_load_queue := 1)):', '    return q'),
         '_load_queue'),
        ('test_def_annotation.py', suite(
            'def g(q: int = (_load_queue := 1)):', '    return q'),
         '_load_queue'),
        ('test_decorator.py', suite(
            'def _deco():', '    return 1',
            '@(_load_queue := _deco)',
            'def g():', '    pass'), '_load_queue'),
        # A PEP 695 `type X = ...` binds X, so over an import it is a
        # shadow like any other rebind. It is a bind and not a
        # definition, which is why the re-implementation control's three
        # defining forms do not list it.
        ('test_type_alias.py', suite('type _load_queue = int'),
         '_load_queue'),
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
    # The set comparison above ignores linenos, so pin them here.
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


# One case per shape where a walrus binds the module scope, and one per
# shape where it does not. The expected half of each pair is not written
# down: the interpreter is asked, and the walker has to agree with it.
# Every case here is one this control got wrong at some point, or one
# adjacent to one it did.
_WALRUS_CASES = (
    ('comprehension element', 'out = [(y := x) for x in range(3)]', 'y'),
    ('comprehension condition',
     'out = [x for x in range(3) if (y := x * 2)]', 'y'),
    ('generator expression',
     'out = list(x for x in range(3) if (y := x))', 'y'),
    ('nested comprehension',
     'out = [[z for x in range(3)] for _ in range(3) if (z := 1)]', 'z'),
    ('dict display', 'out = {(_y := 1): 2}', '_y'),
    ('set display', 'out = {(_y := 1)}', '_y'),
    ('lambda default', 'out = lambda q=(_y := 1): q', '_y'),
    ('def default', 'def g(q=(_y := 1)):\n    return q', '_y'),
    ('def annotation',
     'def g(q: int = (_y := 1)):\n    return q', '_y'),
    # A class BODY is a scope of its own; a class HEADER is not. Its
    # decorators, its bases and its keywords all evaluate where the
    # class is written, so a walrus in any of them binds the module.
    ('class decorator', 'def _d(f):\n    return f\n@(_y := _d)\n'
     'class K:\n    pass', '_y'),
    ('class base expression', 'class K((_y := object)):\n    pass', '_y'),
    ('class keyword',
     'class K(metaclass=(_y := type)):\n    pass', '_y'),
    ('class body statement', 'class K:\n    (_y := 1)', None),
    ('class body method default',
     'class K:\n    def m(self, q=(_y := 1)):\n        return q', None),
    ('decorator',
     'def _d(q=None):\n    return 1\n@(_y := _d)\ndef g():\n    pass',
     '_y'),
    ('f-string', 'out = f"{(_y := 1)}"', '_y'),
    ('lambda body', 'out = lambda: (_y := 1)', None),
    ('def body', 'def g():\n    (_y := 1)\n    return _y', None),
    ('class body', 'class K:\n    (_y := 1)', None),
    ('comprehension inside a lambda body',
     'out = lambda: [x for x in range(3) if (_y := x)]', None),
    ('comprehension inside a method body',
     'class K:\n    def m(self):\n        return [x for x in range(3) '
     'if (_y := x)]', None),
    ('method default', 'class K:\n    def m(self, q=(_y := 1)):\n'
     '        return q', None),
)


def test_the_binder_agrees_with_cpython(tmp):
    """The walker's module-scope binds, against the interpreter's.

    A fabricated case says only what this control believes about a
    shape; the interpreter says which of those shapes actually bind the
    module namespace, so asking it turns a disputed boundary into a
    measured one. This is the control that would have caught the four
    walrus spellings the walker skipped — a comprehension's element and
    condition, a generator expression and a lambda default — and the
    three it would still have skipped: a def's default, a def's
    decorator, and a PEP 695 type alias.
    """
    del tmp
    disagreed = []
    for label, source, name in _WALRUS_CASES:
        compile(source, label, 'exec')
        _, binds = _scan(ast.parse(source))
        walked = name in binds
        namespace = {}
        # The interpreter IS the oracle here, so executing the case is
        # the point rather than a shortcut.
        code = compile(source, label, 'exec')
        exec(code, namespace)  # pylint: disable=exec-used
        interpreted = name in namespace
        if walked != interpreted:
            disagreed.append((label, walked, interpreted))
    assert not disagreed, disagreed


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
