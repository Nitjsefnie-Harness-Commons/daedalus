"""WHICH RECEIVER a call is a call on, which is a different question.

`tests/_launch_path.py` answers "which source" and `tests/_launch_census.py`
answers "what in it is a bound". This is the half between them: a callee
and a parameter both NAME a receiver, and a rule cannot say whether the
number in front of it reaches a child until it has said what the thing it
is in front of IS.

Two questions live here, both of them #1299's:

  - `is_network_read` — the complementary statement to `_is_launch`. That
    one proves a call IS a launch; this one proves a callee is NOT a
    child, and a network read is the receiver this tree actually writes a
    `timeout` on that is not a child.
  - `deadline_reaches_a_child` — whether a `timeout` PARAMETER can reach a
    child from inside the function that takes it, which is what a
    signature-only refusal was standing in for.

Neither is keyed on a name the source could spell another way. The
network arm resolves a callee to a live OBJECT and asks whether that
object is a member of a stdlib network module, so `urlopen`, an aliased
import of it, a module aliased at the import and a local bound from it
are ONE receiver; the reachability arm asks whether the number the
parameter names reaches a child the census has RESOLVED, never whether
the signature looks like a launcher's.
"""
import ast
import importlib
import inspect
import sys

import _launch_path as path

# The modules a NETWORK READ is a member of. This names MODULES and never
# callees: the members are read off the stdlib at use, so a member a
# future interpreter adds is a control that fires rather than a gap, and
# the list cannot be tuned to whatever sample the suite happens to hold.
# A name-keyed oracle is what four consecutive waves on
# `tests/_coverage_guard.py` measured, each green while nine of ten
# held-out launcher-carrying shapes went unrefused.
_NETWORK_MODULES = ('urllib.request', 'http.client', 'socket')
_NETWORK_READS = None
_NETWORK_OBJECTS = ()


def _resolve_dotted(node, bound):
    """The canonical path a Name or an Attribute spells, or None.

    The root of the spelling is the module's own binding and the rest is
    what the source wrote after it, so `urlr.urlopen` resolves under a
    module bound to `urllib.request` without this walk knowing what a URL
    is.
    """
    key = path._dotted_key(node)
    if not key:
        return None
    root, _, rest = key.partition('.')
    base = bound.get(root)
    if base is None:
        return None
    return f'{base}.{rest}' if rest else base


def _dotted_bindings(tree):
    """`local name -> canonical dotted path` for everything a module binds.

    What a NAME is bound to, which is the question `_imported` asks for a
    launch and this asks for anything: `import a.b`, `import a.b as x`,
    `from a.b import c`, `from a.b import c as x`, and a local bound from
    any of them, run to a small fixpoint. A callee is a RECEIVER before it
    is a spelling, and a rule that has to answer "what is this a call on"
    cannot answer it from the text of the call.
    """
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split('.')[0]
                bound[alias.asname or head] = (
                    alias.name if alias.asname else head)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound[alias.asname or alias.name] = (
                    f'{node.module}.{alias.name}')
    for _ in range(3):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or node.value is None:
                continue
            resolved = _resolve_dotted(node.value, bound)
            if resolved is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = resolved
    return bound


def _takes_a_timeout(value):
    """Whether a live object's own signature names a `timeout`."""
    try:
        return 'timeout' in inspect.signature(value).parameters
    except (TypeError, ValueError):
        return False


def _network_reads():
    """`id -> None` for every live object a network read is spelled with.

    The stdlib's own member tables, filtered to the members whose
    signature takes a `timeout` — which is what a read is bounded with,
    and is also what keeps a module's unrelated re-exports out. Keyed by
    IDENTITY, so the membership test is the object a call resolves to and
    not the name it was written under. The objects are held beside the
    ids, because an id set alone lets a collected object lend its address
    to the next one allocated.
    """
    global _NETWORK_READS, _NETWORK_OBJECTS  # noqa: PLW0603
    if _NETWORK_READS is None:
        found = [value for name in _NETWORK_MODULES
                 for value in vars(importlib.import_module(name)).values()
                 if callable(value) and _takes_a_timeout(value)]
        _NETWORK_OBJECTS = tuple(found)
        _NETWORK_READS = frozenset(map(id, found))
    return _NETWORK_READS


def _live_object(dotted):
    """The live object a canonical dotted path names, or None.

    Only a stdlib head is imported, and only a name the module's own
    source already binds is resolved, so asking what a call is a call on
    never reaches a third-party import to find out.
    """
    head, _, rest = dotted.partition('.')
    if head not in sys.stdlib_module_names:
        return None
    try:
        value = importlib.import_module(head)
    except ImportError:
        return None
    for part in filter(None, rest.split('.')):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value


def is_network_read(func, bound):
    """Whether a callee IS a network read, resolved rather than spelled.

    The receiver question `_is_launch` asks for a launch, asked here for
    the thing it proved was NOT one. Resolution is what makes this an arm
    rather than an allowance: `urlopen`, `from urllib.request import
    urlopen as fetch`, `import urllib.request as urlr` and a local bound
    from any of them are one object, and anything else that calls itself
    `urlopen` is not a member of it.
    """
    dotted = _resolve_dotted(func, bound)
    reads = _network_reads()
    if dotted is None:
        return False
    value = _live_object(dotted)
    return value is not None and id(value) in reads


def _mentions(node, names):
    """Whether any of `names` appears anywhere inside `node`."""
    return any(isinstance(inner, ast.Name) and inner.id in names
               for inner in ast.walk(node))


def deadline_reaches_a_child(function, name, callees, receivers, direct,
                             aliases, parameters):
    """Whether the deadline this signature takes can reach a child.

    Two ways, and both are the census's own question asked of a parameter
    instead of a call. The function PLACES a launch, so a caller has an
    undeclared way to bound the child it owns and the signature is the
    only place that shows. Or it HANDS the parameter — or a name computed
    from it — to a call the census has resolved to a child, or to another
    path function, which is the shape the concept's reachability takes.

    A METHOD is not a path function here however its name is spelled: the
    receiver is the subject of a method call, and the question for a
    receiver is the one above, not whether some class in the same file
    happens to define a method of that name.

    A function that does none of this cannot put the number it was given
    on a child, which is the whole of what the signature refusal stood in
    for. A parameter the CALLER fills is a third thing and not this one:
    that number is the caller's, and `_parameter_bound_faults` reads it
    at the caller's line.
    """
    if any(path._is_launch(call, receivers, direct, aliases)
           for call in ast.walk(function) if path._is_call(call)):
        return True
    derived = {name}
    for _ in range(3):
        for node in ast.walk(function):
            if (isinstance(node, ast.Assign) and node.value is not None
                    and _mentions(node.value, derived)):
                derived.update(target.id for target in node.targets
                               if isinstance(target, ast.Name))
    launched = path._launch_bound_names(function, receivers, direct, aliases)
    launched |= {'<launch>', *parameters}
    slots = path._child_wait_slots()
    for node in ast.walk(function):
        if not path._is_call(node) or not _mentions(node, derived):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in callees:
                return True
            continue
        if (isinstance(func, ast.Attribute) and func.attr in slots
                and path._dotted_key(func.value) in launched):
            return True
    return False
