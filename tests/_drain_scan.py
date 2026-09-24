#!/usr/bin/env python3
"""Every stop-then-drain of one process object in this tree is bounded.

Not a suite itself — run_tests.py only loads `test_*.py`.

The property: a call that STOPS a process object - `kill`, `terminate` or
`send_signal` - that is followed, in the same scope and on the same object,
by a DRAIN of that object - `communicate` or `wait` - carrying no
`timeout=` keyword is refused. Those two member sets are the whole spelling
surface, read once from `_STOPS` and `_DRAINS`.

A drain is bounded only by an explicit `timeout=` of its own. A `**` spread
supplies no bound and is never read as one, so `process.communicate(**opts)`
drains unbounded.

An object is its receiver expression, compared structurally, so
`spawned[0].kill()` and `spawned[0].wait()` are one object. A name bound to
another receiver is followed to that receiver and re-read until it stops
moving, so `p = process` before `p.kill()` is the object
`process.communicate()` drains. Every form that binds a name to a value is
read the same way: an assignment, a walrus, a `with ... as` target, and a
`for` or comprehension target over a one-element sequence. A loop target
over anything else walks a collection of handles, so it is one of them and
not the collection, and the guard will not read that as an alias.

Within a scope, calls are read in source order, except that a
comprehension's element is read after its conditions: the element is
produced only once the conditions have passed, so
`[p.wait() for p in procs if p.kill()]` reads as the stop-then-drain it is.

Not recognised, each gap named because the guard claims it:

* The stop and the drain in DIFFERENT scopes. `tests/_speedharness.py`
  stops a process in `_kill_process_tree` and drains that same process in
  `_reap_process`, the two joined only by `_cleanup_process`; the calls
  share a parameter, not a scope, so this scan reads neither. The drain
  there carries `timeout=_CLEANUP_TIMEOUT` and
  `tests/test_speedharness.py::`
  `test_the_harness_bounds_cleanup_when_tree_kill_fails` pins the reap, so
  the shape is held by that test rather than by this scan.
* A drain in a helper CALLED WITH the process, which no binding in this
  scope can reach. A name bound more than once in its scope, or bound to
  something that is not a receiver, stands for itself instead. A stop and a
  drain that can only be connected through one of those are invisible here.
* A stop or a drain in an ARGUMENT of a call on that same object. An
  argument is evaluated before the call it is an argument of, so
  `proc.communicate(proc.kill())` kills and then drains where source order
  reads the drain first. Ordering on the expression tree would model that;
  the sort here does not, and no real site spells it.
* A computed member is refused rather than read, because an unread name is
  neither "no stop" nor "a bound": `getattr(proc, 'kill')()` and
  `proc['wait']()` are refused outright, and so is `proc[name]()` when the
  same scope already stops or drains `proc` plainly.
* A drain reached through `tests/_drain.py::kill_and_drain` is not a drain
  spelling and is out of the scan; that helper's own suite pins the default
  bound its name-only call sites inherit.
"""
import ast
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _coverage_memo import analysed  # noqa: E402
from _coverage_scopes import (  # noqa: E402
    _containing_binding_scope, _evaluation_scopes, _scope_shadows)
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT, iter_tree_files  # noqa: E402
_STOPS = frozenset({'kill', 'terminate', 'send_signal'})
_DRAINS = frozenset({'communicate', 'wait'})
_STOP = 'stop'
_DRAIN = 'drain'
_TIMEOUT = 'timeout'
_GETATTR = 'getattr'
_PYTHON = '.py'

_COMPREHENSION_SCOPES = (
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# Node kinds that carry no per-parse identity: `ast` shares ONE instance of
# each across a whole parse, so a set of them matches every expression in
# the file -- the `_element_ids` sort silently did nothing until these
# were skipped.
_SYNTAX_ONLY = (ast.expr_context, ast.operator, ast.unaryop, ast.boolop,
                ast.cmpop)


def _receiver(node):
    """The receiver expression `node` names, or None when it names none.

    A one-element list or tuple is the receiver it holds, which is what
    makes `for p in (proc,)` a binding of `proc` under the name `p`.
    """
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) == 1:
        node = node.elts[0]
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return node
    return None


def _binding_of(node):
    """(bound names, value) for every form that binds a name to a value.

    The `=`-less forms bind exactly as an assignment does, so the walk
    carries all of them or a stop on one of their targets is unjoined
    from the drain of the object that target names.
    """
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    elif isinstance(node, ast.NamedExpr):
        targets, value = [node.target], node.value
    elif isinstance(node, ast.withitem) and node.optional_vars is not None:
        targets, value = [node.optional_vars], node.context_expr
    else:
        return (), None
    names = [part.id for target in targets for part in ast.walk(target)
             if isinstance(part, ast.Name)
             and isinstance(part.ctx, ast.Store)]
    return names, value


def _sequence_receiver(node):
    """The element a loop target binds, or None when it is not readable.

    A loop binds each ELEMENT of what it walks, so a target is only the
    receiver itself when the walked value is a one-element sequence. The
    collection case is declared in the module docstring.
    """
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    if len(node.elts) != 1 or isinstance(node.elts[0], ast.Starred):
        return None
    return node.elts[0]


def _loop_targets(node):
    """(name, element) per loop target this guard can read."""
    if isinstance(node, (ast.For, ast.AsyncFor)):
        walked = [(node.target, node.iter)]
    elif isinstance(node, _COMPREHENSION_SCOPES):
        walked = [(part.target, part.iter) for part in node.generators]
    else:
        return ()
    found = []
    for target, sequence in walked:
        element = _sequence_receiver(sequence)
        if element is not None and isinstance(target, ast.Name):
            found.append((target.id, element))
    return tuple(found)


def _literal_index(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _computed_member(call):
    """(member, receiver) for a member read without a plain attribute.

    `(None, receiver)` is a subscript whose index is computed: it names
    nothing the guard can read. `(None, None)` reads no member this way.
    """
    function = call.func
    if (isinstance(function, ast.Call)
            and isinstance(function.func, ast.Name)
            and function.func.id == _GETATTR and not function.keywords
            and len(function.args) == 2):
        return _literal_index(function.args[1]), function.args[0]
    if isinstance(function, ast.Subscript):
        return _literal_index(function.slice), function.value
    return (None, None)


def _carries_bound(call):
    """Whether a drain carries a `timeout=` of its own.

    A `**` spread is not one: a caller that means to bound the drain says so
    in the call.
    """
    return any(keyword.arg == _TIMEOUT for keyword in call.keywords)


class _ObjectKeys:
    """Receiver expressions as object keys, aliases read to a fixpoint."""

    def __init__(self, tree):
        self.scoped, self.parents = _evaluation_scopes(tree)
        self.shadows = _scope_shadows(tree, (self.scoped, self.parents))
        self.calls: dict = {scope: [] for scope in self.parents}
        self.bindings: dict = {scope: {} for scope in self.parents}
        for node, scope in self.scoped:
            if isinstance(node, ast.Call):
                self.calls[scope].append(node)
            if isinstance(node, _COMPREHENSION_SCOPES):
                # A comprehension target binds the comprehension's own
                # scope, but the sequence it walks was evaluated outside.
                for name, receiver in _loop_targets(node):
                    self.bindings[node].setdefault(name, []).append(
                        (receiver, scope))
                continue
            names, value = _binding_of(node)
            target = scope
            if isinstance(node, ast.NamedExpr):
                target = _containing_binding_scope(scope, self.parents)
            for name in names:
                self.bindings[target].setdefault(name, []).append(
                    (value, scope))
            for name, receiver in _loop_targets(node):
                self.bindings[scope].setdefault(name, []).append(
                    (receiver, scope))

    def key(self, node, scope):
        """One key naming the object a receiver denotes, or None."""
        if isinstance(node, ast.Name):
            return self._followed(node.id, scope)
        receiver = _receiver(node)
        return None if receiver is None else ast.unparse(receiver)

    def _followed(self, name, scope):
        """The receiver a chain of plain-name aliases ends at.

        A cycle has no end, so it names no object this guard can read.
        """
        seen = set()
        while name not in seen:
            seen.add(name)
            found = self._bound(name, scope)
            if found is None:
                return name
            bound, scope = found
            if isinstance(bound, ast.Name):
                name = bound.id
                continue
            receiver = _receiver(bound)
            return name if receiver is None else ast.unparse(receiver)
        return None

    def _bound(self, name, scope):
        """(value, the scope it means in), or None when it is unprovable.

        The walk stops at the first scope that binds the name at all, so a
        parameter is never resolved through an outer binding of the same
        spelling.
        """
        while scope is not None:
            values = self.bindings[scope].get(name)
            if values is not None:
                return values[0] if len(values) == 1 else None
            if name in self.shadows[scope]:
                return None
            scope = self.parents[scope]
        return None


class _Op:
    """One call as a process operation, on one object, read some way."""

    def __init__(self, kind, key, bounded, computed, call):
        self.kind = kind
        self.key = key
        self.bounded = bounded
        self.computed = computed
        self.call = call

    def stops(self):
        return self.kind == _STOP

    def drains(self):
        return self.kind == _DRAIN

    def named(self):
        return self.kind is not None


def _operation(call, keys, scope):
    """The call as an operation on the object its receiver names.

    A computed member reports the kind it names with `bounded` False,
    because a name the guard cannot read is not a bound it has found.
    """
    function = call.func
    if isinstance(function, ast.Attribute):
        if function.attr in _STOPS:
            return _Op(_STOP, keys.key(function.value, scope), False, False,
                       call)
        if function.attr in _DRAINS:
            return _Op(_DRAIN, keys.key(function.value, scope),
                       _carries_bound(call), False, call)
        return _Op(None, None, True, False, call)
    member, receiver = _computed_member(call)
    if member in _STOPS:
        kind = _STOP
    elif member in _DRAINS:
        kind = _DRAIN
    else:
        return _Op(None, keys.key(receiver, scope), True, True, call)
    return _Op(kind, keys.key(receiver, scope), False, True, call)


def _element_ids(scope):
    """`id()` of the nodes a comprehension evaluates after its conditions.

    A comprehension runs every `if` before it produces its element, so
    source order reads `[p.wait() for p in procs if p.kill()]` backwards.

    Ids rather than nodes, and the syntax-only kinds left out, because
    neither can say which tree a node is in -- see `_SYNTAX_ONLY`.
    """
    if not isinstance(scope, _COMPREHENSION_SCOPES):
        return frozenset()
    parts = ((scope.key, scope.value) if isinstance(scope, ast.DictComp)
             else (scope.elt,))
    return frozenset(
        id(node) for part in parts for node in ast.walk(part)
        if not isinstance(node, _SYNTAX_ONLY))


def _scope_violations(calls, keys, scope, relative, violations):
    late = _element_ids(scope)
    ordered = sorted(calls, key=lambda call: (
        any(id(node) in late for node in ast.walk(call)),
        call.lineno, call.col_offset))
    records = [_operation(call, keys, scope) for call in ordered]
    judged = {op.key for op in records
              if op.key is not None and op.named() and not op.computed}
    stops = {}
    for op in records:
        if op.key is None:
            continue
        if op.computed and (op.named() or op.key in judged):
            violations.append(
                f'{relative}:{op.call.lineno}: {ast.unparse(op.call)} names a '
                f'member of {op.key}, and cannot be shown to carry a '
                f'{_TIMEOUT}= bound')
            if op.stops():
                stops[op.key] = op.call.lineno
            continue
        if op.stops():
            stops[op.key] = op.call.lineno
        elif op.drains() and op.key in stops and not op.bounded:
            violations.append(
                f'{relative}:{op.call.lineno}: {ast.unparse(op.call)} drains '
                f'{op.key} with no {_TIMEOUT}= bound, after the stop at line '
                f'{stops[op.key]}')


def _scan(relative, source, keeps):
    """The scan over one module's text; `keeps` is the memo's replay."""
    del keeps
    tree = ast.parse(source, filename=relative)
    keys = _ObjectKeys(tree)
    scopes = sorted((scope for scope, calls in keys.calls.items() if calls),
                    key=lambda scope: keys.calls[scope][0].lineno)
    violations = []
    for scope in scopes:
        _scope_violations(keys.calls[scope], keys, scope, relative,
                          violations)
    return violations


def _analyze(relative, source, keeps=None):
    """The module's violations; the memo keys this on content, not path."""
    return analysed(_scan, relative, source,
                    [] if keeps is None else keeps)


def _python_sources(root):
    """Every tracked Python file of the release rooted at `root`."""
    return sorted((path for path in iter_tree_files(root)
                   if path.suffix == _PYTHON),
                  key=lambda path: path.as_posix())


def _tree_violations(root):
    violations = []
    for path in _python_sources(root):
        relative = path.relative_to(root).as_posix()
        violations.extend(
            _analyze(relative, path.read_text(encoding='utf-8')))
    return violations


# Every one of the 20 inline sites on this tree, one row each, as
# (module, the calls as they stand today, the same calls with the
# drain's `timeout=` removed). The anchor spans the whole drain call, so
# a wrapped one is replaced whole rather than leaving a continuation.
_SITES = (
    ('run_tests.py',
     'def _terminate_and_reap(process):\n'
     '    process.terminate()\n'
     '    try:\n'
     '        process.wait(timeout=10)',
     'def _terminate_and_reap(process):\n'
     '    process.terminate()\n'
     '    try:\n'
     '        process.wait()'
     ),
    ('run_tests.py',
     '    except subprocess.TimeoutExpired:\n'
     '        process.kill()\n'
     '        try:\n'
     '            process.wait(timeout=10)',
     '    except subprocess.TimeoutExpired:\n'
     '        process.kill()\n'
     '        try:\n'
     '            process.wait()'
     ),
    ('tests/_clientstate.py',
     '            killed = True\n'
     '            proc.kill()\n'
     '            try:\n'
     '                out, err = proc.comm'
     'unicate(timeout=killed_pipe_release)',
     '            killed = True\n'
     '            proc.kill()\n'
     '            try:\n'
     '                out, err = proc.communicate()'
     ),
    ('tests/_dashnode.py',
     '        child_cpu_at_timeout = _child_cpu_at_timeout(process)\n'
     '        process.kill()\n'
     '        cleanup_failure = None\n'
     '        cleanup_failed = False\n'
     '        cancelled = set()\n'
     '        drain_started = time.monotonic()\n'
     '        try:\n'
     '            stdout, stderr = process.communicate(\n'
     '                timeout=_DASHBOARD_DRAIN_TIMEOUT_S)',
     '        child_cpu_at_timeout = _child_cpu_at_timeout(process)\n'
     '        process.kill()\n'
     '        cleanup_failure = None\n'
     '        cleanup_failed = False\n'
     '        cancelled = set()\n'
     '        drain_started = time.monotonic()\n'
     '        try:\n'
     '            stdout, stderr = process.communicate()'
     ),
    ('tests/_drain.py',
     '    """\n'
     '    process.kill()\n'
     '    try:\n'
     '        out, err = process.communicate(timeout=drain_timeout)',
     '    """\n'
     '    process.kill()\n'
     '    try:\n'
     '        out, err = process.communicate()'
     ),
    ('tests/_realbrowser_workers.py',
     'def _retire_browser(process):\n'
     '    process.terminate()\n'
     '    try:\n'
     '        process.wait(timeout=10)',
     'def _retire_browser(process):\n'
     '    process.terminate()\n'
     '    try:\n'
     '        process.wait()'
     ),
    ('tests/_realbrowser_workers.py',
     '    except subprocess.TimeoutExpired:\n'
     '        process.kill()\n'
     '        process.wait(timeout=10)',
     '    except subprocess.TimeoutExpired:\n'
     '        process.kill()\n'
     '        process.wait()'
     ),
    ('tests/_speedharness.py',
     '    try:\n'
     '        process.kill()\n'
     '    except ProcessLookupError:\n'
     "        cleanup += '; fallback process was already gone'\n"
     '    except OSError as error:\n'
     "        cleanup += f'; fallback process kill failed: {error}'\n"
     '    else:\n'
     "        cleanup += '; fallback process kill requested'\n"
     '    try:\n'
     '        process.wait(timeout=_CLEANUP_TIMEOUT)',
     '    try:\n'
     '        process.kill()\n'
     '    except ProcessLookupError:\n'
     "        cleanup += '; fallback process was already gone'\n"
     '    except OSError as error:\n'
     "        cleanup += f'; fallback process kill failed: {error}'\n"
     '    else:\n'
     "        cleanup += '; fallback process kill requested'\n"
     '    try:\n'
     '        process.wait()'
     ),
    ('tests/_util.py',
     '        try:\n'
     '            proc.terminate()\n'
     '            try:\n'
     '                proc.wait(timeout=10)',
     '        try:\n'
     '            proc.terminate()\n'
     '            try:\n'
     '                proc.wait()'
     ),
    ('tests/_util.py',
     '            except subprocess.TimeoutExpired:\n'
     '                proc.kill()\n'
     '                proc.wait(timeout=10)',
     '            except subprocess.TimeoutExpired:\n'
     '                proc.kill()\n'
     '                proc.wait()'
     ),
    ('tests/test_bridge_startup.py',
     '    finally:\n'
     '        proc.terminate()\n'
     '        proc.wait(timeout=10)',
     '    finally:\n'
     '        proc.terminate()\n'
     '        proc.wait()'
     ),
    ('tests/test_mcp_entry_point.py',
     'def _cleanup_mcp(proc):\n'
     '    proc.terminate()\n'
     '    try:\n'
     '        proc.wait(timeout=10)',
     'def _cleanup_mcp(proc):\n'
     '    proc.terminate()\n'
     '    try:\n'
     '        proc.wait()'
     ),
    ('tests/test_mcp_entry_point.py',
     '    except subprocess.TimeoutExpired:\n'
     '        proc.kill()\n'
     '        proc.wait(timeout=10)',
     '    except subprocess.TimeoutExpired:\n'
     '        proc.kill()\n'
     '        proc.wait()'
     ),
    ('tests/test_parent_watch.py',
     '    except subprocess.TimeoutExpired as exc:\n'
     '        proc.kill()\n'
     '        output, _ = proc.communicate(timeout=WAIT_TIMEOUT)',
     '    except subprocess.TimeoutExpired as exc:\n'
     '        proc.kill()\n'
     '        output, _ = proc.communicate()'
     ),
    ('tests/test_parent_watch.py',
     '    finally:\n'
     '        proc.terminate()\n'
     '        proc.wait(timeout=10)',
     '    finally:\n'
     '        proc.terminate()\n'
     '        proc.wait()'
     ),
    ('tests/test_stream_lifecycle.py',
     '        assert status == 200 and hea'
     "lth['ok'] is True, (status, health)\n"
     '    finally:\n'
     '        proc.terminate()\n'
     '        proc.wait(timeout=10)',
     '        assert status == 200 and hea'
     "lth['ok'] is True, (status, health)\n"
     '    finally:\n'
     '        proc.terminate()\n'
     '        proc.wait()'
     ),
    ('tests/test_stream_lifecycle.py',
     "        release.write_text('go', encoding='utf-8')\n"
     '        proc.terminate()\n'
     '        proc.wait(timeout=10)',
     "        release.write_text('go', encoding='utf-8')\n"
     '        proc.terminate()\n'
     '        proc.wait()'
     ),
    ('tests/test_stream_lifecycle.py',
     "        assert 'nothing to do with the port' in failure, failure\n"
     '    finally:\n'
     '        proc.terminate()\n'
     '        proc.wait(timeout=10)',
     "        assert 'nothing to do with the port' in failure, failure\n"
     '    finally:\n'
     '        proc.terminate()\n'
     '        proc.wait()'
     ),
    ('tests/test_suite_runner.py',
     '                reaped = False\n'
     '                spawned[0].terminate()\n'
     '                try:\n'
     '                    spawned[0].wait(timeout=10)',
     '                reaped = False\n'
     '                spawned[0].terminate()\n'
     '                try:\n'
     '                    spawned[0].wait()'
     ),
    ('tests/test_suite_runner.py',
     '                except subprocess.TimeoutExpired:\n'
     '                    spawned[0].kill()\n'
     '                    spawned[0].wait(timeout=10)',
     '                except subprocess.TimeoutExpired:\n'
     '                    spawned[0].kill()\n'
     '                    spawned[0].wait()'
     ),
)


# A real unbounded drain with no stop before it, as (module, the drain):
# the control against a scan that reads a drain on its own.
_UNBOUNDED_WITHOUT_STOP = (
    ('scripts/ci/time_tests.py', 'child.wait()'),
    ('tests/_cdpharness.py', 'proc.communicate()'),
    ('tests/_relayharness.py', 'proc.communicate()'),
)


# The binding forms that carry an alias without an `=`: a walrus target, a
# `for` target and a `with ... as` target, planted into a REAL converted site
# as (form, module, the calls as they stand, the aliased form with the
# drain's bound removed). The stop is on the alias, the drain on the receiver.
#
# Each site is chosen so the planted drain has exactly ONE stop on that
# receiver before it -- the one being aliased. A site with an earlier stop
# would be caught for that earlier stop and the alias would never be tested.
_ALIASED = (
    ('walrus target', 'tests/test_bridge_startup.py',
     '        proc.terminate()\n        proc.wait(timeout=10)',
     '        if (p := proc):\n'
     '            p.terminate()\n'
     '        proc.wait()'),
    ('for target', 'tests/_util.py',
     '            proc.terminate()\n            try:\n'
     '                proc.wait(timeout=10)',
     '            for p in (proc,):\n'
     '                p.terminate()\n'
     '            try:\n'
     '                proc.wait()'),
    ('with target', 'tests/_realbrowser_workers.py',
     '    process.terminate()\n    try:\n'
     '        process.wait(timeout=10)',
     '    managed = process\n'
     '    with managed as p:\n'
     '        p.terminate()\n'
     '    try:\n'
     '        process.wait()'),
)


def _planted(root, site):
    """One converted site reverted in a scratch tree; where it now drains."""
    relative, bounded, unbounded = site
    target = root / relative
    source = target.read_text(encoding='utf-8')
    assert source.count(bounded) == 1, f'the {relative} site shape changed'
    mutated = source.replace(bounded, unbounded, 1)
    target.write_text(mutated, encoding='utf-8')
    drain = unbounded.rsplit('\n', 1)[-1]
    offset = mutated.index(drain)
    return f'{relative}:{mutated[:offset].count(chr(10)) + 1}:'


def _scratch_git_tree(root):
    """A throwaway tracked tree, so the scan reads Git's own enumeration."""
    copy_test_tree(root)
    # `copy_test_tree` mirrors the test package; the root entry point is a
    # converted site too, so the scratch tree carries it as well.
    (root / 'run_tests.py').write_bytes((ROOT / 'run_tests.py').read_bytes())
    for command in (['init', '-q'], ['add', '--', 'tests', 'run_tests.py']):
        subprocess.run(['git', '-C', str(root), *command],
                       capture_output=True, check=True, timeout=60)


def _synthetic(source):
    """The scan over one source string, for the shapes no site spells."""
    return _analyze('tests/synthetic.py', source)
