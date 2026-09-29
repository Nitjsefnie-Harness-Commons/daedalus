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
