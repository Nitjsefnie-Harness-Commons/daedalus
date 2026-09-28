#!/usr/bin/env python3
"""A position the runtime provably cannot reach, on synthetic input.

The closure walk reads a CALLEE as a value and applies one rule to every
value it reads: a value the runtime provably cannot reach through is CLEAN
rather than suspicious. This file applies the same rule to POSITIONS, and
every case here says what the RUNTIME does — each runs the composition in a
fresh interpreter and reads `sys.modules` or the exception — so a control
never compares two of the walk's own answers.

The cases that carry the axis are the soundness ones: a `try`, a `with`, a
loop, a `match` and a bare `if` all look like barriers to a reader that has
not asked what the runtime does past them, and each of them here is a
module the runtime really imports, so a rule that over-reaches loses it.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_dead_code  # noqa: E402
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402


_RUNTIME_PROBE = '''
import importlib.util, json, os, sys
target = sys.argv[1]
sys.path.insert(0, os.path.dirname(target))
spec = importlib.util.spec_from_file_location('probe_target', target)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
raised = None
try:
    arguments = [] if len(sys.argv) < 4 else sys.argv[3].split(',')
    getattr(module, sys.argv[2])(*arguments)
except BaseException as error:
    raised = type(error).__name__
sys.stderr.write(json.dumps({
    'raised': raised,
    'loaded': sorted(name for name in sys.modules
                     if name.startswith('pkg.'))}))
'''


def _write_tree(directory, files):
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


def _scan_set(_tmp):
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    return {path.relative_to(Path(_tmp)).as_posix() for path in scanned}


def _runtime(_tmp, entry='load', arguments=''):
    """What the composition does when `entry` is called, read from a fresh
    interpreter: the exception it raised, and the `pkg.` modules it loaded."""
    probe = Path(_tmp) / 'runtime_probe.py'
    probe.write_text(_RUNTIME_PROBE, encoding='utf-8')
    command = [sys.executable, str(probe),
               str(Path(_tmp) / 'composition.py'), entry]
    if arguments:
        command.append(arguments)
    done = subprocess.run(
        command, capture_output=True, text=True, check=True)
    return json.loads(done.stderr)


def _scan_is(_tmp, expected):
    names = _scan_set(_tmp)
    assert names == expected, names


def _pkg(**modules):
    tree = {'composition.py': modules.pop('source'),
            'pkg/__init__.py': ''}
    for name in modules:
        tree[f'pkg/{name}.py'] = f'{name} = True\n'
    return tree


def test_a_call_after_a_raise_is_out_of_the_scan_set(_tmp):
    """The reported case: the composition raises before the index is read.

    Nothing imports `pkg.leaf` at runtime, so a scan set carrying it is not
    the closure it claims to be — the floor would scan a module the
    composition cannot import and ask it to declare raise sites no tool
    reaches.
    """
    _write_tree(Path(_tmp), _pkg(source="""
import importlib


def load():
    raise RuntimeError('before the index')
    return [importlib.import_module, 0][0]('pkg.leaf')
""", leaf=''))
    _scan_is(_tmp, {'composition.py'})
    assert _runtime(_tmp) == {'raised': 'RuntimeError', 'loaded': []}


def _a_barrier_tail_is_out_of_the_scan_set(_tmp, source, expected):
    """Everything after the FIRST barrier is out, and the runtime says which
    modules it loaded and what it raised."""
    _write_tree(Path(_tmp), _pkg(source=source, leaf=''))
    _scan_is(_tmp, {'composition.py'})
    assert _runtime(_tmp) == expected


def test_a_return_before_the_call_ends_the_scan_set(_tmp):
    _a_barrier_tail_is_out_of_the_scan_set(_tmp, """
import importlib


def load():
    return 'early'
    return importlib.import_module('pkg.leaf')
""", {'raised': None, 'loaded': []})


def test_a_break_before_the_call_ends_that_loops_tail_only(_tmp):
    """`break` leaves the LOOP BODY, so the tail it protects is that body's
    and the code after the loop is still reached — which the runtime
    confirms by returning."""
    _a_barrier_tail_is_out_of_the_scan_set(_tmp, """
import importlib


def load():
    for item in ():
        break
        importlib.import_module('pkg.leaf')
    return 'after the loop'
""", {'raised': None, 'loaded': []})


def test_a_continue_before_the_call_ends_that_loops_tail_only(_tmp):
    _a_barrier_tail_is_out_of_the_scan_set(_tmp, """
import importlib


def load():
    for item in (1,):
        continue
        importlib.import_module('pkg.leaf')
    return 'after the loop'
""", {'raised': None, 'loaded': []})


def test_a_statement_between_two_barriers_is_still_unreachable(_tmp):
    """A barrier protects the WHOLE tail, however many follow it and however
    far apart they are. Bounding the skip to the statement after the barrier
    is the bug this shape invites."""
    _a_barrier_tail_is_out_of_the_scan_set(_tmp, """
import importlib


def load():
    raise RuntimeError('first')
    importlib.import_module('pkg.leaf')
    between = 'never assigned'
    importlib.import_module('pkg.leaf')
    return between
""", {'raised': 'RuntimeError', 'loaded': []})


def test_a_statement_before_the_barrier_is_still_resolved(_tmp):
    """The near miss: a dead region that starts one statement too early is a
    silent under-report, so the statement BEFORE the barrier keeps its
    module and the runtime proves it is loaded."""
    _write_tree(Path(_tmp), _pkg(source="""
import importlib


def load():
    before = importlib.import_module('pkg.leaf')
    raise RuntimeError('the barrier')
    return importlib.import_module('pkg.after')
""", leaf='', after=''))
    _scan_is(_tmp, {'composition.py', 'pkg/__init__.py', 'pkg/leaf.py'})
    assert _runtime(_tmp) == {'raised': 'RuntimeError', 'loaded': ['pkg.leaf']}


def test_a_static_import_after_a_raise_is_still_in_the_scan_set(_tmp):
    """The deliberate asymmetry, and the reason the gate is on the CALL
    chain: a static import is a DECLARATION the walk collects wherever it is
    written, dead code included, while a call the runtime never makes is
    not."""
    _write_tree(Path(_tmp), _pkg(source="""
def load():
    raise RuntimeError('before the import')
    from pkg import leaf
""", leaf=''))
    _scan_is(_tmp, {'composition.py', 'pkg/__init__.py', 'pkg/leaf.py'})
    assert _runtime(_tmp) == {'raised': 'RuntimeError', 'loaded': []}


def _a_raising_container_does_not_end_the_block(_tmp, source):
    """A container that may let execution past it keeps the call after it in
    the scan set, and the runtime confirms it really gets there: the module
    is loaded, which is the one thing a walk that lost it cannot fake."""
    _write_tree(Path(_tmp), _pkg(source=source, leaf=''))
    _scan_is(_tmp, {'composition.py', 'pkg/__init__.py', 'pkg/leaf.py'})
    assert _runtime(_tmp) == {'raised': None, 'loaded': ['pkg.leaf']}


def test_a_caught_raise_does_not_end_the_block(_tmp):
    _a_raising_container_does_not_end_the_block(_tmp, """
import importlib


def load():
    try:
        raise ValueError('caught')
    except ValueError:
        pass
    return importlib.import_module('pkg.leaf')
""")


def test_a_suppressed_raise_does_not_end_the_block(_tmp):
    """`contextlib.suppress` swallows the exception in `__exit__`, so
    execution continues past the `with` and the call after it is live."""
    _a_raising_container_does_not_end_the_block(_tmp, """
import contextlib
import importlib


def load():
    with contextlib.suppress(ValueError):
        raise ValueError('suppressed')
    return importlib.import_module('pkg.leaf')
""")


def test_an_if_without_an_orelse_does_not_end_the_block(_tmp):
    """A condition that is false falls straight through the `if`, so its
    body leaving the block is not the `if` leaving the block."""
    _a_raising_container_does_not_end_the_block(_tmp, """
import importlib


def load(raised=False):
    if raised:
        raise ValueError('not taken')
    return importlib.import_module('pkg.leaf')
""")


def _a_partly_leaving_if_keeps_the_call(_tmp, source, runs):
    """An `if` leaves the block only when BOTH branches leave, and the runtime
    is asked once per condition — the module is loaded on the run that falls
    through, which is the one a walk that lost a branch cannot fake."""
    _write_tree(Path(_tmp), _pkg(source=source, leaf=''))
    _scan_is(_tmp, {'composition.py', 'pkg/__init__.py', 'pkg/leaf.py'})
    for entry, arguments, expected in runs:
        outcome = _runtime(_tmp, entry, arguments)
        assert outcome == expected, (arguments, outcome)


def test_an_if_with_only_one_branch_leaving_does_not_end_the_block(_tmp):
    """The `orelse` limb alone, and the only input that limb decides.

    One branch leaving is not the `if` leaving: the run that takes the other
    branch falls through to the call and the runtime loads the module. A rule
    that read the `body` and stopped there calls this a barrier and loses the
    module on both runs, and every suite stays green over that — the whole
    reason this row asks the runtime rather than the walk.
    """
    _a_partly_leaving_if_keeps_the_call(_tmp, """
import importlib


def load(cond='raise'):
    if cond == 'raise':
        raise ValueError('taken')
    else:
        pass
    return importlib.import_module('pkg.leaf')
""", [('load', 'raise', {'raised': 'ValueError', 'loaded': []}),
      ('load', 'pass', {'raised': None, 'loaded': ['pkg.leaf']})])


def test_a_raising_loop_body_does_not_end_the_block(_tmp):
    """The body raising says nothing about the loop, which may run zero
    times and fall through either way."""
    _a_raising_container_does_not_end_the_block(_tmp, """
import importlib


def load():
    for item in ():
        raise ValueError('never')
    return importlib.import_module('pkg.leaf')
""")


def test_a_raising_try_body_with_no_handler_does_not_end_the_block(_tmp):
    """Two readings of a `try` with no handler, and both are pinned.

    A `finally` runs whether or not the body raised, so a call inside one is
    a call the runtime makes. A call after the whole `try` is not: the
    exception leaves the frame, and the walk still reports the module,
    because a `try` is not a barrier and a rule that treated it as one
    would lose a module it may not refuse.
    """
    _a_raising_container_does_not_end_the_block(_tmp, """
import importlib


def load():
    try:
        raise ValueError('unhandled')
    finally:
        return importlib.import_module('pkg.leaf')
""")
    _write_tree(Path(_tmp), _pkg(source="""
import importlib


def load():
    try:
        raise ValueError('unhandled')
    finally:
        pass
    return importlib.import_module('pkg.leaf')
""", leaf=''))
    _scan_is(_tmp, {'composition.py', 'pkg/__init__.py', 'pkg/leaf.py'})
    assert _runtime(_tmp) == {'raised': 'ValueError', 'loaded': []}


def test_every_statement_the_standard_library_declares_is_answered(_tmp):
    """The whole domain, derived rather than listed.

    `_leaves` answers True for the barriers and for an `if` whose two
    branches leave, and False for every other statement there is — the
    containers that may or may not run among them, which is why the walk
    carries those as prose beside the fall-through and checks none of them.
    Reading the domain off `ast.stmt` is what makes that a claim about all
    of it: a kind a future release declares is inside this row the moment it
    exists, with no edit here to bring it in, and a kind this walk starts
    treating as a barrier has to be a barrier somewhere public.
    """
    kinds, pending = {}, [ast.stmt]
    while pending:
        for child in pending.pop().__subclasses__():
            if child not in kinds:
                kinds[child.__name__] = child
                pending.append(child)
    assert len(kinds) >= 20, f'the domain enumerated as {sorted(kinds)}'
    barriers = {kind.__name__ for kind in _mcp_dead_code._BARRIERS}
    for name, kind in sorted(kinds.items()):
        if name == 'If':
            statement = ast.If.__new__(ast.If)
            statement.body = [ast.Raise.__new__(ast.Raise)]
            statement.orelse = [ast.Raise.__new__(ast.Raise)]
        else:
            statement = kind.__new__(kind)
        assert _mcp_dead_code._leaves(statement, set()) is (
            name in barriers or name == 'If'), name


def _a_nested_body_tail_is_out_of_the_scan_set(_tmp, source, runs):
    """A barrier inside one container kills that container's OWN tail and
    leaves the code after the container alone.

    The container forms are read generically, so each of them is pinned
    here: a `match` case body, an `except` handler body, a `with` body and
    a `for`'s `orelse`. Each run is a fresh interpreter, so what the
    runtime loaded is read rather than assumed.
    """
    _write_tree(Path(_tmp), _pkg(source=source, dead='', live=''))
    _scan_is(_tmp, {'composition.py', 'pkg/__init__.py', 'pkg/live.py'})
    for entry, arguments, expected in runs:
        outcome = _runtime(_tmp, entry, arguments)
        assert outcome == expected, (entry, outcome)


def test_a_match_case_body_tail_is_out_of_the_scan_set(_tmp):
    _a_nested_body_tail_is_out_of_the_scan_set(_tmp, """
import importlib


def load():
    try:
        match 'first':
            case 'first':
                raise ValueError('body')
                importlib.import_module('pkg.dead')
    except ValueError:
        pass
    return importlib.import_module('pkg.live')
""", [('load', '', {'raised': None, 'loaded': ['pkg.live']})])


def test_an_except_handler_body_tail_is_out_of_the_scan_set(_tmp):
    """The handler has to RUN for the control to say anything, so the first
    run takes the raise and the second does not — and `pkg.live` is loaded
    on the run that gets past the handler. The flag is a string because the
    probe passes arguments through a command line, where `'False'` is a
    true value."""
    _a_nested_body_tail_is_out_of_the_scan_set(_tmp, """
import importlib


def load(fail='yes'):
    try:
        if fail == 'yes':
            raise ValueError('raised')
    except ValueError:
        if fail == 'yes':
            raise RuntimeError('handler')
            importlib.import_module('pkg.dead')
    return importlib.import_module('pkg.live')
""", [('load', 'no', {'raised': None, 'loaded': ['pkg.live']}),
      ('load', 'yes', {'raised': 'RuntimeError', 'loaded': []})])


def test_a_with_body_tail_is_out_of_the_scan_set(_tmp):
    _a_nested_body_tail_is_out_of_the_scan_set(_tmp, """
import importlib
import os


def load():
    try:
        with open(os.devnull) as handle:
            raise RuntimeError('body')
            importlib.import_module('pkg.dead')
    except RuntimeError:
        pass
    return importlib.import_module('pkg.live')
""", [('load', '', {'raised': None, 'loaded': ['pkg.live']})])


def test_a_for_orelse_tail_is_out_of_the_scan_set(_tmp):
    _a_nested_body_tail_is_out_of_the_scan_set(_tmp, """
import importlib


def load():
    try:
        for item in ():
            pass
        else:
            raise RuntimeError('orelse')
            importlib.import_module('pkg.dead')
    except RuntimeError:
        pass
    return importlib.import_module('pkg.live')
""", [('load', '', {'raised': None, 'loaded': ['pkg.live']})])


# One row per reader the walk consults, with the phrase that reader refuses
# with when the runtime reaches the form. The last row is the one reader a
# dead region cannot reach: a parameter default lives in a signature, which
# no barrier covers, so it is still refused behind a `raise` and is marked
# as not gated rather than quietly left out.
COHERENCE_ROWS = (
    ('the import name',
     '\nimport importlib\n\n\ndef load(name="pkg.leaf"):',
     '    return importlib.import_module(name)',
     'cannot read statically', True),
    ('a callee that is a conditional',
     '\nimport importlib\n\n\ndef load(c=1):',
     "    return (importlib.import_module if c else print)('pkg.leaf')",
     'reaches the import-by-name operation', True),
    ('a position the fold cannot read',
     '\nimport importlib\n\n\ndef load(i=0):',
     "    return [importlib.import_module][i]('pkg.leaf')",
     'reaches the import-by-name operation', True),
    ('a getattr that hands the operation out',
     '\nimport importlib\n\n\ndef load(name="pkg.leaf"):',
     "    return getattr(importlib, 'import_module')(name)",
     'cannot follow', True),
    ('a string naming the operation',
     '\nimport importlib\n\n\ndef load():',
     "    return 'import_module'",
     'names the import-by-name operation', True),
    ('a module read out of the registry by string',
     '\nimport sys\n\n\ndef load(name="pkg.leaf"):',
     '    return sys.__dict__["modules"][name]',
     'cannot resolve', True),
    ('a store binding the operation',
     '\nimport importlib\n\n\ndef load():',
     '    alias = importlib.import_module\n    return alias',
     'cannot follow', True),
    ('a store binding the registry',
     '\nimport sys\n\n\ndef load():',
     '    held = sys\n    return held',
     'module registry', True),
    ('a constant program handed to a code-evaluating builtin',
     '\nimport builtins\n\n\ndef load():',
     '    return builtins.eval("importlib")("pkg.leaf")',
     'code-evaluating builtin', True),
    ('a parameter default that hides the operation',
     '\nimport importlib\n\n\ndef load(name="pkg.leaf",'
     ' loader=importlib):',
     '    return loader.import_module(name)',
     'cannot follow', False),
)


def test_a_refusal_the_runtime_can_never_trigger_is_clean(_tmp):
    """Coherence, one row per reader, and the widening it buys.

    Behind a `raise` each of these forms is refused today and is clean after
    this change, because the runtime never gets to any of them — which each
    row's own run confirms by raising at the barrier and loading nothing.
    The same form in a REACHED position still refuses, with the reader's
    own phrase: the gate is on the CALL chain and nothing else moved.
    """
    for label, header, body, phrase, gated in COHERENCE_ROWS:
        _write_tree(Path(_tmp), {
            'composition.py': f'{header}\n    raise RuntimeError("never")\n'
                              f'{body}\n'})
        assert _runtime(_tmp) == {'raised': 'RuntimeError', 'loaded': []}, (
            label, 'the runtime reached a dead position')
        try:
            scanned = _scan_set(_tmp)
        except AssertionError as raised:
            assert not gated, (label, 'refused behind a barrier', str(raised))
            assert phrase in str(raised), (label, str(raised))
        else:
            assert gated, (label, 'skipped behind a barrier', sorted(scanned))
        _write_tree(Path(_tmp), {'composition.py': f'{header}\n{body}\n'})
        try:
            _scan_set(_tmp)
        except AssertionError as raised:
            assert phrase in str(raised), (label, 'reached', str(raised))
        else:
            raise AssertionError(f'{label} was silently skipped when reached')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
