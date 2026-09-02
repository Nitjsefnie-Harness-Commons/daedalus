"""Property reads and writes on tracked receivers as operations."""
import re

from _jsroute_calls import _NON_CALL_WORDS, _trim
from _jsroute_keys import decode_string_literal


_DOT_READ = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?)?\.\s*([\w$]+)(?![\w$])(?!\s*[=(])')
_DOT_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*\.\s*([\w$]+)(?![\w$])\s*'
    r'(?:=(?!=|>)|[-+*/%]=|\*\*=|\|\|=|&&=|\?\?=|\+\+|--)')
_PREFIX_UPDATE = re.compile(
    r'(?<![\w$])(?:\+\+|--)\s*([\w$]+)\s*\.\s*([\w$]+)(?![\w$])')
_QUOTED_READ = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?\.)?\[\s*([\'"])'
    r'((?:\\[\s\S]|(?!\2)[^\\])*)\2\s*\]'
    r'(?!\s*=(?!=))')
_QUOTED_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?\.)?\[\s*([\'"])'
    r'((?:\\[\s\S]|(?!\2)[^\\])*)\2\s*\]\s*'
    r'(?:=(?!=|>)|[-+*/%]=|\*\*=|\|\|=|&&=|\?\?=|\+\+|--)')
_QUOTED_PREFIX_UPDATE = re.compile(
    r'(?<![\w$])(?:\+\+|--)\s*([\w$]+)\s*\[\s*([\'"])'
    r'((?:\\[\s\S]|(?!\2)[^\\])*)\2\s*\]')
_DYNAMIC_READ = re.compile(
    r'(?<![\w$])([\w$]+)\s*\[\s*([\w$]+)\s*\]'
    r'(?!\s*=(?!=))(?!\s*\()')
_DYNAMIC_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*\[\s*([\w$]+)\s*\]\s*'
    r'(?:=(?!=|>)|[-+*/%]=|\*\*=|\|\|=|&&=|\?\?=|\+\+|--)')
_DYNAMIC_PREFIX_UPDATE = re.compile(
    r'(?<![\w$])(?:\+\+|--)\s*([\w$]+)\s*\[\s*([\w$]+)\s*\]')
_EXPRESSION_READ = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?\.)?\[([^]\n]+)\]'
    r'(?!\s*=(?!=))(?!\s*\()')
_EXPRESSION_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?\.)?\[([^]\n]+)\]\s*'
    r'(?:=(?!=|>)|[-+*/%]=|\*\*=|\|\|=|&&=|\?\?=|\+\+|--)')
_EXPRESSION_PREFIX_UPDATE = re.compile(
    r'(?<![\w$])(?:\+\+|--)\s*([\w$]+)\s*\[([^]\n]+)\]')
_SPREAD_SOURCE = re.compile(r'(?<![\w$.])\.\.\.\s*([\w$]+)')
_DESTRUCTURE_SOURCE = re.compile(r'\}\s*(?:=|of)\s*([\w$]+)')
_OBJECT_FUNCTION = re.compile(
    r'(?<![\w$])Object\s*\.\s*(?:assign|values|entries)\s*\(')


def operation_call(position, scope, status, body=None, args=(),
                   argument_status=None):
    """A property operation replayed through the call machinery."""
    return {
        'start': position, 'order': position, 'binding': None,
        'args': list(args), 'body': body, 'status': status, 'scope': scope,
        'name': None, 'member': None, 'source': None, 'parent': False,
        'form': None, 'argument_calls': [],
        'argument_status': argument_status}


def _unwrap_parens(mask, span, pair_end):
    """Text of the operand at `span`, without wrapping parentheses."""
    left, right = _trim(mask, *span)
    while (mask[left:left + 1] == '('
           and pair_end.get(left) == right):
        left, right = _trim(mask, left + 1, right - 1)
    return mask[left:right].strip()


def _object_pattern(scope):
    """True when a scope declares a destructured object parameter."""
    return any(
        isinstance(binding['path'][0], str)
        for spec in scope['param_order']
        for binding in spec['bindings']
        if binding['path'])


def discover_operations(mask, text, pairs, resolution, reader, calls):
    """Record property reads and writes on tracked receivers as calls.

    An accessor runs its own body: a read of a getter and a write of a
    setter replay that half, so its writes stay inside the invocation that
    reached them. A spread, a destructuring or an Object function operand
    whose literal holds an accessor (or an entry the model cannot read)
    answers unprovable; every other operation on a tracked receiver is
    inert, and any operation on an untracked name stays silent.
    """
    receivers = resolution['receivers']
    scope_at = resolution['scope']
    found = []

    def deleting(position):
        return bool(re.search(r'(?<![\w$])delete\s*$', mask[:position]))

    def spread_unprovable(owner, position):
        span = receivers.literal_span(owner, position)
        if span is None or not receivers.holds_literal(span):
            return
        if not receivers.spread_is_inert(span):
            found.append(operation_call(
                position, scope_at(position), 'unprovable'))

    def setter_arguments(match):
        operator = re.search(
            r'(?:=(?!=|>)|[-+*/%]=|\*\*=|\|\|=|&&=|\?\?=|\+\+|--)$',
            match.group(0).rstrip()).group(0)
        if operator not in ('=', '||=', '&&=', '??='):
            return (), 'unprovable'
        left, right = _trim(
            mask, match.end(), reader['expression_end'](mask, match.end()))
        if left >= right:
            return (), 'unprovable'
        return ((left, right),), None

    def record(match, target, wanted, args=(), argument_status=None):
        if target['status'] == 'unprovable':
            found.append(operation_call(
                match.start(), scope_at(match.start()), 'unprovable',
                args=args, argument_status=argument_status))
        elif target['status'] == 'known' and target['form'] == wanted:
            found.append(operation_call(
                match.start(), scope_at(match.start()), 'known',
                target['body'], args, argument_status))

    for match in _DOT_READ.finditer(mask):
        if deleting(match.start()):
            continue
        target = receivers.member(match.group(1), match.group(2),
                                  match.start(), wanted='get')
        record(match, target, 'get')
    for match in _DOT_WRITE.finditer(mask):
        if deleting(match.start()):
            continue
        target = receivers.member(match.group(1), match.group(2),
                                  match.start(), wanted='set')
        args, argument_status = setter_arguments(match)
        record(match, target, 'set', args, argument_status)
    for match in _PREFIX_UPDATE.finditer(mask):
        if deleting(match.start()):
            continue
        target = receivers.member(match.group(1), match.group(2),
                                  match.start(), wanted='set')
        record(match, target, 'set', argument_status='unprovable')
    covered = set()
    for pattern, source_text, form_wanted in (
            (_QUOTED_READ, text, 'get'), (_QUOTED_WRITE, text, 'set'),
            (_QUOTED_PREFIX_UPDATE, text, 'set'),
            (_DYNAMIC_READ, mask, 'get'), (_DYNAMIC_WRITE, mask, 'set'),
            (_DYNAMIC_PREFIX_UPDATE, mask, 'set'),
            (_EXPRESSION_READ, mask, 'get'),
            (_EXPRESSION_WRITE, mask, 'set'),
            (_EXPRESSION_PREFIX_UPDATE, mask, 'set')):
        for match in pattern.finditer(source_text):
            if (mask[match.start()] != text[match.start()]
                    or deleting(match.start())
                    or (match.start(), form_wanted) in covered):
                continue
            covered.add((match.start(), form_wanted))
            owner = match.group(1)
            if pattern in (_QUOTED_READ, _QUOTED_WRITE,
                           _QUOTED_PREFIX_UPDATE):
                key = decode_string_literal(
                    match.group(2) + match.group(3) + match.group(2))
            elif pattern in (
                    _DYNAMIC_READ, _DYNAMIC_WRITE,
                    _DYNAMIC_PREFIX_UPDATE):
                key = ('computed', match.group(2), match.start(2))
            else:
                key = None
            if isinstance(key, tuple):
                key = receivers.computed_key(key)
            target = receivers.member(owner, key, match.start(),
                                      wanted=form_wanted)
            if form_wanted == 'set':
                if pattern in (_QUOTED_PREFIX_UPDATE,
                               _DYNAMIC_PREFIX_UPDATE,
                               _EXPRESSION_PREFIX_UPDATE):
                    args, argument_status = (), 'unprovable'
                else:
                    args, argument_status = setter_arguments(match)
                record(match, target, form_wanted, args, argument_status)
            else:
                record(match, target, form_wanted)
    for match in _SPREAD_SOURCE.finditer(mask):
        spread_unprovable(match.group(1), match.start())
    for match in _DESTRUCTURE_SOURCE.finditer(mask):
        spread_unprovable(match.group(1), match.start())
    for match in _OBJECT_FUNCTION.finditer(mask):
        open_paren = mask.index('(', match.end() - 1)
        close = pairs['end'].get(open_paren)
        for span in reader['split'](mask, text, open_paren + 1, close - 1):
            operand = _unwrap_parens(mask, span, pairs['end'])
            if re.fullmatch(r'[\w$]+', operand):
                spread_unprovable(operand, span[0])
    for match in re.finditer(r'(?<![\w$])([\w$]+)\s*\(', mask):
        if match.group(1) in _NON_CALL_WORDS or deleting(match.start()):
            continue
        body = receivers.callable_body(match.group(1), match.start())
        if body is None:
            continue
        scope = receivers.scope_for(body)
        if scope is None or not _object_pattern(resolution['scopes'][scope]):
            continue
        open_paren = mask.index('(', match.start())
        close = pairs['end'].get(open_paren)
        for span in reader['split'](mask, text, open_paren + 1, close - 1):
            operand = _unwrap_parens(mask, span, pairs['end'])
            if re.fullmatch(r'[\w$]+', operand):
                spread_unprovable(operand, span[0])
    calls.extend(found)
    calls.sort(key=lambda call: (call['order'], call['start']))
    return calls


_CARRIED = re.compile(r'(?<![\w$])([\w$]+)\s*=\s*$')
_FOROF_SOURCE = re.compile(r'\bof\s*\(?\s*$')
_SPREAD_PREFIX = re.compile(r'\[\s*\.\.\.\s*$')


def _consumed(mask, start, end):
    """True when the iterator a generator call creates is consumed."""
    prefix = mask[:start]
    if _FOROF_SOURCE.search(prefix) or _SPREAD_PREFIX.search(prefix):
        return True
    carried = _CARRIED.search(prefix)
    if carried is None:
        return False
    name = re.escape(carried.group(1))
    suffix = mask[end:]
    return bool(
        re.search(rf'(?<![\w$]){name}\s*\.\s*next\s*\(', suffix)
        or re.search(rf'\bof\s*\(\s*{name}\b', suffix)
        or re.search(rf'\[\s*\.\.\.\s*{name}\b', suffix))


def mark_generator_consumption(mask, calls):
    """Drop a generator body from the call that creates its iterator.

    Calling a generator runs nothing; the body runs when the iterator is
    consumed, so the body stays on the call only for the creation sites
    whose iterator a for-of, an array spread or a `.next()` reaches.
    """
    for call in calls:
        if call['form'] != 'generator' or call['status'] != 'known':
            continue
        if not call['body']:
            continue
        end = call.get('end') or call['order'] + 1
        if _consumed(mask, call['start'], end):
            continue
        call['body'] = None
