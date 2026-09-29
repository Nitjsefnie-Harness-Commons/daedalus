"""Planting a defect in a copied module by node, not by source spelling.

Not a suite itself — run_tests.py only loads `test_*.py`.

A verbatim needle cannot match on a CRLF checkout and an ordinary edit to
the copied file strands it, so each anchor here is a node. An anchor that
is not unique is a refusal rather than a guess: an edit to the wrong
occurrence is indistinguishable from no edit at all.
"""
import ast
from typing import TypeVar

_Node = TypeVar('_Node', bound=ast.AST)


def _nodes(tree: ast.AST, kind: type[_Node]) -> list[_Node]:
    return [node for node in ast.walk(tree) if isinstance(node, kind)]


def _calls_of(tree, name):
    return [node for node in _nodes(tree, ast.Call)
            if isinstance(node.func, ast.Name) and node.func.id == name]


def after_call(text, called, plant):
    """`text` with `plant` below its one call of `called`; the plant's line.

    The line the guard reports is the one the plant landed on.
    """
    found = _calls_of(ast.parse(text), called)
    assert len(found) == 1, f'the {called} anchor is not unique: {found}'
    lines = text.split('\n')
    lines[found[0].lineno:found[0].lineno] = [plant]
    return '\n'.join(lines), found[0].lineno + 1


def after_import(text, module, plant):
    """`text` with `plant` below its one `from <module>` import."""
    found = [node for node in _nodes(ast.parse(text), ast.ImportFrom)
             if node.module == module]
    assert len(found) == 1, f'the {module} import is not unique: {found}'
    lines = text.split('\n')
    lines[found[0].end_lineno:found[0].end_lineno] = [plant]
    return '\n'.join(lines)


def first_call_line(text, name):
    """The first line at which `text` calls `name`.

    Position, not spelling: a control calls a shared helper several times.
    """
    lines = sorted(node.lineno for node in _calls_of(ast.parse(text), name))
    assert lines, f'no call of {name} in this copy'
    return lines[0]


def the_call_line(text, name):
    """The line of `text`'s one call of `name`; a count, not a first match."""
    found = _calls_of(ast.parse(text), name)
    assert len(found) == 1, f'the {name} call is not unique: {found}'
    return found[0].lineno


def before_first_call(text, name, plant):
    """`text` with `plant` above the first line that calls `name`."""
    first = first_call_line(text, name)
    lines = text.split('\n')
    lines[first - 1:first - 1] = plant.split('\n')
    return '\n'.join(lines)
