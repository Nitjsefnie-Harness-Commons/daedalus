"""A closing net over every handle on a body that carries a sender.

Keyed on containers, not names; covered only by what the walks recorded
or proved, so a spelling neither enumerates taints."""
import re

from _jsroute_net_read import (constructor_promotes, returns_promoting,
                               unkeyed_writes)
from _jsroute_operations import operation_call
from _jsroute_promote import Promotion
from _jsroute_receiver import next_offset
from _jsroute_source import (previous_nonspace, record_work,
                             word_before)


_IDENTIFIER = re.compile(r'(?<![\w$])([\w$]+)(?![\w$])')
_NEW_OF = re.compile(r'(?<![\w$])new\s+([\w$]+)')
_DECLARATOR = re.compile(
    r'(?<![\w$])(?:(?:const|let|var)(?:\s*[\w$]+\s*,)*'
    r'|class|function\s*\*?)\s+$')
# The two initializer shapes the sender walk reads in full: a name, and
# that name bound. Reading one is what accounts for the mention in it.
_READ_IN_FULL = re.compile(
    r'\s*[\w$]+\s*(?:\.\s*bind\s*\([\s\S]*\)\s*)?')
_BLOCK_BEFORE = re.compile(
    r'(?:[)}{;]|=>|(?<![\w$])(?:else|do|try|finally|import|export)'
    r'|(?<![\w$])class\b[^;{}()]*)\s*$')


def _enclosing_brackets(mask):
    found = [-1] * (len(mask) + 1)
    stack = []
    for position, char in enumerate(mask):
        if char in ')]}' and stack:
            stack.pop()
        found[position] = stack[-1] if stack else -1
        if char in '([{':
            stack.append(position)
    return found


def sender_pattern(senders):
    return re.compile(
        r'(?<![\w$])(?:' + '|'.join(map(re.escape, senders))
        + r')(?![\w$])')


class _Reader:
    """Answers whether a value carries a sender, failing closed."""

    def __init__(self, receivers, work=None, method_positions=(),
                 routed=()):
        self.receivers = receivers
        self.method_positions = frozenset(method_positions)
        self.mask = receivers.mask
        self.pattern = sender_pattern(receivers.senders)
        self.work = work
        self.memo = {}
        self.promotion = Promotion(receivers, work, routed)
        self.sender_at = [found.start()
                          for found in self.pattern.finditer(self.mask)]
        self.enclosing = _enclosing_brackets(self.mask)

    def holds_sender(self, start, end):
        record_work(self.work, 'net_container_queries')
        return self.promotion.text_promotes(start, end)


def promoting_handles(reader):
    """Names reaching a sender body; `extra` the unbacked ones.

    A sender's own spelling is left out: the walks own those."""
    promoting = reader.promotion.promoting_bindings()
    names = reader.promotion.names - set(reader.receivers.senders)
    return promoting, names, names - {binding[0] for binding in promoting}


def _pattern_target(mask, close):
    """True when a brace pair is a pattern, not a value."""
    cursor = next_offset(mask, close)
    if mask[cursor:cursor + 2] in ('==', '=>'):
        return False
    return (mask[cursor:cursor + 1] == '='
            or re.match(r'(?:of|in)(?![\w$])', mask[cursor:cursor + 3])
            is not None)


def _constructed_at(mask, start):
    word = word_before(mask, start)
    if word != 'new':
        return start
    return previous_nonspace(mask, start) + 1 - len(word)


def unbound_containers(reader, calls, bound):
    """Where a container is produced and unbound."""
    mask = reader.mask
    found = set()
    for opening, close in reader.receivers.pair_end.items():
        if mask[opening] != '{' or opening in bound:
            continue
        if mask[opening - 1:opening] == '$':
            continue
        before = previous_nonspace(mask, opening)
        interpolated = (mask[before:before + 1] == '{'
                        and mask[before - 1:before] == '$')
        if not interpolated and _BLOCK_BEFORE.search(
                mask[max(0, opening - 96):opening]):
            continue
        if _pattern_target(mask, close):
            continue
        if reader.holds_sender(opening, close):
            found.add(opening)
    for match in _NEW_OF.finditer(mask):
        # A constructor naming a sender runs wherever it is written.
        if constructor_promotes(reader, match.group(1)):
            found.add(match.start())
    for call in calls:
        if _constructed_at(mask, call['start']) in bound:
            continue
        if call['start'] in bound or call['start'] in found:
            continue
        if call['order'] <= call['start']:
            continue
        if call['body'] is not None and returns_promoting(
                reader, call['body']):
            found.add(call['start'])
    return found


def _member_access(mask, position):
    before = previous_nonspace(mask, position)
    return (before >= 0 and mask[before] == '.'
            and mask[max(0, before - 2):before] != '..')


def _discarded(reader, start, end):
    mask = reader.mask
    left = previous_nonspace(mask, start)
    word = word_before(mask, start)
    if word:
        if word != 'void':
            return False
        left = previous_nonspace(mask, left + 1 - len(word))
    if left >= 0 and mask[left] not in ';{}\n':
        return False
    text = reader.receivers.text
    right = end
    while right < len(mask) and text[right] in ' \t':
        right += 1
    return text[right:right + 1] in (';', '')


def _named_entry(reader, start, end):
    """True when the mention names a member or an entry key."""
    mask = reader.mask
    cursor = next_offset(mask, end)
    if mask[cursor:cursor + 1] == '(':
        return start in reader.method_positions
    after = mask[cursor:cursor + 1]
    if after not in (':', ',', '}'):
        return False
    left = previous_nonspace(mask, start)
    if after == ':':
        return left >= 0 and mask[left] in '{,'
    # A shorthand names a member of the literal around it.
    if left < 0 or mask[left] not in '{,':
        return False
    opening = reader.enclosing[start]
    return (opening >= 0 and mask[opening] == '{'
            and mask[opening - 1:opening] != '$')


def _declared_here(mask, start):
    return bool(_DECLARATOR.search(mask[max(0, start - 96):start]))


def _accounted(reader, start, end):
    """True when the mention's own syntax refers to nothing."""
    mask = reader.mask
    return (_member_access(mask, start)
            or _named_entry(reader, start, end)
            or _discarded(reader, start, end)
            or _declared_here(mask, start))


def _statement_level(mask, when):
    if _DECLARATOR.search(mask[max(0, when - 96):when]):
        return True
    left = previous_nonspace(mask, when)
    return left < 0 or mask[left] in ';{}\n)'


def _bound_positions(receivers, statement_only=False):
    """Where a binding's value begins, parens unwrapped."""
    mask = receivers.mask
    bound = set()
    for entries in receivers.values.values():
        for when, span in entries:
            if span is None:
                continue
            if statement_only and not _statement_level(mask, when):
                continue
            cursor = span[0]
            while cursor < span[1] and mask[cursor].isspace():
                cursor += 1
            bound.add(cursor)
            bound.add(receivers._unwrap(span)[0])
    return bound


def _continues(reader, end):
    mask = reader.mask
    end = next_offset(mask, end)
    # A wrapper closes after the span; what follows it still applies.
    while mask[end:end + 1] == ')':
        end = next_offset(mask, end + 1)
    return reader.receivers.text[end:end + 1] in (
        '.', '[', '(', '`', '?')


def _keep(covered, position, end, work=None):
    record_work(work, 'net_span_lookups')
    covered[position] = max(covered.get(position, position), end)


def _value_is_applied(reader, call):
    """True when something is applied to the recorded value: the name
    holds that result, which no index followed."""
    receivers = reader.receivers
    span = receivers._latest(
        receivers.values.get(call['binding']), call['start'])
    if span is None or receivers.body_at(reader.mask, span[0]) is not None:
        # A span opening a function is that function: what a concise
        # body applies, it applies inside.
        return False
    return _continues(reader, span[1])


def _callee_is_dead(reader, call):
    """True when a call names a callee nothing will replay."""
    receivers = reader.receivers
    if call['status'] != 'known' or call['body'] or not call['binding']:
        return False
    if call['binding'] not in receivers.values:
        return True
    if _value_is_applied(reader, call):
        return True
    return bool(call['name']) and receivers.callable_body(
        call['name'], call['start']) is None


def _covered_positions(reader, calls, proofs, bound, written=()):
    receivers = reader.receivers
    mask = reader.mask
    covered = {}
    for call in calls:
        if call['status'] not in ('known', 'unprovable'):
            continue
        if _callee_is_dead(reader, call):
            continue
        end = (call['order'] + 1 if call['order'] > call['start']
               else proofs['span'].get(call['start'], call['start'] + 1))
        _keep(covered, call['start'], end, reader.work)
        for span in call['consumed']:
            _keep(covered, next_offset(mask, span[0]), len(mask),
                  reader.work)
    for position, end in proofs['inert'].items():
        # A receiver written under a key the member index cannot
        # record was not read inertly at all.
        named = _IDENTIFIER.match(mask, position)
        if (named is not None and written.get(named.group(1), len(mask))
                < position):
            continue
        _keep(covered, position, end, reader.work)
    for position, end in proofs['value'].items():
        # Reading a callable runs nothing; a name keeping it or a
        # discard accounts for the value.
        if position in bound or _discarded(reader, position, end):
            _keep(covered, position, len(mask), reader.work)
    for entries in receivers.values.values():
        for when, span in entries:
            _keep(covered, when, len(mask), reader.work)
            if span is None:
                continue
            text = mask[span[0]:span[1]]
            if _READ_IN_FULL.fullmatch(text):
                _keep(covered, span[0] + len(text) - len(text.lstrip()),
                      len(mask), reader.work)
    return covered


def uncovered_receiver_mentions(mask, receivers, calls, proofs, work=None,
                                method_positions=(), candidates=()):
    """Positions reaching an unaccounted container."""
    candidates = frozenset(candidates)
    invoked = {call['start']: call for call in calls
               if call['order'] > call['start']}
    invoked_at = {start: call['binding'] for start, call in invoked.items()}
    # A candidate that is called is a binding the timeline judges.
    routed = {binding for binding in set(invoked_at.values())
              if binding in candidates}
    reader = _Reader(receivers, work, method_positions, routed)
    if not reader.sender_at:
        return []
    promoting, names, extra = promoting_handles(reader)
    bound = _bound_positions(receivers, statement_only=True)
    covered = _covered_positions(reader, calls, proofs, bound,
                                 unkeyed_writes(reader))
    found = set(unbound_containers(
        reader, calls, _bound_positions(receivers)))
    for match in _IDENTIFIER.finditer(mask):
        position = match.start()
        if match.group(1) not in names:
            continue
        record_work(reader.work, 'net_mentions')
        record_work(reader.work, 'net_span_lookups')
        if position in covered and not _continues(
                reader, covered[position]):
            continue
        if _accounted(reader, position, match.end()):
            continue
        constructed = _constructed_at(mask, position)
        if constructed != position and constructed in bound:
            continue
        binding = receivers.visible_binding(match.group(1), position)
        # A call of a binding the sender walk judges is its verdict,
        # unless that is not what the name ends up holding.
        if (binding in candidates and invoked_at.get(position) == binding
                and not _value_is_applied(reader, invoked[position])):
            continue
        if (binding is not None and binding not in promoting
                and match.group(1) not in extra):
            continue
        if reader.promotion.held(position):
            continue
        found.add(position)
    return sorted(found)


def record_uncovered_mentions(mask, receivers, calls, scope_at, proofs,
                              work=None, method_positions=(),
                              candidates=()):
    """Record an unprovable operation per uncovered mention."""
    found = [operation_call(position, scope_at(position), 'unprovable')
             for position in uncovered_receiver_mentions(
                 mask, receivers, calls, proofs, work, method_positions,
                 candidates)]
    calls.extend(found)
    calls.sort(key=lambda call: (call['order'], call['start']))
    return calls
