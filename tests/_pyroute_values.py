"""Deferred values and expression helpers for Python routing flow."""
import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _pyroute_state import FlowState


@dataclass(frozen=True)
class DeferredGenerator:
    """A generator expression and its known remaining yield count."""

    expression: ast.GeneratorExp
    remaining: int | None
    evaluate_zero: bool = False


@dataclass
class DeferredCallable:
    """A callable body and its definition-time lexical state."""

    scope: ast.AST
    state: 'FlowState'
    locals: frozenset
    captured: frozenset = frozenset()
    escaped: bool = False


@dataclass(frozen=True)
class DeferredClass:
    """Methods belonging to one statically known class object."""

    methods: dict


def assignment_escapes(statement, targets, value):
    if type(statement) not in (ast.Assign, ast.AnnAssign):
        return False
    stored = any(not isinstance(target, (ast.Name, ast.Tuple, ast.List))
                 for target in targets)
    return stored or isinstance(value, (ast.List, ast.Tuple,
                                        ast.Set, ast.Dict))


def expression_value(node, state, generator_factory, sender_resolver,
                     unprovable_sender):
    if isinstance(node, ast.GeneratorExp):
        return generator_factory(node)
    if isinstance(node, ast.Name) and node.id in state.generators:
        return state.generators[node.id]
    if isinstance(node, ast.Name) and node.id in state.callables:
        return state.callables[node.id]
    if isinstance(node, (ast.NamedExpr, ast.Starred)):
        child = node.value
        if id(child) in state.evaluated:
            return state.evaluated[id(child)]
    if isinstance(node, (ast.Tuple, ast.List)):
        values = [state.evaluated.get(id(item)) for item in node.elts]
        if any(value is not None
               and not isinstance(value, DeferredGenerator)
               for value in values):
            return unprovable_sender
    return sender_resolver(node, state.aliases)


def mark_callable_escapes(node, state):
    """Mark deferred callables carried out through an expression."""
    if node is None:
        return
    value = state.evaluated.get(id(node))
    if value is None and isinstance(node, ast.Name):
        value = state.callables.get(node.id)
    if isinstance(value, DeferredCallable):
        value.escaped = True
        return
    children = ([*node.args, *(item.value for item in node.keywords)]
                if isinstance(node, ast.Call) else ast.iter_child_nodes(node))
    for child in children:
        mark_callable_escapes(child, state)


def payload_key(dicts):
    return tuple(sorted(
        (name, frozenset((key, value[0], id(value[1]))
                         for key, value in keys.items()))
        for name, keys in dicts.items()))
