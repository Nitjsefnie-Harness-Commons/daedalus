"""Indexed callable values for JavaScript receiver expressions."""
from bisect import bisect_right
import re

from _jsroute_calls import parameter_sources
from _jsroute_keys import (class_accessor, decode_string_literal,
                           entry_head, head_left, source_key)
from _jsroute_returns import (callable_body, callable_return,
                              constructed_value, member_value)
from _jsroute_source import BUILTIN_CHAINS, record_work


def target(status, binding=None, body=None, member=None, name=None,
           source=None, form=None):
    return {'status': status, 'binding': binding, 'body': body,
            'member': member, 'name': name, 'source': source, 'form': form}


def next_offset(mask, position):
    """First non-space offset at or after `position`."""
    while position < len(mask) and mask[position].isspace():
        position += 1
    return position


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


class _History:
    """A sorted history whose real entry reads are charged."""

    def __init__(self, work):
        self.items = []
        self.work = work

    def append(self, item):
        self.items.append(item)

    def sort(self):
        self.items.sort()

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        record_work(self.work, 'receiver_history_entries')
        return self.items[index]

    def __iter__(self):
        for item in self.items:
            record_work(self.work, 'receiver_history_entries')
            yield item


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
        self.optional_write = resolution.get('optional_write') or (
            lambda start, limit: False)
        self.senders = senders
        self.work = work
        self.values = {}
        self.members = {}
        self.globals = {}
        self.computed = {}
        self._index_computed_keys(bindings)
        self._index_values(bindings)
        self._index_functions()
        self._index_member_writes()

    def _history(self, index, key):
        if key not in index:
            index[key] = _History(self.work)
        return index[key]

    def computed_key(self, key):
        record_work(self.work, 'receiver_computed_queries')
        if not (isinstance(key, tuple) and len(key) == 3
                and key[0] == 'computed'):
            return key
        binding = self.visible_binding(key[1], key[2])
        if binding is None:
            return None
        entries = self.computed.get(binding, ())
        index = bisect_right(entries, (key[2], chr(0x10ffff))) - 1
        return entries[index][1] if index >= 0 else None

    def member(self, owner_name, key, position, seen=frozenset(),
               env=None, wanted=None):
        record_work(self.work, 'receiver_member_queries')
        key = self.computed_key(key)
        if key is None and wanted is None:
            return target('unprovable')
        binding = self.visible_binding(owner_name, position)
        if binding is None:
            status, body, form = class_accessor(
                self, owner_name, key, wanted, static=True)
            if status == 'known':
                return target('known', body=body, form=form)
            if status == 'opaque':
                return target('unprovable')
            span = self._latest(self.globals.get(owner_name), position)
            if span is None:
                return target('irrelevant')
            return self._member_from_span(
                span, key, position, seen, env or {}, wanted)
        if (key in BUILTIN_CHAINS['array']
                and self.known_array(owner_name, position)):
            return target('irrelevant')
        return self._member_binding(
            binding, key, position, seen, env or {}, wanted)

    def returned_member(self, name, opening, close, key, position):
        body, env = self._call_environment(
            name, opening, close, position)
        if body is None:
            return target('unprovable')
        expression = self._returned_expression(body)
        if expression is None:
            return target('unprovable')
        return self._member_from_span(
            expression, key, expression[0], frozenset(), env)

    def constructed_member(self, name, opening, close, key, position,
                           wanted=None):
        return constructed_value(
            self, name, opening, close, key, position, wanted)

    def _call_environment(self, name, opening, close, position):
        binding = self.visible_binding(name, position)
        body = callable_body(self, binding, position)
        if body is None:
            return None, None
        scope = self._scope_for(body)
        if scope is None:
            return None, None
        args = self.split(self.mask, self.text, opening + 1, close - 1)
        return body, self._argument_environment(scope, args)

    def callable_body(self, name, position):
        """Body of the function `name` names at `position`, or None."""
        binding = self.visible_binding(name, position)
        return callable_body(self, binding, position) if binding else None

    def scope_for(self, body):
        """Index of the scope a function body belongs to, or None."""
        return self._scope_for(body)

    def literal_span(self, name, position):
        """Span of the object literal `name` holds at `position`, or None."""
        binding = self.visible_binding(name, position)
        if binding is None:
            return None
        return self._latest(self.values.get(binding), position)

    def holds_literal(self, span):
        """True when the unwrapped value at `span` is an object literal."""
        left, _ = self._unwrap(span)
        return self.mask[left:left + 1] == '{'

    def spread_is_inert(self, span, seen=frozenset()):
        """True when spreading the literal at `span` runs nothing.

        Reading every property runs a getter and never a setter, so a
        spread is inert exactly when no entry is an accessor, a generator
        or a spread the model cannot resolve.
        """
        left, right = self._unwrap(span)
        if self.mask[left:left + 1] != '{':
            return False
        for item_left, item_right in _segments(self.mask, left + 1,
                                               right - 1):
            item_left, item_right = _trim(self.mask, item_left, item_right)
            if self.mask[item_left:item_left + 3] == '...':
                source = self.mask[item_left + 3:item_right].strip()
                if not re.fullmatch(r'[\w$]+', source) or source in seen:
                    return False
                nested = self.literal_span(source, item_left + 3)
                if nested is None:
                    return False
                if not self.spread_is_inert(nested, seen | {source}):
                    return False
                continue
            colon = self.top_level(self.mask, item_left, item_right, ':')
            equals = self.top_level(self.mask, item_left, item_right, '=')
            if colon is None and equals is None:
                found = self._method_entry(item_left, item_right)
                if found is not None and found[0] is None:
                    return False
                if found is not None and found[2] in ('get', 'set',
                                                      'generator'):
                    return False
                if ('(' in self.mask[item_left:item_right]
                        and found is None):
                    return False
                continue
            if colon is not None:
                key = self._source_key(item_left, colon)
            else:
                key = self._source_key(item_left, equals)
            if key is None:
                return False
        return True

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

    def callable_value(self, span, env=None, form=None, wanted=None):
        left, right = self._unwrap(span)
        body = self.body_at(self.mask, left)
        if body is not None:
            return target('known', body=body, form=form)
        returned = callable_return(self, left, right)
        if returned is not None:
            return returned
        member = member_value(self, left, right, env)
        if member is not None:
            return member
        name = self.mask[left:right].strip()
        if name in self.senders:
            return target('known', name=name)
        if re.fullmatch(r'[\w$]+', name):
            binding = self.visible_binding(name, left)
            if binding is not None:
                source = env.get(binding) if env is not None else None
                if source is not None:
                    if (source['status'] != 'known'
                            or source['span'] is None):
                        return target('unprovable')
                    return self.callable_value(
                        source['span'], env, form, wanted)
                return target(
                    'known', binding=binding, source=source)
        return target('unprovable')

    def _member_binding(self, binding, key, position, seen, env,
                        wanted=None):
        if binding in seen:
            return target('unprovable')
        source = env.get(binding)
        if source is not None:
            return self._member_from_source(
                source, key, position, seen | {binding}, env, wanted)
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
            return self.callable_value(assigned, wanted=wanted)
        if binding is None:
            return target('irrelevant')
        span, source = self._latest_value(self.values.get(binding), position)
        if span is None:
            # The receiver is declared after the call site's text position;
            # a body runs when invoked, which is after the declarations, so
            # the value it sees is the one the declaration built.
            span, source = self._latest_value(self.values.get(binding),
                                              len(self.mask))
        if span is None:
            return target('irrelevant')
        if source is not None and self.optional_write(source, position):
            return target('unprovable')
        return self._member_from_span(
            span, key, position, seen | {binding}, env, wanted, source)

    def _member_from_source(self, source, key, position, seen, env,
                            wanted=None):
        if source['status'] != 'known' or source['span'] is None:
            return target(
                'unprovable' if source['status'] == 'unprovable'
                else 'irrelevant')
        span = source['span']
        for part in source.get('path', ()):
            status, span, _ = self._property_span(
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

    def _member_from_span(self, span, key, position, seen, env,
                          wanted=None, created=None):
        left, right = self._unwrap(span)
        call = self._simple_call(left, right)
        if call is not None:
            return self.returned_member(
                call[0], call[1], call[2], key, left)
        constructor = self._constructor_call(left, right)
        if constructor is not None:
            status, _, _ = class_accessor(
                self, constructor[0], key, wanted)
            if (self.visible_binding(constructor[0], left) is None
                    and status == 'none'):
                return target('irrelevant')
            return self.constructed_member(
                constructor[0], constructor[1], constructor[2], key, left,
                wanted)
        status, value, form = self._property_span(
            span, key, position, seen, env, wanted)
        if status != 'known':
            return target(status, form=form)
        if form is not None and form != 'data':
            return target('known', body=value, form=form)
        if created is not None:
            captured = self._captured_value(value, created)
            if captured is not None:
                return captured
        return self.callable_value(value, env, form)

    def _captured_value(self, value, created):
        """Resolve a bare identifier property value at its creation time.

        A named property holds the callable the identifier named when the
        object was built, not whatever the name holds at a later call.
        """
        left, right = self._unwrap(value)
        name = self.mask[left:right].strip()
        if not re.fullmatch(r'[\w$]+', name) or name in self.senders:
            return None
        binding = self.visible_binding(name, created)
        if binding is None:
            return None
        span, _ = self._latest_value(self.values.get(binding), created)
        if span is None:
            return None
        return self.callable_value(span)

    def _method_entry(self, left, right):
        """Key, body and form of a method shorthand entry, or None.

        Head and name reading lives in _jsroute_keys.entry_head, which
        reads quoted, escaped and computed heads exactly; a recognised
        computed key that is not statically known returns a None key, so
        the caller treats the entry as unread rather than skippable.
        """
        found = entry_head(self.mask, self.text, left, right,
                           self.pair_end, self.computed_key)
        if found is None:
            return None
        key, cursor, form, _ = found
        open_paren = next_offset(self.mask, cursor)
        if self.mask[open_paren:open_paren + 1] != '(':
            return None
        close = self.pair_end.get(open_paren)
        if close is None:
            return None
        body_open = next_offset(self.mask, close)
        if (self.mask[body_open:body_open + 1] != '{'
                or self.pair_end.get(body_open) != right):
            return None
        return key, ('block', body_open, right), form

    def _property_span(self, span, key, position, seen, env, wanted=None):
        left, right = self._unwrap(span)
        expression = self.mask[left:right].strip()
        if (self.mask[left:left + 1] == '['
                or self.body_at(self.mask, left) is not None
                or re.match(
                    r'(?:new\b|null|undefined|true|false|[0-9]|["\'])',
                    self.text[left:right])):
            return 'irrelevant', None, None
        if re.fullmatch(r'[\w$]+', expression):
            binding = self.visible_binding(expression, left)
            if binding is None or binding in seen:
                return 'unprovable', None, None
            source = env.get(binding)
            if source is not None:
                if source['status'] != 'known':
                    return source['status'], None, None
                nested = source['span']
                for part in source.get('path', ()):
                    status, nested, _ = self._property_span(
                        nested, part, position, seen | {binding}, env)
                    if status != 'known':
                        return status, None, None
                return self._property_span(
                    nested, key, position, seen | {binding}, env, wanted)
            structured = self.member_bindings.get(binding)
            if (structured is not None
                    and structured.get('source') is not None):
                nested = structured['source']
                for part in structured['path']:
                    status, nested, _ = self._property_span(
                        nested, part, position, seen | {binding}, env)
                    if status != 'known':
                        return status, None, None
                if key in {
                        self.computed_key(item)
                        for item in structured['excluded']}:
                    return 'irrelevant', None, None
                return self._property_span(
                    nested, key, position, seen | {binding}, env, wanted)
            nested = self._latest(self.values.get(binding), position)
            if nested is None:
                return 'unprovable', None, None
            return self._property_span(
                nested, key, position, seen | {binding}, env, wanted)
        if self.mask[left:left + 1] != '{':
            return 'unprovable', None, None
        unknown = False
        for item_left, item_right in reversed(
                _segments(self.mask, left + 1, right - 1)):
            item_left, item_right = _trim(
                self.mask, item_left, item_right)
            if item_left >= item_right:
                continue
            if self.mask[item_left:item_left + 3] == '...':
                status, value, form = self._property_span(
                    (item_left + 3, item_right), key,
                    position, seen, env)
                if status == 'known':
                    return status, value, form
                unknown = unknown or status == 'unprovable'
                continue
            colon = self.top_level(
                self.mask, item_left, item_right, ':')
            equals = self.top_level(
                self.mask, item_left, item_right, '=')
            if colon is None and equals is None:
                found = self._method_entry(item_left, item_right)
                if found is not None:
                    if found[0] is None:
                        unknown = True
                        continue
                    if key is None:
                        if found[2] == wanted:
                            return 'unprovable', None, None
                        continue
                    if found[0] != key:
                        continue
                    if wanted is not None and found[2] != wanted:
                        if found[2] not in ('get', 'set'):
                            break
                        continue
                    return 'known', found[1], found[2]
            key_end = (colon if colon is not None else
                       equals if equals is not None else item_right)
            entry_key = self._source_key(
                head_left(self.mask, self.text, item_left), key_end)
            if entry_key is None:
                unknown = True
                continue
            if entry_key != key:
                continue
            if wanted is not None and wanted != 'data':
                break
            value = ((colon + 1, item_right)
                     if colon is not None else (item_left, key_end))
            return 'known', _trim(self.mask, *value), 'data'
        return ('unprovable' if unknown else 'irrelevant', None, None)

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

    def _simple_call(self, left, right):
        found = re.match(r'([\w$]+)\s*\(', self.mask[left:right])
        if found is None:
            return None
        opening = left + found.end() - 1
        return ((found.group(1), opening, self.pair_end[opening])
                if self.pair_end.get(opening) == right else None)

    def _constructor_call(self, left, right):
        found = re.match(r'new\s+([\w$]+)\s*\(', self.mask[left:right])
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
        return source_key(self.text, left, right, self.computed_key)

    def _latest_value(self, entries, position):
        """Latest value for `entries` at `position`, with its assignment."""
        record_work(self.work, 'receiver_history_queries')
        if not entries:
            return None, None
        index = bisect_right(entries, (position, ())) - 1
        if index < 0:
            return None, None
        when, span = entries[index]
        return span, when

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
                self._history(self.values, binding).append(
                    (match.start(), self._expression_span(match.end())))

    def _index_functions(self):
        record_work(self.work, 'receiver_source_characters', len(self.mask))
        for match in re.finditer(
                r'\bfunction\s+([\w$]+)\s*\(', self.mask):
            binding = self.visible_binding(match.group(1), match.start())
            body = self.body_at(self.mask, match.start())
            if binding is not None and body is not None:
                self._history(self.values, binding).append(
                    (binding[1], (match.start(), body[2])))
        for entries in self.values.values():
            entries.sort()

    def _index_member_writes(self):
        record_work(
            self.work, 'receiver_source_characters', len(self.mask) * 2)
        patterns = [(re.compile(
            r'\b([\w$]+)\s*\.\s*([\w$]+)\s*=(?!=|>)'), self.mask)]
        patterns.append((re.compile(
            r'\b([\w$]+)\s*\[\s*(["\'])'
            r'((?:\\[\s\S]|(?!\2)[^\\])*)\2\s*\]'
            r'\s*=(?!=|>)'), self.text))
        for pattern, source in patterns:
            for match in pattern.finditer(source):
                if self.mask[match.start()] != self.text[match.start()]:
                    continue
                owner = match.group(1)
                key = (match.group(2) if source is self.mask else
                       decode_string_literal(
                           match.group(2) + match.group(3)
                           + match.group(2)))
                span = self._expression_span(match.end())
                if owner in ('globalThis', 'window'):
                    self._history(self.globals, key).append(
                        (match.start(), span))
                    continue
                binding = self.visible_binding(owner, match.start())
                if binding is not None:
                    self._history(self.members, (binding, key)).append(
                        (match.start(), span))
        for entries in (*self.members.values(), *self.globals.values()):
            entries.sort()

    def _index_computed_keys(self, bindings):
        record_work(self.work, 'receiver_source_characters', len(self.mask))
        for match in bindings:
            binding = self.visible_binding(match.group(1), match.start())
            quoted = re.match(
                r'\s*(["\'])(?:\\[\s\S]|(?!\1)[^\\])*\1',
                self.text[match.end():])
            if binding is None or quoted is None:
                continue
            # The literal must be the whole initializer: 'g' + 'o' would
            # otherwise be indexed under its leading fragment alone.
            expression_end = self.expression_end(self.mask, match.end())
            if self.mask[match.end() + quoted.end():expression_end].strip():
                continue
            self._history(self.computed, binding).append(
                (match.start(), decode_string_literal(quoted.group(0))))
