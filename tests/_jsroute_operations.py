"""Property reads and writes on tracked receivers as operations."""
import re

from _jsroute_calls import _NON_CALL_WORDS, _trim
from _jsroute_keys import decode_string_literal
from _jsroute_source import record_work, word_before

_WRITE_OPS = (r'(?:=(?!=|>)|[-+*/%&|^]=|\*\*=|<<=|>>>=|>>='
              r'|\|\|=|&&=|\?\?=|\+\+|--')


_DOT_READ = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?)?\.\s*([\w$]+)(?![\w$])(?!\s*[=(])')
_DOT_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*\.\s*([\w$]+)(?![\w$])\s*'
    + _WRITE_OPS + r')')
_PREFIX_UPDATE = re.compile(
    r'(?<![\w$])(?:\+\+|--)\s*([\w$]+)\s*\.\s*([\w$]+)(?![\w$])')
_QUOTED_READ = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?\.)?\[\s*([\'"])'
    r'((?:\\[\s\S]|(?!\2)[^\\])*)\2\s*\]'
    r'(?!\s*=(?!=))')
_QUOTED_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?\.)?\[\s*([\'"])'
    r'((?:\\[\s\S]|(?!\2)[^\\])*)\2\s*\]\s*'
    + _WRITE_OPS + r')')
_QUOTED_PREFIX_UPDATE = re.compile(
    r'(?<![\w$])(?:\+\+|--)\s*([\w$]+)\s*\[\s*([\'"])'
    r'((?:\\[\s\S]|(?!\2)[^\\])*)\2\s*\]')
_DYNAMIC_READ = re.compile(
    r'(?<![\w$])([\w$]+)\s*\[\s*([\w$]+)\s*\]'
    r'(?!\s*=(?!=))(?!\s*\()')
_DYNAMIC_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*\[\s*([\w$]+)\s*\]\s*'
    + _WRITE_OPS + r')')
_DYNAMIC_PREFIX_UPDATE = re.compile(
    r'(?<![\w$])(?:\+\+|--)\s*([\w$]+)\s*\[\s*([\w$]+)\s*\]')
_EXPRESSION_READ = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?\.)?\[([^]\n]+)\]'
    r'(?!\s*=(?!=))(?!\s*\()')
_EXPRESSION_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\?\.)?\[([^]\n]+)\]\s*'
    + _WRITE_OPS + r')')
_EXPRESSION_PREFIX_UPDATE = re.compile(
    r'(?<![\w$])(?:\+\+|--)\s*([\w$]+)\s*\[([^]\n]+)\]')
_SPREAD_SOURCE = re.compile(r'(?<![\w$.])\.\.\.\s*([\w$]+)')
_DESTRUCTURE_SOURCE = re.compile(r'\}\s*(?:=|of)\s*([\w$]+)')
_OBJECT_FUNCTION = re.compile(
    r'(?<![\w$])Object\s*\.\s*(?:assign|values|entries)\s*\(')


_WRITE_OPERATOR = re.compile(_WRITE_OPS + r')$')
_WRITE_EXACT = re.compile(_WRITE_OPS + r')\Z')


_ASSIGNING = re.compile(r'\s*([-+*/%&|^<>=!?~]+)')
# A member standing where a value is assigned INTO it: a
# destructuring target or a for-of head, both of which run
# the setter this walk never records.
_TARGET_AFTER = re.compile(
    r'\s*(?:[\]}][\s\]}]*=(?!=|>)|(?<![\w$])of(?![\w$]))')


def _tagged_template(mask, text, end):
    """True when a template is applied to what was read.

    The mask blanks a template whole, backticks included.
    """
    cursor = end
    while (cursor < len(text) and text[cursor].isspace()
           and mask[cursor] == text[cursor]):
        cursor += 1
    return text[cursor:cursor + 1] == '`'


def unmodelled_use(mask, text, end):
    """True when an operation this walk does not model follows a read,
    whose record would otherwise account for the mention."""
    if _TARGET_AFTER.match(mask, end) is not None:
        return True
    if _tagged_template(mask, text, end):
        return True
    found = _ASSIGNING.match(mask, end)
    if found is None:
        return False
    operator = found.group(1)
    if not operator.endswith('=') or operator in (
            '==', '===', '!=', '!==', '<=', '>='):
        return False
    return _WRITE_EXACT.fullmatch(operator) is None


def operation_call(position, scope, status, body=None, args=(),
                   argument_status=None):
    """A property operation replayed through the call machinery."""
    return {
        'start': position, 'order': position, 'binding': None,
        'args': list(args), 'body': body, 'status': status, 'scope': scope,
        'name': None, 'member': None, 'source': None, 'parent': False,
        'form': None, 'argument_calls': [], 'consumed': (),
        'argument_status': argument_status}


def unwrap_parens(mask, span, pair_end):
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


def discover_operations(mask, text, pairs, resolution, reader, calls,
                        proofs=None, work=None):
    """Record property reads and writes on tracked receivers as calls.

    An accessor runs its own body: a read of a getter and a write of a
    setter replay that half. A spread, a destructuring or an Object
    function operand whose literal holds an accessor answers unprovable;
    every other operation on a tracked receiver is inert.

    `proofs` collects the receiver positions this walk answered without
    recording anything, split into the reads that produced a value worth
    following and the ones that provably run and produce nothing. It is
    the only thing entitled to call a position accounted for.
    """
    if proofs is None:
        proofs = {'inert': {}, 'value': {}, 'span': {}}
    receivers = resolution['receivers']
    scope_at = resolution['scope']
    found = []

    def deleting(position):
        # The word before, not a prefix scan; what it reads is counted.
        word = word_before(mask, position)
        record_work(work, 'operation_prefix_bytes', len(word) + 1)
        return word == 'delete'

    def prove(position, end, target=None, read=False, carries=False):
        # Only a read hands a value out; a write the walk proved inert
        # produces nothing that can escape.
        held = carries or (read and target is not None
                           and target['status'] == 'known'
                           and target['body'])
        kept = proofs['value' if held else 'inert']
        kept[position] = max(kept.get(position, 0), end)

    def spread_unprovable(owner, position, name_at=None,
                          provable=True):
        span = receivers.literal_span(owner, position)
        if span is None or not receivers.holds_literal(span):
            return
        if not receivers.spread_is_inert(span):
            found.append(operation_call(
                position, scope_at(position), 'unprovable'))
            return
        if not provable:
            return
        prove(position if name_at is None else name_at,
              (name_at or position) + 1)

    def unrun_setter(match, owner, key):
        """True when `&&=` provably leaves the setter alone.

        A member with no read side answers `undefined`, and `undefined
        && …` assigns nothing.
        """
        operator = _WRITE_OPERATOR.search(match.group(0).rstrip())
        if operator is None or operator.group(0) != '&&=':
            return False
        return receivers.member(owner, key, match.start(),
                                wanted='get')['status'] == 'irrelevant'

    def setter_arguments(match):
        operator = _WRITE_OPERATOR.search(
            match.group(0).rstrip()).group(0)
        if operator not in ('=', '||=', '&&=', '??='):
            return (), 'unprovable'
        left, right = _trim(
            mask, match.end(), reader['expression_end'](mask, match.end()))
        if left >= right:
            return (), 'unprovable'
        return ((left, right),), None

    def record(match, target, wanted, args=(), argument_status=None,
               key=None, skipped=False):
        proofs['span'][match.start(1)] = max(
            proofs['span'].get(match.start(1), 0), match.end())
        if skipped:
            prove(match.start(1), match.end())
        elif target['status'] == 'unprovable':
            found.append(operation_call(
                match.start(), scope_at(match.start()), 'unprovable',
                args=args, argument_status=argument_status))
        elif target['status'] == 'known' and target['form'] == wanted:
            found.append(operation_call(
                match.start(), scope_at(match.start()), 'known',
                target['body'], args, argument_status))
        else:
            # An unnamed key hands back whatever stands under it.
            prove(match.start(1), match.end(),
                  target if key is None else receivers.member(
                      match.group(1), key, match.start()),
                  read=wanted == 'get',
                  carries=key is None and wanted == 'get')

    for match in _DOT_READ.finditer(mask):
        if deleting(match.start()):
            prove(match.start(1), match.end())
            continue
        if unmodelled_use(mask, text, match.end()):
            continue
        target = receivers.member(match.group(1), match.group(2),
                                  match.start(), wanted='get')
        record(match, target, 'get', key=match.group(2))
    for match in _DOT_WRITE.finditer(mask):
        if deleting(match.start()):
            prove(match.start(1), match.end())
            continue
        target = receivers.member(match.group(1), match.group(2),
                                  match.start(), wanted='set')
        args, argument_status = setter_arguments(match)
        record(match, target, 'set', args, argument_status,
               key=match.group(2),
               skipped=unrun_setter(match, match.group(1),
                                    match.group(2)))
    for match in _PREFIX_UPDATE.finditer(mask):
        if deleting(match.start()):
            prove(match.start(1), match.end())
            continue
        target = receivers.member(match.group(1), match.group(2),
                                  match.start(), wanted='set')
        record(match, target, 'set', argument_status='unprovable',
               key=match.group(2))
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
                    or (match.start(), form_wanted) in covered):
                continue
            if deleting(match.start()):
                prove(match.start(1), match.end())
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
            if form_wanted == 'get' and unmodelled_use(
                    mask, text, match.end()):
                continue
            if form_wanted == 'set':
                if pattern in (_QUOTED_PREFIX_UPDATE,
                               _DYNAMIC_PREFIX_UPDATE,
                               _EXPRESSION_PREFIX_UPDATE):
                    args, argument_status = (), 'unprovable'
                else:
                    args, argument_status = setter_arguments(match)
                record(match, target, form_wanted, args, argument_status,
                       key=key,
                       skipped=unrun_setter(match, owner, key))
            else:
                record(match, target, form_wanted, key=key)
    for match in _SPREAD_SOURCE.finditer(mask):
        spread_unprovable(match.group(1), match.start(), match.start(1))
    for match in _DESTRUCTURE_SOURCE.finditer(mask):
        spread_unprovable(match.group(1), match.start(), match.start(1))
    for match in _OBJECT_FUNCTION.finditer(mask):
        open_paren = mask.index('(', match.end() - 1)
        close = pairs['end'].get(open_paren)
        for span in reader['split'](mask, text, open_paren + 1, close - 1):
            operand = unwrap_parens(mask, span, pairs['end'])
            if re.fullmatch(r'[\w$]+', operand):
                spread_unprovable(operand, span[0], mask.index(
                    operand, span[0], span[1]))
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
            operand = unwrap_parens(mask, span, pairs['end'])
            if re.fullmatch(r'[\w$]+', operand):
                # The callee's body is entered without the pattern bound,
                # so an inert copy proves nothing about what it runs.
                spread_unprovable(operand, span[0], mask.index(
                    operand, span[0], span[1]), provable=False)
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
