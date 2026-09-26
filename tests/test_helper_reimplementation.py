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
comparison, so the hundred and sixty suites that each have a `main` are
not copies of the one helper module that also has one.

What this control does not see, by design: a local spelled without the
owner's leading underscore, which is a spelling difference and not a
shared-helper one; a `from X import *`, whose names the rule cannot
enumerate; a helper reached through a name computed at run time; and a
same-named local that genuinely re-implements nothing, which is what
every row of UNCONSOLIDATED_NAMES is.
"""
import ast
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _helper_binds import definitions, scan  # noqa: E402

ROOT = _util.ROOT

Reimplementation = namedtuple('Reimplementation', 'path name lines owners')

# The same-named locals that are not re-implementations, keyed by
# (repo-relative path, name) so a row cannot be widened by a prefix or a
# substring match. The table may only shrink.
UNCONSOLIDATED_NAMES = {
    ('tests/_bash_resolver_scan.py', '_ModuleFacts'):
        'each guard analyses a different launcher surface, and the facts '
        'class each builds carries that surface own names, so neither is a '
        'copy of the other',
    ('tests/_bash_resolver_scan.py', '_analyze'):
        'the three analysers return their own violation records and take '
        'different memo arguments, so none composes the others',
    ('tests/_bash_resolver_scan.py', '_binding_of'):
        'the drain reader also accepts the = -less forms, so the two would '
        'answer differently for one node',
    ('tests/_bash_resolver_scan.py', '_check_launch'):
        'one reads the argv a resolver passes, the other refuses a cwd it '
        'cannot resolve, and the signatures do not meet',
    ('tests/_bash_resolver_scan.py', '_launch_method'):
        'the coverage guard also accepts any callee that spells cwd readably, '
        'which the bash resolver launcher set does not',
    ('tests/_bash_resolver_scan.py', '_synthetic_violations'):
        'each runs its own analyser over the synthetic source, and the '
        'coverage guard threads the memo keeps it needs',
    ('tests/_bash_resolver_scan.py', '_tree_violations'):
        'one enumerates test modules and the other every python source, so a '
        'shared reader would have to carry both sets',
    ('tests/_bash_resolver_scan.py', '_visit'):
        'each walks its own facts object under its own signature and its own '
        'per-call state',
    ('tests/_clientstate.py', '_output_text'):
        'the client-state reader strips the decoded value and the dashboard '
        'reader keeps it verbatim, so one would lose a behaviour',
    ('tests/_command_type_readers.py', '_literal_value'):
        'one reads a text literal and the other an evaluated expression node, '
        'so the argument types are not interchangeable',
    ('tests/_command_type_readers.py', '_parents'):
        'the scope map is built over memoised nodes only, so it is not the '
        'plain walk the command readers want',
    ('tests/_command_type_readers.py', '_refuse'):
        'each refuses with its own message shape: a where and what pair '
        'against a path, root, node and detail',
    ('tests/_control_writes.py', '_bound_names'):
        'one yields the names an assignment target binds and the other '
        'returns every name a node stores, so they answer differently',
    ('tests/_coverage_guard.py', '_ModuleFacts'):
        'each guard analyses a different launcher surface, and the facts '
        'class each builds carries that surface own names, so neither is a '
        'copy of the other',
    ('tests/_coverage_guard.py', '_analyze'):
        'the three analysers return their own violation records and take '
        'different memo arguments, so none composes the others',
    ('tests/_coverage_guard.py', '_check_launch'):
        'one reads the argv a resolver passes, the other refuses a cwd it '
        'cannot resolve, and the signatures do not meet',
    ('tests/_coverage_guard.py', '_launch_method'):
        'the coverage guard also accepts any callee that spells cwd readably, '
        'which the bash resolver launcher set does not',
    ('tests/_coverage_guard.py', '_synthetic_violations'):
        'each runs its own analyser over the synthetic source, and the '
        'coverage guard threads the memo keeps it needs',
    ('tests/_coverage_guard.py', '_visit'):
        'each walks its own facts object under its own signature and its own '
        'per-call state',
    ('tests/_coverage_scopes.py', '_bound_names'):
        'one yields the names an assignment target binds and the other '
        'returns every name a node stores, so they answer differently',
    ('tests/_coverage_scopes.py', '_parents'):
        'the scope map is built over memoised nodes only, so it is not the '
        'plain walk the command readers want',
    ('tests/_dashnode.py', '_output_text'):
        'the client-state reader strips the decoded value and the dashboard '
        'reader keeps it verbatim, so one would lose a behaviour',
    ('tests/_drain_scan.py', '_analyze'):
        'the three analysers return their own violation records and take '
        'different memo arguments, so none composes the others',
    ('tests/_drain_scan.py', '_binding_of'):
        'the drain reader also accepts the = -less forms, so the two would '
        'answer differently for one node',
    ('tests/_drain_scan.py', '_scan'):
        'one scans a module text and the other evaluates an expression '
        'against a binding set, so they share no argument',
    ('tests/_drain_scan.py', '_tree_violations'):
        'one enumerates test modules and the other every python source, so a '
        'shared reader would have to carry both sets',
    ('tests/_jsroute_sweep.py', '_indent'):
        'the sweep helper indents a block of generated JavaScript while the '
        'two yaml readers measure one line, so three unrelated meanings',
    ('tests/_mcp_code_eval.py', '_scan'):
        'one scans a module text and the other evaluates an expression '
        'against a binding set, so they share no argument',
    ('tests/_mcp_import_closure.py', '_refuse'):
        'each refuses with its own message shape: a where and what pair '
        'against a path, root, node and detail',
    ('tests/_pyroute_keys.py', '_literal_value'):
        'one reads a text literal and the other an evaluated expression node, '
        'so the argument types are not interchangeable',
    ('tests/_pyroute_live.py', '_argument_value'):
        'one reads a call-site entry and the other an expression with a '
        'caller and a sender resolver',
    ('tests/_pyroute_values.py', '_argument_value'):
        'one reads a call-site entry and the other an expression with a '
        'caller and a sender resolver',
    ('tests/_util.py', 'load'):
        'this one imports a module by path and the workflow one decodes a '
        'workflow file jobs, so neither name can serve the other',
    ('tests/_wfjobs.py', 'load'):
        'this one imports a module by path and the workflow one decodes a '
        'workflow file jobs, so neither name can serve the other',
    ('tests/_workflows.py', '_entry'):
        'the workflow reader decodes a mapping key through the bounded scalar '
        'reader, where the yaml one only splits at the first colon',
    ('tests/_workflows.py', '_indent'):
        'the sweep helper indents a block of generated JavaScript while the '
        'two yaml readers measure one line, so three unrelated meanings',
    ('tests/_yamllines.py', '_entry'):
        'the workflow reader decodes a mapping key through the bounded scalar '
        'reader, where the yaml one only splits at the first colon',
    ('tests/_yamllines.py', '_indent'):
        'the sweep helper indents a block of generated JavaScript while the '
        'two yaml readers measure one line, so three unrelated meanings',
    ('tests/test_aggregate_gate.py', '_run'):
        'this builds one workflow run as the actions API reports it, where '
        'the shared _run boots a node scenario',
    ('tests/test_aggregate_needs.py', '_fixture'):
        'this writes one fixture workflow into a fresh tmp directory, where '
        'the owner reads a fake-GitHub answer fragment',
    ('tests/test_bash_resolver_scan.py', '_synthetic'):
        'a one-line delegate to the bash resolver own synthetic entry, and '
        'the name it collides with is the drain analyser',
    ('tests/test_case_fold_parent.py', '_load'):
        'this loads a bridge module by path under a name of its own, where '
        'the owner is a JSON file reader',
    ('tests/test_ci_ratchets.py', '_git'):
        'this runs git with text output and a scrubbed child environment, '
        'which the shared runner does not set',
    ('tests/test_ci_wait.py', '_run'):
        'this builds one workflow run against a SHA, optionally without a '
        'workflow id, where the shared _run boots a node scenario',
    ('tests/test_cli_waits.py', '_run'):
        'this runs one argv under a supplied environment with a 60s bound, '
        'where the shared _run boots a node scenario',
    ('tests/test_config_boot_generation.py', '_run'):
        'this drives the worker under node for one plan and reads back its '
        'streams, where the shared _run boots a recorded scenario',
    ('tests/test_coverage_bindings.py', '_scope_violations'):
        'this renders the expected violation strings for a synthetic source, '
        'where the owner orders real calls within a scope',
    ('tests/test_dashboard_fanout.py', '_order'):
        'this returns the real daedalus_bridge.queue_order the loaded '
        'command_queue mints with, where the owner builds a JS case tuple',
    ('tests/test_dashboard_harness.py', '_harness_failure'):
        'this drives the shipped retry entry with bounded steps, where the '
        'owner reads a relay harness failure under a plan',
    ('tests/test_dashboard_tab_events.py', '_run'):
        'this runs the dashboard node and parses its JSON, where the shared '
        '_run boots a recorded boundary scenario',
    ('tests/test_delivery_stripe_acceptance.py', '_lines'):
        'this splits a file own text into lines, where the owner retains '
        'whether each physical line ended',
    ('tests/test_diff_coverage.py', '_git'):
        'this runs one git command in a fixture repository with text output, '
        'where the shared runner captures bytes only',
    ('tests/test_diff_coverage_javascript.py', '_run'):
        'this runs the reporter script inside the fixture directory, where '
        'the shared _run boots a node scenario',
    ('tests/test_env_publication.py', '_bound_names'):
        'this one yields each name with the value bound beside it, in source '
        'order, which neither owner takes an argument for',
    ('tests/test_extension_manifest.py', '_entry'):
        'this builds a manifest mutant from an index, key, subkey and value, '
        'so it is not a mapping-line reader at all',
    ('tests/test_js_coverage.py', '_git'):
        'this runs git with text output in a scratch index, where the shared '
        'runner captures bytes',
    ('tests/test_line_lengths.py', '_git'):
        'this runs git under a scrubbed child environment, which the shared '
        'runner does not set',
    ('tests/test_line_lengths.py', '_lines'):
        'this joins texts and encodes them as the byte-length source, where '
        'the owner splits a workflow keeping line endings',
    ('tests/test_mcp_live_tools.py', '_row'):
        'this builds one tool row from a command type, its fields and a '
        'builder, where the owner builds a getter-argument case',
    ('tests/test_mcp_refusal_drain.py', '_load_mcp'):
        'this drives the already-booted bridge with an empty token, which the '
        'shared loader base_url-first signature does not express',
    ('tests/test_overlap_bound.py', '_bound_source'):
        'this slices the shipped prelude bound machinery at the entry IIFE, '
        'where the owner reads a job field after timeout-minutes',
    ('tests/test_real_browser_harness.py', '_browser_version'):
        'this asserts a stubbed --version call, where the owner asks a '
        'browser object what it calls itself',
    ('tests/test_real_browser_harness_recovery.py', '_control_target'):
        'this names a different extension origin under test, which is the '
        'whole point of the case',
    ('tests/test_relay_example_placeholders.py', '_run'):
        'this runs the relay harness under one source and a plan, where the '
        'shared _run boots a recorded scenario',
    ('tests/test_repo_layout.py', '_enclosing_function'):
        'this finds the innermost function whose body spans a line, where the '
        'owner walks up from a node through a parent map',
    ('tests/test_result_routes.py', '_load'):
        'this loads result_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_result_stripe.py', '_load'):
        'this loads result_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_runner_refuses_unawaited.py', '_run'):
        'this runs the suite collector with warnings recorded, where the '
        'shared _run boots a node scenario',
    ('tests/test_static_routes.py', '_load'):
        'this loads static_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_stream_backoff.py', '_run'):
        'this runs one backoff plan against the shipped worker, where the '
        'shared _run boots a recorded scenario',
    ('tests/test_tab_registry.py', '_load'):
        'this loads tab_registry by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_tab_routing_js_heads.py', '_literal'):
        'this builds a method entry plus a plain sibling, where the owner '
        'builds a getter-returning object',
    ('tests/test_tab_routing_positions.py', '_literal'):
        'this builds one member-access position case from a named shape, '
        'where the owner builds a getter-returning object',
    ('tests/test_tab_routing_positions.py', '_run'):
        'this runs one masked source through the position verdict, where the '
        'shared _run boots a node scenario',
    ('tests/test_tab_routing_unprovable.py', '_scan'):
        'this writes one synthetic module and asks the route scanner, where '
        'the owner scans a module text with a memo',
    ('tests/test_timed_planner.py', '_run'):
        'this runs the planner main with stdout captured, where the shared '
        '_run boots a node scenario',
    ('tests/test_timed_refresh.py', '_run'):
        'this runs the refresh main with both streams captured, where the '
        'shared _run boots a node scenario',
    ('tests/test_type_errors.py', '_git'):
        'this runs git under a scrubbed child environment, which the shared '
        'runner does not set',
    ('tests/test_upload_races.py', '_load'):
        'this loads upload_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_upload_routes.py', '_load'):
        'this loads upload_routes by path under a name of its own, where the '
        'owner is a JSON file reader',
    ('tests/test_watch_all.py', '_run'):
        'this builds one shared-client workflow run against a SHA, where the '
        'shared _run boots a node scenario',
    ('tests/test_watcher_budget.py', '_comment'):
        'this builds one review-comment node, where the owner asks whether a '
        'line is a YAML comment',
    ('tests/test_worker_register_throttle.py', '_observe'):
        'this drives the register-throttle harness under a plan with expected '
        'streams, where the owner reads one relay mode answer',
    ('tests/test_worker_result_post.py', '_post'):
        'this drives one postResult call through the shipped worker source, '
        'where the owner builds one MCP answer tuple',
    ('tests/test_workflow_eslint.py', '_run'):
        'this returns one named step run block through the bounded reader, '
        'where the shared _run boots a node scenario',
    ('tests/test_workflow_job_timeouts.py', '_fixture'):
        'this writes one fixture workflow into a fresh tmp directory, where '
        'the owner reads a fake-GitHub answer fragment',
    ('tests/test_workflow_job_timeouts.py', '_planted'):
        'this copies the real workflow minus the aggregate job bound, where '
        'the owner reverts one converted site in a scratch tree',
    ('tests/test_workflow_job_timeouts.py', '_scan'):
        'this checks one workflow and through a local caller its target, '
        'where the owner scans a module text with a memo',
}


def _mod(*lines):
    return ''.join(line + '\n' for line in lines)


def _in_tests(path):
    return Path(path).parent == Path('tests')


def _is_shared_helper(path):
    return _in_tests(path) and Path(path).stem.startswith('_')


def _parse(path, source):
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise AssertionError(
            f'tests module does not parse: {path}: {exc}') from exc


def _entry_points(tree):
    """The names the module calls inside a `__main__` guard.

    A script's own entry point is not a shared helper's name: every
    module that runs itself under that guard has one, and none of them
    is a copy of any other. Both operand orders of the comparison are
    the same guard.
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (not isinstance(test, ast.Compare)
                or not isinstance(test.ops[0], ast.Eq)
                or len(test.comparators) != 1):
            continue
        left, right = test.left, test.comparators[0]
        if isinstance(left, ast.Name) and left.id == '__name__':
            guarded = right
        elif isinstance(right, ast.Name) and right.id == '__name__':
            guarded = left
        else:
            continue
        if (not isinstance(guarded, ast.Constant)
                or guarded.value != '__main__'):
            continue
        for statement in node.body:
            names.update(sub.id for sub in ast.walk(statement)
                         if isinstance(sub, ast.Name))
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
        tree = _parse(path, sources[path])
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
        text=True, check=True).stdout.splitlines()
    assert listed, 'git ls-files named no tests module'
    sources = {name: (ROOT / name).read_text(encoding='utf-8')
               for name in listed}
    return sources, reimplementations(sources)


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

    def found(**limbs):
        return {(item.path, item.name)
                for item in reimplementations(sources, **limbs)}

    assert found() == {('tests/test_owner.py', '_trim'),
                       ('tests/_second.py', '_twice'),
                       ('tests/_third.py', '_twice')}, sorted(found())
    # Limb two dropped: the sole owner stops being the definition.
    without_owner = found(owner_is_the_definition=lambda path, name, o: False)
    assert ('tests/_owner.py', '_trim') in without_owner, without_owner
    # Limb three dropped: a module that imports the name and defines it
    # too is this control's finding, and the shadow control's.
    without_import = found(an_import_settles_it=lambda i, n, s: False)
    assert ('tests/test_twice.py', '_twice') in without_import, without_import
    # The owner set read per owner rather than as a set: both `_twice`
    # owners become clean and the pair goes unreported.
    per_owner = found(owner_is_the_definition=lambda path, name, o:
                      path in o.get(name, ()))
    assert not [item for item in per_owner if item[1] == '_twice'], per_owner


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
