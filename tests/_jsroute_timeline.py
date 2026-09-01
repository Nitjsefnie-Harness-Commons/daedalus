"""Source-ordered function values for JavaScript lexical bindings."""
from bisect import bisect_left, bisect_right
import re

from _jsroute_calls import parameter_sources


def declaration_bindings(mask, text, split_top_level):
    """Yield each simple binding in a const, let, or var declaration."""
    for head in re.finditer(r'\b(const|let|var)\b', mask):
        depth = 0
        end = len(mask)
        for pos in range(head.end(), len(mask)):
            char = mask[pos]
            if char in '([{':
                depth += 1
            elif char in ')]}':
                if depth == 0:
                    end = pos
                    break
                depth -= 1
            elif char in ';\n' and depth == 0:
                end = pos
                break
        for left, right in split_top_level(
                mask, text, head.end(), end):
            found = re.match(r'\s*([\w$]+)', mask[left:right])
            if found:
                yield (head.group(1), found.group(1),
                       left + found.start(1))


def function_reference(binding, position, owner):
    return ('reference', binding, position, owner)


def lexical_limits(scopes, start, position):
    limits = []
    while start is not None:
        limits.append((start, position))
        start = scopes[start]['parent']
    return tuple(reversed(limits))


class FunctionTimeline:
    """Function values of each binding, selected at an invocation position."""

    def __init__(self, is_optional):
        self._entries = {}
        self._is_optional = is_optional

    def add(self, binding, owner, effective, definition, value):
        self._entries.setdefault(binding, []).append(
            (owner, effective, definition, value))

    def values_at(self, binding, overrides=None, limits=(),
                  seen=frozenset()):
        if overrides is not None and binding in overrides:
            return overrides[binding]
        if binding is None or binding in seen:
            return ()
        active = []
        entries = self._entries.get(binding, ())
        for owner, cutoff in limits:
            owned = sorted(
                (entry for entry in entries if entry[0] == owner),
                key=lambda entry: entry[1:3])
            for _, effective, definition, value in owned:
                if effective > cutoff:
                    continue
                resolved = self._resolve(
                    value, overrides, seen | {binding}, limits)
                if self._is_optional(definition, cutoff):
                    active.extend(
                        item for item in resolved if item not in active)
                else:
                    active = list(resolved)
        return tuple(active)

    def _resolve(self, value, overrides, seen, limits):
        if isinstance(value, tuple) and value[:1] == ('reference',):
            clipped = []
            for owner, cutoff in limits:
                clipped.append((
                    owner, value[2] - 1 if owner == value[3] else cutoff))
                if owner == value[3]:
                    break
            return self.values_at(
                value[1], overrides, tuple(clipped), seen - {value[1]})
        return (value,)

    def assignment_value(self, binding, definition, overrides, limits):
        for _, _, found, value in self._entries.get(binding, ()):
            if found == definition:
                return self._resolve(
                    value, overrides, {binding}, limits)
        return ()


class InvocationReplay:
    """Replay callable-binding writes in the order known calls execute."""

    def __init__(self, timeline, scopes, events, invocations, source,
                 scope_at, visible_binding, optional_write):
        self.timeline = timeline
        self.scopes = scopes
        self.mask = source['mask']
        self.text = source['text']
        self.scope_at = scope_at
        self.visible_binding = visible_binding
        self.body_at = source['body']
        self.expression_end = source['expression_end']
        self.optional_write = optional_write
        self.top_level = source['top_level']
        self.bind_events = {}
        self.operations = {}
        for start, kind, match in events:
            if kind != 'bind':
                continue
            owner = scope_at(start)
            self.bind_events.setdefault(owner, []).append(
                (start, match))
            self.operations.setdefault(owner, []).append(
                (start, 'bind', match))
        for call in invocations:
            if call['status'] == 'irrelevant' or call['parent']:
                continue
            self.operations.setdefault(call['scope'], []).append(
                (call['order'], 'call', call))
        for operations in self.operations.values():
            operations.sort(key=lambda item: item[0])
        self.bind_starts = {
            owner: [item[0] for item in values]
            for owner, values in self.bind_events.items()}

    def clear_between(self, values, lower, upper, owners):
        for owner in owners:
            starts = self.bind_starts.get(owner, ())
            lower_index = bisect_right(starts, lower)
            upper_index = bisect_left(starts, upper)
            for start, match in self.bind_events.get(
                    owner, ())[lower_index:upper_index]:
                values.pop(
                    self.visible_binding(match.group(1), start), None)

    def run(self, call, inherited, seen=frozenset(),
            inherited_optional=False, limits=None, sender_sources=None,
            execution=None):
        call_start = call['start']
        if limits is None:
            limits = lexical_limits(
                self.scopes, self.scope_at(call_start), call_start)
        if execution is None:
            execution = call['order']
        writes = []
        calls = []
        unknowns = []
        active_sources = (sender_sources if sender_sources is not None
                          else {})
        for argument_call in call['argument_calls']:
            calls.append((argument_call, execution,
                          dict(active_sources)))
            nested_writes, nested_calls, nested_unknowns = self.run(
                argument_call, inherited, seen, inherited_optional,
                limits, active_sources, execution)
            writes.extend(nested_writes)
            calls.extend(nested_calls)
            unknowns.extend(nested_unknowns)
        if call['status'] == 'unprovable':
            unknowns.append(call)
            return writes, calls, unknowns
        if call['status'] == 'irrelevant':
            return writes, calls, unknowns
        if call['body'] is not None:
            bodies = (call['body'],)
        else:
            bodies = self.timeline.values_at(
                call['binding'], inherited, limits)
        for body in bodies:
            if body is None:
                continue
            function_scope = self._scope_for(body)
            if function_scope is None or function_scope in seen:
                continue
            overrides = dict(inherited)
            sources = dict(active_sources)
            self._override_parameters(
                overrides, inherited, function_scope, call_start,
                call['args'], limits, sources)
            path_optional = inherited_optional or len(bodies) > 1
            for start, kind, item in self.operations.get(
                    function_scope, ()):
                if kind == 'bind':
                    applied = self._apply_assignment(
                        item, start, function_scope, path_optional,
                        overrides, inherited, limits)
                    if applied:
                        target = applied['target']
                        source = self._assignment_source(
                            item, applied['optional'])
                        sources[target] = source
                        if applied['captured']:
                            active_sources[target] = source
                        writes.append((start, item, path_optional))
                    continue
                nested_optional = (
                    path_optional or self.optional_write(
                        start, self.scopes[function_scope]['end']))
                nested_limits = limits + ((function_scope, start),)
                calls.append((item, execution, dict(sources)))
                nested_writes, nested_calls, nested_unknowns = self.run(
                    item, overrides, seen | {function_scope},
                    nested_optional, nested_limits, sources, execution)
                writes.extend(nested_writes)
                calls.extend(nested_calls)
                unknowns.extend(nested_unknowns)
        return writes, calls, unknowns

    def _scope_for(self, body):
        inset = body[0] == 'block'
        start = body[1] + inset
        end = body[2] - inset
        return next((
            index for index, scope in enumerate(self.scopes)
            if scope['start'] == start and scope['end'] == end), None)

    def _override_parameters(self, overrides, inherited, function_scope,
                             call_start, args, limits, sender_sources):
        scope = self.scopes[function_scope]
        for source in parameter_sources(
                scope['param_order'], args, self.mask, self.text,
                self.top_level):
            target = self.visible_binding(
                source['name'], scope['start'])
            if target is None:
                continue
            status = source['status']
            span = source['span']
            if status != 'known':
                overrides[target] = ()
                sender_sources[target] = {
                    'status': status, 'span': span,
                    'rest': source['rest'],
                    'excluded': source['excluded']}
                continue
            raw_expr = self.mask[span[0]:span[1]]
            expr = raw_expr.strip()
            source_binding = (
                self.visible_binding(expr, call_start)
                if re.fullmatch(r'[\w$]+', expr) else None)
            direct = self.body_at(self.mask, span[0])
            if source_binding is not None:
                value = self.timeline.values_at(
                    source_binding, inherited, limits)
            else:
                value = (direct,) if direct is not None else ()
            overrides[target] = value
            sender_sources[target] = sender_sources.get(
                source_binding, {
                    'status': status, 'span': span,
                    'rest': source['rest'],
                    'excluded': source['excluded']})

    def _apply_assignment(self, match, start, function_scope,
                          path_optional, overrides, inherited, limits):
        target = self.visible_binding(match.group(1), start)
        if target is None:
            return None
        before_body = start < self.scopes[function_scope]['start']
        if before_body and target in overrides:
            return None
        execution = limits + ((function_scope, start),)
        value = self.timeline.assignment_value(
            target, start, overrides, execution)
        optional = path_optional or self.optional_write(
            start, self.scopes[function_scope]['end'])
        if optional:
            before = self.timeline.values_at(
                target, overrides,
                limits + ((function_scope, start - 1),))
            value = tuple(dict.fromkeys((*before, *value)))
        overrides[target] = value
        owner = self.scopes[function_scope]
        local = owner['param_start'] <= target[1]
        captured = not local or target[2] > owner['end']
        if captured:
            inherited[target] = value
        return {'target': target, 'optional': optional,
                'captured': captured}

    def _assignment_source(self, match, optional):
        if optional:
            return {'status': 'unprovable', 'span': None,
                    'rest': None, 'excluded': ()}
        return {
            'status': 'known',
            'span': (match.end(), self.expression_end(
                self.mask, match.end())),
            'rest': None, 'excluded': ()}
