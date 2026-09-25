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
# the cap keeps a self-referential binding from spinning the head read. It
# bounds the unwrap passes of each resolver, so a name chain or concat
# longer than the cap stops at the cap and the head is reported unreadable
# (the name path resolves one hop fewer than the concat path, because its
# terminal value is only checked on the next pass).
_ARGV_UNWRAP_CAP = 8


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
    ambiguous = set()
    for name, value in bindings:
        if name in binding_map:
            ambiguous.add(name)
        binding_map[name] = value

    def resolve_argv(expr):
        """The argv's literal list/tuple, or None when it is dynamic.

        Unwraps a left-nested `+` chain and follows a plain name through
        the bindings table, both bounded by _ARGV_UNWRAP_CAP, so a
        tuple-concatenated or name-held git argv is classified rather than
        refused as unreadable. A name bound more than once in the module
        resolves to None (unreadable): last-wins is a guess, and a guess
        that lands on a non-git head would assert a provable non-git for a
        git launch.
        """
        seen = set()
        for _ in range(_ARGV_UNWRAP_CAP):
            if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
                expr = expr.left
                continue
            if isinstance(expr, ast.Name):
                if expr.id in seen or expr.id in ambiguous \
                        or expr.id not in binding_map:
                    return None
                seen.add(expr.id)
                expr = binding_map[expr.id]
                continue
            if isinstance(expr, (ast.List, ast.Tuple)):
                return expr
            return None
        return None

    def head_is_ambiguous(expr):
        """Did the argv's resolution hit a name bound more than once?

        Such a head is a guess, not a reading, so it is a bound site in its
        own right (in scope) rather than the residual dynamic-argv boundary.
        """
        seen = set()
        for _ in range(_ARGV_UNWRAP_CAP):
            if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
                expr = expr.left
                continue
            if isinstance(expr, ast.Name):
                if expr.id in seen or expr.id not in binding_map:
                    return False
                if expr.id in ambiguous:
                    return True
                seen.add(expr.id)
                expr = binding_map[expr.id]
                continue
            return False
        return False

    def resolve_constant(element):
        """An argv element's string constant, following a name chain.

        Resolves a name through the bindings table to a fixpoint behind a
        seen-guard bounded by _ARGV_UNWRAP_CAP, the same idiom as
        resolve_argv, so a multi-step binding (`A = 'git'; B = A; run([B,
        ...])`) reaches its constant and a self-referential one (`A = A`)
        stops instead of looping. A name bound more than once resolves to
        None (unreadable), for the same last-wins reason as resolve_argv.
        """
        seen = set()
        for _ in range(_ARGV_UNWRAP_CAP):
            if isinstance(element, ast.Constant) \
                    and isinstance(element.value, str):
                return element.value
            if not (isinstance(element, ast.Name)
                    and element.id in binding_map
                    and element.id not in ambiguous
                    and element.id not in seen):
                return None
            seen.add(element.id)
            element = binding_map[element.id]
        return None

    def read_words(container):
        """A literal list/tuple's string words, names resolved; else None."""
        if container is None:
            return []
        return [resolve_constant(element) for element in container.elts]

    def is_interpreter(element):
        return (isinstance(element, ast.Attribute)
                and isinstance(element.value, ast.Name)
                and element.value.id == 'sys'
                and element.attr == 'executable')

    def first_word(container):
        """The head's string constant and whether the audit could read it.

        A head element bound to a name resolves to its constant, so
        `[GIT, 'status']` with `GIT = 'git'` classifies as a git launch.
        A literal `sys.executable` head is read as a known non-git
        interpreter. Any other head that is not a readable string constant
        is unreadable, never a provable non-git: on the exemption path a
        wrong non-git label would silently widen the exempt set.
        """
        if container is None or not container.elts:
            return (None, container is not None)
        first = container.elts[0]
        if is_interpreter(first):
            return (None, True)
        value = resolve_constant(first)
        return (value, value is not None)

    def head_label(container):
        """git / non-git / unreadable for one launch's resolved argv.

        A for-target bound to a sequence of argv literals (the launch runs
        once per element) is git if ANY iteration's head is git, because
        the loop runs them all and one refusal covers the site; otherwise
        it is unreadable, never a non-git inferred from the loop shape. A
        non-git label is only ever a single-head reading.
        """
        if container is None:
            return 'unreadable'
        if container.elts and isinstance(container.elts[0], (ast.List,
                                                             ast.Tuple)):
            labels = [head_label(element) for element in container.elts]
            return 'git' if 'git' in labels else 'unreadable'
        first_value, first_readable = first_word(container)
        if first_value == 'git':
            return 'git'
        return 'non-git' if first_readable else 'unreadable'

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

    def mentions_subprocess(expr):
        """Does this expression name subprocess, builtins, or the machinery?"""
        tracked = {'subprocess', 'builtins'} | import_module_aliases \
            | partial_aliases | bound | module_factories
        return any(isinstance(sub, ast.Name) and sub.id in tracked
                   for sub in ast.walk(expr))

    def unplaced_bounded_call(node):
        """A subprocess launch the analyser refused or skipped, carrying a
        bound the source shows: a readable ``timeout=`` or a ``**``-unpacked
        mapping that could hide one. Reported as an unreadable bound site,
        never accepted, so a spelling it refuses at source tier cannot hide
        a bound from the tree-wide rule. The receiver is recognised by the
        analyser's own ``derives`` predicate, which reaches ``sys.modules
        [...]``, a subprocess-derived binding and the import machinery; the
        refused import spellings (``subprocess_names``) and a name walk for
        receivers no expression predicate reaches (a getattr call) are kept
        because ``derives`` cannot see either."""
        func = node.func
        if not any(keyword.arg == 'timeout' or keyword.arg is None
                   for keyword in node.keywords):
            return False
        if isinstance(func, ast.Attribute):
            return (derives(func.value, bound)
                    or (isinstance(func.value, ast.Name)
                        and func.value.id in subprocess_names))
        if isinstance(func, ast.Name):
            return func.id in subprocess_names or derives(func, bound)
        return derives(func, bound) or mentions_subprocess(func)

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
        container = resolve_argv(argv) if argv is not None else None
        words = read_words(container)
        head = head_label(container)
        if head == 'unreadable' and argv is not None \
                and head_is_ambiguous(argv):
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
                           and is_interpreter(container.elts[0]))
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
