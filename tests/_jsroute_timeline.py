"""Source-ordered function values for JavaScript lexical bindings."""
from bisect import bisect_left, bisect_right
import re
from typing import NamedTuple

from _jsroute_calls import parameter_bindings, parameter_sources
from _jsroute_source import record_work


class ReplayResult(NamedTuple):
    writes: list
    calls: list
    unknowns: list


def declaration_records(mask, text, pair_end, top_level, split_top_level):
    """Return structured bindings and initializer spans for declarations."""
    found = []
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
            equals = top_level(mask, left, right, '=')
            pattern_end = equals if equals is not None else right
            _, specs = parameter_bindings(
                mask, text, left, pattern_end, pair_end,
                top_level, split_top_level)
            source = ((equals + 1, right)
                      if equals is not None else None)
            for spec in specs:
                for binding in spec['bindings']:
                    found.append({
                        **binding, 'kind': head.group(1),
                        'declaration_position': head.start(),
                        'source': source})
    return found


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

    def entries(self):
        """Every binding's stored values as (binding, entries) pairs."""
        return self._entries.items()

    def assignment_value(self, binding, definition, overrides, limits):
        for _, _, found, value in self._entries.get(binding, ()):
            if found == definition:
                return self._resolve(
                    value, overrides, {binding}, limits)
        return ()


class InvocationReplay:
    """Replay callable-binding writes in the order known calls execute."""

    def __init__(self, timeline, scopes, events, invocations, source,
                 scope_at, visible_binding, optional_write, work=None,
                 carries=()):
        self.timeline = timeline
        self.scopes = scopes
        self.mask = source['mask']
        self.text = source['text']
        self.scope_at = scope_at
        self.visible_binding = visible_binding
        self.body_at = source['body']
        self.expression_end = source['expression_end']
        self.optional_write = optional_write
        self.work = work
        self.top_level = source['top_level']
        self.computed_key = source['computed_key']
        self.body_scopes = {
            (scope['start'], scope['end']): index
            for index, scope in enumerate(scopes)}
        self.carries = set(carries)
        # Bindings whose invocations provably held a body other than the one
        # a send sits in: the record a dead-body verdict is read from.
        self.invoked = {}
        # The state the top-level walk reads: a write to a binding declared
        # outside the body that wrote it outlives the body.
        self.root_state = None
        self.bind_events = {}
        self.operations = {}
        for start, kind, match in events:
            if kind not in ('bind', 'interp'):
                continue
            owner = scope_at(start)
            if kind == 'bind':
                self.bind_events.setdefault(owner, []).append(
                    (start, match))
            self.operations.setdefault(owner, []).append(
                (start, kind, match))
        for call in invocations:
            if call['status'] == 'irrelevant' or call['parent']:
                continue
            self.operations.setdefault(call['scope'], []).append(
                (call['order'], 'call', call))
        for operations in self.operations.values():
            operations.sort(key=lambda item: item[0])
        record_work(
            work, 'replay_index_entries',
            sum(len(items) for items in self.operations.values()))
        self.bind_starts = {
            owner: [item[0] for item in values]
            for owner, values in self.bind_events.items()}

    def clear_between(self, values, lower, upper, owners):
        for owner in owners:
            starts = self.bind_starts.get(owner, ())
            lower_index = bisect_right(starts, lower)
            upper_index = bisect_left(starts, upper)
            selected = self.bind_events.get(
                owner, ())[lower_index:upper_index]
            record_work(self.work, 'replay_clear_entries', len(selected))
            for start, match in selected:
                values.pop(
                    self.visible_binding(match.group(1), start), None)

    def run(self, call, inherited, seen=frozenset(),
            inherited_optional=False, limits=None, sender_sources=None,
            execution=None):
        record_work(self.work, 'replay_calls')
        if self.root_state is None:
            self.root_state = inherited
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
        record_work(
            self.work, 'replay_argument_calls', len(call['argument_calls']))
        for argument_call in call['argument_calls']:
            calls.append((argument_call, execution,
                          dict(active_sources)))
            nested = self.run(
                argument_call, inherited, seen, inherited_optional,
                limits, active_sources, execution)
            writes.extend(nested.writes)
            calls.extend(nested.calls)
            unknowns.extend(nested.unknowns)
        if call['status'] == 'unprovable' or (
                call['binding'] is not None
                and call['binding'] in self.carries):
            unknowns.append(call)
            return ReplayResult(writes, calls, unknowns)
        if call['status'] == 'irrelevant':
            return ReplayResult(writes, calls, unknowns)
        if call['body'] is not None:
            bodies = (call['body'],)
        else:
            bodies = self.timeline.values_at(
                call['binding'], inherited, limits)
        record_work(self.work, 'replay_body_candidates', len(bodies))
        if call['binding'] is not None and self.root_state is inherited:
            held = {self.scope_of(body) for body in bodies}
            held.discard(None)
            self.invoked.setdefault(call['binding'], set()).update(held)
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
                call['args'], limits, sources,
                call.get('argument_status'))
            path_optional = inherited_optional or len(bodies) > 1
            operations = self.operations.get(function_scope, ())
            record_work(self.work, 'replay_operations', len(operations))
            for start, kind, item in operations:
                if kind == 'interp':
                    target = self.visible_binding(item.group(1), start)
                    if target is None:
                        continue
                    source = {'status': 'unprovable', 'span': None,
                              'rest': None, 'excluded': ()}
                    overrides[target] = ()
                    sources[target] = source
                    owner = self.scopes[function_scope]
                    local = (owner['start'] <= target[1]
                             and target[2] <= owner['end'])
                    if not local:
                        inherited[target] = ()
                        active_sources[target] = source
                        if self.root_state is not None:
                            self.root_state[target] = ()
                    writes.append((start, item, path_optional, kind, source))
                    continue
                if kind == 'bind':
                    applied = self._apply_assignment(
                        item, start, function_scope, path_optional,
                        overrides, inherited, limits)
                    if applied:
                        target = applied['target']
                        source = self._assignment_source(
                            item, applied['optional'])
                        value_span = source['span']
                        value_name = (
                            self.mask[value_span[0]:value_span[1]].strip()
                            if value_span is not None else '')
                        value_binding = (
                            self.visible_binding(value_name, start)
                            if re.fullmatch(r'[\w$]+', value_name) else None)
                        delivered = sources.get(value_binding)
                        sources[target] = source
                        if applied['captured']:
                            active_sources[target] = source
                        writes.append((start, item, path_optional, kind,
                                       delivered))
                    continue
                nested_optional = (
                    path_optional or self.optional_write(
                        start, self.scopes[function_scope]['end']))
                nested_limits = limits + ((function_scope, start),)
                calls.append((item, execution, dict(sources)))
                nested = self.run(
                    item, overrides, seen | {function_scope},
                    nested_optional, nested_limits, sources, execution)
                writes.extend(nested.writes)
                calls.extend(nested.calls)
                unknowns.extend(nested.unknowns)
        return ReplayResult(writes, calls, unknowns)

    def scope_of(self, body):
        """Scope index a function body belongs to, or None."""
        if not (isinstance(body, tuple)
                and body[:1] in (('block',), ('expr',))):
            return None
        return self._scope_for(body)

    def _scope_for(self, body):
        inset = body[0] == 'block'
        start = body[1] + inset
        end = body[2] - inset
        return self.body_scopes.get((start, end))

    def _override_parameters(self, overrides, inherited, function_scope,
                             call_start, args, limits, sender_sources,
                             argument_status=None):
        scope = self.scopes[function_scope]
        sources = parameter_sources(
            scope['param_order'], args, self.mask, self.text,
            self.top_level, self.computed_key)
        if argument_status is not None:
            for source in sources:
                source['status'] = argument_status
                source['span'] = None
        for source in sources:
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
        # A write to a binding declared outside this body outlives the body:
        # it escapes to the caller's state, so the caller sees the write.
        local = owner['start'] <= target[1] and target[2] <= owner['end']
        captured = not local
        if captured:
            inherited[target] = value
            if self.root_state is not None:
                self.root_state[target] = value
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


def body_death_index(timeline, replay, invocations, bindings, mask,
                     visible_binding):
    """Answer whether a body is provably dead: no invocation can hold it.

    A body no binding holds, one a holder was never invoked for, or one a
    handed-away binding could still take, keeps its report: the model
    proves a send dead, it does not assume it.
    """
    body_holders = {}
    for binding, entries in timeline.entries():
        for entry in entries:
            if entry[3] is None:
                continue
            scope = replay.scope_of(entry[3])
            if scope is not None:
                body_holders.setdefault(scope, set()).add(binding)

    alias_targets = {}
    for match in bindings:
        alias = re.match(r'\s*([\w$]+)\s*(?=[,;)\n])',
                         mask[match.end():])
        if alias is None:
            continue
        source = visible_binding(alias.group(1), match.start())
        target = visible_binding(match.group(1), match.start())
        if source is not None and target is not None:
            alias_targets.setdefault(source, set()).add(target)

    escaped = set()
    for call in invocations:
        for span in call['args']:
            name = mask[span[0]:span[1]].strip()
            if re.fullmatch(r'[\w$]+', name):
                binding = visible_binding(name, span[0])
                if binding is not None:
                    escaped.add(binding)

    def is_dead(scope):
        holders = set(body_holders.get(scope, ()))
        for holder in set(holders):
            holders |= alias_targets.get(holder, set())
        if not holders or holders & escaped:
            return False
        return all(replay.invoked.get(holder)
                   and scope not in replay.invoked[holder]
                   for holder in holders)

    return is_dead
