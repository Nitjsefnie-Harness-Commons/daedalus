"""Callable parameters and invocation targets for the JavaScript guard."""
import re


_NON_CALL_WORDS = {
    'catch', 'for', 'function', 'if', 'return', 'switch', 'while', 'with'}


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
    return value if re.fullmatch(r'[\w$]+', value) else None


def parameter_bindings(mask, text, start, end, pair_end, top_level,
                       _split_top_level):
    """Return lexical names and fixed-shape structured parameter bindings."""
    def records(left, right, path=(), default=None):
        left, right = _trim(mask, left, right)
        if mask[left:left + 3] == '...':
            return records(left + 3, right, path, default)
        if re.fullmatch(r'[\w$]+', mask[left:right]):
            return ({'name': mask[left:right], 'path': path,
                     'default': default},)
        if left >= right or mask[left] not in '({[':
            return ()
        if mask[left] == '(' and pair_end.get(left) == right:
            return records(left + 1, right - 1, path, default)
        if mask[left] not in '{[':
            return ()
        closing = pair_end.get(left, right) - 1
        found = []
        for index, (item_left, item_right) in enumerate(
                _segments(mask, left + 1, closing)):
            item_left, item_right = _trim(
                mask, item_left, item_right)
            if item_left >= item_right:
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
                    item_default))
                continue
            colon = top_level(mask, item_left, value_right, ':')
            if colon is None:
                name_left, name_right = _trim(
                    mask, item_left, value_right)
                name = mask[name_left:name_right]
                key = name if re.fullmatch(r'[\w$]+', name) else None
                if key is not None:
                    found.extend(records(
                        name_left, name_right, path + (key,),
                        item_default))
                continue
            key = _key(text, item_left, colon)
            if key is not None:
                found.extend(records(
                    colon + 1, value_right, path + (key,),
                    item_default))
        return tuple(found)

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
        bindings = records(left, pattern_right)
        names.update(item['name'] for item in bindings)
        order.append({'bindings': bindings, 'default': default})
    return names, order


def _object_value(mask, text, span, key, top_level):
    left, right = _trim(mask, *span)
    if left >= right or mask[left] != '{':
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


def parameter_sources(specs, args, mask, text, top_level):
    """Map each structured binding to its call-site or default source."""
    found = []
    for index, spec in enumerate(specs):
        source = args[index] if index < len(args) else None
        if source is not None:
            left, right = _trim(mask, *source)
            if mask[left:right] == 'undefined':
                source = None
        if source is None:
            source = spec['default']
        for binding in spec['bindings']:
            status = 'known' if source is not None else 'empty'
            value = source
            for part in binding['path']:
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
                          'span': value})
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


def _member_value(owner_name, key, call_start, mask, text, bindings,
                  pair_end, visible_binding, top_level, body_at):
    owner = visible_binding(owner_name, call_start)
    if owner is None:
        return 'irrelevant', None, None
    for assignment in reversed(bindings):
        if assignment.start() >= call_start:
            continue
        if visible_binding(
                assignment.group(1), assignment.start()) != owner:
            continue
        value_start, _ = _trim(
            mask, assignment.end(), len(mask))
        if mask[value_start:value_start + 1] != '{':
            return 'irrelevant', None, None
        value_end = pair_end.get(value_start, value_start + 1)
        if key is None:
            return 'unprovable', None, None
        status, span = _object_value(
            mask, text, (value_start, value_end), key, top_level)
        if status == 'missing':
            return 'irrelevant', None, None
        if status != 'known':
            return 'unprovable', None, None
        left, right = _trim(mask, *span)
        body = body_at(mask, left)
        if body is not None:
            return 'known', None, body
        name = mask[left:right]
        if re.fullmatch(r'[\w$]+', name):
            binding = visible_binding(name, left)
            if binding is not None:
                return 'known', binding, None
        return 'unprovable', None, None
    return 'irrelevant', None, None


def _identifier_before(mask, pos):
    pos -= 1
    while pos >= 0 and mask[pos].isspace():
        pos -= 1
    end = pos + 1
    while pos >= 0 and (mask[pos].isalnum() or mask[pos] in '_$'):
        pos -= 1
    return mask[pos + 1:end], pos + 1, pos


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
        name = None
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
                    continue
                status, binding, body = _member_value(
                    owner, name, start, mask, text, bindings, pair_end,
                    visible_binding, top_level, body_at)
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
            owner, owner_start, _ = _identifier_before(mask, bracket)
            if not owner:
                continue
            raw_key = text[bracket + 1:before].strip()
            quoted = re.fullmatch(
                r'(["\'])([^"\']+)\1', raw_key)
            key = quoted.group(2) if quoted else None
            status, binding, body = _member_value(
                owner, key, owner_start, mask, text, bindings, pair_end,
                visible_binding, top_level, body_at)
            start = owner_start
        elif before >= 0 and mask[before] == ')':
            wrapper = pair_start.get(before)
            if wrapper is None:
                continue
            inner, _ = _trim(mask, wrapper + 1, before)
            body = body_at(mask, inner)
            status = 'known' if body is not None else 'unprovable'
            start = inner
        else:
            continue
        args = split_top_level(
            mask, text, opening + 1, close - 1)
        calls.append({
            'start': start, 'order': close - 1,
            'binding': binding, 'args': args, 'body': body,
            'status': status, 'scope': scope_at(start), 'name': name})
    calls.sort(key=lambda call: (call['order'], call['start']))
    return calls
