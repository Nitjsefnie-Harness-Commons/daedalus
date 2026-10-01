"""Whether an expression mentions one of a set of names.

`tests/_lint_tool_roles.py` reads every guard a suite writes against a
subprocess call, to decide whether that guard names the binary the call
launches. A guard is a node, so the question is asked of the whole subtree
rather than of its outermost expression.

The names arrive as a set, because a guard names one and every caller wraps
its name in a set of one.
"""
import ast


def _mentions(node, names):
    return any(isinstance(inner, ast.Name) and inner.id in names
               for inner in ast.walk(node))
