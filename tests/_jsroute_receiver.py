"""Indexed callable values for JavaScript receiver expressions."""
from bisect import bisect_right
import re

from _jsroute_calls import parameter_sources
from _jsroute_source import BUILTIN_CHAINS, record_work


def target(status, binding=None, body=None, member=None, name=None):
    return {'status': status, 'binding': binding, 'body': body,
            'member': member, 'name': name}


def _trim(mask, left, right):
    while left < right and mask[left].isspace():
        left += 1
    while right > left and mask[right - 1].isspace():
        right -= 1
    return left, right


def _segments(mask, start, end):
    found = []
    depth = 0
    left = start
    for position in range(start, end):
        char = mask[position]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ',' and depth == 0:
            found.append((left, position))
            left = position + 1
    found.append((left, end))
    return found


class ReceiverIndex:
    """Resolve member values from one source-ordered assignment index."""

    def __init__(self, mask, text, pairs, bindings, resolution, reader,
                 senders, work=None):
        self.mask = mask
        self.text = text
        self.pair_end = pairs['end']
        self.visible_binding = resolution['binding']
        self.member_bindings = resolution['member_bindings']
        self.scopes = resolution['scopes']
        self.body_scopes = {
            (scope['start'], scope['end']): index
            for index, scope in enumerate(self.scopes)}
        self.body_at = reader['body']
        self.expression_end = reader['expression_end']
        self.top_level = reader['top_level']
        self.split = reader['split']
        self.senders = senders
        self.work = work
        self.values = {}
        self.members = {}
        self.globals = {}
        self.computed = {}
        self._index_computed_keys()
        self._index_values(bindings)
        self._index_functions()
        self._index_member_writes()

    def computed_key(self, key):
        record_work(self.work, 'receiver_computed_queries')
        if not isinstance(key, tuple):
            return key
        entries = self.computed.get(key[1], ())
        index = bisect_right(entries, (key[2], chr(0x10ffff))) - 1
        return entries[index][1] if index >= 0 else None

    def member(self, owner_name, key, position, seen=frozenset(), env=None):
        record_work(self.work, 'receiver_member_queries')
        key = self.computed_key(key)
        if key is None:
            return target('unprovable')
        if (key in BUILTIN_CHAINS['array']
                and self.known_array(owner_name, position)):
            return target('irrelevant')
        binding = self.visible_binding(owner_name, position)
        if binding is None:
            span = self._latest(self.globals.get(owner_name), position)
            if span is None:
                return target('irrelevant')
            return self._member_from_span(
                span, key, position, seen, env or {})
        return self._member_binding(
            binding, key, position, seen, env or {})

    def returned_member(self, name, opening, close, key, position):
        binding = self.visible_binding(name, position)
        body = self._callable_body(binding, position)
        if body is None:
            return target('unprovable')
        scope = self._scope_for(body)
        if scope is None:
            return target('unprovable')
        args = self.split(self.mask, self.text, opening + 1, close - 1)
        env = self._argument_environment(scope, args)
        expression = self._returned_expression(body)
        if expression is None:
            return target('unprovable')
        return self._member_from_span(
            expression, key, expression[0], frozenset(), env)

    def known_array(self, name, position, seen=frozenset()):
        binding = self.visible_binding(name, position)
        if binding is None or binding in seen:
            return False
        span = self._latest(self.values.get(binding), position)
        if span is None:
            return False
        left, right = self._unwrap(span)
        if self.mask[left:left + 1] == '[':
            return True
        if re.match(r'Array\s*\.\s*from\s*\(', self.mask[left:right]):
            return True
        alias = re.fullmatch(r'[\w$]+', self.mask[left:right].strip())
        return bool(alias and self.known_array(
            alias.group(), left, seen | {binding}))

    def callable_value(self, span):
        left, right = self._unwrap(span)
        body = self.body_at(self.mask, left)
        if body is not None:
            return target('known', body=body)
        name = self.mask[left:right].strip()
        member = re.fullmatch(
            r'([\w$]+)\s*\.\s*([\w$]+)', name)
        if member:
            return self.member(member.group(1), member.group(2), left)
        computed = re.fullmatch(
            r'([\w$]+)\s*\[\s*(["\'])([^"\']+)\2\s*\]',
            self.text[left:right].strip())
        if computed:
            return self.member(computed.group(1), computed.group(3), left)
        if name in self.senders:
            return target('known', name=name)
        if re.fullmatch(r'[\w$]+', name):
            binding = self.visible_binding(name, left)
            if binding is not None:
                return target('known', binding=binding)
        return target('unprovable')

    def _member_binding(self, binding, key, position, seen, env):
        if binding in seen:
            return target('unprovable')
        source = env.get(binding)
        if source is not None:
            return self._member_from_source(
                source, key, position, seen | {binding}, env)
        structured = self.member_bindings.get(binding)
        if structured is not None:
            if structured.get('source') is None:
                return target('known', binding=binding, member=key)
            source = {'status': 'known', 'span': structured['source'],
                      'path': structured['path'],
                      'excluded': structured['excluded']}
            return self._member_from_source(
                source, key, position, seen | {binding}, env)
        assigned = self._latest(self.members.get((binding, key)), position)
        if assigned is not None:
            return self.callable_value(assigned)
        span = self._latest(self.values.get(binding), position)
        if span is None:
            return target('irrelevant')
        return self._member_from_span(
            span, key, position, seen | {binding}, env)

    def _member_from_source(self, source, key, position, seen, env):
        if source['status'] != 'known' or source['span'] is None:
            return target(
                'unprovable' if source['status'] == 'unprovable'
                else 'irrelevant')
        span = source['span']
        for part in source.get('path', ()):
            status, span = self._property_span(
                span, part, position, seen, env)
            if status != 'known':
                return target(
                    'unprovable' if status == 'unprovable'
                    else 'irrelevant')
        if key in {
                self.computed_key(item)
                for item in source.get('excluded', ())}:
            return target('irrelevant')
        return self._member_from_span(span, key, position, seen, env)

    def _member_from_span(self, span, key, position, seen, env):
        left, right = self._unwrap(span)
        call = self._simple_call(left, right)
        if call is not None:
            return self.returned_member(
                call[0], call[1], call[2], key, left)
        status, value = self._property_span(
            span, key, position, seen, env)
        if status != 'known':
            return target(status)
        return self.callable_value(value)

    def _property_span(self, span, key, position, seen, env):
        left, right = self._unwrap(span)
        expression = self.mask[left:right].strip()
        if re.fullmatch(r'[\w$]+', expression):
            binding = self.visible_binding(expression, left)
            if binding is None or binding in seen:
                return 'unprovable', None
            source = env.get(binding)
            if source is not None:
                if source['status'] != 'known':
                    return source['status'], None
                nested = source['span']
                for part in source.get('path', ()):
                    status, nested = self._property_span(
                        nested, part, position, seen | {binding}, env)
                    if status != 'known':
                        return status, None
                return self._property_span(
                    nested, key, position, seen | {binding}, env)
            structured = self.member_bindings.get(binding)
            if (structured is not None
                    and structured.get('source') is not None):
                nested = structured['source']
                for part in structured['path']:
                    status, nested = self._property_span(
                        nested, part, position, seen | {binding}, env)
                    if status != 'known':
                        return status, None
                if key in {
                        self.computed_key(item)
                        for item in structured['excluded']}:
                    return 'irrelevant', None
                return self._property_span(
                    nested, key, position, seen | {binding}, env)
            nested = self._latest(self.values.get(binding), position)
            if nested is None:
                return 'unprovable', None
            return self._property_span(
                nested, key, position, seen | {binding}, env)
        if (self.mask[left:left + 1] == '['
                or self.body_at(self.mask, left) is not None
                or re.match(
                    r'(?:new\b|null|undefined|true|false|[0-9]|["\'])',
                    self.text[left:right])):
            return 'irrelevant', None
        if self.mask[left:left + 1] != '{':
            return 'unprovable', None
        unknown = False
        for item_left, item_right in reversed(
                _segments(self.mask, left + 1, right - 1)):
            item_left, item_right = _trim(
                self.mask, item_left, item_right)
            if self.mask[item_left:item_left + 3] == '...':
                status, value = self._property_span(
                    (item_left + 3, item_right), key,
                    position, seen, env)
                if status == 'known':
                    return status, value
                unknown = unknown or status == 'unprovable'
                continue
            colon = self.top_level(
                self.mask, item_left, item_right, ':')
            equals = self.top_level(
                self.mask, item_left, item_right, '=')
            key_end = (colon if colon is not None else
                       equals if equals is not None else item_right)
            if self._source_key(item_left, key_end) != key:
                continue
            value = ((colon + 1, item_right)
                     if colon is not None else (item_left, key_end))
            return 'known', _trim(self.mask, *value)
        return ('unprovable', None) if unknown else ('irrelevant', None)

    def _argument_environment(self, scope_index, args):
        scope = self.scopes[scope_index]
        env = {}
        for source in parameter_sources(
                scope['param_order'], args, self.mask, self.text,
                self.top_level, self.computed_key):
            binding = self.visible_binding(source['name'], scope['start'])
            if binding is None:
                continue
            env[binding] = {
                'status': source['status'], 'span': source['span'],
                'path': (), 'excluded': source['excluded']}
        return env

    def _returned_expression(self, body):
        kind, start, end = body
        if kind == 'expr':
            return _trim(self.mask, start, end)
        returns = list(re.finditer(r'\breturn\b', self.mask[start:end]))
        if len(returns) != 1:
            return None
        left = start + returns[0].end()
        right = self.expression_end(self.mask, left)
        return _trim(self.mask, left, min(right, end))

    def _scope_for(self, body):
        inset = body[0] == 'block'
        start = body[1] + inset
        end = body[2] - inset
        return self.body_scopes.get((start, end))

    def _callable_body(self, binding, position):
        span = self._latest(self.values.get(binding), position)
        return self.body_at(self.mask, span[0]) if span is not None else None

    def _simple_call(self, left, right):
        found = re.match(r'([\w$]+)\s*\(', self.mask[left:right])
        if found is None:
            return None
        opening = left + found.end() - 1
        return ((found.group(1), opening, self.pair_end[opening])
                if self.pair_end.get(opening) == right else None)

    def _unwrap(self, span):
        left, right = _trim(self.mask, *span)
        while (self.mask[left:left + 1] == '('
               and self.pair_end.get(left) == right):
            left, right = _trim(self.mask, left + 1, right - 1)
        return left, right

    def _expression_span(self, start):
        left, _ = _trim(self.mask, start, len(self.mask))
        body = self.body_at(self.mask, left)
        if body is not None:
            return left, body[2]
        if self.mask[left:left + 1] in '([{':
            end = self.pair_end.get(left)
            if end is not None:
                return left, end
        return left, self.expression_end(self.mask, left)

    def _source_key(self, left, right):
        raw = self.text[left:right].strip()
        quoted = re.fullmatch(r'["\']([^"\']+)["\']', raw)
        if quoted:
            return quoted.group(1)
        computed = re.fullmatch(r'\[\s*(["\'])([^"\']+)\1\s*\]', raw)
        if computed:
            return computed.group(2)
        dynamic = re.fullmatch(r'\[\s*([\w$]+)\s*\]', raw)
        if dynamic:
            return self.computed_key(('computed', dynamic.group(1), left))
        return raw if re.fullmatch(r'[\w$]+', raw) else None

    def _latest(self, entries, position):
        record_work(self.work, 'receiver_history_queries')
        if not entries:
            return None
        index = bisect_right(entries, (position, ())) - 1
        return entries[index][1] if index >= 0 else None

    def _index_values(self, bindings):
        record_work(self.work, 'receiver_binding_entries', len(bindings))
        for match in bindings:
            binding = self.visible_binding(match.group(1), match.start())
            if binding is not None:
                self.values.setdefault(binding, []).append(
                    (match.start(), self._expression_span(match.end())))

    def _index_functions(self):
        record_work(self.work, 'receiver_source_characters', len(self.mask))
        for match in re.finditer(
                r'\bfunction\s+([\w$]+)\s*\(', self.mask):
            binding = self.visible_binding(match.group(1), match.start())
            body = self.body_at(self.mask, match.start())
            if binding is not None and body is not None:
                self.values.setdefault(binding, []).append(
                    (binding[1], (match.start(), body[2])))
        for entries in self.values.values():
            entries.sort()

    def _index_member_writes(self):
        record_work(
            self.work, 'receiver_source_characters', len(self.mask) * 2)
        patterns = [(re.compile(
            r'\b([\w$]+)\s*\.\s*([\w$]+)\s*=(?!=|>)'), self.mask)]
        patterns.append((re.compile(
            r'\b([\w$]+)\s*\[\s*(["\'])([^"\']+)\2\s*\]'
            r'\s*=(?!=|>)'), self.text))
        for pattern, source in patterns:
            for match in pattern.finditer(source):
                if self.mask[match.start()] != self.text[match.start()]:
                    continue
                owner = match.group(1)
                key = match.group(2) if source is self.mask else match.group(3)
                span = self._expression_span(match.end())
                if owner in ('globalThis', 'window'):
                    self.globals.setdefault(key, []).append(
                        (match.start(), span))
                    continue
                binding = self.visible_binding(owner, match.start())
                if binding is not None:
                    self.members.setdefault((binding, key), []).append(
                        (match.start(), span))
        for entries in (*self.members.values(), *self.globals.values()):
            entries.sort()

    def _index_computed_keys(self):
        record_work(self.work, 'receiver_source_characters', len(self.mask))
        pattern = re.compile(
            r'\b(?:const|let|var)\s+([\w$]+)\s*=\s*'
            r'(["\'])([^"\']+)\2')
        for match in pattern.finditer(self.text):
            if self.mask[match.start()] == self.text[match.start()]:
                self.computed.setdefault(match.group(1), []).append(
                    (match.start(), match.group(3)))
