"""Static argparse, builtin-identity, and constant-resolution helpers. DECLARED
covers stored action destinations and parser defaults; GUARANTEED adds required
and non-suppressed values. A required mutually exclusive group guarantees a
destination only when every member stores that same non-SUPPRESS destination.
Direct plain and annotated namespace stores are neither reads nor escapes;
stores never satisfy reads.
Semantic claims are ``DECIDED`` consists only of the resolver's explicitly
enumerated expression node types | every other ``ast.expr`` node type is
``OUTSIDE`` by definition, so future AST node types enter the fail-closed side
automatically and no third bucket exists | comparisons and tuple-literal keys
stay ``OUTSIDE`` because reproducing their Python semantics would widen the
trusted evaluator | builtin aliases are trusted only with exact builtin
identity at the specific call site | uncertain, rebound, closure-dependent,
or conditional bindings fail closed | captured local aliases require exact
identity at every proven direct invocation. Semantic claims end. An OUTSIDE key
that could select a frame route fails closed.
UAdd and USub sign integer operands. Invert complements integer operands. Not
converts any resolved literal to bool. All four recurse; bool values count as
integer indices and slice bounds use that definition.
Current named known-gap control families are non-exact descriptors, partial
callables, traceback frames, other containers and iterators, comprehension
results, instance attributes, attribute getters, runtime-built names,
mapping-proxy reads, call-produced indices, and external frame acquisition.
Each named family maps once; contract prose and control tables cover each
other."""
import argparse
import ast
import builtins
import inspect
import sys
_FRAME_ROUTE_OBJECTS = (sys._getframe, inspect.currentframe)
_OUTSIDE_EXPRESSION, _UNKNOWN_MODULE_BINDING = object(), object()
EXPRESSION_DECIDED, EXPRESSION_OUTSIDE = 'DECIDED', 'OUTSIDE'
DECIDED_EXPRESSION_TYPES = frozenset((
    ast.Attribute, ast.Call, ast.Constant, ast.Name, ast.Slice,
    ast.Subscript, ast.UnaryOp))


def _expression_node_types():
    return frozenset(value for value in vars(ast).values() if isinstance(
        value, type) and value is not ast.expr and issubclass(value, ast.expr))


def expression_type_disposition(node_type):
    if (not isinstance(node_type, type) or node_type is ast.expr
            or not issubclass(node_type, ast.expr)):
        raise TypeError('expected a concrete ast.expr node type')
    return (EXPRESSION_DECIDED if node_type in DECIDED_EXPRESSION_TYPES
            else EXPRESSION_OUTSIDE)


def is_outside_expression(value):
    return value is _OUTSIDE_EXPRESSION


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
        value = resolve_frame_value(
            node, function, handler_globals, bindings, unresolved,
            scope_binds, constant_string)
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
    value = resolve_frame_value(
        node, function, handler_globals, bindings, unresolved,
        scope_binds, constant_string)
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
        if isinstance(parent.ctx, ast.Store) and isinstance(
                parent._parent, (ast.Assign, ast.AnnAssign)):
            return parent.attr, None, False
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


def is_frame_route(value):
    return any(value is route for route in _FRAME_ROUTE_OBJECTS)


def _has_exact_type(value, *expected):
    # Exact identity is intentional: subclasses remain outside the resolver.
    return type(value) in expected  # pylint: disable=unidiomatic-typecheck


def _constant_value(node, unresolved):
    """Resolve constants, slices, and all four Python unary operators.
    ``UAdd`` and ``USub`` sign an integer, ``Invert`` complements an integer,
    and ``Not`` converts any resolved literal to ``bool``. Operators recurse;
    unsupported operands and nodes remain unresolved."""
    if (isinstance(node, ast.expr)
            and expression_type_disposition(type(node)) == EXPRESSION_OUTSIDE):
        return _OUTSIDE_EXPRESSION
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Slice):
        bounds = []
        for bound in (node.lower, node.upper, node.step):
            value = None if bound is None else _constant_value(
                bound, unresolved)
            if is_outside_expression(value):
                return value
            if (value is unresolved
                    or (value is not None
                        and not isinstance(value, int))):
                return unresolved
            bounds.append(value)
        return slice(*bounds)
    if isinstance(node, ast.UnaryOp):
        value = _constant_value(node.operand, unresolved)
        if value is unresolved or is_outside_expression(value):
            return value
        if isinstance(node.op, ast.Not):
            return not value
        if not isinstance(value, int):
            return unresolved
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.Invert):
            return ~int(value)
    return unresolved


def _static_attribute(base, attribute, unresolved):
    if is_outside_expression(base):
        return base
    if _has_exact_type(base, type(sys)):
        return (base.__dict__ if attribute == '__dict__' else
                base.__dict__.get(attribute, unresolved))
    if _has_exact_type(base, type):
        for owner in base.__mro__:
            namespace = owner.__dict__
            if attribute not in namespace:
                continue
            value = namespace[attribute]
            if _has_exact_type(value, staticmethod):
                return value.__func__
            if _has_exact_type(value, classmethod):
                return value.__func__
            return value
        return unresolved
    return unresolved


def _static_subscript(base, key, unresolved):
    if (_has_exact_type(base, list, tuple)
            and (isinstance(key, int) or _has_exact_type(key, slice))):
        try:
            return base[key]
        except (IndexError, TypeError, ValueError):
            return unresolved
    if _has_exact_type(base, dict):
        try:
            return base.get(key, unresolved)
        except TypeError:
            return unresolved
    return unresolved


def _contains_frame_route(container):
    if is_frame_route(container):
        return True
    if _has_exact_type(container, dict):
        container = container.values()
    elif not _has_exact_type(container, list, tuple):
        return False
    return any(_contains_frame_route(value) for value in container)


def _builtin_call(node, function, handler_globals, imports, unresolved,
                  scope_binds, string_resolver):
    if not isinstance(node, ast.Call):
        return unresolved
    if (isinstance(node.func, ast.Attribute)
            and node.func.attr == 'get'
            and len(node.args) in (1, 2)
            and not node.keywords):
        key = _constant_value(node.args[0], unresolved)
        base = resolve_frame_value(
            node.func.value, function, handler_globals, imports, unresolved,
            scope_binds, string_resolver)
        if is_outside_expression(base):
            return base
        if is_outside_expression(key):
            return (_OUTSIDE_EXPRESSION
                    if _contains_frame_route(base) else unresolved)
        if key is not unresolved and _has_exact_type(base, dict):
            if key in base:
                return base[key]
            if len(node.args) == 2:
                default = _constant_value(node.args[1], unresolved)
                return (default if default is not unresolved else
                        resolve_frame_value(
                            node.args[1], function, handler_globals, imports,
                            unresolved, scope_binds, string_resolver))
    if (not isinstance(node.func, ast.Name)
            or node.keywords
            or any(isinstance(arg, ast.Starred) for arg in node.args)):
        return unresolved
    name = node.func.id
    if (scope_binds(function, name) or name in handler_globals
            or not node.args):
        return unresolved
    base = resolve_frame_value(
        node.args[0], function, handler_globals, imports, unresolved,
        scope_binds, string_resolver)
    if name == 'getattr' and len(node.args) in (2, 3):
        attribute = string_resolver(node.args[1])
        if attribute is not None:
            return _static_attribute(base, attribute, unresolved)
    if name == 'vars' and len(node.args) == 1:
        if _has_exact_type(base, type(sys), type):
            return base.__dict__
    return unresolved


def resolve_frame_value(node, function, handler_globals, imports, unresolved,
                        scope_binds, string_resolver):
    if isinstance(node, ast.Name):
        if node.id in imports:
            return imports[node.id]
        if scope_binds(function, node.id):
            return unresolved
        return handler_globals.get(node.id, unresolved)
    if isinstance(node, ast.Attribute):
        base = resolve_frame_value(
            node.value, function, handler_globals, imports, unresolved,
            scope_binds, string_resolver)
        return _static_attribute(base, node.attr, unresolved)
    if isinstance(node, ast.Subscript):
        key = _constant_value(node.slice, unresolved)
        base = resolve_frame_value(
            node.value, function, handler_globals, imports, unresolved,
            scope_binds, string_resolver)
        if is_outside_expression(base):
            return base
        if is_outside_expression(key):
            return (_OUTSIDE_EXPRESSION
                    if _contains_frame_route(base) else unresolved)
        if key is not unresolved:
            return _static_subscript(base, key, unresolved)
    if isinstance(node, ast.Call):
        return _builtin_call(
            node, function, handler_globals, imports, unresolved,
            scope_binds, string_resolver)
    return unresolved


def assert_exact_class_vars(frame_value):
    class FrameRoutes:
        active = sys._getframe

    function = ast.parse(
        "def do_tabs(args):\n"
        "    return vars(FrameRoutes)\n").body[0]
    value = frame_value(
        function.body[0].value, function, {'FrameRoutes': FrameRoutes}, {})
    assert isinstance(value, type(FrameRoutes.__dict__))
    assert value['active'] is sys._getframe


def assert_total_expression_partition():
    universe = _expression_node_types()
    classified = {
        node_type: expression_type_disposition(node_type)
        for node_type in universe}
    decided = {
        node_type for node_type, disposition in classified.items()
        if disposition == EXPRESSION_DECIDED}
    outside = set(universe) - decided
    assert DECIDED_EXPRESSION_TYPES <= universe
    assert decided == set(DECIDED_EXPRESSION_TYPES)
    assert decided.isdisjoint(outside)
    assert decided | outside == set(universe)
    assert set(classified.values()) == {EXPRESSION_DECIDED, EXPRESSION_OUTSIDE}
    assert {ast.Compare, ast.Tuple} <= outside


def _documented_semantic_claims(document):
    normalized = ' '.join(document.split())
    claims = set()
    prefix, suffix = 'Semantic claims are ', '. Semantic claims end.'
    while prefix in normalized:
        _, _, remainder = normalized.partition(prefix)
        block, marker, normalized = remainder.partition(suffix)
        if not marker:
            return frozenset()
        claims.update(block.split(' | '))
    return frozenset(claims)


def _documented_known_gap_families(document):
    normalized = ' '.join(document.split())
    prefix = 'Current named known-gap control families are '
    suffix = '. Each named family'
    _, marker, remainder = normalized.partition(prefix)
    if not marker:
        return frozenset()
    families, marker, _ = remainder.partition(suffix)
    if not marker:
        return frozenset()
    return frozenset(families.replace(', and ', ', ').split(', '))


def _document_drift(documents, controlled, extractor):
    controlled = set(controlled)
    drift = {}
    for module, document in documents.items():
        documented = extractor(document)
        unsupported = sorted(documented - controlled)
        undocumented = sorted(controlled - documented)
        if unsupported or undocumented:
            drift[module] = {
                'unsupported': unsupported, 'undocumented': undocumented}
    return drift


def assert_docstrings_match(documents, rule_phrases, semantic_claims,
                            known_gap_families, known_gap_cases):
    required_documents = {'test_cli_arg_audit', '_cli_arg_audit_support',
                          '_cli_arg_audit_resolver'}
    document_drift = {
        'missing': sorted(required_documents - documents.keys()),
        'unknown': sorted(documents.keys() - required_documents)}
    assert not any(document_drift.values()), document_drift
    missing = {
        module: [phrase for phrase in rule_phrases
                 if phrase not in ' '.join(document.split())]
        for module, document in documents.items()}
    missing = {module: claims for module, claims in missing.items() if claims}
    assert missing == {}, missing
    semantic_drift = _document_drift(
        documents, semantic_claims, _documented_semantic_claims)
    assert semantic_drift == {}, semantic_drift
    control_families = {family for family, _ in known_gap_families}
    route_drift = _document_drift(
        documents, control_families, _documented_known_gap_families)
    assert route_drift == {}, route_drift
    controls = {case[0] for case in known_gap_cases}
    mapped = [
        control for _, family_controls in known_gap_families
        for control in family_controls]
    duplicates = sorted({
        control for control in mapped if mapped.count(control) > 1})
    assert duplicates == [], duplicates
    assert set(mapped) == controls, {
        'unmapped': sorted(controls - set(mapped)),
        'unknown': sorted(set(mapped) - controls)}
