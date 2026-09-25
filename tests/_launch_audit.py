"""The launch-refusal analyser behind the repo-layout launch policy.

Given one Python source, names every refusal limb its subprocess
launches trip, so a caller can assert the launch carries no wall-clock
bound, fails loudly, and is readable argv. The wall-clock-bound refusal
names the argv head (git / non-git / unreadable) so a tree-wide caller
can keep the rule to git launches. Lives beside the suite, not in it,
so the suite file stays under its size ceiling.
"""
import ast

CLONE_SILENCING_CONFIG = ('init.defaultBranch=main',
                          'advice.detachedHead=false')

# A concat/literal chain deeper than this is not a shape the tree spells;
# the cap keeps a self-referential binding from spinning the head read.
_ARGV_UNWRAP_CAP = 8


def launch_refusals(source, here):
    """Every refusal limb one Python source's launches trip, naming its
    limb."""
    import builtins
    tree = ast.parse(source)
    safe_names = set(dir(builtins))
    partial_aliases = {'functools.partial', 'partial'}
    import_module_aliases = {'importlib.import_module', 'import_module'}
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
        if isinstance(value, (ast.Attribute, ast.NamedExpr)):
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
            return derives(base, bound)
        if isinstance(value, ast.Call):
            called = normalize(callee_of(value))
            if called in partial_aliases and any(
                    derives(arg, bound) for arg in value.args):
                return True
            if called in import_module_aliases \
                    and any(isinstance(arg, ast.Constant)
                            and arg.value == 'subprocess'
                            for arg in value.args):
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
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'subprocess' and alias.asname:
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
                    launcher_factories.add(name)
                    bound.add(name)
                    changed = True
    binding_map = {}
    for name, value in bindings:
        binding_map[name] = value

    def resolve_argv(expr):
        """The argv's literal list/tuple, or None when it is dynamic.

        Unwraps a left-nested `+` chain and follows a plain name through
        the bindings table, both bounded by _ARGV_UNWRAP_CAP, so a
        tuple-concatenated or name-held git argv is classified rather than
        refused as unreadable.
        """
        seen = set()
        for _ in range(_ARGV_UNWRAP_CAP):
            if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
                expr = expr.left
                continue
            if isinstance(expr, ast.Name):
                if expr.id in seen or expr.id not in binding_map:
                    return None
                seen.add(expr.id)
                expr = binding_map[expr.id]
                continue
            if isinstance(expr, (ast.List, ast.Tuple)):
                return expr
            return None
        return None

    def read_words(container):
        """A literal list/tuple's string words; non-strings become None."""
        if container is None:
            return []
        return [elt.value if isinstance(elt, ast.Constant)
                and isinstance(elt.value, str) else None
                for elt in container.elts]

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) \
                and isinstance(node.ctx, ast.Store) \
                and node.id not in bound:
            safe_names.add(node.id)
        if isinstance(node, ast.Assign) \
                and not all(isinstance(t, ast.Name) for t in node.targets) \
                and derives(node.value, bound):
            refusals.append(
                f'{here}:{node.lineno} unpacks subprocess-derived values '
                'the audit cannot follow')
        if isinstance(node, (ast.For, ast.AsyncFor)) \
                and not isinstance(node.target, ast.Name) \
                and derives(node.iter, bound):
            refusals.append(
                f'{here}:{node.lineno} unpacks subprocess-derived values '
                'the audit cannot follow')
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
        elif isinstance(func, ast.Attribute):
            unresolved = not resolves_safe(func.value)
            if unresolved:
                refusals.append(
                    f'{here}:{node.lineno} calls through a receiver the '
                    'audit cannot resolve')
    if not launches:
        refusals.append(
            f'{here} declares no launch the audit can see through '
            '"subprocess" or a binding derived from it')
    for node in launches:
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        argv = node.args[0] if node.args else None
        container = resolve_argv(argv) if argv is not None else None
        words = read_words(container)
        if words and words[0] == 'git':
            head = 'git'
        elif container is not None:
            head = 'non-git'
        else:
            head = 'unreadable'
        if None in keywords:
            refusals.append(
                f'{here}:{node.lineno} unpacks a keyword mapping the '
                f'audit cannot read on a {head} launch')
            continue
        if 'timeout' in keywords:
            refusals.append(
                f'{here}:{node.lineno} carries a '
                f'timeout={ast.dump(keywords["timeout"])} argument on a '
                f'{head} launch')
        check = keywords.get('check')
        if not (isinstance(check, ast.Constant) and check.value is True):
            refusals.append(
                f'{here}:{node.lineno} does not fail loudly on a failed '
                'git command')
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr != 'run':
            refusals.append(
                f'{here}:{node.lineno} launches through '
                f'{node.func.value.id}.{node.func.attr}, '
                'which the audit does not see')
        if container is None:
            refusals.append(
                f'{here}:{node.lineno} builds an argv the audit '
                'cannot read')
        if words and words[0] != 'git':
            interpreter = (container is not None
                           and isinstance(container.elts[0], ast.Attribute)
                           and isinstance(container.elts[0].value, ast.Name)
                           and container.elts[0].value.id == 'sys'
                           and container.elts[0].attr == 'executable')
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
