"""The payload model's own pass over one scope, with its key bindings.

`dict_assignments` reads a scope without the flow, so it has to carry the
name-to-literal table itself — a key position is resolved by the literal
its name was last bound to, and a reader that resolved payload keys
without that table would report the same key as the flow does and for a
different reason. It lives beside the store that maintains the table for
exactly that reason, and out of the flow module, which was a line from
its ceiling.
"""
import ast

from _pyroute_keys import _UNSAFE_LITERAL, _literal_value
from _pyroute_state import apply_dict_statement, scope_nodes

_STORES = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)


def _bind_literals(node, literals):
    """Record the literal each name this store binds is bound to."""
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return
    value = getattr(node, 'value', None)
    literal = _UNSAFE_LITERAL if value is None else _literal_value(value)
    targets = (node.targets if isinstance(node, ast.Assign) else [node.target])
    for target in targets:
        if not isinstance(target, ast.Name):
            continue
        if literal is _UNSAFE_LITERAL:
            literals.pop(target.id, None)
        else:
            literals[target.id] = literal


def dict_assignments(scope):
    """Map local names to string keys, retaining provable mutations."""
    dicts = {}
    literals = {}
    nodes = [node for node in scope_nodes(scope) if isinstance(node, _STORES)]
    for node in sorted(nodes, key=lambda item: (item.lineno, item.col_offset)):
        apply_dict_statement(node, dicts, literals)
        _bind_literals(node, literals)
    return dicts
