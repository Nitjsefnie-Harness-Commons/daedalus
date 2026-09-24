#!/usr/bin/env python3
"""Every stop-then-drain of one process object in this tree is bounded.

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
`process.communicate()` drains.

Not recognised, each gap named because the guard claims it:

* The stop and the drain in DIFFERENT scopes. `tests/_speedharness.py`
  stops a process in `_kill_process_tree` and drains that same process in
  `_reap_process`, the two joined only by `_cleanup_process`; the calls
  share a parameter, not a scope, so this scan reads neither. The drain
  there carries `timeout=_CLEANUP_TIMEOUT` and
  `tests/test_speedharness.py::`
  `test_the_harness_bounds_cleanup_when_tree_kill_fails` pins the reap, so
  the shape is held by that test rather than by this scan.
* A drain in a helper CALLED WITH the process, and a name bound more than
  once in its scope or bound to something that is not a receiver. Both
  stand for themselves, so a stop and a drain that can only be connected
  through an argument list are invisible here.
* A computed member is refused rather than read, because an unread name is
  neither "no stop" nor "a bound": `getattr(proc, 'kill')()` and
  `proc['wait']()` are refused outright, and so is `proc[name]()` when the
  same scope already stops or drains `proc` plainly.
* A drain reached through `tests/_drain.py::kill_and_drain` is not a drain
  spelling and is out of the scan; that helper's own suite pins the default
  bound its name-only call sites inherit.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_memo import analysed  # noqa: E402
from _coverage_scopes import (  # noqa: E402
    _containing_binding_scope, _evaluation_scopes, _scope_shadows)
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT, iter_tree_files  # noqa: E402

# The two member sets are the whole spelling surface. A stop asks the
# process to end; a drain is the read that must end with it.
_STOPS = frozenset({'kill', 'terminate', 'send_signal'})
_DRAINS = frozenset({'communicate', 'wait'})
_STOP = 'stop'
_DRAIN = 'drain'
_TIMEOUT = 'timeout'
_GETATTR = 'getattr'
_PYTHON = '.py'


def _binding_of(node):
    """(bound names, value) when `node` plainly binds a name."""
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    else:
        return (), None
    names = [part.id for target in targets for part in ast.walk(target)
             if isinstance(part, ast.Name)
             and isinstance(part.ctx, ast.Store)]
    return names, value


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
            names, value = _binding_of(node)
            if isinstance(node, ast.NamedExpr):
                scope = _containing_binding_scope(scope, self.parents)
            for name in names:
                self.bindings[scope].setdefault(name, []).append(value)

    def key(self, node, scope):
        """One key naming the object a receiver denotes, or None."""
        if isinstance(node, ast.Name):
            return self._followed(node.id, scope)
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            return ast.unparse(node)
        return None

    def _followed(self, name, scope):
        """The receiver a chain of plain-name aliases ends at.

        A cycle has no end, so it names no object this guard can read.
        """
        seen = set()
        while name not in seen:
            seen.add(name)
            bound = self._bound(name, scope)
            if isinstance(bound, ast.Name):
                name = bound.id
            elif isinstance(bound, (ast.Attribute, ast.Subscript)):
                return ast.unparse(bound)
            else:
                return name
        return None

    def _bound(self, name, scope):
        """The one value `name` holds, or None when it is not provable.

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


def _scope_violations(calls, keys, scope, relative, violations):
    ordered = sorted(calls, key=lambda call: (call.lineno, call.col_offset))
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


# One real converted inline site each, as (module, the calls as they stand
# today, the same calls with the drain's `timeout=` removed).
_SITES = (
    ('run_tests.py',
     '        process.kill()\n        try:\n'
     '            process.wait(timeout=10)',
     '        process.kill()\n        try:\n'
     '            process.wait()'),
    ('tests/_clientstate.py',
     '            proc.kill()\n            try:\n'
     '                out, err = proc.communicate('
     'timeout=killed_pipe_release)',
     '            proc.kill()\n            try:\n'
     '                out, err = proc.communicate()'),
    ('tests/_dashnode.py',
     '        stdout, stderr = process.communicate(\n'
     '                timeout=_DASHBOARD_DRAIN_TIMEOUT_S)',
     '        stdout, stderr = process.communicate()'),
    ('tests/_realbrowser_workers.py',
     '        process.kill()\n        process.wait(timeout=10)',
     '        process.kill()\n        process.wait()'),
    ('tests/_util.py',
     '                proc.kill()\n'
     '                proc.wait(timeout=10)',
     '                proc.kill()\n                proc.wait()'),
    ('tests/test_bridge_startup.py',
     '        proc.terminate()\n        proc.wait(timeout=10)',
     '        proc.terminate()\n        proc.wait()'),
    ('tests/test_mcp_entry_point.py',
     '        proc.kill()\n        proc.wait(timeout=10)',
     '        proc.kill()\n        proc.wait()'),
    ('tests/test_parent_watch.py',
     '        proc.kill()\n'
     '        output, _ = proc.communicate(timeout=WAIT_TIMEOUT)',
     '        proc.kill()\n        output, _ = proc.communicate()'),
    ('tests/test_suite_runner.py',
     '                    spawned[0].kill()\n'
     '                    spawned[0].wait(timeout=10)',
     '                    spawned[0].kill()\n'
     '                    spawned[0].wait()'),
)

# A real unbounded drain with no stop before it, as (module, the drain):
# the control against a scan that reads a drain on its own.
_UNBOUNDED_WITHOUT_STOP = (
    ('scripts/ci/time_tests.py', 'child.wait()'),
    ('tests/_cdpharness.py', 'proc.communicate()'),
    ('tests/_relayharness.py', 'proc.communicate()'),
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


def test_every_stop_then_drain_carries_a_bound(tmp):
    del tmp
    violations = _tree_violations(ROOT)
    assert not violations, '\n'.join(violations)


def test_the_scan_reads_exactly_the_tracked_python_tree(tmp):
    """A tracked Python file Git does not list is one the scan never reads."""
    del tmp
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z', '*.py'],
        capture_output=True, check=True, timeout=60)
    tracked = {os.fsdecode(name) for name in listed.stdout.split(b'\0')
               if name}
    assert tracked, 'Git returned no tracked Python paths'
    scanned = {path.relative_to(ROOT).as_posix()
               for path in _python_sources(ROOT)}
    assert scanned == tracked, sorted(scanned ^ tracked)


def test_each_real_site_is_caught_when_its_bound_is_removed(tmp):
    for index, site in enumerate(_SITES):
        root = Path(tmp, f'site{index}')
        _scratch_git_tree(root)
        expected = _planted(root, site)
        violations = _tree_violations(root)
        assert any(entry.startswith(expected) for entry in violations), (
            site[0], expected, violations)


def test_a_scratch_tree_is_clean_before_a_bound_is_removed(tmp):
    """The same scratch tree the plants use, with nothing planted in it."""
    root = Path(tmp, 'pristine')
    _scratch_git_tree(root)
    assert not _tree_violations(root), _tree_violations(root)


def test_a_bounded_drain_is_clean(tmp):
    del tmp
    assert _synthetic("""def reap(proc):
    proc.kill()
    proc.wait(timeout=10)
""") == []


def test_the_bounded_helper_is_clean(tmp):
    """The helper every routed site calls kills, then drains under a bound."""
    del tmp
    relative = 'tests/_drain.py'
    source = (ROOT / relative).read_text(encoding='utf-8')
    assert 'process.kill()\n    try:\n        out, err = process.' \
        'communicate(timeout=drain_timeout)' in source, source
    assert _analyze(relative, source) == []


def test_an_alias_is_followed_to_its_receiver(tmp):
    del tmp
    violations = _synthetic("""def reap(process):
    p = process
    p.kill()
    p.communicate()
""")
    expected = ('tests/synthetic.py:4: p.communicate() drains process with '
                'no timeout= bound, after the stop at line 3')
    assert violations == [expected]


def test_an_alias_chain_is_read_to_a_fixpoint(tmp):
    del tmp
    violations = _synthetic("""def reap(proc):
    a = proc
    b = a
    q = b
    q.kill()
    q.wait()
""")
    expected = ('tests/synthetic.py:6: q.wait() drains proc with no '
                'timeout= bound, after the stop at line 5')
    assert violations == [expected]


def test_a_drain_of_another_object_is_clean(tmp):
    del tmp
    assert _synthetic("""def reap(proc):
    proc.kill()
    other.wait()
""") == []


def test_a_drain_before_the_stop_is_clean(tmp):
    del tmp
    assert _synthetic("""def reap(proc):
    proc.communicate()
    proc.kill()
""") == []


def test_a_kwargs_spread_is_not_a_bound(tmp):
    del tmp
    violations = _synthetic("""def reap(proc, options):
    proc.kill()
    proc.communicate(**options)
""")
    expected = ('tests/synthetic.py:3: proc.communicate(**options) drains '
                'proc with no timeout= bound, after the stop at line 2')
    assert violations == [expected]


def test_a_computed_stop_is_refused(tmp):
    del tmp
    violations = _synthetic("""def reap(proc):
    getattr(proc, 'kill')()
    proc.wait(timeout=10)
""")
    expected = ("tests/synthetic.py:2: getattr(proc, 'kill')() names a "
                'member of proc, and cannot be shown to carry a timeout= '
                'bound')
    assert violations == [expected]


def test_a_computed_drain_is_refused(tmp):
    del tmp
    violations = _synthetic("""def reap(proc):
    proc.kill()
    proc['wait']()
""")
    expected = ("tests/synthetic.py:3: proc['wait']() names a member of "
                'proc, and cannot be shown to carry a timeout= bound')
    assert violations == [expected]


def test_a_computed_member_of_a_judged_object_is_refused(tmp):
    del tmp
    violations = _synthetic("""def reap(proc, key):
    proc.kill()
    proc[key]()
""")
    expected = ('tests/synthetic.py:3: proc[key]() names a member of proc, '
                'and cannot be shown to carry a timeout= bound')
    assert violations == [expected]


def test_a_computed_member_of_an_unread_object_is_clean(tmp):
    del tmp
    assert _synthetic("""def reap(proc, table, key):
    proc.kill()
    table[key]()
""") == []


def test_a_bound_reached_through_a_parameter_name_is_clean(tmp):
    del tmp
    assert _synthetic("""def reap(proc, limit):
    proc.kill()
    proc.communicate(timeout=limit)
""") == []


def test_a_name_bound_twice_stands_for_itself(tmp):
    del tmp
    assert _synthetic("""def reap(proc, other):
    proc = other
    proc.kill()
    proc.wait(timeout=10)
""") == []


def test_a_cyclic_alias_is_unreadable(tmp):
    del tmp
    assert _synthetic("""def reap(proc):
    a = b
    b = a
    proc.kill()
    a.wait()
""") == []


def test_a_stop_in_another_scope_is_not_a_stop_here(tmp):
    """The `tests/_speedharness.py` shape: joined by a call, not a scope."""
    del tmp
    assert _synthetic("""def stop(process):
    process.kill()


def drain(process):
    process.wait()
""") == []


def test_the_real_cross_scope_shape_is_the_speedharness(tmp):
    """The declared cross-scope gap is a real site, named as declared."""
    del tmp
    relative = 'tests/_speedharness.py'
    source = (ROOT / relative).read_text(encoding='utf-8')
    tree = ast.parse(source)
    lines = source.splitlines()
    stops = drains = None
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        if function.name == '_kill_process_tree':
            stops = function
        elif function.name == '_reap_process':
            drains = function
    assert stops is not None and drains is not None
    assert any('process.kill()' in lines[node.lineno - 1]
               for node in ast.walk(stops)
               if isinstance(node, ast.Call)), lines
    assert any('process.wait(timeout=_CLEANUP_TIMEOUT)'
               in lines[node.lineno - 1]
               for node in ast.walk(drains)
               if isinstance(node, ast.Call)), lines
    assert _analyze(relative, source) == []


def test_a_non_kill_unbounded_drain_is_not_flagged(tmp):
    """The false-positive control: a real unbounded drain with no stop."""
    for relative, drain in _UNBOUNDED_WITHOUT_STOP:
        source = (ROOT / relative).read_text(encoding='utf-8')
        assert source.count(drain) >= 1, relative
        assert _analyze(relative, source) == [], relative


def test_the_false_positive_control_still_catches_a_stop(tmp):
    """A control only means something if a stop beside it is still caught."""
    for relative, drain in _UNBOUNDED_WITHOUT_STOP:
        source = (ROOT / relative).read_text(encoding='utf-8')
        receiver = drain[:drain.index('.')]
        lead = source[:source.index(drain)].rsplit('\n', 1)[-1]
        planted = source.replace(
            drain, f'{receiver}.kill()\n{lead}{drain}', 1)
        assert _analyze(relative, planted), relative


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
