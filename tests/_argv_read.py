"""The argv and head reading behind the launch audit's wall-clock verdict.

Given a launch's first argument, says whether it is a readable argv and
what its head word is: the constant `git`, a readable non-git constant, or
unreadable. Unreadable is never turned into a provable non-git — on the
exemption path a wrong non-git label would silently widen the exempt set.

A name is followed through the caller's bindings table, bounded and
behind a seen-guard, so a name-held or name-concatenated git argv
classifies rather than reading unreadable. A name bound more than once
in the module is a guess, so it resolves to nothing and the caller labels
the head `ambiguous` rather than taking either reading.

One consumer: `tests/_launch_audit.py`, which builds a reader over its
own bindings tables and takes every head label from it. Nothing else
reads this module and it reads no configuration of its own, so a change
here reaches the launch policy and nothing else.
"""
import ast

# A concat/literal chain deeper than this is not a shape the tree spells;
# the cap keeps a self-referential binding from spinning the head read. It
# bounds the unwrap passes of each resolver, so a name chain or concat
# longer than the cap stops at the cap and the head is reported unreadable
# (the name path resolves one hop fewer than the concat path, because its
# terminal value is only checked on the next pass).
ARGV_UNWRAP_CAP = 8


class ArgvReader:
    """One module's argv and head, read through its bindings tables.

    The tables are the module-wide `binding_map` and its `ambiguous` set,
    both final by the time the reader is built: the launch audit passes
    them in rather than rebuilding them, so this reader never sees a
    binding the caller has not finished collecting.
    """

    def __init__(self, binding_map, ambiguous):
        self.binding_map = binding_map
        self.ambiguous = ambiguous

    def resolve_constant(self, element):
        """An argv element's string constant, following a name chain.

        Resolves a name through the bindings table to a fixpoint behind a
        seen-guard bounded by ARGV_UNWRAP_CAP, the same idiom as
        resolve_argv, so a multi-step binding (`A = 'git'; B = A; run([B,
        ...])`) reaches its constant and a self-referential one (`A = A`)
        stops instead of looping. A name bound more than once resolves to
        None (unreadable), for the same last-wins reason as resolve_argv.
        """
        seen = set()
        for _ in range(ARGV_UNWRAP_CAP):
            if isinstance(element, ast.Constant) \
                    and isinstance(element.value, str):
                return element.value
            if not (isinstance(element, ast.Name)
                    and element.id in self.binding_map
                    and element.id not in self.ambiguous
                    and element.id not in seen):
                return None
            seen.add(element.id)
            element = self.binding_map[element.id]
        return None

    # The seen-guards below are redundant with ARGV_UNWRAP_CAP: these
    # resolvers do not recurse, so the cap bounds them and a guard cannot
    # change an answer. They are kept as belt and braces, and may be
    # deleted rather than defended.
    def resolve_argv(self, expr):
        """The argv's literal list/tuple, or None when it is dynamic.

        Unwraps a left-nested `+` chain and follows a plain name through
        the bindings table, both bounded by ARGV_UNWRAP_CAP, so a
        tuple-concatenated or name-held git argv is classified rather than
        refused as unreadable. A name bound more than once in the module
        resolves to None (unreadable): last-wins is a guess, and a guess
        that lands on a non-git head would assert a provable non-git for a
        git launch.
        """
        seen = set()
        for _ in range(ARGV_UNWRAP_CAP):
            if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
                expr = expr.left
                continue
            if isinstance(expr, ast.Name):
                if expr.id in seen or expr.id in self.ambiguous \
                        or expr.id not in self.binding_map:
                    return None
                seen.add(expr.id)
                expr = self.binding_map[expr.id]
                continue
            if isinstance(expr, (ast.List, ast.Tuple)):
                return expr
            return None
        return None

    def head_is_ambiguous(self, expr):
        """Did the argv's resolution hit a name bound more than once?

        Such a head is a guess, not a reading, so it is a bound site in its
        own right (in scope) rather than the residual dynamic-argv boundary.
        """
        seen = set()
        for _ in range(ARGV_UNWRAP_CAP):
            if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
                expr = expr.left
                continue
            if isinstance(expr, ast.Name):
                if expr.id in seen or expr.id not in self.binding_map:
                    return False
                if expr.id in self.ambiguous:
                    return True
                seen.add(expr.id)
                expr = self.binding_map[expr.id]
                continue
            return False
        return False

    def read_words(self, container):
        """A literal list/tuple's string words, names resolved; else None."""
        if container is None:
            return []
        return [self.resolve_constant(element) for element in container.elts]

    @staticmethod
    def is_interpreter(element):
        """Is this element a literal `sys.executable` head?"""
        return (isinstance(element, ast.Attribute)
                and isinstance(element.value, ast.Name)
                and element.value.id == 'sys'
                and element.attr == 'executable')

    def first_word(self, container):
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
        if self.is_interpreter(first):
            return (None, True)
        value = self.resolve_constant(first)
        return (value, value is not None)

    def head_label(self, container):
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
            labels = [self.head_label(element)
                      for element in container.elts]
            return 'git' if 'git' in labels else 'unreadable'
        first_value, first_readable = self.first_word(container)
        if first_value == 'git':
            return 'git'
        return 'non-git' if first_readable else 'unreadable'
