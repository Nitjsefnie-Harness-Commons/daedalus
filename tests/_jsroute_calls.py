"""Callable parameters and invocation targets for the JavaScript guard."""
import re

from _jsroute_source import (BUILTIN_CHAINS,  # noqa: E402
                             previous_nonspace as _js_previous_nonspace,
                             word_before as _js_word_before)


_NON_CALL_WORDS = {
    'catch', 'for', 'function', 'if', 'instanceof', 'return', 'switch',
    'while', 'with'}


def _trim(mask, left, right):
    while left < right and mask[left].isspace():
        left += 1
    while right > left and mask[right - 1].isspace():
        right -= 1
    return left, right


def _segments(mask, start, end):
    spans = []
    depth = 0
    left = start
    for pos in range(start, end):
        char = mask[pos]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ',' and depth == 0:
            spans.append((left, pos))
            left = pos + 1
    spans.append((left, end))
    return spans


def _key(text, left, right):
    value = text[left:right].strip()
    quoted = re.fullmatch(r'["\']([^"\']+)["\']', value)
    computed = re.fullmatch(
        r'\[\s*(["\'])([^"\']+)\1\s*\]', value)
    if quoted:
        return quoted.group(1)
    if computed:
        return computed.group(2)
    dynamic = re.fullmatch(r'\[\s*([\w$]+)\s*\]', value)
    if dynamic:
        return ('computed', dynamic.group(1), left)
    return value if re.fullmatch(r'[\w$]+', value) else None


def parameter_bindings(mask, text, start, end, pair_end, top_level,
                       _split_top_level):
    """Return lexical names and fixed-shape structured parameter bindings."""
    def records(left, right, path=(), default=None, rest=None,
                excluded=()):
        left, right = _trim(mask, left, right)
        if mask[left:left + 3] == '...':
            return records(
                left + 3, right, path, default, rest, excluded)
        if re.fullmatch(r'[\w$]+', mask[left:right]):
            return [{'name': mask[left:right], 'path': path,
                     'position': left,
                     'default': default, 'rest': rest,
                     'excluded': excluded}]
        if left >= right or mask[left] not in '({[':
            return []
        if mask[left] == '(' and pair_end.get(left) == right:
            return records(
                left + 1, right - 1, path, default, rest, excluded)
        if mask[left] not in '{[':
            return []
        closing = pair_end.get(left, right) - 1
        found = []
        object_keys = []
        for index, (item_left, item_right) in enumerate(
                _segments(mask, left + 1, closing)):
            item_left, item_right = _trim(
                mask, item_left, item_right)
            if item_left >= item_right:
                continue
            if mask[left] == '{' and mask[item_left:item_left + 3] == '...':
                found.extend(records(
                    item_left + 3, item_right, path, None,
                    'object', tuple(object_keys)))
                continue
            equals = top_level(mask, item_left, item_right, '=')
            item_default = None
            value_right = item_right
            if equals is not None:
                item_default = _trim(mask, equals + 1, item_right)
                value_right = equals
            if mask[left] == '[':
                found.extend(records(
                    item_left, value_right, path + (index,),
                    item_default, rest, excluded))
                continue
            colon = top_level(mask, item_left, value_right, ':')
            if colon is None:
                name_left, name_right = _trim(
                    mask, item_left, value_right)
                name = mask[name_left:name_right]
                key = name if re.fullmatch(r'[\w$]+', name) else None
                if key is not None:
                    object_keys.append(key)
                    found.extend(records(
                        name_left, name_right, path + (key,),
                        item_default, rest, excluded))
                continue
            key = _key(text, item_left, colon)
            if key is not None:
                object_keys.append(key)
                found.extend(records(
                    colon + 1, value_right, path + (key,),
                    item_default, rest, excluded))
        return found

    names = set()
    order = []
    for left, right in _segments(mask, start, end):
        left, right = _trim(mask, left, right)
        if left >= right:
            continue
        equals = top_level(mask, left, right, '=')
        default = None
        pattern_right = right
        if equals is not None:
            default = _trim(mask, equals + 1, right)
            pattern_right = equals
        is_rest = mask[left:left + 3] == '...'
        pattern_left = left + 3 if is_rest else left
        bindings = records(pattern_left, pattern_right)
        names.update(item['name'] for item in bindings)
        order.append({'bindings': bindings, 'default': default,
                      'rest': is_rest})
    return names, order


def _object_value(mask, text, span, key, top_level, computed_key):
    left, right = _trim(mask, *span)
    if left >= right or mask[left] != '{':
        return 'unprovable', None
    key = computed_key(key)
    if key is None:
        return 'unprovable', None
    for item_left, item_right in _segments(mask, left + 1, right - 1):
        item_left, item_right = _trim(mask, item_left, item_right)
        colon = top_level(mask, item_left, item_right, ':')
        equals = top_level(mask, item_left, item_right, '=')
        stop = item_right if equals is None else equals
        if colon is None:
            found = _key(text, item_left, stop)
            if found == key:
                return 'known', _trim(mask, item_left, stop)
            continue
        if _key(text, item_left, colon) == key:
            return 'known', _trim(mask, colon + 1, item_right)
    return 'missing', None


def _array_value(mask, span, index):
    left, right = _trim(mask, *span)
    if left >= right or mask[left] != '[':
        return 'unprovable', None
    values = _segments(mask, left + 1, right - 1)
    if index >= len(values):
        return 'missing', None
    value = _trim(mask, *values[index])
    return ('known', value) if value[0] < value[1] else ('missing', None)


def member_source(mask, text, span, key, top_level, computed_key,
                  excluded=()):
    """Return one property source from a call-site object value."""
    resolved_excluded = {
        computed_key(item) for item in excluded}
    if key in resolved_excluded:
        return 'missing', None
    return _object_value(
        mask, text, span, key, top_level, computed_key)


def parameter_sources(specs, args, mask, text, top_level, computed_key):
    """Map each structured binding to its call-site or default source."""
    found = []
    for index, spec in enumerate(specs):
        rest_args = args[index:] if spec['rest'] else None
        source = (rest_args[0] if rest_args else None) if spec['rest'] else (
            args[index] if index < len(args) else None)
        if source is not None:
            left, right = _trim(mask, *source)
            if mask[left:right] == 'undefined':
                source = None
        if source is None:
            source = spec['default']
        for binding in spec['bindings']:
            status = 'known' if source is not None else 'empty'
            value = source
            path = binding['path']
            if spec['rest'] and path and isinstance(path[0], int):
                rest_index = path[0]
                if rest_index < len(rest_args):
                    value = rest_args[rest_index]
                    status = 'known'
                else:
                    value = None
                    status = 'missing'
                path = path[1:]
            for part in path:
                if status != 'known':
                    break
                if isinstance(part, int):
                    status, value = _array_value(mask, value, part)
                else:
                    status, value = _object_value(
                        mask, text, value, part, top_level,
                        computed_key)
            if status in ('missing', 'empty'):
                value = binding['default']
                status = 'known' if value is not None else 'empty'
            elif status == 'known':
                left, right = _trim(mask, *value)
                if mask[left:right] == 'undefined':
                    value = binding['default']
                    status = 'known' if value is not None else 'empty'
            found.append({'name': binding['name'], 'status': status,
                          'span': value, 'rest': binding['rest'],
                          'excluded': binding['excluded']})
    return found


def sender_candidate_bindings(scopes, bindings, mask, visible_binding,
                              statement_end, senders,
                              callable_value=None, interpolations=()):
    """Find lexical bindings that an invocation may use as a sender."""
    candidates = set()
    for scope in scopes[1:]:
        for name in scope['params']:
            binding = visible_binding(name, scope['start'])
            if binding is not None:
                candidates.add(binding)
    for match in interpolations:
        target = visible_binding(match.group(1), match.start())
        if target is not None:
            candidates.add(target)
    sender_pattern = (
        r'\b(?:' + '|'.join(map(re.escape, senders)) + r')\b')
    pending = list(bindings)
    while pending:
        changed = False
        retained = []
        for match in pending:
            target = visible_binding(
                match.group(1), match.start())
            end = statement_end(mask, match.end())
            expr = mask[match.end():end].strip()
            source = (visible_binding(expr, match.start())
                      if re.fullmatch(r'[\w$]+', expr) else None)
            bound = re.fullmatch(
                r'([\w$]+)\s*\.\s*bind\s*\(.*\)', expr,
                re.DOTALL)
            if bound:
                source = visible_binding(bound.group(1), match.start())
            direct = (re.search(sender_pattern, expr)
                      and '=>' not in expr
                      and not re.match(
                          r'(?:async\s+)?function\b', expr))
            resolved = (callable_value((match.end(), end))
                        if callable_value is not None else None)
            carried = resolved is not None and resolved['form'] in (
                'get', 'set', 'generator')
            bound_sender = bound and bound.group(1) in senders
            resolved_sender = resolved is not None and (
                resolved['name'] in senders
                or resolved['binding'] in candidates
                or carried)
            if (direct or source in candidates
                    or bound_sender or resolved_sender):
                if target is not None and target not in candidates:
                    candidates.add(target)
                    changed = True
            else:
                retained.append(match)
        if not changed:
            break
        pending = retained
    return candidates


def _target(status, binding=None, body=None, member=None, name=None,
            source=None, form=None):
    return {'status': status, 'binding': binding, 'body': body,
            'member': member, 'name': name, 'source': source, 'form': form}


def _identifier_before(mask, pos):
    pos -= 1
    while pos >= 0 and mask[pos].isspace():
        pos -= 1
    end = pos + 1
    while pos >= 0 and (mask[pos].isalnum() or mask[pos] in '_$'):
        pos -= 1
    return mask[pos + 1:end], pos + 1, pos


def _previous_nonspace(mask, position):
    position -= 1
    while position >= 0 and mask[position].isspace():
        position -= 1
    return position


def _chained_member(owner_end, key, pair_start, context):
    mask = context['mask']
    receiver_close = owner_end - 1
    while receiver_close >= 0 and mask[receiver_close].isspace():
        receiver_close -= 1
    receiver_open = pair_start.get(receiver_close)
    if receiver_open is None:
        return _target('unprovable'), owner_end
    receiver, receiver_start, receiver_before = _identifier_before(
        mask, receiver_open)
    prefix_end = _previous_nonspace(mask, receiver_start)
    array_chain = False
    if (prefix_end >= 0 and mask[prefix_end] == '.'
            and receiver in {
                'concat', 'filter', 'flat', 'flatMap', 'map', 'slice'}):
        array_owner, array_start, _ = _identifier_before(
            mask, receiver_before)
        array_chain = context['receivers'].known_array(
            array_owner, array_start)
    string_chain = (
        receiver == 'getAttribute'
        and key in BUILTIN_CHAINS['string'])
    if array_chain or string_chain:
        target = _target('irrelevant')
    elif receiver:
        target = context['receivers'].returned_member(
            receiver, receiver_open, receiver_close + 1,
            key, receiver_start)
    else:
        target = _target('unprovable')
    start = receiver_start if receiver else owner_end
    return target, start


def discover_invocations(mask, text, pairs, resolution, method_positions,
                         reader, senders):
    """Classify every syntactic invocation through one target resolver."""
    pair_end = pairs['end']
    pair_start = pairs['start']
    visible_binding = resolution['binding']
    scope_at = resolution['scope']
    split_top_level = reader['split']
    body_at = reader['body']
    target_context = {
        'mask': mask, 'receivers': resolution['receivers']}
    calls = []
    for opening in sorted(pair_end):
        if mask[opening:opening + 1] != '(':
            continue
        close = pair_end[opening]
        if re.match(r'\s*=>', mask[close:]):
            continue
        before = opening - 1
        while before >= 0 and mask[before].isspace():
            before -= 1
        status = None
        binding = None
        body = None
        member = None
        name = None
        source = None
        form = None
        call_mode = None
        start = opening
        if before >= 1 and mask[before - 1:before + 1] == '?.':
            name, start, _ = _identifier_before(mask, before - 1)
            if not name:
                continue
            prefix_end = _previous_nonspace(mask, start)
            if prefix_end >= 0 and mask[prefix_end] == '.':
                owner, owner_start, _ = _identifier_before(
                    mask, prefix_end)
                target = resolution['receivers'].member(
                    owner, name, start)
                status = target['status']
                binding = target['binding']
                body = target['body']
                member = target['member']
                name = target['name']
                source = target['source']
                form = target['form']
                start = owner_start
            else:
                binding = visible_binding(name, start)
                status = 'known' if binding is not None else 'irrelevant'
        elif before >= 0 and (
                mask[before].isalnum() or mask[before] in '_$'):
            name, start, _ = _identifier_before(mask, opening)
            prefix_end = _previous_nonspace(mask, start)
            before_word, _, _ = _identifier_before(mask, start)
            if (name in _NON_CALL_WORDS or before_word == 'function'
                    or start in method_positions):
                continue
            if prefix_end >= 0 and mask[prefix_end] == '.':
                owner_end = prefix_end
                if prefix_end > 0 and mask[prefix_end - 1] == '?':
                    owner_end -= 1
                owner, owner_start, _ = _identifier_before(
                    mask, owner_end)
                if not owner:
                    target, owner_start = _chained_member(
                        owner_end, name, pair_start, target_context)
                elif name in BUILTIN_CHAINS['function']:
                    target = _target('irrelevant')
                elif name in ('call', 'apply'):
                    owner_prefix = _previous_nonspace(mask, owner_start)
                    if owner == 'Reflect' and name == 'apply':
                        target = _target('unprovable')
                        call_mode = 'reflect_apply'
                    elif (owner_prefix >= 0
                          and mask[owner_prefix] == '.'):
                        receiver, receiver_start, _ = _identifier_before(
                            mask, owner_prefix)
                        target = resolution['receivers'].member(
                            receiver, owner, owner_start)
                        owner_start = receiver_start
                    else:
                        owner_binding = visible_binding(owner, owner_start)
                        known_owner = (
                            owner_binding is not None or owner in senders)
                        target = _target(
                            'known' if known_owner else 'irrelevant',
                            binding=owner_binding,
                            name=owner if owner in senders else None)
                    if call_mode is None:
                        call_mode = name
                else:
                    target = resolution['receivers'].member(
                        owner, name, start)
                status = target['status']
                binding = target['binding']
                body = target['body']
                member = target['member']
                start = owner_start
                name = target['name']
                source = target['source']
                form = target['form']
            else:
                binding = visible_binding(name, start)
                status = ('known' if binding is not None
                          else 'irrelevant')
        elif before >= 0 and mask[before] == ']':
            bracket = pair_start.get(before)
            if bracket is None:
                continue
            if opening in method_positions:
                continue
            owner_at = bracket
            prefix_end = _previous_nonspace(mask, bracket)
            optional_computed = (
                prefix_end > 0 and mask[prefix_end] == '.'
                and mask[prefix_end - 1] == '?')
            if optional_computed:
                owner_at = prefix_end - 1
            owner, owner_start, _ = _identifier_before(mask, owner_at)
            if not owner:
                status = 'unprovable'
                owner_start = opening
                target = _target(status)
            else:
                raw_key = text[bracket + 1:before].strip()
                quoted = re.fullmatch(
                    r'(["\'])([^"\']+)\1', raw_key)
                if quoted:
                    key = quoted.group(2)
                elif (optional_computed
                      and re.fullmatch(r'[\w$]+', raw_key)):
                    key = ('computed', raw_key, bracket)
                else:
                    key = None
                target = resolution['receivers'].member(
                    owner, key, owner_start)
            status = target['status']
            binding = target['binding']
            body = target['body']
            member = target['member']
            name = target['name']
            source = target['source']
            form = target['form']
            start = owner_start
        elif before >= 0 and mask[before] == ')':
            wrapper = pair_start.get(before)
            if wrapper is None:
                continue
            inner, inner_end = _trim(mask, wrapper + 1, before)
            while (mask[inner:inner + 1] == '('
                   and pair_end.get(inner) == inner_end):
                inner, inner_end = _trim(mask, inner + 1, inner_end - 1)
            body = body_at(mask, inner)
            inner_name = mask[inner:inner_end]
            if body is not None:
                status = 'known'
            elif re.fullmatch(r'[\w$]+', inner_name):
                name = inner_name
                binding = visible_binding(name, inner)
                status = 'known' if binding is not None else 'irrelevant'
            else:
                status = 'unprovable'
            start = inner
        else:
            continue
        args = split_top_level(
            mask, text, opening + 1, close - 1)
        if call_mode == 'reflect_apply':
            if len(args) != 3:
                status = 'unprovable'
            else:
                applied = resolution['receivers'].callable_value(args[0])
                status = applied['status']
                binding = applied['binding']
                body = applied['body']
                member = applied['member']
                name = applied['name']
                source = applied['source']
                call_mode = 'reflect_apply'
        if call_mode == 'call':
            args = args[1:] if args else []
        elif call_mode in ('apply', 'reflect_apply'):
            array_index = 1 if call_mode == 'apply' else 2
            if len(args) <= array_index:
                status = 'unprovable'
                args = []
            else:
                left, right = _trim(mask, *args[array_index])
                if (mask[left:left + 1] != '['
                        or pair_end.get(left) != right):
                    status = 'unprovable'
                    args = []
                else:
                    args = split_top_level(
                        mask, text, left + 1, right - 1)
        calls.append({
            'start': start, 'order': close - 1,
            'binding': binding, 'args': args, 'body': body,
            'status': status, 'scope': scope_at(start), 'name': name,
            'member': member, 'source': source, 'parent': False,
            'form': form, 'argument_calls': []})
    calls.sort(key=lambda call: (call['order'], call['start']))
    nesting = []
    for parent in calls:
        for left, right in parent['args']:
            nesting.append((left, 1, right, parent))
            nesting.append((right, 0, right, parent))
    for child in calls:
        nesting.append((child['start'], 2, child['order'], child))
    active = []
    for _, kind, right, item in sorted(
            nesting, key=lambda entry: entry[:3]):
        if kind == 0:
            active.remove((right, item))
            continue
        if kind == 1:
            active.append((right, item))
            continue
        parent = next((candidate for end, candidate in reversed(active)
                       if item['scope'] == candidate['scope']
                       and item['order'] < end), None)
        if parent is not None:
            child = item
            child['parent'] = True
            parent['argument_calls'].append(child)
    for call in calls:
        call['argument_calls'].sort(
            key=lambda item: (item['order'], item['start']))
    return calls


_TEMPLATE = re.compile(r'`(?:\\.|[^`])*`', re.DOTALL)
_INTERPOLATION = re.compile(r'\$\{')
_INTERP_ASSIGN = re.compile(r'(?<![\w$])([\w$]+)\s*=(?!=|>)')


def _interpolation_spans(template):
    """Spans of the `${...}` expressions inside one template's text."""
    found = []
    body = template.group(0)
    for opening in _INTERPOLATION.finditer(body):
        depth = 0
        cursor = opening.end()
        left = cursor
        while cursor < len(body):
            char = body[cursor]
            if char in '\'"`':
                closing = char
                cursor += 1
                while cursor < len(body):
                    if body[cursor] == '\\':
                        cursor += 2
                        continue
                    if body[cursor] == closing:
                        break
                    cursor += 1
            elif char == '{':
                depth += 1
            elif char == '}':
                if depth == 0:
                    found.append((left, cursor))
                    break
                depth -= 1
            cursor += 1
    return found


def interpolation_events(mask, text):
    """Bind records for the assignments a template's interpolation runs.

    The mask blanks a template whole, so the writes its `${...}` performs
    are invisible to the assignment walk; they are recorded here from the
    raw text instead, and the sender walk answers them as unprovable.
    """
    found = []
    for template in _TEMPLATE.finditer(text):
        for left, right in _interpolation_spans(template):
            for assignment in _INTERP_ASSIGN.finditer(
                    template.group(0)[left:right]):
                found.append((
                    template.start() + left + assignment.start(),
                    'interp', assignment))
    return found


def routing_events(mask, text, senders):
    """Every source position that carries routing state, in source order.

    Declarations and assignments initialise or retarget tracked names, a
    `.tab` write retargets the routing field, an `Object.assign` merges into
    a tracked object, and a tracked object handed to any other call escapes:
    a helper that writes through its parameter is invisible from here, so
    the object stops being provable at that point rather than being trusted
    on its literal. The bracket form is found in the raw text because the
    mask blanks string contents, making `['tab']` unreadable there — a match
    that begins inside a blanked span is a mention, not code, and the mask
    preserves positions, so the two diverge at the very first character of
    the name exactly when the mention is not real code.
    """
    declarations = list(re.finditer(
        r'\b(const|let|var)\s+([\w$]+)\s*=', mask))
    bindings = []
    for m in re.finditer(r'(?<![\w$])([\w$]+)\s*=(?!=|>)', mask):
        before = _js_previous_nonspace(mask, m.start())
        if before < 0 or mask[before] != '.':
            bindings.append(m)
    events = interpolation_events(mask, text)
    for m in declarations:
        events.append((m.start(), 'init', m))
    for m in bindings:
        events.append((m.start(), 'bind', m))
    for m in re.finditer(
            r'(?<![\w$])([\w$]+)\s*\.\s*tab(?![\w$])\s*=', mask):
        events.append((m.start(), 'prop', m))
    for m in re.finditer(r'\bObject\s*\.\s*assign\s*\(', mask):
        events.append((m.start(), 'assign', m))
    # The bracket form is found in the raw text because the mask blanks
    # string contents, making `['tab']` unreadable there — but then a match
    # that begins inside a blanked span is a mention in a string or comment,
    # not code. The mask preserves positions, so the two diverge at the very
    # first character of the name exactly when the mention is not real code.
    for m in re.finditer(
            r'(?<![\w$])([\w$]+)\s*\[\s*["\']tab["\']\s*\]\s*=', text):
        if mask[m.start()] == text[m.start()]:
            events.append((m.start(), 'prop', m))
    # A tracked object handed to any other call escapes: a helper that writes
    # through its parameter is invisible from here, so the object stops being
    # provable at that point rather than being trusted on its literal.
    for m in re.finditer(r'(?<![\w$])([\w$]+)\s*\(', mask):
        if m.group(1) in senders or m.group(1) in ('if', 'for', 'while',
                                                   'switch', 'catch',
                                                   'function', 'return'):
            continue
        # Object.assign's target is modelled above, so it is not an escape;
        # every other call taking the object is one, member calls included.
        dot = _js_previous_nonspace(mask, m.start())
        if (dot >= 0 and mask[dot] == '.'
                and _js_word_before(mask, dot) == 'Object'):
            continue
        events.append((m.start(), 'escape', m))
    events.sort(key=lambda e: e[0])
    return events, bindings
