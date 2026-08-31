"""Source-ordered function values for JavaScript lexical bindings."""
import re


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
        for left, right in split_top_level(mask, text, head.end(), end):
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

    def values_at(self, binding, overrides=None, limits=(), seen=frozenset()):
        if overrides is not None and binding in overrides:
            return overrides[binding]
        if binding is None or binding in seen:
            return ()
        active = []
        entries = self._entries.get(binding, ())
        for owner, cutoff in limits:
            owned = sorted((entry for entry in entries if entry[0] == owner),
                           key=lambda entry: entry[1:3])
            for _, effective, definition, value in owned:
                if effective > cutoff:
                    continue
                resolved = self._resolve(
                    value, overrides, seen | {binding}, limits)
                if self._is_optional(definition, cutoff):
                    active.extend(item for item in resolved
                                  if item not in active)
                else:
                    active = list(resolved)
        return tuple(active)

    def _resolve(self, value, overrides, seen, limits):
        if isinstance(value, tuple) and value[:1] == ('reference',):
            clipped = []
            for owner, cutoff in limits:
                clipped.append((owner, value[2] - 1 if owner == value[3]
                                else cutoff))
                if owner == value[3]:
                    break
            return self.values_at(
                value[1], overrides, tuple(clipped), seen - {value[1]})
        return (value,)

    def assignment_value(self, binding, definition, overrides, limits):
        for _, _, found, value in self._entries.get(binding, ()):
            if found == definition:
                return self._resolve(value, overrides, {binding}, limits)
        return ()


class InvocationReplay:
    """Replay callable-binding writes in the order known calls execute."""

    def __init__(self, timeline, scopes, events, invocations, mask,
                 scope_at, visible_binding, body_at, optional_write):
        self.timeline = timeline
        self.scopes = scopes
        self.events = events
        self.invocations = invocations
        self.mask = mask
        self.scope_at = scope_at
        self.visible_binding = visible_binding
        self.body_at = body_at
        self.optional_write = optional_write

    def clear_between(self, values, lower, upper, owners):
        for start, kind, match in self.events:
            if (kind == 'bind' and lower < start < upper
                    and self.scope_at(start) in owners):
                values.pop(self.visible_binding(match.group(1), start), None)

    def run(self, call, inherited, seen=frozenset(),
            inherited_optional=False, limits=None, sender_sources=None,
            execution=None):
        call_start, binding, args = call
        if limits is None:
            limits = lexical_limits(
                self.scopes, self.scope_at(call_start), call_start)
        if execution is None:
            execution = call_start
        writes = []
        calls = []
        bodies = self.timeline.values_at(binding, inherited, limits)
        for body in bodies:
            if body is None:
                continue
            function_scope = self._scope_for(body)
            if function_scope is None or function_scope in seen:
                continue
            overrides = dict(inherited)
            sources = dict(sender_sources or {})
            self._override_parameters(
                overrides, inherited, function_scope, call_start, args,
                limits, sources)
            operations = [(start, 'bind', match)
                          for start, kind, match in self.events
                          if kind == 'bind'
                          and self.scope_at(start) == function_scope]
            operations.extend((nested[0], 'call', nested)
                              for nested in self.invocations
                              if self.scope_at(nested[0]) == function_scope)
            path_optional = inherited_optional or len(bodies) > 1
            for start, kind, item in sorted(operations):
                if kind == 'bind':
                    applied = self._apply_assignment(
                        item, start, function_scope, path_optional,
                        overrides, inherited, limits)
                    if applied:
                        sources.pop(self.visible_binding(
                            item.group(1), start), None)
                        writes.append((start, item, path_optional))
                    continue
                nested_optional = (path_optional or self.optional_write(
                    start, self.scopes[function_scope]['end']))
                nested_limits = limits + ((function_scope, start),)
                calls.append((item, execution, dict(sources)))
                nested_writes, nested_calls = self.run(
                    item, overrides, seen | {function_scope},
                    nested_optional, nested_limits, sources, execution)
                writes.extend(nested_writes)
                calls.extend(nested_calls)
        return writes, calls

    def _scope_for(self, body):
        inset = body[0] == 'block'
        start, end = body[1] + inset, body[2] - inset
        return next((index for index, scope in enumerate(self.scopes)
                     if scope['start'] == start and scope['end'] == end),
                    None)

    def _override_parameters(self, overrides, inherited, function_scope,
                             call_start, args, limits, sender_sources):
        scope = self.scopes[function_scope]
        for names, span in zip(scope['param_order'], args):
            if isinstance(names, str):
                names = (names,)
            raw_expr = self.mask[span[0]:span[1]]
            expr = raw_expr.strip()
            expr_start = span[0] + len(raw_expr) - len(raw_expr.lstrip())
            if expr == 'undefined':
                continue
            for name in names:
                source_span = span
                source_expr = expr
                if expr.startswith('{'):
                    member = re.search(
                        r'\b' + re.escape(name)
                        + r'\s*:\s*([\w$]+)', expr)
                    if member:
                        source_span = (
                            expr_start + member.start(1),
                            expr_start + member.end(1))
                        source_expr = member.group(1)
                source = (self.visible_binding(source_expr, call_start)
                          if re.fullmatch(r'[\w$]+', source_expr) else None)
                direct = self.body_at(self.mask, source_span[0])
                value = (
                    self.timeline.values_at(source, inherited, limits)
                    if source is not None else (direct,))
                target = self.visible_binding(name, scope['start'])
                overrides[target] = value
                sender_sources[target] = sender_sources.get(
                    source, source_span)

    def _apply_assignment(self, match, start, function_scope, path_optional,
                          overrides, inherited, limits):
        target = self.visible_binding(match.group(1), start)
        if target is None:
            return False
        before_body = start < self.scopes[function_scope]['start']
        if before_body and target in overrides:
            return False
        execution = limits + ((function_scope, start),)
        value = self.timeline.assignment_value(
            target, start, overrides, execution)
        optional = path_optional or self.optional_write(
            start, self.scopes[function_scope]['end'])
        if optional:
            before = self.timeline.values_at(
                target, overrides, limits + ((function_scope, start - 1),))
            value = tuple(dict.fromkeys((*before, *value)))
        overrides[target] = value
        owner = self.scopes[function_scope]
        local = owner['param_start'] <= target[1]
        if not local or target[2] > owner['end']:
            inherited[target] = value
        return True
