"""Static argparse, builtin-identity, and origin helpers. DECLARED covers
stored action destinations and parser defaults; GUARANTEED adds required and
non-suppressed values. A required mutually exclusive group guarantees a
destination only when every member stores that same non-SUPPRESS destination.
Namespace stores are refused as namespace store escapes.
An origin the audit can see is a name a scope it reads binds, or an attribute
of such a value. Every other expression is unproven, which is a refusal and
not a silent pass.
FRAME_SURFACE is the member descriptors types.FrameType carries, read once
from that type, so a frame attribute this file has never heard of is refused
like a known one. A read of one of those members, or of a frame mapping under
the namespace key the caller passes, is refused unless the audit can see the
receiver's origin and that origin is not a frame. The key is a parameter
rather than a constant, so the frame rule reads the same name the namespace
rule derives from a handler's own AST."""
import argparse
import ast
import builtins
import sys
import types

# The object model, not an author's list: every descriptor a live frame
# carries, read from the interpreter's own frame type.
_FRAME_DESCRIPTORS = (types.GetSetDescriptorType, types.MemberDescriptorType)
FRAME_SURFACE = frozenset(
    name for name, member in vars(types.FrameType).items()
    if isinstance(member, _FRAME_DESCRIPTORS))
UNPROVEN = object()  # the verdict for a value whose origin is untraceable
_UNKNOWN_MODULE_BINDING = object()


def namespace_dests(parser):
    never_store = (argparse._HelpAction, argparse._VersionAction)
    actions = [action for action in parser._actions
               if action.dest != argparse.SUPPRESS
               and not isinstance(action, never_store)]
    defaults = set(parser._defaults)
    declared = {action.dest for action in actions} | defaults
    guaranteed = {action.dest for action in actions
                  if (action.default is not argparse.SUPPRESS
                      or action.required
                      or (not action.option_strings
                          and action.nargs == argparse.REMAINDER))}
    required_group_dests = set()
    for group in parser._mutually_exclusive_groups:
        destinations = {action.dest for action in group._group_actions}
        if (group.required
                and len(destinations) == 1
                and argparse.SUPPRESS not in destinations
                and not any(isinstance(action, never_store)
                            for action in group._group_actions)):
            required_group_dests |= destinations
    return declared, guaranteed | required_group_dests | defaults


def constant_string(node):
    return (node.value if isinstance(node, ast.Constant)
            and isinstance(node.value, str) else None)


def _constant_mapping_read(node):
    parent = node._parent
    if (isinstance(parent, ast.Subscript) and parent.value is node
            and isinstance(parent.ctx, ast.Load)):
        attribute = constant_string(parent.slice)
        if attribute is not None:
            return attribute, parent, True
    if (not isinstance(parent, ast.Attribute) or parent.value is not node
            or parent.attr != 'get'):
        return None
    call = parent._parent
    if (not isinstance(call, ast.Call) or call.func is not parent
            or len(call.args) not in (1, 2) or call.keywords
            or any(isinstance(argument, ast.Starred)
                   for argument in call.args)):
        return None
    attribute = constant_string(call.args[0])
    if attribute is not None:
        return attribute, call, False
    return None


def _builtin_name_is_shadowed(node, name, function, scope_binds,
                              comprehension_shadows, ignore_root=False):
    current = node
    generator, before_target = None, False
    callables = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp,
                      ast.GeneratorExp)
    while current is not function:
        parent = current._parent
        if isinstance(parent, ast.comprehension):
            generator, before_target = parent, current is parent.iter
        if (isinstance(parent, comprehensions)
                and comprehension_shadows(
                    parent, name, generator, before_target)):
            return True
        if isinstance(parent, comprehensions):
            generator = None
        if (isinstance(parent, callables)
                and (current is parent.body
                     if isinstance(parent, ast.Lambda)
                     else current in parent.body)
                and not (ignore_root and parent is function)
                and scope_binds(parent, name)):
            return True
        current = parent
    return False


def _current_module_expression(node):
    return (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == 'modules'
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == 'sys'
            and isinstance(node.slice, ast.Name)
            and node.slice.id == '__name__')


def _module_binding_write(node):
    if (isinstance(node, ast.Call)
            and getattr(node.func, 'id', None) == 'setattr'
            and len(node.args) == 3
            and not node.keywords
            and _current_module_expression(node.args[0])):
        attribute = constant_string(node.args[1])
        return attribute if attribute is not None else _UNKNOWN_MODULE_BINDING
    if (not isinstance(node, (ast.Attribute, ast.Subscript))
            or not isinstance(node.ctx, (ast.Store, ast.Del))):
        return None
    if isinstance(node, ast.Attribute) \
            and _current_module_expression(node.value):
        return node.attr
    if (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == '__dict__'
            and _current_module_expression(node.value.value)):
        attribute = constant_string(node.slice)
        return attribute if attribute is not None else _UNKNOWN_MODULE_BINDING
    return None


def _statement_binding_writes(statement):
    names, module_names, builtin_module_attributes = set(), set(), set()
    stack = [statement]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {alias.asname or alias.name.split('.')[0]
                      for alias in node.names}
            continue
        if isinstance(node, ast.ExceptHandler) and node.name is not None:
            names.add(node.name)
        module_binding = _module_binding_write(node)
        if module_binding is not None:
            module_names.add(module_binding)
        if (isinstance(node, ast.Attribute)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and isinstance(node.value, ast.Name)):
            builtin_module_attributes.add((node.value.id, node.attr))
        if isinstance(node, ast.Name) \
                and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        stack.extend(ast.iter_child_nodes(node))
    return names, module_names, builtin_module_attributes


def _update_builtin_bindings(statement, bindings, handler_globals,
                             unresolved, function, scope_binds):
    if isinstance(statement, ast.Import):
        for alias in statement.names:
            name = alias.asname or alias.name.split('.')[0]
            bindings[name] = (
                builtins if alias.name == 'builtins' else unresolved)
        return
    if isinstance(statement, ast.ImportFrom):
        for alias in statement.names:
            name = alias.asname or alias.name
            bindings[name] = (
                builtins.__dict__.get(alias.name, unresolved)
                if statement.module == 'builtins' else unresolved)
        return
    names, module_names, builtin_module_attributes = \
        _statement_binding_writes(statement)
    for name in names:
        bindings[name] = unresolved
    for name in module_names:
        if name is _UNKNOWN_MODULE_BINDING:
            bindings[name] = unresolved
        elif name not in bindings or not scope_binds(function, name):
            bindings[name] = unresolved
    for name, attribute in builtin_module_attributes:
        value = bindings.get(name, handler_globals.get(name, unresolved))
        if value is builtins:
            bindings[(name, attribute)] = unresolved


def _statement_prefixes(node, function):
    """Yield enclosing statement prefixes from inner to outer scope."""
    prefixes = []
    current = node
    while current is not function:
        parent = current._parent
        for _, children in ast.iter_fields(parent):
            if (isinstance(children, list)
                    and current in children
                    and all(isinstance(child, ast.stmt)
                            for child in children)):
                prefixes.append(children[:children.index(current)])
                break
        current = parent
    return reversed(prefixes)


def _builtin_bindings_at(node, function, handler_globals, unresolved,
                         scope_binds):
    bindings = {}
    for prefix in _statement_prefixes(node, function):
        for statement in prefix:
            _update_builtin_bindings(
                statement, bindings, handler_globals, unresolved, function,
                scope_binds)
    return bindings


def _execution_callable(node, function):
    current = node
    callables = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    while current is not function:
        parent = current._parent
        if (isinstance(parent, callables)
                and (current is parent.body
                     if isinstance(parent, ast.Lambda)
                     else current in parent.body)):
            return parent
        current = parent
    return function


def _direct_invocations(captured, function):
    """Return every proven direct call of an unrebound nested function."""
    if not isinstance(captured, ast.FunctionDef):
        return ()
    references = tuple(node for node in ast.walk(function)
                       if isinstance(node, ast.Name)
                       and isinstance(node.ctx, ast.Load)
                       and node.id == captured.name
                       and node.lineno > captured.end_lineno
                       and _execution_callable(node, function) is function)
    if (not references
            or any(not isinstance(node._parent, ast.Call)
                   or node._parent.func is not node
                   for node in references)):
        return ()
    rebound = any(isinstance(node, ast.Name)
                  and isinstance(node.ctx, (ast.Store, ast.Del))
                  and node.id == captured.name
                  and node.lineno > captured.end_lineno
                  for node in ast.walk(function))
    if rebound:
        return ()
    return tuple(node._parent for node in references)


def _captured_identity_is_exact(node, reference_name, expected, function,
                                handler_globals, unresolved, scope_binds):
    if not scope_binds(function, reference_name):
        return True
    captured = _execution_callable(node, function)
    if captured is function:
        return True
    invocations = _direct_invocations(captured, function)
    if not invocations:
        return False
    return all(
        _builtin_bindings_at(
            call, function, handler_globals, unresolved, scope_binds
        ).get(reference_name, unresolved) is expected
        for call in invocations)


def _has_exact_type(value, *expected):
    # Exact identity is intentional: subclasses remain outside the resolver.
    return type(value) in expected  # pylint: disable=unidiomatic-typecheck


def _static_attribute(base, attribute, unresolved):
    if not _has_exact_type(base, type(sys)):
        return unresolved
    return (base.__dict__ if attribute == '__dict__' else
            base.__dict__.get(attribute, unresolved))


def resolve_origin(node, function, handler_globals, unresolved, scope_binds,
                   bindings=None):
    """Return the value the audit can see a name or attribute names.

    A name resolves through ``bindings`` when the audit has tracked one for
    it, then through the scope it reads; an attribute resolves through a base
    it has already resolved. Every other expression — a call, a subscript, a
    comprehension — is unproven, because its value is produced by running code
    the audit does not run.
    """
    if isinstance(node, ast.Name):
        if bindings is not None and node.id in bindings:
            return bindings[node.id]
        if scope_binds(function, node.id):
            return unresolved
        return handler_globals.get(node.id, unresolved)
    if isinstance(node, ast.Attribute):
        return _static_attribute(
            resolve_origin(
                node.value, function, handler_globals, unresolved,
                scope_binds, bindings),
            node.attr, unresolved)
    return unresolved


def is_builtin_reference(node, name, function, handler_globals,
                         scope_binds, comprehension_shadows):
    expected = getattr(builtins, name)
    unresolved = object()
    bindings = _builtin_bindings_at(
        node, function, handler_globals, unresolved, scope_binds)
    reference_name = (
        node.id if isinstance(node, ast.Name) else
        node.value.id if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) else None)
    proven = bindings.get(reference_name)
    if (reference_name is not None
            and bindings.get(_UNKNOWN_MODULE_BINDING) is unresolved
            and not scope_binds(function, reference_name)
            and proven is not expected and proven is not builtins):
        return False
    if isinstance(node, ast.Name):
        exact_local = bindings.get(reference_name) is expected
        if _builtin_name_is_shadowed(
                node, reference_name, function, scope_binds,
                comprehension_shadows, exact_local):
            return False
        if not _captured_identity_is_exact(
                node, reference_name, expected, function,
                handler_globals, unresolved, scope_binds):
            return False
        value = resolve_origin(
            node, function, handler_globals, unresolved, scope_binds,
            bindings)
        if reference_name in bindings or value is not unresolved:
            return value is expected
        if reference_name != name:
            return False
        namespace = handler_globals.get('__builtins__', builtins)
        namespace = (namespace if isinstance(namespace, dict) else
                     namespace.__dict__
                     if type(namespace) is type(builtins) else {})
        return namespace.get(name) is expected
    if (not isinstance(node, ast.Attribute)
            or node.attr != name
            or not isinstance(node.value, ast.Name)):
        return False
    module_name = reference_name
    if bindings.get((module_name, node.attr)) is unresolved:
        return False
    exact_local = bindings.get(module_name) is builtins
    if _builtin_name_is_shadowed(
            node, module_name, function, scope_binds,
            comprehension_shadows, exact_local):
        return False
    if not _captured_identity_is_exact(
            node, module_name, builtins, function,
            handler_globals, unresolved, scope_binds):
        return False
    value = resolve_origin(
        node, function, handler_globals, unresolved, scope_binds, bindings)
    return value is expected


def reflective_builtin_call(node, function, handler_globals, scope_binds,
                            comprehension_shadows):
    if not isinstance(node, ast.Call):
        return False
    names = ('locals', 'globals', 'eval', 'exec')
    if (isinstance(node.func, ast.Name)
            and (node.func.id in names
                 or (node.func.id == 'vars' and not node.args))):
        return True
    if not node.args:
        names += ('vars',)
    return any(is_builtin_reference(
        node.func, name, function, handler_globals,
        scope_binds, comprehension_shadows)
        for name in names)


def permitted_namespace_read(name, function, handler_globals, scope_binds,
                             comprehension_shadows):
    parent = name._parent
    if isinstance(parent, ast.Attribute) and parent.value is name:
        if not isinstance(parent.ctx, ast.Load):
            return None
        if parent.attr != '__dict__':
            return parent.attr, parent, True
        return _constant_mapping_read(parent)
    if not (isinstance(parent, ast.Call)
            and parent.args and parent.args[0] is name
            and not parent.keywords):
        return None
    if (len(parent.args) == 1
            and is_builtin_reference(
                parent.func, 'vars', function, handler_globals,
                scope_binds, comprehension_shadows)):
        return _constant_mapping_read(parent)
    builtin_name = next((candidate for candidate in ('getattr', 'hasattr')
                         if is_builtin_reference(
                             parent.func, candidate, function, handler_globals,
                             scope_binds, comprehension_shadows)), None)
    arities = {'getattr': (2, 3), 'hasattr': (2,)}.get(builtin_name)
    if (not arities or len(parent.args) not in arities
            or any(isinstance(argument, ast.Starred)
                   for argument in parent.args)):
        return None
    attribute = constant_string(parent.args[1])
    if attribute is None:
        return None
    needs_presence = builtin_name == 'getattr' and len(parent.args) == 2
    return attribute, parent, needs_presence


def frame_read(node, namespace_key):
    """Return the receiver of a frame read, or ``None``.

    The member a node names and the receiver it selects from are decided in one
    place, because a member this file has never heard of has to be refused like
    a known one and the answer must not be spread over three arms. The three
    carriers are the shapes the grammar gives a member name: an attribute, a
    constant-string subscript, and a call's constant-string selection argument.
    Only the subscript reaches the audited namespace by its own key, so a
    call naming a path rather than a member reads nothing, while a member
    named through a callee the audit cannot prove is still refused, because
    the call arm asks only what member the argument names.
    """
    if isinstance(node, ast.Attribute):
        member, receiver = node.attr, node.value
        names_member = member in FRAME_SURFACE
    elif isinstance(node, ast.Subscript):
        member, receiver = constant_string(node.slice), node.value
        names_member = member in FRAME_SURFACE or member == namespace_key
    elif (isinstance(node, ast.Call) and len(node.args) in (2, 3)
          and not node.keywords):
        member, receiver = constant_string(node.args[1]), node.args[0]
        names_member = member in FRAME_SURFACE
    else:
        return None
    return receiver if names_member else None


def reads_frame_namespace(receiver, origin):
    """Refuse a frame read whose receiver the audit cannot account for.

    ``origin`` is what the audit can see the receiver to be, or ``UNPROVEN``
    when it cannot. A receiver it has resolved to a live frame is refused on
    the frame's own account, which is the one case a name the audit CAN see
    still has to refuse.
    """
    if origin is not UNPROVEN and not isinstance(origin, types.FrameType):
        return None
    return receiver


def assert_exact_module_vars():
    """A module attribute is the one attribute the audit can see through.

    Drives ``resolve_origin`` on a real attribute of a real module, so a
    resolver that stopped reading modules would fail here rather than quietly
    widening every module attribute to an unproven origin.
    """
    function = ast.parse(
        "def do_tabs(args):\n"
        "    return sys._getframe\n").body[0]
    unresolved = object()
    value = resolve_origin(
        function.body[0].value, function, {'sys': sys}, unresolved,
        lambda _function, _name: False)
    assert value is sys._getframe
    assert resolve_origin(
        ast.parse('text.upper', mode='eval').body, function,
        {'text': 'not a module'}, unresolved,
        lambda _function, _name: False) is unresolved
