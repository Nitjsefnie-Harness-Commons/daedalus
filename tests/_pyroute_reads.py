"""The read half of a deferred value: what an expression reads.

Every read of a value the model tracks is resolved here -- a position, an
attribute, a literal key, a whole mapping literal, a receiver expression, a
mapping read-back -- and answers from the storage the model recorded rather
than from the source text alone. A read the model cannot name joins the
unknown slot instead of naming one value. `pop` is the one write, and it is
here because `resolve_expression_value` reaches it while resolving the read
that names the key it removes, and `popitem` for the same reason. The store
side is `_pyroute_mapping` and `_pyroute_stores`.
"""
import ast

from _pyroute_containers import SpreadContainer, iterated_key
from _pyroute_indexing import reversed_read, static_slice_read
from _pyroute_keys import (_UNRESOLVED_KEY, _literal_key,
                           _unhashable_key_sender)
from _pyroute_pop import _unknown_lookup_default
from _pyroute_positions import (at_position, from_position,
                                sequence_method_value)
from _pyroute_setops import fold_set_operation, set_operands
from _pyroute_storage import (container_copy, dict_length, fold_dynamic,
                              replace_deferred_storage, retired_into,
                              stale_after_store)
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,
                             DeferredAlternatives, DeferredClass,
                             DeferredContainer, DeferredGenerator,
                             DeferredInstance, _known_value,
                             is_deferred_value, mapping_lookup_owner,
                             merge_yielded, sender_value)


def _selected_values(value, key, attribute=False):
    if isinstance(value, DeferredAlternatives):
        return [selected for item in value.values
                for selected in _selected_values(item, key, attribute)]
    if attribute and isinstance(value, DeferredInstance):
        return [value.attributes.get(key)]
    if attribute and isinstance(value, DeferredClass):
        return [value.methods.get(key)]
    if not attribute and isinstance(value, DeferredContainer):
        return at_position(value, key)
    return []


def _display_value(node, state):
    """Positions after a star of unknown length share the DYNAMIC_KEY slot."""
    items = {}
    index = 0
    for item in node.elts:
        starred = isinstance(item, ast.Starred)
        value = _known_value(item.value if starred else item, state)
        if not starred:
            if index is None:
                fold_dynamic(items, value)
            else:
                if value is not None:
                    items[index] = value
                index += 1
        elif not isinstance(value, DeferredContainer):
            index = None
            fold_dynamic(items, UNPROVABLE_SENDER)
        elif value.kind == 'dict':
            # Unpacking a dict yields its keys, never the modelled values.
            index = (None if index is None or value.length is None
                     else index + value.length)
        elif index is None or value.length is None:
            index = None
            fold_dynamic(items, from_position(value, 0))
        else:
            for offset in range(value.length):
                nested = merge_yielded(at_position(value, offset))
                if nested is not None:
                    items[index + offset] = nested
            index += value.length
    starred_any = any(isinstance(item, ast.Starred) for item in node.elts)
    if isinstance(node, ast.Set) and (items or starred_any):
        # A set has no positions; equal elements collapse at runtime.
        joined = merge_yielded(items.values())
        return DeferredContainer(
            {} if joined is None else {DYNAMIC_KEY: joined}, None, 'set')
    if items or isinstance(node, ast.List) or starred_any:
        return DeferredContainer(items, index, type(node).__name__.lower(),
                                 star_display=index is None)
    return None


def _fold_items(target, source):
    for key, value in source.items():
        if key is DYNAMIC_KEY:
            fold_dynamic(target, value)
        else:
            target[key] = value


def _dict_value(node, state):
    items = {}
    stale = frozenset()
    counted = True
    for key, item in zip(node.keys, node.values):
        if key is not None:
            value = _known_value(item, state)
            literal = _literal_key(key, state)
            if literal is not _UNRESOLVED_KEY:
                items[literal] = value
                # A literal key the display writes settles it, whatever an
                # earlier fold retired there.
                stale = frozenset(
                    key for key in stale if key != literal)
            else:
                fold_dynamic(items, value)
            continue
        value = (_dict_value(item, state) if isinstance(item, ast.Dict)
                 else _known_value(item, state))
        if isinstance(value, DeferredContainer) and value.kind == 'dict':
            _fold_items(items, value.items)
            # The last fold at a key is the value the display holds, so a
            # source that carries a key un-retires whatever an earlier one
            # retired there.
            stale = ((stale - set(value.items))
                     | retired_into(value.stale, items))
            counted = counted and value.length is not None
        else:
            fold_dynamic(items, UNPROVABLE_SENDER)
    if len(node.keys) == 1 and node.keys[0] is not None:
        return DeferredContainer(items, 1, 'dict', node)  # one entry, one key
    return DeferredContainer(items, dict_length(items, counted), 'dict', node,
                             stale=stale)


def _merge_or_value(node, state):
    """Mapping value of `left | right` from the provable dict sides."""
    items = {}
    stale = frozenset()
    counted = True
    for side in (node.left, node.right):
        if isinstance(side, ast.Dict):
            known = _dict_value(side, state)
        else:
            known = _known_value(side, state)
        if isinstance(known, DeferredContainer) and known.kind == 'dict':
            _fold_items(items, known.items)
            stale = ((stale - set(known.items))
                     | retired_into(known.stale, items))
            counted = counted and known.length is not None
        else:
            fold_dynamic(items, UNPROVABLE_SENDER)
    if not items:
        return None
    return DeferredContainer(items, dict_length(items, counted), 'dict', node,
                             stale=stale)


def _dict_call_value(node, state):
    """Mapping value of a builtin dict() call, or None when untracked."""
    items = {}
    stale = frozenset()
    counted = True
    sources = node.args[:1] + [
        keyword.value for keyword in node.keywords if keyword.arg is None]
    for source in sources:
        known = _source_items(source, state)
        if known is None:
            fold_dynamic(items, UNPROVABLE_SENDER)
        else:
            _fold_items(items, known[0])
            stale = ((stale - set(known[0]))
                     | retired_into(known[2], items))
            counted = counted and known[1]
    for keyword in node.keywords:
        if keyword.arg is not None:
            items[keyword.arg] = _known_value(keyword.value, state)
            stale = stale.difference((keyword.arg,))
    if not items:
        return None
    return DeferredContainer(items, dict_length(items, counted), 'dict', node,
                             stale=stale)


def _setdefault_value(node, state):
    """The stored-or-existing item one setdefault call evaluates to."""
    owner = _known_value(node.func.value, state)
    key = _literal_key(node.args[0], state) if node.args else _UNRESOLVED_KEY
    default = _known_value(node.args[1], state) if len(node.args) > 1 else None
    if (not isinstance(owner, DeferredContainer)
            or (key is _UNRESOLVED_KEY and owner.kind != 'dict')):
        return merge_yielded((default, UNPROVABLE_SENDER)) \
            if default is not None else None
    if key is _UNRESOLVED_KEY:
        return merge_yielded((*owner.items.values(), default,
                              _unhashable_key_sender(
                                  node.args[0] if node.args else None,
                                  state)))
    return _mapping_lookup(owner, key, default)


def _mapping_lookup(owner, key, default):
    """A mapping read carrying a default.

    The default answers an absent key and nothing else, so a key the
    container holds keeps the value RECORDED there -- the value the model
    recorded, which is the value the runtime holds only while no
    unreadable source has since overwritten that key. A store whose source
    the model could not read may have named any key the container already
    held, so it RETIRED those keys; a retired one still contributes the
    value recorded there, and joins it with the unprovable sender, because
    a candidate the model can name is worth keeping beside one it cannot.
    An unknown-key slot joins the read as it always has. An absent key on
    a mapping the model cannot enumerate is not an absent key: an unknown
    length with no unknown-key slot says the key set holds entries the
    model never learned, so that read joins rather than answering the
    default for a key the runtime may well hold. This read answers from
    the container and has to join for itself, where the subscript of the
    same mapping reaches the model's own unprovable marking for the name
    -- but only where the STORE left one: a store that marks the owner as
    it folds the source is what the subscript picks up, and a store that
    merely propagates an unknown length from a source reached through a
    name marks the source name instead, leaving this read to join for
    itself and the subscript with nothing to reach."""
    if DYNAMIC_KEY in owner.items:
        return merge_yielded((*_selected_values(owner, key), default))
    if key in owner.stale:
        return merge_yielded((owner.items.get(key), UNPROVABLE_SENDER,
                              default))
    if key in owner.items:
        return owner.items[key]
    if owner.length is None:
        return merge_yielded((UNPROVABLE_SENDER, default))
    return default


def _mapping_item_value(node, owner, state):
    default = _known_value(node.args[1], state) if len(node.args) > 1 else None
    key = _literal_key(node.args[0], state) if node.args else _UNRESOLVED_KEY
    if key is not _UNRESOLVED_KEY: return _mapping_lookup(owner, key, default)
    return merge_yielded((*owner.items.values(), default,
                          _unhashable_key_sender(
                              node.args[0] if node.args else None, state)))


# Sequence builtins whose single argument is the pair sequence itself.
_PAIR_WRAPPERS = ('frozenset', 'list', 'set', 'tuple')

_MAPPING_READBACKS = ('values', 'items', 'popitem', 'copy')


def _readback_items(owner, pair):
    """The items one mapping read-back hands back, in the order recorded.

    A SNAPSHOT of the recorded items, not a live view of the mapping: a store
    the model records after the read is not in it (issue 1314). A read-back
    names no key, so every recorded value is a candidate and the recorded
    order is all that separates them. A value the model holds at the
    unknown-key slot cannot be placed among the rest, so it joins the
    unpositioned slot and takes the count with it. `pair` wraps each value in
    the (key, value) pair `items` really yields, and leaves the key half
    empty: a key routes nothing, and a callable the model holds as one is a
    missing datum tracked on its own.
    """
    def entry(value):
        return DeferredContainer({0: None, 1: value}, 2, 'tuple') \
            if pair else value
    items = {index: entry(stored) for index, stored in enumerate(
        value for key, value in owner.items.items() if key is not DYNAMIC_KEY)}
    if DYNAMIC_KEY in owner.items:
        fold_dynamic(items, entry(owner.items[DYNAMIC_KEY]))
    return DeferredContainer(items, dict_length(items), 'list')


def _readback_values(node, state, owner):
    return _readback_items(owner, False)


def _readback_items_arm(node, state, owner):
    return _readback_items(owner, True)


def _readback_copy(node, state, owner):
    """A mapping holding what the receiver holds, under its OWN identity.

    A copy given the original's identity is a second name for the one
    container, so a store through the copy rewrote the original and the
    value the model kept at a key it never wrote came back on the next read
    of the receiver. The items and the length are what a copy holds; only
    the identity is new.
    """
    items = dict(owner.items)
    return DeferredContainer(
        items, dict_length(items, owner.length is not None), 'dict', node,
        stale=owner.stale)


def _popitem_entry(owner):
    """The (key, value) pair one popitem call removes, or None.

    popitem takes the LAST entry, and the model records its mappings in
    insertion order, so a mapping whose key set it can account for says
    exactly which pair went. A mapping carrying the unknown-key slot, or one
    whose count it lost, holds entries whose order against the recorded ones
    it cannot name, so it returns None and the caller falls back to the whole
    recorded key set.
    """
    if DYNAMIC_KEY in owner.items or owner.length is None:
        return None
    return next(reversed(list(owner.items.items())))


def _readback_popitem(node, state, owner):
    """The pair one popitem call returns, and the mapping it leaves.

    The value half is the last entry, because that is the one the call
    returns; where the model cannot say which entry that is, it is every
    value the mapping holds, because the call takes one of them. The removal
    is applied the way `pop` applies its own -- a mapping the model can
    account for loses exactly the entry taken, and one it cannot keeps what
    it recorded and joins the removed entry to the unknown-key slot, the only
    account of a key set with one entry the model cannot name taken out of
    it.
    """
    if owner.length == 0:
        return None            # an empty mapping has no pair to pop
    taken = _popitem_entry(owner)
    if taken is None:
        pair = DeferredContainer(
            {0: None, 1: merge_yielded(owner.items.values())}, 2, 'tuple',
            node)
        items = dict(owner.items)
        fold_dynamic(items, UNPROVABLE_SENDER)
        replace_deferred_storage(state, owner, container_copy(
            owner, items, True))
        return pair
    key, value = taken
    items = dict(owner.items)
    del items[key]
    replace_deferred_storage(state, owner, container_copy(owner, items))
    return DeferredContainer({0: None, 1: value}, 2, 'tuple', node)


# One member per read-back, and the membership test IS this table: a name
# that has no arm here is not a read-back the reader consults, so dropping a
# member drops its rows with it.
_READBACK_ARMS = {
    'values': _readback_values,
    'items': _readback_items_arm,
    'popitem': _readback_popitem,
    'copy': _readback_copy,
}
_MAPPING_READBACKS = tuple(_READBACK_ARMS)


def _mapping_readback(node, state, readback):
    """The value one mapping read-back evaluates to, or None.

    A receiver that is not a tracked mapping is not one of these operations,
    so a read-back through one is left to the arms that answer a name.
    """
    owner = _known_value(node.func.value, state)
    if not isinstance(owner, DeferredContainer) or owner.kind != 'dict':
        return None
    return _READBACK_ARMS[readback](node, state, owner)


def resolve_expression_value(node, state, generator_factory, sender_resolver,
                             unprovable_sender):
    """A pop's and a popitem's removals are applied here, at evaluation, once
    per node per state; every other resolution leaves the state as it found
    it."""
    # A read-back resolves before the cache, which holds the RECEIVER for a
    # call whose receiver is a call result, and a read-back of that receiver
    # is not what the call evaluates to.
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in _MAPPING_READBACKS:
        readback = _mapping_readback(node, state, node.func.attr)
        if readback is not None: return readback
    known = _known_value(node, state)
    if known is not None: return known
    if isinstance(node, ast.GeneratorExp):
        return generator_factory(node)
    if isinstance(node, ast.Name) and node.id in state.generators:
        return state.generators[node.id]
    if isinstance(node, ast.Name) and node.id in state.callables:
        return state.callables[node.id]
    if isinstance(node, (ast.NamedExpr, ast.Starred)):
        value = state.evaluated.get(id(node.value))
        if value is not None:
            return value
    if isinstance(node, (ast.IfExp, ast.BoolOp)):
        value = merge_yielded((sender_resolver(node, state.aliases),
                               *(state.evaluated.get(id(child))
                                 for child in ast.iter_child_nodes(node))))
        if value is not None:
            return value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        value = _display_value(node, state)
        if value is not None:
            return value
        values = [state.evaluated.get(id(item)) for item in node.elts]
        if any(item is not None and not isinstance(item, DeferredGenerator)
               for item in values):
            return unprovable_sender
    if isinstance(node, ast.Dict):
        return _dict_value(node, state) or sender_resolver(
            node, state.aliases)
    if isinstance(node, (ast.ListComp, ast.SetComp)):
        value = state.evaluated.get(id(node.elt))
        iterated = state.evaluated.get(iterated_key(node))
        if is_deferred_value(value) or iterated is not None:
            return SpreadContainer(
                {0: value}, 1, type(node).__name__[:-4].lower(),
                iterated=iterated)
    if isinstance(node, ast.DictComp):
        value = state.evaluated.get(id(node.value))
        if is_deferred_value(value) or sender_value(value) is not None:
            return SpreadContainer({DYNAMIC_KEY: value}, None, 'dict', node)
    if isinstance(node, ast.Subscript):
        owner = _known_value(node.value, state)
        value = static_slice_read(node, owner)
        if value is not None:
            return value
        key = _literal_key(node.slice, state)
        # A dict read the guard cannot resolve has no position to name, so
        # every item is a candidate; every other owner keeps the lookup that
        # recurses into alternatives and reads a list by position.
        if key is _UNRESOLVED_KEY and isinstance(owner, DeferredContainer) \
                and owner.kind == 'dict':
            value = merge_yielded((*owner.items.values(),
                                  _unhashable_key_sender(node.slice, state)))
        else:
            value = merge_yielded(_selected_values(owner, key))
        if value is not None:
            return value
    if isinstance(node, ast.BinOp):
        operands = set_operands(node.op, node.left, node.right, state)
        if operands is not None:
            return fold_set_operation(node.op, operands, node)
        if isinstance(node.op, ast.BitOr):
            return _merge_or_value(node, state)
    if isinstance(node, ast.Attribute):
        owner = _known_value(node.value, state)
        value = merge_yielded(
            _selected_values(owner, node.attr, attribute=True))
        if value is not None:
            return value
    if isinstance(node, ast.Call):
        owner = _known_value(node.func, state)
        if isinstance(owner, DeferredClass):
            return DeferredInstance(dict(owner.methods))
        if isinstance(node.func, ast.Name) and node.func.id == 'dict' \
                and 'dict' not in state.builtin_globals \
                and 'dict' not in state.builtin_locals:
            return _dict_call_value(node, state)
        value = reversed_read(node, state) or sequence_method_value(
            node, state)
        if value is not None:
            return value
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr == 'setdefault'):
            return _setdefault_value(node, state)
        owner = mapping_lookup_owner(node, state)
        if owner is not None:
            value = _mapping_item_value(node, owner, state)
            _apply_pop(state, node)
            return value
        default = _unknown_lookup_default(node, state)
        if default is not None:
            return merge_yielded((default, UNPROVABLE_SENDER))
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr == 'fromkeys' \
                and isinstance(node.func.value, ast.Name):
            static_dict = (node.func.value.id == 'dict'
                           and 'dict' not in state.builtin_globals
                           and 'dict' not in state.builtin_locals)
            known = _known_value(node.args[1], state) \
                if len(node.args) > 1 else None
            if static_dict and known is not None:
                return DeferredContainer(
                    {DYNAMIC_KEY: known}, None, 'dict', node)
    return sender_resolver(node, state.aliases)


def _literal_pair_items(source, state):
    """Occupancy a syntactic sequence of pairs contributes for its literal
    keys, by position, and whether a modelled position is that pair's own.

    The deferred container for a literal list carries the display length but
    not its items, so occupancy is recovered from the source text; a pair
    the loop could name no key for still joins the unknown-key slot. A pair
    value that is an unresolvable call is stored unprovable, as a keyword
    store does. A star splices the display's positions, so the entries fold
    beside the dynamic slot but no position names the pair it sits at.
    """
    entries, aligned = {}, True
    if not isinstance(source, (ast.List, ast.Tuple, ast.Set)):
        return entries, False
    for position, pair in enumerate(source.elts):
        if isinstance(pair, ast.Starred):
            aligned = False
            continue
        if not isinstance(pair, (ast.Tuple, ast.List, ast.Set)) \
                or len(pair.elts) != 2:
            continue
        key = _literal_key(pair.elts[0], state)
        if key is _UNRESOLVED_KEY:
            continue
        value = _known_value(pair.elts[1], state)
        if value is None and isinstance(pair.elts[1], ast.Call):
            value = UNPROVABLE_SENDER
        entries[position] = (key, value)
    return entries, aligned


def _spelled_pair_items(source, state):
    """The pairs a source spells in its own syntax, and whether it spells
    every one of them; None marks a source of some other shape.

    Two shapes name their keys outright. A `zip` of two equal-length
    literal columns pairs them by position, which is exactly what `zip`
    does, and taking the key from the FIRST column is what keeps the fold
    exact: a value-blind scan would put `relay()` at `"k"` and manufacture
    a false positive on a source that only ever wrote `"j"`. A
    single-argument sequence wrapper over a literal sequence of two-element
    pairs spells the same pairs one layer down from the call.

    A key the model cannot resolve leaves the source partly unaccounted,
    and a source that spells no pair at all accounts for nothing: an empty
    `zip` is no more an account than an opaque source is, and declining it
    is what keeps a store that folds one from claiming a key set it never
    computed.
    """
    if not (isinstance(source, ast.Call)
            and isinstance(source.func, ast.Name) and not source.keywords):
        return None
    if (source.func.id in state.builtin_globals
            or source.func.id in state.builtin_locals):
        return None
    if source.func.id == 'zip' and len(source.args) == 2:
        first, second = source.args
        if not isinstance(first, (ast.List, ast.Tuple, ast.Set)) \
                or not isinstance(second, (ast.List, ast.Tuple, ast.Set)):
            return None
        if not first.elts or len(first.elts) != len(second.elts):
            return None
        pairs = list(zip(first.elts, second.elts))
    elif source.func.id in _PAIR_WRAPPERS and len(source.args) == 1:
        argument = source.args[0]
        if not isinstance(argument, (ast.List, ast.Tuple, ast.Set)) \
                or not argument.elts:
            return None
        pairs = []
        for element in argument.elts:
            if not isinstance(element, (ast.Tuple, ast.List, ast.Set)) \
                    or len(element.elts) != 2:
                return None
            pairs.append((element.elts[0], element.elts[1]))
    else:
        return None
    items, counted = {}, True
    for key_node, value_node in pairs:
        key = _literal_key(key_node, state)
        if key is _UNRESOLVED_KEY:
            counted = False
            continue
        value = _known_value(value_node, state)
        if value is None and isinstance(value_node, ast.Call):
            value = UNPROVABLE_SENDER
        items[key] = value
    return items, counted


def _pair_sequence_items(known, source, state):
    """The pairs a tracked sequence source yields, and whether its yield is
    all of them; None when the sequence is not one this can pair."""
    items = {}
    entries, aligned = _literal_pair_items(source, state)
    for position, value in known.items.items():
        # A slot holding alternatives may hold any one of them as its pair.
        for pair in (value.values if isinstance(
                value, DeferredAlternatives) else (value,)):
            if not isinstance(pair, DeferredContainer):
                return None
            if pair.kind not in ('list', 'tuple'):
                return None
            if aligned and position in entries \
                    and pair.length == 2 and DYNAMIC_KEY not in pair.items:
                # A pair whose key the source folds is stored under that
                # key below, so its value is not an unknown-key entry.
                continue
            if pair.length != 2 or DYNAMIC_KEY in pair.items:
                fold_dynamic(items, from_position(pair, 0))
            else:
                # A modelled key is a callable or a sender, never a key.
                fold_dynamic(items, pair.items.get(1))
    # The last pair written at a key is the one the dict holds (`1` and
    # `True` one key there), and only the dynamic slot precedes it.
    for key, value in entries.values():
        items[key] = value
    return items, len(known.items) == known.length


def _source_items(source, state):
    """Items one mapping store contributes, whether their keys are all
    counted, and which of them the source has already retired; None marks
    unknown contents.

    The retirement travels with the items because it is a fact about them:
    a destination built out of these values cannot claim at a retired key
    that its own value there is current."""
    known = (_dict_value(source, state) if isinstance(source, ast.Dict)
             else _known_value(source, state))
    if isinstance(known, DeferredContainer):
        if known.kind == 'dict':
            return known.items, known.length is not None, known.stale
        if known.kind in ('list', 'tuple', 'set'):
            paired = _pair_sequence_items(known, source, state)
            if paired is not None:
                # A pair source is a sequence, so it retires nothing: the
                # keys the destination ends up holding are named here.
                return *paired, frozenset()
    spelled = _spelled_pair_items(source, state)
    return None if spelled is None else (*spelled, frozenset())


def _pop_key(call, state):
    """The literal key one pop names, or _UNRESOLVED_KEY."""
    return _literal_key(call.args[0], state) if call.args \
        else _UNRESOLVED_KEY


def _apply_pop(state, call):
    """The mapping one pop call removes a key from, or None when this call is
    not a mutation the model follows: no tracked mapping behind it, so the
    general invalidation is what answers for it.

    A pop takes an entry out and writes nothing anywhere, so the entries it
    leaves keep the values the model recorded -- the key it names stops
    being retired along with being held, and every other one is untouched
    even when the key it took is one the model could not resolve."""
    owner = mapping_lookup_owner(call, state)
    if owner is None or call.func.attr != 'pop':
        return None
    key = _pop_key(call, state)
    items = dict(owner.items)
    if key is not _UNRESOLVED_KEY:
        items.pop(key, None)
    elif owner.kind != 'dict':
        return None
    replace_deferred_storage(state, owner, container_copy(
        owner, items, key is _UNRESOLVED_KEY, stale_after_store(
            owner, () if key is _UNRESOLVED_KEY else (key,))))
    return owner


def _receiver_value(receiver, state):
    """The deferred value a receiver expression names.

    A named expression and a class or instance attribute resolve through the
    storage the model records for them; a name resolves through its own
    binding, so an alias reaches the one container it shares.
    """
    if isinstance(receiver, ast.NamedExpr):
        receiver = receiver.value
    if isinstance(receiver, ast.Attribute):
        owner = _receiver_value(receiver.value, state)
        if isinstance(owner, DeferredClass):
            return owner.methods.get(receiver.attr)
        if isinstance(owner, DeferredInstance):
            return owner.attributes.get(receiver.attr)
    return _known_value(receiver, state)


def _attribute_selection(selection, state):
    """The receiver and method name a call that selects a method by name
    names, or None when the call is not such a selection. A name the model
    cannot fold is reported as None, so the selection is read as a mutation
    of whatever container the receiver may hold."""
    if isinstance(selection.func, ast.Attribute) \
            and selection.func.attr == '__getattribute__' \
            and len(selection.args) == 1:
        receiver, name = selection.func.value, selection.args[0]
    elif isinstance(selection.func, ast.Name) \
            and selection.func.id == 'getattr' \
            and 2 <= len(selection.args) <= 3 and not selection.keywords:
        receiver, name = selection.args[0], selection.args[1]
    else:
        return None
    if isinstance(name, ast.Constant) and isinstance(name.value, str):
        return receiver, name.value
    return receiver, None
