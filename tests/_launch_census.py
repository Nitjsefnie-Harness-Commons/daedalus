"""How a number ends a harness child, read from the tree the child is on.

The subject is the LAUNCH PATH: the shared gate's launcher modules, the
functions that reach them, and the module the child's `Popen` hands the
child to. All three are derived; only the launcher pair is named, and the
pair names the gate rather than the set of modules under audit.

## The boundary with `tests/_launch_audit.py`

`_launch_audit` owns the LAUNCH CALL across the tracked tree; its unit of
analysis is that call and its scope is a filter over launch calls. A bound
handed to a launch, a `**` mapping unpacked on one, a keyword the
`subprocess` does not take, and a launcher built by `functools.partial` are
all inside that unit, so they are its rows and not this module's. What is
left here is what a launch-call filter cannot reach at any width: a method
OF a launched object, and a number that ends the process rather than a
child.

  R3  the timeout slot of `Popen.wait` / `Popen.communicate`, by position
      or by keyword, on a receiver resolved to a launched child through
      local, module, `self.`, chained and caller-supplied bindings, reached
      through every import spelling a caller can use to get there
  R6  a process-level deadline: `signal.alarm`, `signal.setitimer`'s
      `seconds`, `os.waitfor`
  R7  a `timeout(1)` wrapper in the argv handed to a path function or a
      launch, head read through a name

A bound on any of those is refused unless BOTH hold:

  P1  its value is a Name bound to a module constant whose own definition
      computes from at least one other module constant that ITSELF computes,
      so the number is derived through a named chain rather than written at
      the call site or retyped as a literal one link down;
  P2  the enclosing function (or module) catches the expiry in a handler
      whose type IS an expiry error AND raises inside that same handler, so
      expiry names itself instead of ending the child silently.

P1 is a statement about source, not about a comment. #1117's separate
requirement that the measurement and the multiple sit BESIDE the constant
is a comment requirement, and no rule in this file can see a comment.

## The launch-position `**` blind spot is the AUDIT's

`**{'timeout': 30}` unpacked on a launch is a launch-call site.
`_launch_audit` classifies it `unpack` for every head, and
`tests/test_repo_layout.py`'s gate keeps that kind. This module does not
read the launch position of a `**`, and the sentence here is the
assignment rather than a gap: one control, one owner, and a control in
`tests/test_harness_launch_bounds.py` fails if the audit's row is removed.

## Not enforced, and each named in `tests/test_harness_launch_bounds.py`
## beside the control that would fail if it stopped being true

  - a deadline assembled with no argument to anything: a clock comparison
    plus a kill, or a `signal.alarm` reaching the child through a wrapper
    this walk cannot type;
  - a bound the callee never sees, which this input language cannot read
    at all: a deadline in the Node harness read from `process.env`, and a
    deadline inside a shell string (`Popen('timeout 30 node x',
    shell=True)`);
  - `os.waitid`, which blocks but carries no number, so there is nothing in
    it for a rule about numbers to read;
  - a bound handed to a class method reached through a constructor, which
    the walk resolves no body for;
  - an UNBOUNDED drain, which is the other control's subject rather than
    this one: this census refuses bounds, and `tests/_drain_scan.py` owns a
    drain with no bound after a stop, with `tests/_processtree.py` in its
    site table. The cleanup's fallback reap is that shape and is correct;
  - a child's `wait` or `communicate` on a receiver the walk CANNOT resolve
    to a launched child — an attribute or a subscript nothing here binds, or
    a name bound in a scope this walk does not read. `self.child.wait(30)`
    with no `self.child = Popen(...)` in the same file is therefore not
    refused. The narrowing is deliberate and is the one that keeps a socket
    or a thread's `wait` from reading as a child's, and the attribute case
    IS refused when the attribute is bound from a launch; what is left is
    the unresolved receiver, and it is named here rather than left to be
    found. A fail-closed reading of it — an unresolvable receiver is a
    fault — is the one change that would close this, and it would have to
    land with a measure of how often a non-child receiver appears on the
    path;
  - FOUR classes of site the analyser REFUSES and the repo-layout gate does
    not act on, so they are reported and not policed. The gate's keep rule
    (`tests/test_repo_layout.py::_bound_sites`) admits a site only when the
    head reads `git` or `ambiguous`, or when the kind is `unplaced`; every
    other classified site is dropped. Measured on the tracked tree, the four
    are a `timeout` at a `non-git` head, a `timeout` at an `unreadable` head,
    a `keyword` (an argument the `subprocess` does not take) at an
    `unreadable` head, and a `unpack` (a `**` mapping) at an `unreadable`
    head. The launch-call routes above are the last two of those four. The
    first two are the tree-wide Node form issue #1121 describes. Where the
    gate arrives for all four is issue #1170, filed by mechanism, because the
    keep rule is being redesigned under #1140 and #1155 and a widening here
    would be redone by that redesign;
  - a helper the launcher modules import from outside themselves —
    `tests/_processtree.py`, named because it is the one that ends the
    child — whose body is read only where it is on the path.
"""
import ast

import _launch_path as path

# The binding readers live in the path module — reading what a source binds
# a launcher to is the same work whichever question is asked of it — and the
# scope API is re-exported so a caller of the RULES needs one import.
_subprocess_receivers = path._subprocess_receivers
_from_import_launches = path._from_import_launches
_member_aliases = path._member_aliases
_is_launch = path._is_launch
_launch_bound_names = path._launch_bound_names
_dotted = path._dotted
_is_def = path._is_def
_is_call = path._is_call
_callee_name = path._callee_name
_function_bodies = path._function_bodies
_const_str = path._const_str
_module_constants = path._module_constants
_constant_computes = path._constant_computes
_LAUNCH_MEMBERS = path._LAUNCH_MEMBERS
_launch_members = path._launch_members
_child_wait_slots = path._child_wait_slots
# The positions as they stand, for a reader and for the control that
# recomputes them; the rule reads _child_wait_slots() at use.
_CHILD_WAIT_SLOTS = path._child_wait_slots()
LAUNCHER_MODULES = path.LAZY_MODULES
CHILD_ENDING_MODULES = path.CHILD_ENDING_MODULES
path_functions = path.path_functions
seed_functions = path.seed_functions
callable_path_names = path.callable_path_names
_CHILD_PARAMETERS = path._CHILD_PARAMETERS


# The calls that end the PROCESS rather than a child, and which argument
# each takes its deadline from. `signal.setitimer`'s FIRST argument is the
# timer; the bound is its `seconds`, the second. `os.waitid` is absent
# because it blocks but carries no number.
_PROCESS_DEADLINES = {
    ('signal', 'alarm'): 0,
    ('signal', 'setitimer'): 1,
    ('os', 'waitfor'): 1,
}
# A handler that catches one of these is handling an expiry. Matched on the
# final component, so `unrelated.TimeoutErrorLike` is not one of them.
_EXPIRY_NAMES = frozenset({'TimeoutExpired', 'TimeoutError',
                           'ChildDeadlineExceeded'})
# GNU `timeout(1)`'s own names, in argv position zero.
_ARGV_WRAPPERS = frozenset({'timeout', 'gtimeout'})

# The shared gate's launcher modules, and the module that ends the child.
# The first pair is the gate; the second is named because the callee marker
# cannot reach it through a launch, only through the child being handed to
# it, and pinning that the set has not emptied is what keeps that true.


def _bodies_in_scope(tree, in_path):
    """The scopes this audit reads: the module's statements and its path
    functions, each walked once.

    Module-level statements are always in the list, because that is where a
    module constant consumed by a bound lives — one of the routes that got
    past the first version, which walked function bodies only. The
    definitions are NOT in the statement list, so adding the path functions
    cannot read the same body twice.
    """
    scopes = [node for node in tree.body
              if not _is_def(node) and not isinstance(node, ast.ClassDef)]
    scopes += [body for name, body in _function_bodies(tree).items()
               if name in in_path]
    return scopes


def _constant_is_computed(name, constants):
    """P1, whole: the chain is composed, and one link of it is not typed.

    The bound's own definition must read another module constant, and that
    constant must itself be defined by something other than a written
    number. A chain of one (`round(30 * 10)`) is refused, and so is a chain
    whose only named link is a retyped literal (`SLOWEST = 30` beside
    `round(SLOWEST * 10)`), which is the same defect one link further from
    the call. A measured table of samples is a `Tuple`, not a `Constant`,
    so the figure a maintainer re-derives passes while a number typed beside
    it does not.
    """
    if not _constant_computes(name, constants):
        return False
    return any(
        not isinstance(constants[inner.id], ast.Constant)
        for inner in ast.walk(constants[name])
        if isinstance(inner, ast.Name) and inner.id in constants
        and inner.id != name)


def _handler_is_expiry(handler):
    """Whether a handler's type IS an expiry error, not merely contains one."""
    if handler.type is None:
        return False
    if isinstance(handler.type, ast.Name):
        return handler.type.id in _EXPIRY_NAMES
    if isinstance(handler.type, ast.Attribute):
        return handler.type.attr in _EXPIRY_NAMES
    return False


def _handles_expiry(scope):
    """P2: this scope catches an expiry AND raises inside that handler.

    The raise must be in the handler that MATCHED. A raise in a sibling
    handler beside an expiry handler that only cleans up satisfies
    "something is raised somewhere" and not "the expiry is reported", which
    is the whole of the difference between a detector and a silent kill.
    """
    for node in ast.walk(scope):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _handler_is_expiry(handler):
                continue
            if any(isinstance(inner, ast.Raise)
                   for inner in ast.walk(handler)):
                return True
    return False


def _permitted(bound, scope, constants):
    """Both permission conditions, or the reason one of them is missing."""
    if not isinstance(bound, ast.Name):
        return 'the bound is written at the call site, not named'
    if not _constant_is_computed(bound.id, constants):
        return (f'the constant {bound.id} is not computed from a named '
                'chain, so the figure behind it is written rather than '
                'composed')
    if not _handles_expiry(scope):
        return 'nothing catches the expiry and raises a named failure'
    return None


def _resolve(value, scopes, depth=5):
    """A local or module binding for `value` when it is a bare Name.

    `scopes` is a list because a constant bound at module level is
    resolvable from inside a function body, and the argv a launcher is
    handed is written in both places.
    """
    if not isinstance(value, ast.Name) or depth <= 0:
        return value
    if isinstance(scopes, ast.AST):
        scopes = [scopes]
    for scope in scopes:
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign) or node.value is None:
                continue
            if any(isinstance(target, ast.Name) and target.id == value.id
                   for target in node.targets):
                return _resolve(node.value, scopes, depth - 1)
    return value


def _timeout_slots(tree, in_path, receivers, direct, aliases=(),
                   parameters=()):
    """R3: every `child.wait(...)` / `child.communicate(...)` with a bound.

    Both spellings: the positional slot, which is the one that carries a
    bound with no keyword anywhere in it, and the `timeout=` keyword. The
    receiver is resolved through local, attribute, chained and
    caller-supplied bindings, so `self.child.wait(30)` and a child the
    caller hands this function both read.
    """
    slots = []
    for scope in _bodies_in_scope(tree, in_path):
        # '<launch>' is the receiver a wait called straight on the
        # launch itself has, so it is always a child.
        launched = _launch_bound_names(
            scope, receivers, direct, aliases) | {'<launch>'}
        launched |= set(parameters)
        for node in ast.walk(scope):
            if not _is_call(node):
                continue
            func = node.func
            if (_is_launch(node, receivers, direct, aliases)
                    or (isinstance(func, ast.Attribute)
                        and _is_call(func.value)
                        and _is_launch(func.value, receivers, direct,
                                       aliases))):
                receiver, method = '<launch>', getattr(func, 'attr', '')
            elif isinstance(func, ast.Attribute):
                receiver, method = _dotted(func.value), func.attr
            else:
                continue
            slots_read = _child_wait_slots()
            if method not in slots_read or receiver not in launched:
                continue
            index = slots_read[method]
            if len(node.args) > index:
                slots.append((node, node.args[index]))
            for keyword in node.keywords:
                if keyword.arg == 'timeout':
                    slots.append((node, keyword.value))
    return slots


def _is_argv_wrapper(call, callees, receivers, direct, scope, tree=None):
    """R7: `['timeout', '30', node, …]` — a bound wearing an argv.

    Read at a launch AND at a call to a path function, because in this
    repository the child is reached through `run_node_program`, not through
    a `subprocess` call, so a rule that reads argv only at a launch it can
    recognise never sees the shape the tree is written in. The argv, the
    head element and a `+` chain are resolved through names, so a module
    constant naming the wrapper is read too.
    """
    if not (_is_launch(call, receivers, direct)
            or _callee_name(call) in callees):
        return False
    # A module-level constant is resolvable from inside a function body, so
    # the scope resolved against is the body AND the module's statements.
    outer = [scope] + (list(tree.body) if tree is not None else [])
    argv = _resolve(call.args[0], outer) if call.args else None
    if argv is None:
        argv = next((_resolve(keyword.value, outer) for keyword
                     in call.keywords if keyword.arg == 'args'), None)
    if argv is None:
        return False
    while isinstance(argv, ast.BinOp) and isinstance(argv.op, ast.Add):
        argv = _resolve(argv.left, outer)
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return False
    head = _resolve(argv.elts[0], outer)
    if isinstance(head, ast.Subscript):
        head = _resolve(head.slice, outer)
    name = _const_str(head)
    return isinstance(name, str) and name in _ARGV_WRAPPERS


def _is_process_deadline(call):
    """R6: a number that ends the process rather than a child."""
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(
            func.value, ast.Name):
        return False
    return (func.value.id, func.attr) in _PROCESS_DEADLINES


def _deadline_faults(relative, call, scope, constants):
    """R6, with the same permission, so a detector is still allowed."""
    index = _PROCESS_DEADLINES[(call.func.value.id, call.func.attr)]
    bound = call.args[index] if len(call.args) > index else None
    if bound is None:
        return []
    reason = _permitted(bound, scope, constants)
    if not reason:
        return []
    return [(relative, call.lineno,
             f'{call.func.value.id}.{call.func.attr} ends the process',
             reason)]


def _functions_in(scope):
    """The function definitions a scope holds, so each is read once.

    A module's top-level statements carry no definitions, and a path
    function's own nested defs are read here rather than twice.
    """
    return [node for node in ast.walk(scope) if _is_def(node)]


def _enclosing_scope(tree, node):
    """The function a node sits in, or the module when it sits in none."""
    best = tree
    for candidate in ast.walk(tree):
        if not _is_def(candidate):
            continue
        end = candidate.end_lineno or candidate.lineno
        if candidate.lineno <= node.lineno <= end:
            if best is tree or candidate.lineno > best.lineno:
                best = candidate
    return best


def _parameter_bound_faults(relative, bound, scope, constants, route):
    """A bound that arrived as a parameter, judged where it was passed.

    The number is the caller's, so it is the CALLER's constants and scope
    that decide whether the chain is a recorded measurement, and the fault
    is reported against the caller's file and line.
    """
    faults = []
    for entry in _CHILD_PARAMETERS.get(relative, {}).get(bound.id, ()):
        other, line, value, caller_scope, caller_constants, _child = entry
        reason = _permitted(value, caller_scope, caller_constants)
        if reason:
            faults.append((other, line, route, reason))
    return faults


def _parameter_names(function):
    """The names a function's signature exposes, star-args excluded."""
    args = function.args
    names = set()
    for group in (args.posonlyargs, args.args, args.kwonlyargs):
        for arg in group:
            names.add(arg.arg)
    return names


def _is_deadline_name(name):
    """Whether a parameter NAME is the deadline concept.

    The concept read from a spelling rather than from a keyword, so the
    signature half of the scan covers a bound handed POSITIONALLY — which is
    how a caller passes a cleanup deadline, and which no `timeout=` reading
    can see.
    """
    return name == 'timeout' or name.endswith('_timeout')


def _positional_deadline_faults(relative, function, scope, constants,
                                handed=frozenset()):
    """A deadline handed to a path FUNCTION by position or by name.

    The counterpart to the keyword arm: the caller's number reaches the
    child's cleanup as an argument, so the call site is where the bound
    lives and the callee's signature is what says the slot is a deadline.
    Judged here, in the CALLER's scope, because the number is the caller's.
    """
    faults = []
    for node in ast.walk(function):
        if not _is_call(node):
            continue
        callee = _callee_name(node)
        if callee is None or callee in handed:
            continue
        target = _path_body(callee)
        if target is None:
            continue
        names = [argument.arg for argument in
                 list(target.args.posonlyargs) + list(target.args.args)]
        pairs = list(enumerate(node.args))
        pairs += [(names.index(keyword.arg), keyword.value)
                  for keyword in node.keywords if keyword.arg in names]
        for position, value in pairs:
            if position >= len(names) or not _is_deadline_name(
                    names[position]):
                continue
            if isinstance(value, ast.Name) and value.id in handed:
                faults.extend(_parameter_bound_faults(
                    relative, value, scope, constants,
                    'deadline handed to a path function'))
                continue
            reason = _permitted(value, scope, constants)
            if reason:
                faults.append((relative, node.lineno,
                               'deadline handed to a path function', reason))
    return faults


def _path_body(name):
    """A path function's body by bare name, or None."""
    return path.body_named(name)


def _timeout_faults(relative, function, scope, constants, handed=frozenset()):
    """Every place the deadline CONCEPT `timeout` appears in one function.

    Receiver-independent and signature-reading, which is the generality the
    route rules gave up: a `timeout=` on ANY call is read here, so
    `queue.get(timeout=5)` and `thread.join(timeout=2)` are refused on a
    launch-path function even though neither is a subprocess launch, and a
    `timeout` parameter on such a function is read from the signature.

    The concept enters a child four ways: a `timeout=` keyword on any call,
    a `timeout` parameter, a `'timeout'` key stored into a container, and a
    `'timeout'` key in a dict a `**` spread forwards. A bare `**opts` with
    no `timeout` anywhere is deliberately not a fault: a spread is not
    evidence of a bound.

    A `timeout` PARAMETER is refused outright, because a launcher that
    accepts one has an undeclared way to bound a child and the signature is
    the only place that shows. Every other hit goes through the same
    permission the route rules use, so the launcher's own detector — a
    derived, classified, failure-reporting deadline — is not a fault here
    either. A parameter the CALLER fills is left to the hand-off rule,
    which judges the number where it was passed, because judging it here
    would refuse the shipped cleanup's own bounded reap.
    """
    faults = []
    if (function is scope and 'timeout' in _parameter_names(function)
            and 'timeout' not in handed):
        faults.append((relative, function.lineno, 'timeout parameter',
                       'a path function takes a deadline parameter, so a '
                       'bound reaches the child through the signature'))
    for node in ast.walk(function):
        if isinstance(node, ast.Subscript) and not isinstance(
                node.ctx, ast.Load) and _const_str(node.slice) == 'timeout':
            faults.append((relative, node.lineno, "'timeout' key write",
                           "a 'timeout' key stored into a container"))
        elif (isinstance(node, ast.Dict)
              and any(_const_str(key) == 'timeout'
                      for key in node.keys)):
            faults.append((relative, node.lineno,
                           "'timeout' key in a dict",
                           "a '**' spread forwards a mapping holding a"
                           " 'timeout' key"))
    for node in ast.walk(function):
        if not isinstance(node, ast.keyword) or node.arg != 'timeout':
            continue
        if isinstance(node.value, ast.Name) and node.value.id in handed:
            faults.extend(_parameter_bound_faults(
                relative, node.value, scope, constants,
                'timeout= keyword at a caller-filled parameter'))
            continue
        reason = _permitted(node.value, scope, constants)
        if reason:
            faults.append((relative, node.lineno, 'timeout= keyword', reason))
    return faults


def _faults(relative, tree, in_path=frozenset(), callable_names=frozenset(),
            parameters=frozenset()):
    """Every bound on the launch path, located in this module's own file."""
    receivers = _subprocess_receivers(tree)
    direct = _from_import_launches(tree)
    aliases = _member_aliases(tree, receivers, direct)
    callees = set(in_path) | set(callable_names)
    constants = _module_constants(tree)
    faults = []
    handed = frozenset(_CHILD_PARAMETERS.get(relative, {}))
    for scope in _bodies_in_scope(tree, in_path):
        for function in _functions_in(scope):
            faults.extend(
                _timeout_faults(relative, function, scope, constants, handed))
            faults.extend(_positional_deadline_faults(
                relative, function, scope, constants, handed))
        for node in ast.walk(scope):
            if not _is_call(node):
                continue
            if _is_argv_wrapper(node, callees, receivers, direct,
                                scope, tree):
                faults.append((
                    relative, node.lineno,
                    'timeout(1) in the launch argv',
                    'a deadline in argv is a margin spelled differently'))
            if _is_process_deadline(node):
                faults.extend(
                    _deadline_faults(relative, node, scope, constants))
    for node, bound in _timeout_slots(
            tree, in_path, receivers, direct, aliases, parameters):
        scope = _enclosing_scope(tree, node)
        if (isinstance(bound, ast.Name)
                and bound.id in _CHILD_PARAMETERS.get(relative, {})):
            faults.extend(_parameter_bound_faults(
                relative, bound, scope, constants,
                'positional timeout on a launched child'))
            continue
        reason = _permitted(bound, scope, constants)
        if reason:
            faults.append((relative, node.lineno,
                           'positional timeout on a launched child', reason))
    return faults


def _parameters_for(relative):
    """The parameters this module's functions receive a CHILD through."""
    return {name for name, entries in _CHILD_PARAMETERS.get(
        relative, {}).items() if any(entry[5] for entry in entries)}


def tree(relative):
    """One module's parse, kept by the closure that produced the path."""
    return path.tree(relative)


def census(tests_dir, launcher_modules=path.LAZY_MODULES):
    """Every wall bound on the launch path, located in the file it is in."""
    paths = path.path_functions(tests_dir, launcher_modules)
    reachable = path.callable_path_names(tests_dir, launcher_modules, paths)
    faults = []
    for relative, in_path in paths.items():
        faults.extend(_faults(relative, path.tree(relative), in_path,
                              reachable.get(relative, frozenset()),
                              frozenset(path.parameters_for(relative))))
    return sorted(faults)
