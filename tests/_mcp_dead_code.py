"""A position the runtime provably cannot reach: the nodes nothing executes.

The walk applies one rule to every VALUE it reads — a value the runtime
provably cannot reach through is CLEAN rather than suspicious, whatever the
spelling — and this module is the same rule asked of POSITIONS. A node the
runtime provably does not execute is not evidence: nothing in its subtree is
resolved into the scan set and nothing in it draws a refusal, because a
refusal the runtime can never trigger answers a question nobody asked.

A block is read as a PROPERTY of the field that holds it rather than as a
list of the node types that happen to carry one, so a block form nobody has
thought of is the same read again. A statement leaves its block when every
path through it leaves, and the containers that may or may not run are
answered by the same fall-through, with the runtime's reason beside it —
which is what keeps a `try`, a `with`, a loop and a `match` from reading as
barriers.
"""
import ast

# The statements every path through leaves: the runtime leaves the block
# where it stands, so nothing after them runs.
_BARRIERS = (ast.Raise, ast.Return, ast.Break, ast.Continue)

# Only those four, and two limits are declared here rather than answered
# there. A call that provably raises — `sys.exit()`, `os._exit()`, `exit()` —
# is not a barrier, so the code after it stays in the scan set; nor is a
# `return` inside a `finally`, which really does leave the frame — the tail
# INSIDE the `finalbody` is a block like any other, so the rule does find
# it, while the one after the whole `try` is dead and the rule does not.
# Both err the same way, keeping a position the runtime cannot reach
# instead of dropping one it can, and that is the direction this whole
# module is written to err in.


def dead_nodes(tree):
    """Every AST node the runtime provably does not execute.

    The one answer, and every reader consults this set rather than deciding
    reachability for itself — a walk that answered two ways about one dead
    region would be two walks.
    """
    dead = set()
    _descend(tree, dead)
    return dead


def _descend(node, dead):
    """Mark inner block tails before the blocks that contain them, so an
    `if` is decided against tails that are already known."""
    if node in dead:
        return
    for child in ast.iter_child_nodes(node):
        _descend(child, dead)
    for block in _blocks(node):
        _mark_tail(block, dead)


def _blocks(node):
    """Every list of statements a node holds: a BLOCK, however it is spelled.

    A block is a property of the FIELD that holds its statements, not of the
    node types that happen to carry one, so `Module.body`, a definition's
    `body`, an `if`'s `body` and `orelse`, a `with`'s, a `try`'s `body`,
    `orelse` and `finalbody`, an `except` handler's `body` and a `match`
    case's `body` are one read here rather than a list of them. A list whose
    members are not all statements is a different list — a `try`'s
    `handlers`, a signature's arguments — and is no block at all; an empty
    one is a block of no statements and marks nothing.
    """
    for _, value in ast.iter_fields(node):
        if isinstance(value, list) and all(
                isinstance(child, ast.stmt) for child in value):
            yield value


def _mark_tail(block, dead):
    """Every statement from the FIRST that leaves to the end of the block.

    The whole tail and not the statement after the barrier: a barrier
    protects everything the runtime would have reached after it, so a
    statement sandwiched between two barriers is still unreachable, and
    bounding the skip to the next statement is the failure this shape
    invites. The barrier itself is not in its own tail — it is the one
    statement of the tail the runtime does execute.
    """
    for index, statement in enumerate(block):
        if _leaves(statement, dead):
            for later in block[index + 1:]:
                dead.update(ast.walk(later))
            return


def _leaves(statement, dead):
    """Whether a statement leaves the block it is written in: every path
    through it leaves, so nothing after it runs.

    A statement already known dead leaves vacuously — no path runs through
    it at all — which is how an `if` whose two branches are entirely dead
    reads as a barrier. An `if` leaves only when it HAS an `orelse`: without
    one, a false condition falls straight through it.
    """
    if statement in dead or isinstance(statement, _BARRIERS):
        return True
    if isinstance(statement, ast.If) and statement.orelse:
        return (_leaves(statement.body[-1], dead)
                and _leaves(statement.orelse[-1], dead))
    # The containers that may run and may not fall through here as well, each
    # for the runtime's reason it is not a barrier: a handler may CATCH and
    # execution continues past a `try` (`except*` is the same `try`), and a
    # `try` with no handler may still run to completion, because the walk
    # cannot know its body always raises; a context manager's `__exit__` may
    # swallow and execution continues past a `with`, which is what
    # `contextlib.suppress` is for; a loop may run zero times; a `match` may
    # match no case at all. They are named here rather than checked, because
    # a list would read as the mechanism and is not one: this return is what
    # answers them, and every other kind besides the barriers and a
    # two-leaving `if` with it.
    return False
