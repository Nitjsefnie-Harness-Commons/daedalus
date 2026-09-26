#!/usr/bin/env python3
"""No tests module may import a sibling `test_*` suite.

`run_tests.py` gives every suite its own process, so a suite that imports
a sibling re-executes that sibling's whole module body inside itself and
then reads that copy of a private helper rather than a shared one. The
offending set is derived by parsing every tracked tests module, so no
maintained list of sites can drift out of step with the tree.

Both static spellings are reported — `import test_x`, `import test_x as y`,
`import tests.test_x`, `from test_x import a`, `from tests.test_x import a`
and `from . import test_x` — and so is a dynamic import of a sibling by
`importlib.import_module`, `__import__`, `runpy.run_path` or
`importlib.util.spec_from_file_location` whose string-literal argument
names one. Every spelling executes the sibling's body, so every one is the
same defect.

Parsing with `ast` rather than a regex is load-bearing, and this tree
proves it: three sites carry the text `import test_dashboard_behaviour as
behaviour` inside triple-quoted synthetic-violation fixtures, and a text
rule reports all three as offenders when none of them executes.

What the control does not see, by design: a `from X import *` names
nothing the rule can enumerate, so only its `X` is examined; a module name
built at runtime rather than written as a literal; a suite spelled by
directory as well as stem, which the literal clause does not match; a
suite whose stem falls outside `^test_[a-z0-9_]+$`, such as one carrying
an upper-case letter; a sibling's private reached by copy rather than by
import; and a name shaped like a suite that no tracked tests module
provides, which is a broken import rather than this defect. A module the
detector cannot parse fails the control, naming the file, rather than
being dropped.

`ALLOWED` is a shrink-only table over the sites whose importing file an
open pull request or another seat owns, which this branch may not edit. A
row is added only by a merge that put the file in someone's territory, and
removing a row is a move rather than a deletion: the table is pinned on
both sides, so an offender absent from it is a finding AND a row that no
longer names a live site is a stale finding. It cannot grow to hide this
branch's own work.

`tests/test_helper_shadow_boundaries.py` is the sibling control, and it
names this one: it reports a name a suite binds locally that a
shared-helper import also binds. Between them the two close the class — a
helper is either imported from a `tests/_*.py` module or is a suite's own.
"""
import ast
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT

_SUITE = re.compile(r'^test_[a-z0-9_]+$')
_DYNAMIC = re.compile(r'^test_[a-z0-9_]+(\.py)?$')
_DYNAMIC_CALLEES = (
    'importlib.import_module',
    'importlib.util.spec_from_file_location',
    'runpy.run_path',
    '__import__',
)

Import = namedtuple('Import', 'path lineno module spelling')
Allowance = namedtuple('Allowance', 'path module reason')

ALLOWED = (
    Allowance('tests/test_dashboard_harness.py',
              'test_dashboard_accessibility', 'PR 1082'),
    Allowance('tests/test_dashboard_harness.py',
              'test_dashboard_behaviour', 'PR 1082'),
    Allowance('tests/test_dashboard_node_retry.py',
              'test_dashboard_behaviour', 'PR 1082'),
    Allowance('tests/test_mcp_live_tools.py', 'test_mcp_server', 'PR 1109'),
    Allowance('tests/test_starvation_bounds.py', 'test_cli', 'PR 1122'),
    Allowance('tests/test_tab_routing_collapse.py',
              'test_tab_routing', 'PR 1063'),
    Allowance('tests/test_tab_routing_comprehension_probe.py',
              'test_tab_routing_collapse', 'PR 1063'),
    Allowance('tests/test_tab_routing_dict_construction.py',
              'test_tab_routing_dict_stores', 'PR 1063'),
    Allowance('tests/test_tab_routing_dict_lengths.py',
              'test_tab_routing_dict_stores', 'PR 1063'),
    Allowance('tests/test_tab_routing_dict_stores.py',
              'test_tab_routing', 'PR 1063'),
    Allowance('tests/test_tab_routing_dict_stores.py',
              'test_tab_routing_store_sweep', 'PR 1063'),
    Allowance('tests/test_tab_routing_js_bodies.py',
              'test_tab_routing_js', 'PR 1063'),
    Allowance('tests/test_tab_routing_js_closure.py',
              'test_tab_routing_js', 'PR 1063'),
    Allowance('tests/test_tab_routing_js_heads.py',
              'test_tab_routing_js', 'PR 1063'),
    Allowance('tests/test_tab_routing_js_keys.py',
              'test_tab_routing_js', 'PR 1063'),
    Allowance('tests/test_tab_routing_js_net.py',
              'test_tab_routing_js', 'PR 1063'),
    Allowance('tests/test_tab_routing_js_operations.py',
              'test_tab_routing_js', 'PR 1063'),
    Allowance('tests/test_tab_routing_js_reach.py',
              'test_tab_routing_js', 'PR 1063'),
    Allowance('tests/test_tab_routing_js_templates.py',
              'test_tab_routing_js', 'PR 1063'),
    Allowance('tests/test_tab_routing_match.py',
              'test_tab_routing', 'PR 1063'),
    Allowance('tests/test_tab_routing_match.py',
              'test_tab_routing_collapse', 'PR 1063'),
    Allowance('tests/test_tab_routing_positions.py',
              'test_tab_routing_dict_stores', 'PR 1063'),
    Allowance('tests/test_tab_routing_sequence_reads.py',
              'test_tab_routing_dict_stores', 'PR 1063'),
    Allowance('tests/test_tab_routing_set_operations.py',
              'test_tab_routing', 'PR 1063'),
    Allowance('tests/test_tab_routing_setdefault.py',
              'test_tab_routing', 'PR 1063'),
    Allowance('tests/test_tab_routing_starred_arity.py',
              'test_tab_routing', 'PR 1063'),
    Allowance('tests/test_tab_routing_store_sweep.py',
              'test_tab_routing', 'PR 1063'),
    Allowance('tests/test_tab_routing_unprovable.py',
              'test_tab_routing', 'PR 1063'),
    Allowance('tests/test_tab_routing_yielded_sender.py',
              'test_tab_routing', 'PR 1063'),
)


def _leaf(dotted):
    """The suite stem of a dotted module name.

    `tests.test_x` names the same module as bare `test_x`, so a leading
    component naming the tests directory is dropped rather than read as
    the module itself.
    """
    parts = dotted.split('.')
    if parts[0] == 'tests' and len(parts) > 1:
        parts = parts[1:]
    return parts[0]


def _dotted(node):
    """The dotted spelling of a call's callee, or None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return '.'.join(reversed(parts))


def _literals(call):
    """Every string-literal argument of a call, whatever its position.

    `spec_from_file_location` takes the module name first and the path
    second, and either can be the literal naming a sibling, so the rule
    reads all of them rather than picking one position.
    """
    words = [word.value for word in call.keywords]
    return [value.value for value in list(call.args) + words
            if isinstance(value, ast.Constant)
            and isinstance(value.value, str)]


def _from_spelling(node):
    names = [alias.name + (f' as {alias.asname}' if alias.asname else '')
             for alias in node.names]
    head = '.' * node.level + (node.module or '')
    return f'from {head} import {", ".join(names)}'


def _scan(tree, own, stems):
    """Every sibling-suite import this module executes, with its spelling.

    `own` is the file's own stem and `stems` every tracked tests stem, so
    a suite importing itself and a name shaped like a suite that no
    tracked module provides are both excluded.
    """
    hits = []

    def sibling(name):
        leaf = _leaf(name)
        if leaf == own or leaf not in stems or not _SUITE.match(leaf):
            return None
        return leaf

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                leaf = sibling(alias.name)
                if leaf is None:
                    continue
                spelling = f'import {alias.name}'
                if alias.asname:
                    spelling += f' as {alias.asname}'
                hits.append((node.lineno, leaf, spelling))
        elif isinstance(node, ast.ImportFrom):
            # `from . import test_x` names its module in the alias, not in
            # `node.module`, so both places are read.
            candidates = [node.module] if node.module else []
            candidates += [alias.name for alias in node.names
                           if alias.name != '*']
            spelling = _from_spelling(node)
            leaves = {sibling(name) for name in candidates}
            for leaf in sorted(leaf for leaf in leaves if leaf):
                hits.append((node.lineno, leaf, spelling))
        elif isinstance(node, ast.Call):
            callee = _dotted(node.func)
            if callee not in _DYNAMIC_CALLEES:
                continue
            for literal in _literals(node):
                if not _DYNAMIC.match(literal):
                    continue
                stem = literal[:-3] if literal.endswith('.py') else literal
                if sibling(stem) is not None:
                    hits.append((node.lineno, stem, f'{callee}({literal!r})'))
    return sorted(hits)


def _import_findings(sources):
    """A module that does not parse fails the control, naming the file,
    rather than being dropped.
    """
    stems = {Path(path).stem for path in sources}
    findings = []
    for path in sorted(sources):
        try:
            tree = ast.parse(sources[path], filename=path)
        except SyntaxError as exc:
            raise AssertionError(
                f'tests module does not parse: {path}: {exc}') from exc
        for lineno, module, spelling in _scan(tree, Path(path).stem, stems):
            findings.append(Import(path, lineno, module, spelling))
    return findings


def _partition(findings, table):
    """(unallowed, stale) — the two ways the table disagrees with the tree.

    Pinned on both sides, so the table can neither grow past the sites it
    holds nor keep a row whose site an owning merge has already removed.
    One finding per site is reported, the first in path-then-line order,
    so a file importing one sibling at two lines is named once.
    """
    allowed = {(row.path, row.module) for row in table}
    first = {}
    for item in findings:
        first.setdefault((item.path, item.module), item)
    live = set(first) & allowed
    unallowed = [first[key] for key in sorted(first) if key not in allowed]
    stale = [row for row in table if (row.path, row.module) not in live]
    return unallowed, stale


def _unallowed_message(unallowed):
    """Each line carries the file, the line, the spelling and the module,
    so a reader can act on it without re-running the grep.
    """
    return '\n'.join(
        ['a tests module imports a sibling suite and no allowance row '
         'covers the site:']
        + [f'  {item.path}:{item.lineno}: {item.spelling} imports sibling '
           f'suite {item.module}' for item in unallowed]
        + ['move the helper out of the sibling into a tests/_*.py module '
           'and import that instead.'])


def _stale_message(stale):
    return '\n'.join(
        ['an allowance row is stale: it names a site that is no longer an '
         'offender, so the table shrank without the row:']
        + [f'  {row.path} -> {row.module} ({row.reason})' for row in stale]
        + ['drop the row; the table only ever shrinks.'])


def _mod(*lines):
    return ''.join(line + '\n' for line in lines)


def _fabricate(root, sources, relpath, text):
    """Write one module of a fabricated tests tree and key it by path."""
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    sources[relpath] = target.read_text(encoding='utf-8')


def test_no_tests_module_imports_a_sibling_suite(tmp):
    del tmp
    listed = subprocess.run(
        ['git', 'ls-files', 'tests/*.py'], cwd=ROOT, capture_output=True,
        text=True, check=True).stdout.splitlines()
    assert listed, 'git ls-files named no tests module'
    sources = {name: (ROOT / name).read_text(encoding='utf-8')
               for name in listed}
    unallowed, stale = _partition(_import_findings(sources), ALLOWED)
    assert not unallowed, _unallowed_message(unallowed)
    assert not stale, _stale_message(stale)


def test_the_detector_names_every_sibling_import_and_nothing_else(tmp):
    root = Path(tmp) / 'tree'
    sources = {}
    _fabricate(root, sources, 'tests/_shared.py',
               _mod('def _load_queue(name):', '    return 1'))
    _fabricate(root, sources, 'tests/test_sibling.py', _mod('SIBLING = 1'))
    _fabricate(root, sources, 'tests/test_Mixed.py', _mod('MIXED = 1'))
    cases = [
        # Every spelling that names a sibling module.
        ('test_a.py', 'import test_sibling', ['test_sibling']),
        ('test_b.py', 'import test_sibling as sib', ['test_sibling']),
        ('test_c.py', 'import tests.test_sibling', ['test_sibling']),
        ('test_d.py', 'from test_sibling import SIBLING', ['test_sibling']),
        ('test_e.py', 'from tests.test_sibling import SIBLING',
         ['test_sibling']),
        ('test_f.py', 'from . import test_sibling', ['test_sibling']),
        ('test_g.py', 'from .test_sibling import SIBLING', ['test_sibling']),
        ('test_h.py', 'import json, test_sibling', ['test_sibling']),
        # A nested import still executes the sibling's body.
        ('test_nested.py', _mod('def load():', '    import test_sibling'),
         ['test_sibling']),
        # A star names nothing the rule can enumerate, so only its module
        # is examined — and that module is a sibling all the same.
        ('test_star.py', 'from test_sibling import *', ['test_sibling']),
        # The four dynamic callees, including the `.py` literal limb and
        # the second argument of spec_from_file_location.
        ('test_i.py', _mod('import importlib',
                           "importlib.import_module('test_sibling')"),
         ['test_sibling']),
        ('test_j.py', "__import__('test_sibling')", ['test_sibling']),
        ('test_k.py', _mod('import runpy',
                           "runpy.run_path('test_sibling')"),
         ['test_sibling']),
        ('test_l.py', _mod('import importlib.util',
                           "importlib.util.spec_from_file_location("
                           "'sibling', 'test_sibling.py')"),
         ['test_sibling']),
        # A triple-quoted synthetic-violation fixture naming a sibling is
        # a string that executes nothing: the shape of the three real
        # sites a text rule would report.
        ('test_fixture.py', _mod('FIXTURE = """', 'import os',
                                 'import test_sibling as behaviour',
                                 "subprocess.run(['python3', 'child.py'],"
                                 ' cwd=behaviour.ROOT)', '"""'), []),
        # A call spelled inside a string constant is a string.
        ('test_call_text.py',
         'CALL = "__import__(\'test_sibling\')"', []),
        # Not a sibling: itself, a shared helper, production code, the
        # tests package itself, a name no tracked module provides, and a
        # stem outside the pattern.
        ('test_self.py', 'import test_self', []),
        ('test_m.py', 'import _shared', []),
        ('test_n.py', 'from _shared import _load_queue', []),
        ('test_o.py', 'import server', []),
        ('test_p.py', 'import tests', []),
        ('test_q.py', 'import test_absent', []),
        ('test_r.py', 'from test_absent import SIBLING', []),
        ('test_s.py', 'import test_Mixed', []),
        # A sibling's private reached by copy rather than by import.
        ('test_copy.py', _mod('def _load_queue(name):', '    return 1'), []),
        # A module name built at runtime, a suite spelled by directory as
        # well as stem, and two literals naming something else.
        ('test_t.py', _mod('import importlib', 'NAME = "test_sibling"',
                           'importlib.import_module(NAME)'), []),
        ('test_u.py', _mod('import runpy',
                           "runpy.run_path('tests/test_sibling.py')"), []),
        ('test_v.py', _mod('import importlib',
                           "importlib.import_module("
                           "'daedalus_mcp.transport')"), []),
        ('test_w.py', _mod('import importlib.util',
                           "importlib.util.spec_from_file_location("
                           "'mod', 'server.py')"), []),
    ]
    for filename, text, _want in cases:
        # A fabricated case is a program a tests module could contain, so
        # it must compile; ast.parse accepts source compile() rejects.
        compile(text, filename, 'exec')
        _fabricate(root, sources, 'tests/' + filename, text)
    findings = _import_findings(sources)
    got = {}
    for item in findings:
        got.setdefault(item.path, []).append(item.module)
    for filename, _text, want in cases:
        relpath = 'tests/' + filename
        assert sorted(got.get(relpath, [])) == sorted(want), (relpath, got)


def test_the_allowance_table_is_pinned_on_both_sides(tmp):
    root = Path(tmp) / 'tree'
    sources = {}
    _fabricate(root, sources, 'tests/test_sibling.py', _mod('SIBLING = 1'))
    _fabricate(root, sources, 'tests/test_importer.py',
               _mod('import test_sibling'))
    _fabricate(root, sources, 'tests/test_fixed.py', _mod('FIXED = 1'))
    findings = _import_findings(sources)

    unallowed, stale = _partition(findings, ())
    assert [item.module for item in unallowed] == ['test_sibling'], unallowed
    assert not stale, stale
    message = _unallowed_message(unallowed)
    for fragment in ('tests/test_importer.py:1', 'import test_sibling',
                     'sibling suite test_sibling'):
        assert fragment in message, message

    unallowed, stale = _partition(findings, (
        Allowance('tests/test_importer.py', 'test_sibling', 'PR 9999'),
        Allowance('tests/test_fixed.py', 'test_sibling', 'PR 9999'),
        Allowance('tests/test_sibling.py', 'test_sibling', 'PR 9999')))
    assert not unallowed, unallowed
    # The first row covers a live site, the second names a site a merge
    # removed, and the third could never have been live: a suite
    # importing itself is never an offender.
    assert [(row.path, row.module) for row in stale] == [
        ('tests/test_fixed.py', 'test_sibling'),
        ('tests/test_sibling.py', 'test_sibling')], stale
    message = _stale_message(stale)
    for fragment in ('stale', 'tests/test_fixed.py', 'PR 9999'):
        assert fragment in message, message


def test_the_detector_refuses_a_module_it_cannot_parse(tmp):
    del tmp
    sources = {
        'tests/_cand.py': _mod('def helper():', '    return 1'),
        'tests/test_broken.py': _mod('def broken(:'),
    }
    try:
        _import_findings(sources)
    except AssertionError as exc:
        assert 'tests/test_broken.py' in str(exc), exc
    else:
        raise AssertionError('the detector accepted an unparseable module')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
