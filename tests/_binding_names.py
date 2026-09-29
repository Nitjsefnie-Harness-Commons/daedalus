"""Every NAME a source binds, and the table a receiver is looked up in.

`tests/_launch_path.py` answers "which source" and
`tests/_receiver_resolution.py` answers "which receiver a call is a call on".
This is the layer under both: the binding sets a receiver resolution reads
before it can decide anything, collected off the tree rather than guessed
from spelling. Every binder that is not a plain `Name` node is one
`getattr`, so a construct the running interpreter does not have is skipped
rather than an `AttributeError` at import.

Two readers, and they are two because the questions are two. What a target
node binds is read per target, so a tuple or a star unpacks and a `match`
capture is text; what a module binds is read per module, as the
Language Reference §4.2.1 list with every entry cited in `_rebindings`' own
docstring. `_receiver_resolution._dotted_bindings` is the import table the
docstring there calls "the reader above" — it stayed behind with the callers
that own it, and it is named here because a moved docstring is not edited
to keep a cross-reference true.
"""
import ast

import _launch_path as path


def _names_a_target_binds(target):
    """Every NAME a target node binds, through a tuple or a star."""
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, (ast.Tuple, ast.List)):
        return tuple(name for child in target.elts
                     for name in _names_a_target_binds(child))
    if isinstance(target, ast.Starred):
        return _names_a_target_binds(target.value)
    if isinstance(target, (ast.MatchAs, ast.MatchStar)):
        # A `match` capture binds its name as TEXT, like `except ... as`.
        return (target.name,) if target.name else ()
    return ()


def type_param_names(node):
    """The names in a node's PEP 695 type parameters; 3.12 and later.

    Guarded by FEATURE rather than by a version literal, because
    `scripts/ci/classify_changes.py`'s `FULL_MATRIX` runs 3.11 through
    3.14 and an unguarded attribute is a red cell on every 3.11 run.
    """
    return tuple(p.name for p in (getattr(node, 'type_params', None) or ()))


def _rebindings(tree):
    """`(node, name)` for every name a module binds except an import.

    The set is the Python Language Reference §4.2.1 binding list, cited so
    a reader can diff this against the reference. Checkable is not closed:
    the first diff after the citation was installed found two of §4.2.1's
    own bullets uncollected — `type_params`, unreachable because the
    `FunctionDef`/`ClassDef` branch precedes the `else` that collected them,
    and `ast.Lambda` parameters, invisible because `_is_def` excludes
    `Lambda`.

    Names come from ONE rule: every `ast.Name` whose `ctx` is `Store` or
    `Del`, whatever statement holds it. The binders that are not `Name`
    nodes are named below and each is one `getattr`, so a construct the
    running interpreter does not have is skipped rather than an
    `AttributeError` at import.

    NOT collected, and named rather than assumed: **formal parameters**,
    which `_function_parameters` owns — collecting them here too is how
    the two readers would silently disagree, and a parameter is a
    binding; and **import statements**, which the reader above keeps as a
    table rather than as rebindings.

    The node reported is the `Name` itself, except for a target of an
    `Assign`, where the `Assign` is reported so the caller can resolve the
    right-hand side.
    """
    assignments = {id(child): node for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)
                   for child in ast.walk(node)
                   if isinstance(child, ast.Name)}
    type_alias = getattr(ast, 'TypeAlias', None)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)):
            found.append((assignments.get(id(node), node), node.id))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            found.append((node, node.name))
            found.extend((node, name) for name in type_param_names(node))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            found.append((node, node.name))
        elif isinstance(node, ast.MatchAs) and node.name:
            found.append((node, node.name))
        elif isinstance(node, ast.MatchStar) and node.name:
            found.append((node, node.name))
        elif isinstance(node, ast.MatchMapping) and node.rest:
            found.append((node, node.rest))
        elif type_alias is not None and isinstance(node, type_alias):
            alias = getattr(node, 'name', None)  # `type X = ...`, 3.12
            if alias:
                found.append((node, alias))
            found.extend((node, name) for name in type_param_names(node))
        else:
            found.extend((node, name) for name in type_param_names(node))
    return found


def _every_use_proven(function, derived):
    """Whether every Load of a derived name sits in a PROVEN position.

    Four: an argument of a call, a `Compare` operand, the value of an
    `Assign` whose targets are all plain Names, and a bare-Name `Assert`
    message. Fail-closed: any other position is a refusal, so a use nobody
    anticipated is never read as permission.

    This checks WHERE the deadline's own names are USED, which is not the
    same question as which statements to FOLLOW. The last five findings on
    this branch were that mistake -- a rule reading a subset and treating the
    complement as proof -- and following more forms would be the next member
    of the same class. Here the list is positions, and the complement of a
    position list is a refusal rather than a permission.

    An argument of a call is judged by the caller's own sink and receiver
    logic exactly as it always was; this gate only decides whether the
    position is a position at all.

    A Load inside a comprehension is the value of a COMPREHENSION, which has
    its own scope and its own target binding -- not the value of the Assign
    whose expression happens to contain it.
    """
    proven = set()
    scoped = {id(inner)
              for comp in ast.walk(function)
              if isinstance(comp, ast.comprehension)
              for inner in ast.walk(comp)}

    def take(inner):
        if (isinstance(inner, ast.Name) and inner.id in derived
                and id(inner) not in scoped):
            proven.add(id(inner))

    def walk(parts):
        for part in parts:
            for inner in ast.walk(part):
                take(inner)

    for node in ast.walk(function):
        if path._is_call(node):
            walk([*node.args, *(k.value for k in node.keywords)])
        elif isinstance(node, ast.Compare):
            walk([node.left, *node.comparators])
        elif (isinstance(node, ast.Assert) and isinstance(node.msg, ast.Name)
              and node.msg.id in derived):
            # The message is a NAME the assertion formats when it FAILS, so
            # it reaches no child; only a bare Name is that, and anything
            # nested under the message gets no exemption.
            proven.add(id(node.msg))
        elif (isinstance(node, ast.Assign) and node.value is not None
                and all(isinstance(t, ast.Name) for t in node.targets)):
            walk([node.value])
        elif (isinstance(node, ast.AugAssign)
              and isinstance(node.target, ast.Name)
              and node.target.id in derived):
            # `timeout += 1` READS the name it writes. The augmented target
            # is the one Store-context Name that is also a Load, and no
            # position list admits it.
            return False
    return all(id(inner) in proven for inner in ast.walk(function)
               if isinstance(inner, ast.Name) and inner.id in derived
               and isinstance(inner.ctx, ast.Load))


def _receiver_escapes(node):
    """Whether a Load of the RECEIVER sits in a proven position.

    ONE, and its complement poisons every key the receiver owns: the `value`
    of an `Attribute` node (`self.handles`, read or written). A CALL
    ARGUMENT poisons too, discarded or not -- whether a call's result is
    thrown away looked like a signal and was read as proof, and a signal is
    not proof. `s = self` and then `s.handles = Popen()` is not a reflective
    write -- no setattr, no `__dict__`, no `vars` -- so the poison rule has
    nothing to fire on, and the census goes on reading `self.handles` from
    its literal binding while the write lands on it at runtime.

    This is the same POSITIONS principle as the deadline guard and
    deliberately carries no alias tracking and no binder list: it does not
    ask which binder produced the alias, only whether the receiver's own
    Load is somewhere a receiver can be used from. Only `self.x` is; an
    assignment's value, a `for` iterable, a `with` target, a match subject,
    a default, a return and a call argument are not.
    """
    args = node.args
    if 'self' not in {arg.arg for arg in
                      (*args.posonlyargs, *args.args, *args.kwonlyargs)}:
        return False
    proven = set()
    for child in ast.walk(node):
        if (isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id == 'self'):
            proven.add(id(child.value))
    return any(isinstance(child, ast.Name) and child.id == 'self'
               and isinstance(child.ctx, ast.Load) and id(child) not in proven
               for child in ast.walk(node))


def _spread_args(call):
    """Whether a call's arguments are a form its receiver cannot be read from.

    Any `Starred` or `**` argument. A setattr-family call in that shape
    cannot prove which receiver it is writing, so it poisons EVERY
    receiver in scope rather than guessing one -- and a guess that picked
    the wrong receiver would be a false green, which is the direction
    this rule exists to close. The arity half is the CALLER's: both sites
    read `len(node.args) != 3 or _spread_args(node)`, and that `3` is the
    form's own arity, which this helper cannot know.
    """
    return (any(isinstance(arg, ast.Starred) for arg in call.args)
            or any(key.arg is None for key in call.keywords))
