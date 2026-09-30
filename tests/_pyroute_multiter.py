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

Two spellings stand between an operand and the position it pairs at, and
they are read differently because they decide different things. A starred
positional operand is a count of streams AT THE PLACE THE SOURCE SPELLS IT:
`f(a, *b, c)` reaches `f` as `(a, *b, c)`, so a star whose container the
guard can read expands in place, between the operands spelled before it and
the operands spelled after it. A star whose container it cannot read is a
count it cannot name, and nothing at or after it can be placed -- the
operands spelled after it sit at positions that count decides -- so the step
is undecided at EVERY position and the element it yields is unprovable
everywhere rather than at the one position the guard would have guessed.

A `**` keyword unpacking passes KEYWORDS, so it never displaces a positional
stream: the operands spelled beside it keep the positions the runtime gives
them, and only the count is open. The count is undecided, so the token sits
in the unknown-key slot alone and does not make the tuple claim a length the
runtime does not pair over.

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

from _pyroute_containers import SpreadContainer
from _pyroute_keys import literal_iterable_cardinality
from _pyroute_state import UNPROVABLE_SENDER, evaluated_value
from _pyroute_values import (
    DYNAMIC_KEY, DeferredAlternatives, DeferredContainer, DeferredGenerator,
    callable_candidates, expression_callables, generator_for,
    is_deferred_value, merge_yielded, sender_value)


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
    # True for the one stream a star or a `**` MAY add and the model could
    # not decide on. It does not count toward the tuple's arity -- the arity
    # the runtime pairs over is the arity the model can NAME -- and it
    # contributes its value to the unknown-key slot alone, so the token
    # reaches every position without the tuple claiming a length the runtime
    # does not pair.
    optional: bool = False


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
        # A generator the model tracks but whose yielded value it does not
        # hold is a stream it cannot read, and it says so the way any other
        # unreadable operand does. Reading it as an EMPTY stream instead is
        # the fail-open this arm exists to remove: the runtime yields
        # something, the model simply does not know what.
        return _Stream({0: generator.yielded} if generator.yielded is not None
                       else {},
                       generator.remaining,
                       None if generator.yielded is not None
                       else UNPROVABLE_SENDER)
    return _container_stream(evaluated_value(operand, state)) \
        or _Stream({}, None, UNPROVABLE_SENDER)


def _stream_element(stream):
    """The join of every value one stream yields, which is what a step reads
    from it."""
    return merge_yielded((*stream.positions.values(),
                          *((stream.unplaced,)
                            if stream.unplaced is not None else ())))


# The one stream a `**` the model could not read contributes. It does not
# count toward the tuple's arity -- a `**` passes KEYWORDS, so the arity the
# runtime pairs over is the arity the model can NAME -- and it contributes
# its value to the unknown-key slot alone, so the token reaches every
# position without the tuple claiming a length the runtime does not pair.
_UNDECIDED = _Stream({}, None, UNPROVABLE_SENDER, optional=True)

# What one state answers when a star's container it could not read. The
# caller keeps it only when NO state could read the container, because a
# state that resolved one is better informed rather than differently so.
_CONTAINER_UNDECIDED = object()


def _comprehension_count(node):
    """How many steps a comprehension's or generator expression's result
    holds, read from the producer the source spells, or None when that count
    is not literal. `literal_iterable_cardinality` is the helper the guard
    already reads a generator's count with, so the two agree by construction
    rather than by a second rule.

    A container reached through a NAME has no node to read a producer from,
    and a comprehension RESULT is modelled as one spread item whose own
    length is a placeholder. Both are counts the model cannot state, so both
    are declined."""
    if not isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp,
                             ast.DictComp)) or not node.generators:
        return None
    return literal_iterable_cardinality(node.generators[0].iter)


def _producer_stream(node, state):
    """The stream a comprehension's or generator expression's PRODUCT is, or
    None when the producer is not a container the model holds.

    Every step of such an expression yields the same modelled value, so the
    product is that value -- and the PRODUCER is where it is read from, not
    from the `yielded` the model records for the expression. `yielded` is
    empty until the clause has bound its target, and the call is read both
    before and after that point, so a container read from it is decided on
    one reading of the call and declined on the other; the producer is the
    same literal display at every point the call is read.
    """
    if not isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp,
                             ast.DictComp)) or not node.generators:
        return None
    return _container_stream(evaluated_value(node.generators[0].iter, state))


def _repeated_streams(count, value):
    """The streams a container that yields the SAME modelled value once per
    element of another yields.

    A value repeated N times IS a container of N iterables -- which is the
    whole of what a star needs, and it is why a comprehension is not a
    one-element container to a reader that has to know how many streams stand
    at the star's position. A count the model cannot state leaves the
    container declined: a star over a container of unknown length is a count
    of streams at that position that nothing names."""
    if not isinstance(count, int) or count < 0:
        return None
    stream = _container_stream(value)
    return None if stream is None else [stream] * count


def _product_streams(node, state):
    """The streams a generator expression's or comprehension's RESULT yields
    as a container of iterables, or None when its length is not stated. A
    set result is declined whatever its length: equal elements collapse and
    the runtime picks the order, so a star over one is a count of streams
    whose order nothing states."""
    if isinstance(node, (ast.SetComp, ast.Set)):
        return None
    produced = _producer_stream(node, state)
    if produced is None:
        return None
    return _repeated_streams(_comprehension_count(node),
                             _stream_element(produced))


def _container_streams(value, node=None, state=None):
    """The streams a container OF ITERABLES yields, or None when the value is
    not one. `zip(*operables)` and `update(**sources)` both pair the
    CONTAINER'S ELEMENTS rather than the container, so reading one is what
    decides how many streams a step has; substituting a token there instead
    is a value the model HOLDS and cannot PAIR, which is the fail-open this
    arm exists to remove. An element that is not a readable iterable is one
    operand the call has and the model cannot place, and it says so.

    What the container is SPELLED as does not decide any of this. A list, a
    tuple, a mapping, a generator expression, a comprehension and a name
    bound to any of them are all containers of iterables, and a reader that
    recognises two of them and declines the rest reads clean on the ones it
    declines -- which is the same fail-open wearing a different container.
    `node` is the expression the container is spelled as and `state` is the
    flow it is read in; a comprehension's contents come from the producer
    the node spells, because that is the one reading of them that does not
    depend on how far this state has walked."""
    if isinstance(value, DeferredAlternatives):
        branches = [_container_streams(item, node, state)
                    for item in value.values]
        return None if any(branch is None for branch in branches) else branches
    if isinstance(value, (DeferredGenerator, SpreadContainer)):
        return _product_streams(node, state)
    if not isinstance(value, DeferredContainer):
        return None
    if DYNAMIC_KEY in value.items:
        return None
    if value.kind == 'set':
        return None
    # An element that is not itself a readable iterable means the container
    # is not a container OF ITERABLES. For a `**` that is the ordinary
    # case -- `zip(a, b, **{"strict": True})` passes a KEYWORD, and reading
    # its value as a third stream would pair a stream the runtime never
    # pairs -- so the count is undecided rather than guessed. Iterating a
    # MAPPING yields its KEYS and never its values, so the keys are what is
    # read there; reading the values pairs a stream the runtime never pairs.
    elements = (value.items if value.kind == 'dict'
                else tuple(value.items.values()))
    streams = [_container_stream(item) for item in elements]
    return None if any(stream is None for stream in streams) else streams


def _keyword_streams(node, state):
    """The streams a `**` unpacking contributes, and whether the model could
    decide the count at all. It could when it can read the unpacked value as
    a container of iterables -- a mapping the model holds. It could NOT when
    that value is one the model holds nothing for, and the count is then
    undecided -- which is a statement about how many streams a step pairs
    over, not about what any of them holds, so the operand the call spelled
    plainly is still read.

    A `**` is here and a starred positional operand is not, because they
    decide different things. A `**` passes KEYWORDS, so it never displaces a
    positional stream: the operands spelled beside it keep the positions the
    runtime gives them, and only the count is open. A star is a count of
    POSITIONAL streams standing at the place the source spells it."""
    extra = []
    for keyword in node.keywords:
        if keyword.arg is None:
            read = _container_streams(
                evaluated_value(keyword.value, state), keyword.value,
                state)
            if read is None:
                return extra, False
            extra.extend(read)
    return extra, True


def _positional_streams(operands, state):
    """The streams the positional operands contribute, in the order the
    runtime pairs them, or None when one of them is a star the model cannot
    read.

    `f(a, *b, c)` reaches `f` as `(a, *b, c)`: a starred operand expands IN
    PLACE, so the streams its container holds sit between the operands
    spelled before it and the operands spelled after it. Appending them at
    the end models a different call, and it models it wrongly in the
    direction that reads clean -- the routed value is placed at a position
    the runtime never gives it, and the position the runtime does give it
    holds something else. That is the same order `bind_call_arguments`
    already reads a call's own operand list in.

    A star whose container the model cannot read stands for a count it
    cannot name, and nothing at or after it can be placed: the operands
    spelled after it sit at positions that count decides. There are only
    two things a model can do with a container it cannot read, and this is
    the first -- decline to place anything, rather than invent a position
    for a count it does not have. The caller answers it with the elements no
    position of a step is decided in."""
    streams = []
    for operand in operands:
        if not isinstance(operand, ast.Starred):
            streams.append(_operand_stream(operand, state))
            continue
        read = _container_streams(
            evaluated_value(operand.value, state), operand.value, state)
        if read is None:
            return None
        streams.extend(read)
    return streams


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


def _step_elements(streams):
    """The container a consumer's elements hold: one tuple per step the
    model can name, or the join of them at every position where it cannot.
    A pairing over no operand reaches no step at all, which is a consumer
    the runtime walks zero times rather than one holding nothing. The arity
    is the arity the model can NAME, which is what leaves a stream the model
    could not decide on out of the count."""
    arity = sum(1 for stream in streams if not stream.optional)
    lengths = [stream.length for stream in streams
               if not stream.optional]
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
def _positional_value(node, state, streams, analyze):
    """The elements a positional pairing yields. The arity is left to
    `_step_elements`, which is what knows a stream the model could not
    decide on does not count toward it."""
    return _step_elements(streams)


def _indexed_operands(node):
    """The one iterable an indexed pairing iterates. The second positional
    operand and the `start` keyword are the index base and not a second
    stream, and a further operand is a call the runtime refuses."""
    return list(node.args[:1]) if len(node.args) <= 2 else None


# pylint: disable-next=unused-argument
def _indexed_value(node, state, streams, analyze):
    """The elements an indexed pairing yields: the index base, and what the
    one operand contributes."""
    stream = streams[0]
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
    item = DeferredContainer({0: start, 1: _stream_element(stream)},
                             2, 'tuple')
    return _element(item)


def _projected_operands(node):
    """The iterables a projection pairs before it projects. The first
    operand is the callable, and `map` with none of its own is a call the
    runtime refuses."""
    return list(node.args[1:]) if len(node.args) > 1 else None


# pylint: disable-next=unused-argument
def _projected_value(node, state, streams, analyze):
    """The elements a projection yields: the RESULT of its callable, walked
    so that the sends inside it are the flow's own and its return value is
    what reaches the target. The walk cannot say which step produces which
    result, so the element is the join of them at every position."""
    steps = _step_elements(streams)
    if steps is None:
        return None
    func = node.args[0]
    candidates = (callable_candidates(evaluated_value(func, state))
                  or expression_callables(func, state))
    if not candidates:
        return _unreadable_elements()
    return _element(merge_yielded(
        _projection_result(candidate, node, streams, state, analyze)
        for candidate in candidates))


def _projection_result(candidate, node, streams, state, analyze):
    """What the projected callable returns when it is handed one step of the
    pairing. Its parameters take the elements the streams yield, which is
    what the runtime passes as that step's tuple, and the call is
    synthesised rather than found because no call node exists: `map` calls
    its callable without one. The arguments are synthetic NAMES carrying the
    element of the stream at that position, so a stream the arm read out of
    a starred operand binds exactly like one the source spelled plainly."""
    # The list is annotated because `ast.Call` declares `args` as
    # `list[expr]` and a comprehension of `ast.Name` infers `list[Name]`,
    # which is not assignable to it. The names themselves are unchanged.
    arguments: list[ast.expr] = [
        ast.Name(id=f'_pair{position}', ctx=ast.Load())
        for position in range(len(streams))]
    call = ast.Call(func=node.args[0], args=arguments, keywords=[])
    prepared = state.copy()
    for argument, stream in zip(arguments, streams):
        prepared.evaluated[id(argument)] = _stream_element(stream)
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
    if any(keyword.arg is not None
           and keyword.arg not in declaration.keywords
           for keyword in node.keywords):
        return None
    operands = declaration.operands(node)
    if not operands:
        return None
    streams = _positional_streams(operands, state)
    if streams is None:
        # A star the model could not read decides how many streams stand at
        # the place the source spells it, and it is a count it cannot name.
        # The step is then unplaceable at EVERY position -- the operands
        # spelled after that star are at positions the count decides, and
        # the tuple the model would otherwise build claims a length the
        # runtime never pairs over, so a destructuring target reads a
        # different number of names from it and the pair is refused
        # outright. The caller answers it, because whether ANY state could
        # read the container is not a question one state can answer.
        return _CONTAINER_UNDECIDED
    extra, decided = _keyword_streams(node, state)
    if not streams and not extra:
        return None
    if decided:
        return declaration.value(node, state, [*streams, *extra], analyze)
    # The count a `**` decides is not one the model could read, and a `**`
    # displaces nothing: the operand the call spelled PLAINLY still is, so it
    # is still read and the undecided one sits beside it carrying the token
    # -- a value the model holds and cannot place, at the position it cannot
    # place it.
    return declaration.value(
        node, state, [*streams, *extra, _UNDECIDED], analyze)


def multi_iterable_elements(consumer, node, states, analyze):
    """The elements a multi-iterable builtin consumer yields, or None when
    the consumer is not one or the runtime reaches no step of it. A name
    shadowed in the source never reaches here: the caller reads the
    shadowing guards before it decides a name is the builtin, because a
    shadow decides the pairing at runtime and not the spelling that matched.

    A state that could not read a star's container does not decide the call
    on its own. The states are one expression read at more than one point,
    and the one that resolved the container's contents is strictly better
    informed than the one that did not -- so a decline is kept only when NO
    state could read it, and is answered then with the elements no position
    of a step is decided in. Deciding per state instead lets an earlier,
    emptier reading of the same container override a later complete one, and
    the target then binds an undecided value where the model held a routed
    one, which reads clean.
    """
    declaration = _BY_NAME.get(consumer)
    if declaration is None:
        return None
    elements = [_declaration_elements(declaration, node, state, analyze)
                for state in states]
    read = [element for element in elements
            if element is not _CONTAINER_UNDECIDED]
    if read:
        return merge_yielded(read)
    return None if not elements else _unreadable_elements()
