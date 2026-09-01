"""Callable parameters and invocation targets for the JavaScript guard."""
import re


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


def _computed_key(text, key):
    if not isinstance(key, tuple):
        return key
    name = re.escape(key[1])
    pattern = re.compile(
        r'\b(?:const|let|var)\s+' + name
        + r'\s*=\s*(["\'])([^"\']+)\1')
    matches = list(pattern.finditer(text, 0, key[2]))
    return matches[-1].group(2) if matches else None


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


def _object_value(mask, text, span, key, top_level):
    left, right = _trim(mask, *span)
    if left >= right or mask[left] != '{':
        return 'unprovable', None
    key = _computed_key(text, key)
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


def member_source(mask, text, span, key, top_level, excluded=()):
    """Return one property source from a call-site object value."""
    resolved_excluded = {
        _computed_key(text, item) for item in excluded}
    if key in resolved_excluded:
        return 'missing', None
    return _object_value(mask, text, span, key, top_level)


def parameter_sources(specs, args, mask, text, top_level):
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
                        mask, text, value, part, top_level)
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
                              statement_end, senders):
    """Find lexical bindings that an invocation may use as a sender."""
    candidates = set()
    for scope in scopes[1:]:
        for name in scope['params']:
            binding = visible_binding(name, scope['start'])
            if binding is not None:
                candidates.add(binding)
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
            direct = (re.search(sender_pattern, expr)
                      and '=>' not in expr
                      and not re.match(
                          r'(?:async\s+)?function\b', expr))
            if direct or source in candidates:
                if target is not None and target not in candidates:
                    candidates.add(target)
                    changed = True
            else:
                retained.append(match)
        if not changed:
            break
        pending = retained
    return candidates


def _target(status, binding=None, body=None, member=None):
    return {'status': status, 'binding': binding, 'body': body,
            'member': member}


def _callable_value(span, mask, visible_binding, body_at):
    left, right = _trim(mask, *span)
    body = body_at(mask, left)
    if body is not None:
        return _target('known', body=body)
    found = re.match(r'[\w$]+', mask[left:right])
    tail = mask[left + found.end():right].lstrip() if found else ''
    if found and (not tail or tail[0] in ',;)]}\n'):
        binding = visible_binding(found.group(), left)
        if binding is not None:
            return _target('known', binding=binding)
    return _target('unprovable')


def _member_assignments(owner_name, key, call_start, mask, text,
                        visible_binding):
    patterns = [re.compile(
        r'\b' + re.escape(owner_name) + r'\s*\.\s*'
        + re.escape(key) + r'\s*=(?!=|>)')]
    raw_pattern = re.compile(
        r'\b' + re.escape(owner_name)
        + r'\s*\[\s*(["\'])' + re.escape(key)
        + r'\1\s*\]\s*=(?!=|>)')
    found = [(match.start(), match.end())
             for pattern in patterns for match in pattern.finditer(
                 mask, 0, call_start)]
    found.extend(
        (match.start(), match.end()) for match in raw_pattern.finditer(
            text, 0, call_start)
        if mask[match.start()] == text[match.start()])
    owner = visible_binding(owner_name, call_start)
    return [item for item in found
            if visible_binding(owner_name, item[0]) == owner]


def _member_value(owner_name, key, call_start, context, seen=frozenset()):
    mask = context['mask']
    text = context['text']
    bindings = context['bindings']
    pair_end = context['pair_end']
    resolution = context['resolution']
    top_level = context['top_level']
    body_at = context['body_at']
    visible_binding = resolution['binding']
    owner = visible_binding(owner_name, call_start)
    if owner is None:
        return _target('irrelevant')
    key = _computed_key(text, key)
    if key is None:
        return _target('unprovable')
    if owner in seen:
        return _target('unprovable')
    rest = resolution['member_bindings'].get(owner)
    if rest is not None:
        return _target('known', binding=owner, member=key)
    assigned = _member_assignments(
        owner_name, key, call_start, mask, text, visible_binding)
    if assigned:
        return _callable_value(
            (max(assigned)[1], call_start), mask,
            visible_binding, body_at)
    for assignment in reversed(bindings):
        if assignment.start() >= call_start:
            continue
        if visible_binding(
                assignment.group(1), assignment.start()) != owner:
            continue
        value_start, _ = _trim(
            mask, assignment.end(), len(mask))
        alias = re.match(r'[\w$]+', mask[value_start:])
        if alias:
            return _member_value(
                alias.group(), key, value_start, context, seen | {owner})
        if mask[value_start:value_start + 1] != '{':
            if (mask[value_start:value_start + 1] == '['
                    or body_at(mask, value_start) is not None
                    or re.match(
                        r'(?:null|undefined|true|false|[0-9])\b',
                        mask[value_start:])):
                return _target('irrelevant')
            return _target('unprovable')
        value_end = pair_end.get(value_start, value_start + 1)
        status, span = _object_value(
            mask, text, (value_start, value_end), key, top_level)
        if status == 'missing':
            return _target('irrelevant')
        if status != 'known':
            return _target('unprovable')
        return _callable_value(span, mask, visible_binding, body_at)
    return _target('irrelevant')


def _returned_member(name, start, key, context):
    mask = context['mask']
    text = context['text']
    bindings = context['bindings']
    resolution = context['resolution']
    top_level = context['top_level']
    body_at = context['body_at']
    binding = resolution['binding'](name, start)
    for assignment in reversed(bindings):
        if assignment.start() >= start:
            continue
        if resolution['binding'](
                assignment.group(1), assignment.start()) != binding:
            continue
        body = body_at(mask, assignment.end())
        if body is None:
            return _target('unprovable')
        _, body_start, body_end = body
        if body[0] == 'block':
            returned = re.search(r'\breturn\b', mask[body_start:body_end])
            if returned is None:
                return _target('unprovable')
            body_start += returned.end()
        left, right = _trim(mask, body_start, body_end)
        if mask[left:left + 1] == '{':
            status, span = _object_value(
                mask, text, (left, right), key, top_level)
            return (_callable_value(span, mask, resolution['binding'], body_at)
                    if status == 'known' else _target(status))
        owner = mask[left:right]
        if re.fullmatch(r'[\w$]+', owner):
            return _member_value(
                owner, key, left, context)
        return _target('unprovable')
    return _target('unprovable')


def _known_array(name, start, mask, bindings, visible_binding,
                 seen=frozenset()):
    binding = visible_binding(name, start)
    if binding is None or binding in seen:
        return False
    for assignment in reversed(bindings):
        if assignment.start() >= start:
            continue
        if visible_binding(
                assignment.group(1), assignment.start()) != binding:
            continue
        value_start, _ = _trim(mask, assignment.end(), len(mask))
        if mask[value_start:value_start + 1] == '[':
            return True
        if re.match(r'Array\s*\.\s*from\s*\(', mask[value_start:]):
            return True
        alias = re.match(r'[\w$]+', mask[value_start:])
        return bool(alias and _known_array(
            alias.group(), value_start, mask, bindings,
            visible_binding, seen | {binding}))
    return False


def _identifier_before(mask, pos):
    pos -= 1
    while pos >= 0 and mask[pos].isspace():
        pos -= 1
    end = pos + 1
    while pos >= 0 and (mask[pos].isalnum() or mask[pos] in '_$'):
        pos -= 1
    return mask[pos + 1:end], pos + 1, pos


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
    receiver_prefix = mask[:receiver_start].rstrip()
    array_chain = False
    if (receiver_prefix.endswith('.')
            and receiver in {
                'concat', 'filter', 'flat', 'flatMap', 'map', 'slice'}):
        array_owner, array_start, _ = _identifier_before(
            mask, receiver_before)
        array_chain = _known_array(
            array_owner, array_start, mask, context['bindings'],
            context['resolution']['binding'])
    string_chain = (
        receiver == 'getAttribute'
        and key in {
            'endsWith', 'includes', 'slice', 'startsWith', 'substring',
            'trim'})
    if array_chain or string_chain:
        target = _target('irrelevant')
    elif receiver:
        target = _returned_member(receiver, receiver_start, key, context)
    else:
        target = _target('unprovable')
    start = receiver_start if receiver else owner_end
    return target, start


def discover_invocations(mask, text, pairs, bindings, resolution,
                         method_positions, reader, senders):
    """Classify every syntactic invocation through one target resolver."""
    pair_end = pairs['end']
    pair_start = pairs['start']
    visible_binding = resolution['binding']
    scope_at = resolution['scope']
    top_level = reader['top_level']
    split_top_level = reader['split']
    body_at = reader['body']
    target_context = {
        'mask': mask, 'text': text, 'bindings': bindings,
        'pair_end': pair_end, 'resolution': resolution,
        'top_level': top_level, 'body_at': body_at}
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
        drop_this = False
        start = opening
        if before >= 1 and mask[before - 1:before + 1] == '?.':
            name, start, _ = _identifier_before(mask, before - 1)
            if not name:
                continue
            binding = visible_binding(name, start)
            status = 'known' if binding is not None else 'irrelevant'
        elif before >= 0 and (
                mask[before].isalnum() or mask[before] in '_$'):
            name, start, _ = _identifier_before(mask, opening)
            prefix = mask[:start].rstrip()
            if (name in _NON_CALL_WORDS or prefix.endswith('function')
                    or start in method_positions):
                continue
            if prefix.endswith('.'):
                owner_end = (
                    len(prefix) - 2 if prefix.endswith('?.')
                    else len(prefix) - 1)
                owner, owner_start, _ = _identifier_before(
                    mask, owner_end)
                if not owner:
                    target, owner_start = _chained_member(
                        owner_end, name, pair_start, target_context)
                elif name == 'call':
                    owner_binding = visible_binding(owner, owner_start)
                    target = _target(
                        'known' if owner_binding is not None else 'irrelevant',
                        binding=owner_binding)
                    drop_this = owner_binding is not None
                elif name == 'apply':
                    target = _target('unprovable')
                else:
                    target = _member_value(
                        owner, name, start, target_context)
                status = target['status']
                binding = target['binding']
                body = target['body']
                member = target['member']
                start = owner_start
                name = None
            else:
                binding = visible_binding(name, start)
                status = ('known' if binding is not None
                          else 'irrelevant')
        elif before >= 0 and mask[before] == ']':
            bracket = pair_start.get(before)
            if bracket is None:
                continue
            owner_at = bracket
            prefix = mask[:bracket].rstrip()
            optional_computed = prefix.endswith('?.')
            if optional_computed:
                owner_at = len(prefix) - 2
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
                target = _member_value(
                    owner, key, owner_start, target_context)
            status = target['status']
            binding = target['binding']
            body = target['body']
            member = target['member']
            start = owner_start
        elif before >= 0 and mask[before] == ')':
            wrapper = pair_start.get(before)
            if wrapper is None:
                continue
            inner, inner_end = _trim(mask, wrapper + 1, before)
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
        if drop_this:
            args = args[1:] if args else []
        calls.append({
            'start': start, 'order': close - 1,
            'binding': binding, 'args': args, 'body': body,
            'status': status, 'scope': scope_at(start), 'name': name,
            'member': member, 'parent': False, 'argument_calls': []})
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
