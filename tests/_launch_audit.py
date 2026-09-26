"""The launch-refusal analyser behind the repo-layout launch policy.

Given one Python source, names every refusal limb its subprocess
launches trip, so a caller can assert the launch carries no wall-clock
bound, fails loudly, and is readable argv. The wall-clock-bound refusal
names the argv head (git / non-git / unreadable) so a tree-wide caller
can keep the rule to git launches.

The wall-clock policy has two arms and both are decidable. A launch the
analyser can place, with a head that reads as the constant `git`, is
refused. Any OTHER call carrying a `timeout=` or a `**`-unpacked mapping
is reported as an `unplaced` site at an unreadable head unless its
receiver is PROVED a fixed, non-launch value — a bare name this module
binds and the analyser read, and nothing else. A receiver the analyser
cannot prove is never passed over, so a bounded git launch cannot reach
the tree however the module was obtained or reached, without a refusal or
an allowance row.

Only the argv and head reading lives in `_argv_read.py`. It binds no
configuration of its own.
"""
import ast

from _argv_read import ArgvReader

CLONE_SILENCING_CONFIG = ('init.defaultBranch=main',
                          'advice.detachedHead=false')


def _parameters(node):
    """The parameter names of a function, lambda or comprehension.

    A parameter is a binding in its own scope whatever shares its
    spelling, and the receiver resolver needs to know which names those
    are. A comprehension has no `args`, so its loop names are the
    parameters it binds.
    """
    args = getattr(node, 'args', None)
    if args is None:
        return {getattr(target, 'id', None) for generator in node.generators
                for target in ast.walk(generator.target)} - {None}
    names = {arg.arg for arg in
             list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)}
    # Load-bearing, and the vararg arm is the discriminator: a `*os` or
    # `**os` parameter binds its name in this scope like any other, and
    # a bare-name receiver is exactly what proved_fixed accepts. Drop it
    # and `def f(*os): ... os(timeout=1)` reads as proved. The four
    # `*receiver-shadowing-a-*` rows in tests/_bound_site_rows.py pin
    # it; the positional spellings keep their row either way.
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            names.add(extra.arg)
    return names


def launch_refusals(source, here, bound_sink=None):
    """Every refusal limb one Python source's launches trip, naming its
    limb."""
    import builtins
    tree = ast.parse(source)
    safe_names = set(dir(builtins))
    partial_aliases = {'functools.partial', 'partial'}
    import_module_aliases = {'importlib.import_module', 'import_module',
                             '__import__', 'builtins.__import__'}
    machinery = {'functools': {'partial': partial_aliases},
                 'importlib': {'import_module': import_module_aliases}}
    module_aliases = {}

    def normalize(called):
        """Map a machinery alias's member to its canonical spelling."""
        if not called or '.' not in called:
            return called
        base, member = called.split('.', 1)
        canonical = module_aliases.get(base)
        if canonical:
            return f'{canonical}.{member}'
        return called

    def callee_of(call):
        """The call's callee as a spellable name, or None."""
        if isinstance(call.func, ast.Attribute) \
                and isinstance(call.func.value, ast.Name):
            return f'{call.func.value.id}.{call.func.attr}'
        if isinstance(call.func, ast.Name):
            return call.func.id
        return None

    def derives(value, bound):
        """Does this expression yield the module or one of its members?"""
        if isinstance(value, ast.Name):
            return (value.id == 'subprocess' or value.id in bound
                    or value.id in module_factories)
        if isinstance(value, ast.Attribute):
            return derives(value.value, bound)
        if isinstance(value, ast.NamedExpr):
            return derives(value.value, bound)
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            return any(derives(elt, bound) for elt in value.elts)
        if isinstance(value, ast.Dict):
            return any(derives(item, bound)
                       for item in value.values if item is not None)
        if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return (derives(value.elt, bound)
                    or any(derives(g.iter, bound)
                           for g in value.generators))
        if isinstance(value, ast.DictComp):
            return (derives(value.key, bound)
                    or derives(value.value, bound)
                    or any(derives(g.iter, bound)
                           for g in value.generators))
        if isinstance(value, ast.IfExp):
            return derives(value.body, bound) or derives(value.orelse, bound)
        if isinstance(value, ast.Lambda):
            return derives(value.body, bound)
        if isinstance(value, ast.Subscript):
            base = value.value
            if isinstance(base, ast.Attribute) \
                    and isinstance(base.value, ast.Name) \
                    and base.value.id == 'sys' and base.attr == 'modules':
                return True
            if reader.resolve_string(value.slice) == 'subprocess':
                return True
            return derives(base, bound)
        if isinstance(value, ast.Call):
            called = normalize(callee_of(value))
            if called in partial_aliases and any(
                    derives(arg, bound) for arg in value.args):
                return True
            if called in import_module_aliases and any(
                    reader.resolve_string(argument) == 'subprocess'
                    for argument in list(value.args) + [
                        keyword.value for keyword in value.keywords
                        if keyword.arg == 'name']):
                return True
            if called == 'getattr' and any(
                    derives(arg, bound) for arg in value.args):
                return True
            if isinstance(value.func, ast.Attribute):
                if isinstance(value.func.value, ast.Name) \
                        and value.func.value.id == 'subprocess':
                    return False
                return derives(value.func.value, bound)
            if isinstance(value.func, ast.Name):
                if (value.func.id in bound
                        or value.func.id in module_factories):
                    return True
                if value.func.id in defined_names:
                    return False
                return any(derives(arg, bound) for arg in value.args)
            return any(derives(arg, bound) for arg in value.args)
        return False

    def resolves_safe(expr):
        """Is this receiver provably free of subprocess-derived values?"""
        if isinstance(expr, ast.Attribute):
            return resolves_safe(expr.value)
        if isinstance(expr, ast.Subscript):
            base = expr.value
            if isinstance(base, ast.Attribute) \
                    and isinstance(base.value, ast.Name) \
                    and base.value.id == 'sys' \
                    and base.attr == 'modules':
                return False
            return resolves_safe(base)
        if isinstance(expr, ast.Call):
            called = callee_of(expr)
            if called in ('eval', 'exec'):
                return False
            if called == 'getattr' or called in partial_aliases \
                    or called in import_module_aliases:
                return False
            if isinstance(expr.func, ast.Name):
                return expr.func.id in safe_names \
                    and expr.func.id not in bound
            if isinstance(expr.func, ast.Attribute):
                if isinstance(expr.func.value, ast.Name):
                    base = expr.func.value.id
                    if base in bound or base in module_factories:
                        return False
                    if base == 'subprocess':
                        return expr.func.attr == 'run'
                    return base in safe_names
                return resolves_safe(expr.func.value)
            return False
        if isinstance(expr, ast.Name):
            return expr.id not in bound and expr.id != 'subprocess'
        return True

    refusals = []
    defined_names = set()
    subprocess_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'subprocess' and alias.asname:
                    subprocess_names.add(alias.asname)
                    refusals.append(
                        f'{here}:{node.lineno} aliases the subprocess '
                        f'import as {alias.asname}')
                elif alias.name in machinery and '.' not in alias.name:
                    module_aliases[alias.asname or alias.name] = alias.name
                elif '.' in alias.name and not alias.asname:
                    safe_names.add(alias.name.split('.')[0])
                else:
                    safe_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'subprocess':
                subprocess_names.update(
                    alias.asname or alias.name for alias in node.names)
                refusals.append(
                    f'{here}:{node.lineno} from-imports subprocess')
            else:
                for alias in node.names:
                    imported = f'{node.module}.{alias.name}'
                    if imported in partial_aliases:
                        partial_aliases.add(alias.asname or alias.name)
                        defined_names.add(alias.asname or alias.name)
                    elif imported in import_module_aliases:
                        import_module_aliases.add(alias.asname
                                                  or alias.name)
                        defined_names.add(alias.asname or alias.name)
                    else:
                        safe_names.add(alias.asname or alias.name)
    if not refusals and not any(
            isinstance(node, ast.Import)
            and any(alias.name == 'subprocess' and not alias.asname
                    for alias in node.names)
            for node in ast.walk(tree)):
        refusals.append(
            f'{here} declares no plain "import subprocess"; the launch '
            'audit cannot vouch for any launch')
    bindings = []
    returns = []
    yields = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bindings.extend(
                (target.id, node.value) for target in node.targets
                if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value \
                and isinstance(node.target, ast.Name):
            bindings.append((node.target.id, node.value))
        elif isinstance(node, ast.NamedExpr):
            bindings.append((node.target.id, node.value))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_names.add(node.name)
            args = node.args
            defaults = args.defaults[-len(args.args):] \
                if args.defaults else []
            for arg, default in zip(args.args[-len(defaults):], defaults):
                bindings.append((arg.arg, default))
            for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                if default is not None:
                    bindings.append((arg.arg, default))
            returns.extend(
                (node.name, statement.value)
                for statement in ast.walk(node)
                if isinstance(statement, ast.Return) and statement.value)
            yields.extend(
                (node.name, statement.value)
                for statement in ast.walk(node)
                if isinstance(statement, (ast.Yield, ast.YieldFrom))
                and statement.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name):
                bindings.append((node.target.id, node.iter))
        elif isinstance(node, ast.ClassDef):
            defined_names.add(node.name)
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    bindings.append((node.name, statement.value))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    bindings.append(
                        (item.optional_vars.id, item.context_expr))
    # A parameter and a module-level import of the same name are different
    # bindings, and only one of them is readable.
    function_scopes = {}
    parameter_names = {}
    scope_walk: list[tuple] = [(tree, None)]
    while scope_walk:
        node, scope = scope_walk.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ListComp, ast.SetComp,
                                  ast.DictComp, ast.GeneratorExp)):
                function_scopes[id(child)] = child
                parameter_names[id(child)] = _parameters(child)
                scope_walk.append((child, child))
            else:
                function_scopes[id(child)] = scope
                scope_walk.append((child, scope))
    binding_map = {}
    ambiguous = set()
    for name, value in bindings:
        if name in binding_map:
            ambiguous.add(name)
        binding_map[name] = value
    reader = ArgvReader(binding_map, ambiguous)

    bound = set()
    module_factories = set()
    launcher_factories = set()
    changed = True
    while changed:
        changed = False
        for name, value in bindings:
            if name not in bound and derives(value, bound):
                bound.add(name)
                changed = True
        for name, value in returns + yields:
            if name in module_factories or name in launcher_factories:
                continue
            if derives(value, bound):
                if isinstance(value, ast.Name):
                    module_factories.add(name)
                else:
                    # LOAD-BEARING: a factory returning a LAUNCH is bound
                    # as well as registered, and the bound is what a name
                    # derived from it reads through. Rows
                    # `launcher-factory-bare-name-receiver-is-a-placed-
                    # launch` and its module-factory control are the
                    # pair that separates the two returns.
                    launcher_factories.add(name)
                    bound.add(name)
                    changed = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) \
                and isinstance(node.ctx, ast.Store) \
                and node.id not in bound:
            safe_names.add(node.id)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) \
                and node.value is not None \
                and derives(node.value, bound):
            # A tuple or list target unpacks the value into names the
            # bindings table never records; an attribute or subscript target
            # binds it to a place receiver resolution cannot read, which is
            # not an unpacking.
            targets = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            if any(isinstance(t, (ast.Tuple, ast.List))
                   for t in targets):
                refusals.append(
                    f'{here}:{node.lineno} unpacks subprocess-derived '
                    'values the audit cannot follow')
            elif not all(isinstance(t, ast.Name) for t in targets):
                refusals.append(
                    f'{here}:{node.lineno} binds a subprocess-derived '
                    'value to an attribute or subscript target the audit '
                    'cannot follow')
        if isinstance(node, (ast.For, ast.AsyncFor)) \
                and not isinstance(node.target, ast.Name) \
                and derives(node.iter, bound):
            if isinstance(node.target, (ast.Tuple, ast.List)):
                refusals.append(
                    f'{here}:{node.lineno} unpacks subprocess-derived '
                    'values the audit cannot follow')
            else:
                refusals.append(
                    f'{here}:{node.lineno} binds a subprocess-derived '
                    'value to an attribute or subscript target the audit '
                    'cannot follow')
    launches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = callee_of(node)
        if called in ('eval', 'exec'):
            refusals.append(
                f'{here}:{node.lineno} calls {called}, which the audit '
                'cannot resolve')
        spelled = normalize(called)
        if spelled and '.' in spelled:
            module, member = spelled.split('.', 1)
            if module in machinery \
                    and member not in machinery[module]:
                refusals.append(
                    f'{here}:{node.lineno} calls {called}, which the '
                    'audit cannot resolve')
        if isinstance(func, ast.Name) \
                and not (func.id in bound
                         or func.id in module_factories
                         or func.id in launcher_factories
                         or func.id in defined_names
                         or func.id in safe_names):
            refusals.append(
                f'{here}:{node.lineno} calls undefined name {func.id!r}, '
                'which the audit cannot resolve')
        if isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) \
                and (func.value.id == 'subprocess'
                     or func.value.id in bound):
            launches.append(node)
        elif isinstance(func, ast.Name) and func.id in bound:
            launches.append(node)
        # LOAD-BEARING and deliberately UNPINNED, so the state is here:
        # `(x := subprocess)(...)` with `x` bound is the shape that
        # reaches this arm, and with the arm gone it is reported
        # `unplaced` instead of placed. No row drives it and this wave
        # added none, which is a choice and not an oversight.
        elif isinstance(func, ast.NamedExpr) and func.target.id in bound:
            launches.append(node)
        elif isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Call) \
                and isinstance(func.value.func, ast.Name) \
                and func.value.func.id in module_factories:
            launches.append(node)
        elif isinstance(func, (ast.Call, ast.Subscript)):
            refusals.append(
                f'{here}:{node.lineno} calls through a receiver the '
                'audit cannot resolve')
        elif isinstance(func, ast.Attribute) \
                and not isinstance(func.value, ast.Name) \
                and derives(func.value, bound) \
                and resolves_safe(func.value):
            # A receiver the Name arms cannot carry — an attribute or a
            # namespace subscript — that derives to subprocess is a launch.
            # A bare name stays out: a Name that derives without being
            # bound is a module factory, a callable rather than the module.
            # Gated on resolves_safe so the sys.modules blanket refusal
            # below still holds.
            launches.append(node)
        elif isinstance(func, ast.Attribute):
            unresolved = not resolves_safe(func.value)
            if unresolved:
                refusals.append(
                    f'{here}:{node.lineno} calls through a receiver the '
                    'audit cannot resolve')

    def machinery_route(held):
        """Is this call the import machinery, by any route that reaches it?

        `import importlib as il` is one route and `il = importlib` is
        another, and the two differ in the spelling the callee carries.
        So the callee's base is followed through the bindings before the
        comparison: a name bound to the module and then used to call its
        member is the same machinery call.
        """
        if not isinstance(held, ast.Call):
            return False
        if normalize(callee_of(held)) in import_module_aliases:
            return True
        func = held.func
        if not isinstance(func, ast.Attribute) \
                or not isinstance(func.value, ast.Name):
            return False
        base, seen = func.value.id, set()
        # `base not in seen` is LOAD-BEARING, and it is the third kind of
        # seen guard: this loop is UNCAPPED, so unlike the flat resolvers
        # in _argv_read there is no fallback but the guard, and a base
        # that feeds itself does not stop — it does not answer wrong, it
        # does not stop. `test_a_cyclic_machinery_base_terminates_within_
        # a_step_ceiling` is the control, and it bounds STEPS rather than
        # time, so the mutant is an assertion and not a spin.
        while base in binding_map and base not in seen \
                and base not in ambiguous:
            seen.add(base)
            target = binding_map[base]
            if not isinstance(target, ast.Name):
                return False
            base = target.id
        return f'{base}.{func.attr}' in import_module_aliases

    def proved_fixed(receiver):
        """Is this receiver PROVED a fixed, non-launch value?

        Proof is the whole standard and it is deliberately narrow: a bare
        name this module binds and the analyser read, and nothing else. Four
        things are named and each is unreadable rather than unknown: a
        PARAMETER, which is a different binding from a module-level
        import of the same name in a different scope; an attribute or a
        subscript, because the analyser cannot know what `self.mod` or
        `ns[key]` holds; a name bound to a call the IMPORT MACHINERY
        makes, by any route that reaches it — `import importlib as il`,
        `il = importlib`, and the callee's base is followed through the
        bindings precisely so the two spellings are one case; and a name
        bound to `getattr`, which hands back whatever the module holds.

        A name the module binds to some OTHER call is proved, because
        the call itself is then the fixed value. A receiver the analyser
        cannot prove is one it must not pass over.

        The family that is NOT closed is the same shape stated without
        the spellings: a module reached through a name the analyser
        cannot read the origin of — a user's own factory that returns
        one, a module bound by an `except ... as` clause, an attribute
        held on an object. Each is the machinery route taken one step
        further from a name the bindings table holds, and the residual
        list names the family rather than the members it has been seen
        in.
        """
        if not isinstance(receiver, ast.Name):
            return False
        # Three of these four limbs are load-bearing and the fourth is DEAD;
        # the rest of this comment is which is which, because a disjunction
        # reads the same either way. `safe_names`: a name the module never
        # accounts for is unreadable — row
        # `module-factory-name-receiver-stays-unplaced` pins it.
        # `subprocess_names`: a name that spelled a subprocess import and
        # was THEN rebound is in `safe_names` and not in `bound`, so
        # nothing else refuses it — the `*-spelled-then-rebound-
        # is-unproved` and `annotated-subprocess-alias-is-unproved` rows.
        # `== 'subprocess'`: a PLAIN import puts `subprocess` in
        # `safe_names` and in neither of the two sets, so only this limb
        # refuses it — row `bare-subprocess-receiver-is-unplaced`.
        # `bound` is the DEAD one: a call whose receiver is a Name in
        # `bound` is collected as a PLACED launch by EITHER arm of the
        # chain that reads `bound` — the attribute arm and the bare-Name
        # arm — so the unplaced loop skips it and the limb is never
        # consulted. Rows `bound-name-called-bare-is-a-placed-launch` and
        # `machinery-call-binding-with-a-readable-argument-is-read` pin
        # those two placements.
        if (receiver.id not in safe_names or receiver.id in bound
                or receiver.id in subprocess_names
                or receiver.id == 'subprocess'):
            return False
        # A parameter is a DIFFERENT binding from a module-level import
        # of the same name, in a different scope, and this one the analyser
        # cannot read.
        if receiver.id in parameter_names.get(
                id(function_scopes.get(id(receiver))), ()):
            return False
        held = binding_map.get(receiver.id)
        if machinery_route(held):
            return False
        if isinstance(held, ast.Call):
            if normalize(callee_of(held)) == 'getattr':
                return False
            # A call is proved when the analyser can name what it calls:
            # a bare name this module accounts for, or an attribute on
            # one. A bare name it cannot account for is a factory whose
            # origin is invisible, and a factory's ARGUMENT decides what
            # it returns exactly as it decides for `import_module` — so a
            # factory result is no more proved than a machinery one.
            origin = callee_of(held)
            while origin and '.' in origin:
                origin = origin.rsplit('.', 1)[0]
            return origin in safe_names and origin not in bound
        return True

    def unplaced_bounded_call(node):
        """Every bounded call whose receiver is not a PROVED fixed value.

        A call carrying a `timeout=` or a `**`-unpacked mapping is a
        bounded call, whatever it calls and whatever the timeout reads.
        That is the whole of the second arm of the launch policy, and it
        is what makes the policy decidable rather than a list of
        spellings. A receiver reached
        through an import name held in a variable, through a class
        attribute, or through a run-time namespace is not proved, so it is
        reported at `unreadable` — the rule then demands a refusal or an
        allowance row for it.
        """
        func = node.func
        if not any(keyword.arg == 'timeout' or keyword.arg is None
                   for keyword in node.keywords):
            return False
        receiver = func.value if isinstance(func, ast.Attribute) else func
        if not proved_fixed(receiver):
            return True
        return False

    if bound_sink is not None:
        placed = {id(node) for node in launches}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and id(node) not in placed \
                    and unplaced_bounded_call(node):
                bound_sink.append((node.lineno, 'unreadable', 'unplaced'))
    if not launches:
        refusals.append(
            f'{here} declares no launch the audit can see through '
            '"subprocess" or a binding derived from it')
    for node in launches:
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        argv = node.args[0] if node.args else None
        container = reader.resolve_argv(argv) \
            if argv is not None else None
        words = reader.read_words(container)
        head = reader.head_label(container)
        if head == 'unreadable' and argv is not None \
                and reader.head_is_ambiguous(argv):
            head = 'ambiguous'
        # The refusal text never says "ambiguous": the head it names is the
        # unreadable one; the sink keeps the guess-distinguishing label.
        msg_head = 'unreadable' if head == 'ambiguous' else head
        if None in keywords:
            refusals.append(
                f'{here}:{node.lineno} unpacks a keyword mapping the '
                f'audit cannot read on a {msg_head} launch')
            if bound_sink is not None:
                bound_sink.append((node.lineno, head, 'unpack'))
            continue
        if 'timeout' in keywords:
            refusals.append(
                f'{here}:{node.lineno} carries a '
                f'timeout={ast.dump(keywords["timeout"])} argument on a '
                f'{msg_head} launch')
            if bound_sink is not None:
                bound_sink.append((node.lineno, head, 'timeout'))
        check = keywords.get('check')
        if not (isinstance(check, ast.Constant) and check.value is True):
            refusals.append(
                f'{here}:{node.lineno} does not fail loudly on a failed '
                'git command')
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr != 'run':
            value = node.func.value
            receiver = value.id if isinstance(value, ast.Name) else 'receiver'
            refusals.append(
                f'{here}:{node.lineno} launches through '
                f'{receiver}.{node.func.attr}, '
                'which the audit does not see')
        if container is None:
            refusals.append(
                f'{here}:{node.lineno} builds an argv the audit '
                'cannot read')
        if words and words[0] != 'git':
            interpreter = (container is not None and container.elts
                           and reader.is_interpreter(container.elts[0]))
            if not interpreter:
                refusals.append(
                    f"{here}:{node.lineno} argv does not start with the "
                    "constant 'git'")
        if words and words[0] == 'git' and 'clone' in words:
            declared = {words[i + 1]
                        for i, slot in enumerate(words[:-1])
                        if slot == '-c' and words[i + 1] is not None}
            missing = [name for name in CLONE_SILENCING_CONFIG
                       if name not in declared]
            if missing:
                refusals.append(
                    f'{here}:{node.lineno} clones without the silencing '
                    + ', '.join(f'-c {name}' for name in missing))
    return refusals


def bound_sites(source, here):
    """The analyser's own (lineno, head, kind) for every bounded launch.

    `kind` is 'timeout' (a readable `timeout=`) or 'unpack' (a
    `**`-unpacked keyword mapping, which could hide a timeout). A caller
    consumes this instead of re-parsing the human-readable refusal, so a
    message-format change cannot move a guard that keys on the head.
    """
    sink = []
    launch_refusals(source, here, bound_sink=sink)
    return sink
