"""Fail-closed checks over what a mutation control may call and write.

Not a suite itself — run_tests.py only loads `test_*.py`.

A control may call only what tests/_control_calls.py names, and may
write only to a path its own text proves lies below storage it owns. A
name proves a path only while it is bound once and never handed on —
to another name, a call, a container or a nested scope; a
helper proves its returns from its body and is judged with the kinds
its callers hand it, and never through a spread argument; and a call
the tables do not name is refused, not skipped.

A helper a control IMPORTS out of a `tests/_*.py` module is read rather
than trusted: its file is parsed, the called function is judged with the
kinds its call sites hand it under the rules above, and a violation
inside it is reported against that file and line. A table row still
decides first, so an import is never the reason a call was allowed.

What the guard does not see, by design: a statement at a shared helper
module's top level, exactly as in the ten helper modules a control
already imports. Only what the imported call reaches is judged.
"""
import ast
from collections import defaultdict
from pathlib import Path

from _control_calls import (ModuleNames, _SHARED_HELPER, call_judgement)
from _control_paths import (SCOPES as _SCOPES, _CONTROL_OWNED_PATH,
                            _UNKNOWN_PATH, _nested_scope_expressions,
                            _owned_path_names, _path_kind,
                            _scope_local_names, _scope_nodes,
                            _seeded_parameters)
from _imported_calls import (Context, SharedResolver, module_functions,
                             module_scopes, reached_functions)


def _call_violation(node, judgement, problem, kind, target, owned, trusted,
                    mutated):
    """The one message this call earns, or None when it is proved."""
    if problem is not None or kind is None:
        return problem
    if target is None:
        return f'{judgement.label}:{node.lineno}: {kind} target is unresolved'
    if _path_kind(target, owned, trusted, mutated,
                  judgement.context) != _CONTROL_OWNED_PATH:
        return (f'{judgement.label}:{node.lineno}: {kind} target path is '
                'not control-owned')
    return None


def _helper_calls(scope, helpers):
    for node in _scope_nodes(scope):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in helpers):
            yield node


def _meet_seeding(seedings, name, function, seeded):
    """Fold one call site's kinds into the helper's seeding, fail-closed."""
    target = seedings.setdefault(name, {})
    parameters = (function.args.posonlyargs + function.args.args
                  + function.args.kwonlyargs)
    for parameter in parameters:
        kind = (_UNKNOWN_PATH if seeded is None
                else seeded[parameter.arg])
        if target.get(parameter.arg, kind) != kind:
            kind = _UNKNOWN_PATH
        target[parameter.arg] = kind


def _runner_seeding(function):
    """What the runner hands a control nothing else calls: its tmp."""
    parameters = (function.args.posonlyargs + function.args.args
                  + function.args.kwonlyargs)
    if any(parameter.arg == 'tmp' for parameter in parameters):
        return {'tmp': _CONTROL_OWNED_PATH}
    return None


class _ModuleJudgement:
    """One module's scopes, judged in an order that seeds its helpers.

    A helper's body is judged with its parameters seeded from the kinds
    every caller hands it, once every caller has been judged; a helper
    nobody calls, or one in a call cycle, is judged unseeded. `reach` is
    the shared-helper form: it names the `tests/_*.py` functions the
    imported call reaches, and the module's other scopes — every statement
    at its top level included — are none of this control's business.
    """

    def __init__(self, tree, label, resolver, reach=None, seeded=None):
        self.label = label
        self.names = ModuleNames(tree)
        self.functions = module_functions(tree, self.names)
        self.resolver = resolver
        self.context = Context(self.functions, self.names, resolver)
        named = {node.id for node in ast.walk(tree)
                 if isinstance(node, ast.Name)
                 and isinstance(node.ctx, ast.Load)}
        self.helpers = {name: function
                        for name, function in self.functions.items()
                        if (reach is None or name in reach)
                        and (not name.startswith('test_') or name in named)}
        self.controls = {function
                         for name, function in self.functions.items()
                         if name not in self.helpers}
        self.seedings = dict(seeded or {})
        self.violations = []
        self.shared = reach is not None
        self.tree = tree
        self.roots, self.scopes = self._scopes(
            tree, {id(function) for function in self.helpers.values()}, reach)

    def _imported(self, node):
        if not isinstance(node.func, ast.Name):
            return None
        return self.resolver.imported(self.names, node.func.id)

    @staticmethod
    def _scopes(tree, helper_nodes, reach):
        """The top-level scopes judged whole, and the ones inside them.

        One rule, over roots rather than over a kind of root, so a kind of
        reached scope added later cannot come without its regions. A root
        is the module for a local judgement and each reached top-level
        scope for an imported one; a root is judged whole by
        `judge_scope`, and the top-level nodes — helpers included — are
        excluded from the walk because a top-level node is not a nested
        scope. Nothing here asks which kind of scope it is looking at.
        """
        reached = ([tree] if reach is None else
                   [node for node in tree.body
                    if getattr(node, 'name', None) in reach])
        roots = [node for node in reached if id(node) not in helper_nodes]
        top_level = helper_nodes | {id(node) for node in roots}
        scopes = [node for root in reached
                  for node in ast.walk(root)
                  if isinstance(node, _SCOPES) and id(node) not in top_level]
        return roots, scopes

    def judge_scope(self, scope, seeding):
        """One top-level scope, whole: the body and the header beside it.

        The single per-scope entry, and the roots loop, the nested-scope
        loop and the readiness loop all reach it. A header is whatever the
        scope evaluates when it is defined, and the same rule reads a
        `def`'s and a `class`'s.
        """
        self.judge(scope, seeding)
        if self.shared:
            self._judge_signature(scope)

    def judge_helper(self, name, seeding):
        """One reached helper, which is one top-level scope."""
        self.judge_scope(self.helpers[name], seeding)

    def _judge_signature(self, scope):
        """The expressions a top-level scope evaluates when it is defined.

        A default, a decorator and a class's bases and keywords are all
        evaluated where the `def` or `class` is, so they are judged with
        the module's names and not the caller's. The shared module's
        statements are not judged, so this is the only place one of these
        can be; a nested scope inside one is left to `scopes`.
        """
        owned, mutated = _owned_path_names(self.tree, self.context)
        trusted = {'Path', 'str', 'os'} - _scope_local_names(self.tree)
        for node in _nested_scope_expressions(scope):
            pending = [node]
            while pending:
                current = pending.pop()
                if isinstance(current, _SCOPES[1:]):
                    continue
                if isinstance(current, ast.Call):
                    message = _call_violation(
                        current, self,
                        *call_judgement(current, self.label, self.names,
                                        self.resolver.root),
                        owned, trusted, mutated)
                    if message is not None:
                        self.violations.append(
                            (self.label, current.lineno, message))
                pending.extend(ast.iter_child_nodes(current))

    def judge(self, scope, seeded):
        owned, mutated = _owned_path_names(scope, self.context, seeded)
        trusted = {'Path', 'str', 'os'} - _scope_local_names(scope)
        for node in _scope_nodes(scope):
            if not isinstance(node, ast.Call):
                continue
            problem, kind, target = call_judgement(
                node, self.label, self.names, self.resolver.root)
            if (kind is _SHARED_HELPER
                    and isinstance(node.func, ast.Name)):
                # The import is not the proof. The name is seeded from
                # the kinds this call site hands it, and its body is
                # judged in the file that writes it, once every caller
                # has been judged; a file this cannot read stays a
                # refusal here rather than becoming a silent pass.
                imported = self._imported(node)
                if imported is not None:
                    _meet_seeding(
                        self.seedings, node.func.id, imported.function,
                        _seeded_parameters(node, imported.function, owned,
                                           trusted, mutated, self.context, 0))
                message = None if imported is not None else problem
            else:
                if (isinstance(node.func, ast.Name)
                        and node.func.id in self.helpers):
                    function = self.helpers[node.func.id]
                    _meet_seeding(self.seedings, node.func.id, function,
                                  _seeded_parameters(node, function, owned,
                                                     trusted, mutated,
                                                     self.context, 0))
                message = _call_violation(node, self, problem, kind, target,
                                          owned, trusted, mutated)
            if message is not None:
                self.violations.append((self.label, node.lineno, message))

    def _judge_imported(self, name, seeding):
        """Judge an imported helper's body, in the file that writes it."""
        imported = self.resolver.imported(self.names, name)
        if imported is None:
            return
        module = imported.module
        self.violations.extend(_ModuleJudgement(
            module.tree, module.label, module.context.resolver,
            reach=reached_functions(imported.function,
                                    module_scopes(module.tree)),
            seeded={name: seeding}).run())

    def run(self):
        callers = defaultdict(set)
        for scope in self.scopes + list(self.helpers.values()):
            for call in _helper_calls(scope, self.helpers):
                callers[call.func.id].add(scope)
        judged = set()
        for root in self.roots:
            self.judge_scope(root, _runner_seeding(root)
                             if root in self.controls else None)
            judged.add(root)
        for scope in self.scopes:
            self.judge(scope, _runner_seeding(scope)
                       if scope in self.controls else None)
            judged.add(scope)
        pending = dict(self.helpers)
        while pending:
            ready = [name for name in pending if callers[name] <= judged]
            if not ready:
                # A cycle, or a helper no caller reaches: judged
                # unseeded, and through the same entry as the ordered
                # path, so a region the ordered path judges is not
                # missing here.
                for name in sorted(pending):
                    self.judge_helper(name, None)
                break
            for name in ready:
                pending.pop(name)
                self.judge_helper(name, self.seedings.get(name))
                judged.add(self.helpers[name])
        for name in sorted(self.seedings):
            if name not in self.helpers:
                self._judge_imported(name, self.seedings[name])
        return sorted(self.violations)


def control_write_violations(source, repository_root):
    """Return every call the control cannot prove harmless."""
    source = Path(source)
    tree = ast.parse(source.read_text(encoding='utf-8'))
    try:
        label = source.relative_to(repository_root).as_posix()
    except ValueError:
        label = source.name
    judgement = _ModuleJudgement(tree, label,
                                 SharedResolver(repository_root))
    return [message for _, _, message in judgement.run()]
