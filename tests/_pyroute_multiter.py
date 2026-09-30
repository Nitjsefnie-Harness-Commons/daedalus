"""Multi-iterable builtin consumers for the Python routing guard.

A builtin that pairs or maps POSITIONAL ITERABLES reaches a routed value
through a pairing, and the operand funnel alone cannot pair it:
`consume_iterable` reduces an expression it does not recognise as a tracked
generator to the merge of what the expression yields, and a merge is not a
tuple, so a destructuring target reads nothing and the value behind it stays
invisible. Three builtin declarations reach that place, and what decides
each one is named here rather than spelled as a list of calls:

  POSITIONAL_PAIRING pairs N positional iterables into one N-tuple per step,
    so the element at tuple position j is the value operand j yields at that
    step. `zip` is the only builtin of this kind; `strict` is keyword-only
    and changes whether the longest operand's tail is reached, not the arity
    of the tuple a reached step carries.

  INDEXED_PAIRING yields an (index, value) pair whose index half counts from
    a caller-supplied start. `enumerate` is the only builtin of this kind,
    and it is why a keyword is in this set at all: its `start` is
    positional-or-keyword, so a reader that took every positional operand as
    an iterable would read the index base as a second stream. The index half
    is an integer at runtime, so it carries no sender; the base is read
    anyway, because a base the model holds a routed value in reaches the
    tuple the same way an operand does.

  PROJECTION projects each step's tuple through a callable and yields the
    RESULT, not the operand element. `map` is the only builtin of this kind;
    its first operand is that callable and is not an iterable, and `strict`
    (3.14 and later) is keyword-only. The projection is walked, so the sends
    inside it are the flow's own, and its return value is the element.

The set is closed over CPython's builtin namespace and over the two keywords
that change a step's arity or its indexing; the member a new builtin of one
of these kinds would carry is the name alone, and the battery in
`test_tab_routing_multiterable.py` is generated from the declarations, so a
member added to one is read by the same two functions every other member of
that kind is read by.

Two spellings decide the arity of every step at runtime, and the guard
cannot read either: a `**` keyword unpacking and a starred positional
operand. `zip(*iterables)` pairs a count the guard cannot name -- the
operand is a container OF iterables, and a container of iterables is not a
stream -- so the element it yields is unprovable at every position rather
than at the one position the guard would have guessed.

The boundary is the module path, and it is stated rather than closed. The
same three declarations are spelled again in `itertools` -- `chain`,
`starmap`, `zip_longest`, `product`, `pairwise` and `tee` among them -- and
this guard models none of them. It knows an import only as a BINDING: the
name it introduces is marked bound and carries no value, which is what keeps
`zip` from being read as a name the file defined. It never records the module
an import came from, and it reads `ast.Name.id` and `ast.Attribute.attr`,
never a `func.value`-rooted module path, so `itertools.chain` is read as the
attribute `chain` on a name the guard holds nothing for. A
`from itertools import chain` in a routed file is therefore silent here,
exactly as it was before this module existed. What this arm closes is the
bare-name route only.
"""
import ast
from dataclasses import dataclass

from _pyroute_state import UNPROVABLE_SENDER, evaluated_value
from _pyroute_values import (
    DYNAMIC_KEY, DeferredAlternatives, DeferredContainer, callable_candidates,
    expression_callables, generator_for, is_deferred_value, merge_yielded,
    sender_value)


@dataclass(frozen=True)
class _Stream:
    """One operand of a multi-iterable consumer, read the way the consumer
    iterates it: the routed values at the positions the model can name, the
    value it cannot place against one, and how many steps there are. A
    length of None is a count the model has lost, and every position of such
    an operand is undecided."""
    positions: dict
    length: int | None
    unplaced: object = None


@dataclass(frozen=True)
class _Declaration:
    """One builtin declaration, by what it decides. `members` is the closed
    set of builtin names carrying it, and `operands` and `value` are what
    the declaration decides about a call, so a member added to a kind is
    read by the same two functions every other member of that kind is."""
    decides: str
    members: frozenset
    keywords: frozenset
    operands: object
    value: object


def _routed_item(value):
    """The value a position contributes, or None when it routes nothing."""
    return value if (is_deferred_value(value)
                     or sender_value(value) is not None) else None


def _join_streams(streams):
    """One stream reading every branch, or one that names no step: branches
    that disagree on the count leave it unknown rather than picking one."""
    lengths = {stream.length for stream in streams}
    positions = {}
    for stream in streams:
        for index, value in stream.positions.items():
            positions[index] = merge_yielded((positions.get(index), value))
    return _Stream(positions, lengths.pop() if len(lengths) == 1 else None,
                   merge_yielded(stream.unplaced for stream in streams
                                 if stream.unplaced is not None))


def _container_stream(value):
    """The stream a tracked container yields, or None when the value is not
    one. Iterating a mapping yields its keys, and iterating a set yields
    values in no order either can name, so a set contributes only its
    unplaced join: the pairing reads a step, and a set does not say which of
    its elements that step takes."""
    if isinstance(value, DeferredAlternatives):
        streams = [_container_stream(item) for item in value.values]
        if any(stream is None for stream in streams):
            return None
        return _join_streams(streams)
    if not isinstance(value, DeferredContainer):
        return None
    dynamic = value.items.get(DYNAMIC_KEY)
    length = None if dynamic is not None else value.length
    if value.kind == 'set':
        return _Stream({}, None, merge_yielded(value.items.values()))
    if value.kind == 'dict':
        return _Stream(
            {index: _routed_item(key) for index, (key, _) in enumerate(
                entry for entry in value.items.items()
                if entry[0] is not DYNAMIC_KEY)}, length, dynamic)
    positions, unplaced = {}, [] if dynamic is None else [dynamic]
    for key, item in value.items.items():
        if isinstance(key, int) and key >= 0:
            positions[key] = _routed_item(item)
        else:
            unplaced.append(item)
    return _Stream(positions, length,
                   merge_yielded(unplaced) if unplaced else None)


def _operand_stream(operand, state):
    """The stream one operand contributes. An operand the model holds no
    container for is not an empty stream: it is one the model cannot read,
    and it contributes the uncertainty token, because the pairing a consumer
    performs still pairs it and the tuple still reaches the target."""
    generator = generator_for(operand, state)
    if generator is not None:
        return _Stream({0: generator.yielded} if generator.yielded is not None
                       else {}, generator.remaining)
    return _container_stream(evaluated_value(operand, state)) \
        or _Stream({}, None, UNPROVABLE_SENDER)


def _operand_element(operand, state):
    """The join of every value one operand yields, which is what a step
    reads from it."""
    stream = _operand_stream(operand, state)
    return merge_yielded((*stream.positions.values(),
                          *((stream.unplaced,)
                            if stream.unplaced is not None else ())))


def _paired_step(streams, index, arity):
    """The tuple one step of a positional pairing holds, or the join of every
    step where the model cannot name which one it is reading. A value the
    model cannot place against the step joins the unknown-key slot, which
    every read of the tuple consults, so it reaches whichever position it
    lands in rather than only the one it was modelled at."""
    items = {}
    for position, stream in enumerate(streams):
        if index is not None and index in stream.positions:
            items[position] = stream.positions[index]
        else:
            joined = merge_yielded(
                stream.positions.values() if index is None else ())
            items[position] = (stream.unplaced if joined is None else joined)
        if stream.unplaced is not None:
            items[DYNAMIC_KEY] = merge_yielded(
                (items.get(DYNAMIC_KEY), stream.unplaced))
    return DeferredContainer(items, arity, 'tuple')


def _step_elements(streams, arity):
    """The container a consumer's elements hold: one tuple per step the
    model can name, or the join of them at every position where it cannot.
    A pairing over no operand reaches no step at all, which is a consumer
    the runtime walks zero times rather than one holding nothing."""
    lengths = [stream.length for stream in streams]
    if all(isinstance(length, int) for length in lengths):
        steps = min(lengths)
        if steps == 0:
            return None
        return DeferredContainer(
            {index: _paired_step(streams, index, arity)
             for index in range(steps)}, steps, 'list')
    step = _paired_step(streams, None, arity)
    return DeferredContainer({0: step, DYNAMIC_KEY: step}, None, 'list')


def _unreadable_elements():
    """The elements a consumer yields when no position of a step is decided.
    They are the token read whole and a tuple of unstated length whose every
    position is the token, because a destructuring target reads positions of
    a tuple of a length it can count -- a token in a one-tuple read against a
    two-element target pairs nothing at all, which is the false green this
    arm exists to remove -- and an arity the caller could not decide is
    exactly why the length is not stated."""
    step = DeferredContainer({DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'tuple')
    return _element(DeferredAlternatives((UNPROVABLE_SENDER, step)))


def _element(value):
    """One element the model holds nowhere in particular, at every position
    a consumer walks it in."""
    return DeferredContainer({0: value, DYNAMIC_KEY: value}, None, 'list')


# pylint: disable-next=unused-argument
def _positional_value(node, state, operands, analyze):
    """The elements a positional pairing yields."""
    streams = [_operand_stream(operand, state) for operand in operands]
    return _step_elements(streams, len(operands))


def _indexed_operands(node):
    """The one iterable an indexed pairing iterates. The second positional
    operand and the `start` keyword are the index base and not a second
    stream, and a further operand is a call the runtime refuses."""
    return list(node.args[:1]) if len(node.args) <= 2 else None


def _indexed_value(node, state, operands, analyze):
    """The elements an indexed pairing yields: the index base, and what the
    one operand contributes."""
    stream = _operand_stream(operands[0], state)
    base = next((keyword.value for keyword in node.keywords
                 if keyword.arg == 'start'), None)
    start = (_routed_item(evaluated_value(base, state))
             if base is not None else None)

    def step(index):
        # `items` is keyed by a tuple position AND by the unknown-key slot,
        # whose key is an `object`, so the dict is left un-annotated: an
        # inferred `dict[int, ...]` is exactly the type error the ratchet
        # names.
        items: dict = {0: start, 1: stream.positions.get(index,
                                                         stream.unplaced)}
        if stream.unplaced is not None:
            items[DYNAMIC_KEY] = stream.unplaced
        return DeferredContainer(items, 2, 'tuple')

    if stream.length is not None:
        if stream.length == 0:
            return None
        return DeferredContainer(
            {index: step(index) for index in range(stream.length)},
            stream.length, 'list')
    item = DeferredContainer({0: start, 1: _operand_element(operands[0],
                                                            state)},
                             2, 'tuple')
    return _element(item)


def _projected_operands(node):
    """The iterables a projection pairs before it projects. The first
    operand is the callable, and `map` with none of its own is a call the
    runtime refuses."""
    return list(node.args[1:]) if len(node.args) > 1 else None


def _projected_value(node, state, operands, analyze):
    """The elements a projection yields: the RESULT of its callable, walked
    so that the sends inside it are the flow's own and its return value is
    what reaches the target. The walk cannot say which step produces which
    result, so the element is the join of them at every position."""
    streams = [_operand_stream(operand, state) for operand in operands]
    steps = _step_elements(streams, len(operands))
    if steps is None:
        return None
    func = node.args[0]
    candidates = (callable_candidates(evaluated_value(func, state))
                  or expression_callables(func, state))
    if not candidates:
        return _unreadable_elements()
    return _element(merge_yielded(
        _projection_result(candidate, node, operands, state, analyze)
        for candidate in candidates))


def _projection_result(candidate, node, operands, state, analyze):
    """What the projected callable returns when it is handed one step of the
    pairing. Its parameters take the elements the operands yield, which is
    what the runtime passes as that step's tuple, and the call is
    synthesised rather than found because no call node exists: `map` calls
    its callable without one."""
    call = ast.Call(func=node.args[0], args=list(operands), keywords=[])
    prepared = state.copy()
    for operand in operands:
        prepared.evaluated[id(operand)] = _operand_element(operand, state)
    return analyze(candidate, [prepared], call)[1]


POSITIONAL_PAIRING = _Declaration(
    'pairs N positional iterables into one N-tuple per step',
    frozenset({'zip'}), frozenset({'strict'}),
    lambda node: list(node.args), _positional_value)
INDEXED_PAIRING = _Declaration(
    'yields an index/value pair whose index counts from a start',
    frozenset({'enumerate'}), frozenset({'start'}),
    _indexed_operands, _indexed_value)
PROJECTION = _Declaration(
    'projects each step through a callable and yields the result',
    frozenset({'map'}), frozenset({'strict'}),
    _projected_operands, _projected_value)

MULTI_ITERABLE_DECLARATIONS = (POSITIONAL_PAIRING, INDEXED_PAIRING, PROJECTION)
_BY_NAME = {name: declaration
            for declaration in MULTI_ITERABLE_DECLARATIONS
            for name in declaration.members}


def _declaration_elements(declaration, node, state, analyze):
    if any(keyword.arg is None for keyword in node.keywords) \
            or any(isinstance(operand, ast.Starred) for operand in node.args):
        return _unreadable_elements()
    if any(keyword.arg is not None
           and keyword.arg not in declaration.keywords
           for keyword in node.keywords):
        return None
    operands = declaration.operands(node)
    return (declaration.value(node, state, operands, analyze)
            if operands else None)


def multi_iterable_elements(consumer, node, states, analyze):
    """The elements a multi-iterable builtin consumer yields, or None when
    the consumer is not one or the runtime reaches no step of it. A name
    shadowed in the source never reaches here: the caller reads the
    shadowing guards before it decides a name is the builtin, because a
    shadow decides the pairing at runtime and not the spelling that matched.
    """
    declaration = _BY_NAME.get(consumer)
    if declaration is None:
        return None
    return merge_yielded(
        _declaration_elements(declaration, node, state, analyze)
        for state in states)
