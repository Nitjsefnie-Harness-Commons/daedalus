"""Indexed sender-state queries for the JavaScript routing guard."""
from _jsroute_calls import member_source


def _ancestors(scopes, owner):
    found = []
    while owner is not None:
        found.append(owner)
        owner = scopes[owner]['parent']
    return tuple(reversed(found))


def sender_query_index(requests, scopes, events, invocations, replay,
                       context):
    """Answer scalar sender queries with one prefix walk per scope chain."""
    grouped = {}
    for request in requests:
        chain = _ancestors(scopes, context['scope_at'](request['limit']))
        grouped.setdefault(chain, []).append(request)
    answers = {}
    for chain, queries in grouped.items():
        owners = set(chain)
        scheduled = []
        for start, kind, match in events:
            if kind in ('bind', 'interp') and (
                    context['scope_at'](start) in owners):
                scheduled.append(
                    (start, 0, start, None, False, match, kind))
        runtime_values = {}
        previous_call = -1
        for call in invocations:
            if call['parent'] or call['scope'] not in owners:
                continue
            replay.clear_between(
                runtime_values, previous_call, call['order'], owners)
            result = replay.run(call, runtime_values)
            scheduled.extend(
                (call['order'], order, start, call, path_optional, match,
                 'bind')
                for order, (start, match, path_optional)
                in enumerate(result.writes, 1))
            previous_call = call['order']
        scheduled.sort(key=lambda item: item[:3])
        states = {}
        cursor = 0
        for request in sorted(queries, key=lambda item: item['limit']):
            limit = request['limit']
            while cursor < len(scheduled) and scheduled[cursor][0] < limit:
                (_, _, start, call, path_optional, match,
                 kind) = scheduled[cursor]
                cursor += 1
                target = context['visible_binding'](
                    match.group(1), start)
                if target is None:
                    continue
                if kind == 'interp':
                    states[target] = context['unprovable']
                    continue
                end = context['expression_end'](
                    context['mask'], match.end())
                value = context['sender_state'](
                    match.end(), end, states)
                owner_end = scopes[context['scope_at'](start)]['end']
                optional = path_optional or context['optional_write'](
                    start, owner_end)
                if call is not None:
                    call_end = scopes[call['scope']]['end']
                    optional = optional or context['optional_write'](
                        call['order'], call_end)
                states[target] = (
                    context['merged_sender'](states.get(target), value)
                    if optional else value)
            if request['kind'] == 'literal':
                value = request['value']
            elif request['kind'] == 'binding':
                value = states.get(request['value'])
            else:
                start, end = request['value']
                value = context['sender_state'](start, end, states)
            answers[request['key']] = value
    return answers


def build_sender_queries(candidate_bindings, reached_calls, scopes, events,
                         invocations, replay, context):
    """Build every sender lookup before the indexed prefix walk."""
    requests = []

    def request_sender(limit, kind, value):
        key = len(requests)
        requests.append({'key': key, 'limit': limit,
                         'kind': kind, 'value': value})
        return key

    call_queries = {}
    for call in invocations:
        if (call['binding'] in candidate_bindings
                and call['start'] not in reached_calls):
            source = call['source']
            if source is None:
                kind, value = 'binding', call['binding']
            elif source['status'] == 'known' and source['span'] is not None:
                kind, value = 'span', source['span']
            elif source['status'] == 'empty':
                kind, value = 'literal', None
            else:
                kind, value = 'literal', context['unprovable']
            call_queries[id(call)] = request_sender(
                call['start'], kind, value)
    record_senders = {}
    for records in reached_calls.values():
        for record in records:
            call, execution, sources = record
            binding = call['binding']
            source_record = sources.get(binding)
            if source_record is None:
                query = request_sender(execution, 'binding', binding)
                record_senders[id(record)] = ('query', query)
                continue
            status = source_record['status']
            source = source_record['span']
            if status == 'unprovable':
                record_senders[id(record)] = (
                    'value', context['unprovable'])
                continue
            if status == 'empty':
                record_senders[id(record)] = ('value', None)
                continue
            if call['member'] is not None:
                status, source = member_source(
                    context['mask'], context['text'], source,
                    call['member'], context['top_level'],
                    context['computed_key'],
                    source_record['excluded'])
                if status != 'known':
                    value = (context['unprovable']
                             if status == 'unprovable' else None)
                    record_senders[id(record)] = ('value', value)
                    continue
            query = request_sender(execution, 'span', source)
            record_senders[id(record)] = ('query', query)
    escape_queries = {}
    for start, kind, match in events:
        if kind != 'escape' or start in reached_calls:
            continue
        binding = context['visible_binding'](match.group(1), start)
        if binding is not None:
            escape_queries[(start, match.group(1))] = request_sender(
                start, 'binding', binding)
    answers = sender_query_index(
        requests, scopes, events, invocations, replay, context)
    return {
        'answers': answers, 'calls': call_queries,
        'records': record_senders, 'escapes': escape_queries}
