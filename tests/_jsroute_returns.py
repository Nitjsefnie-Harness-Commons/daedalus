"""Direct callable returns resolved from receiver expressions."""
import re

from _jsroute_keys import class_accessor


def _target(status, binding=None, body=None, member=None, name=None,
            source=None, form=None):
    return {'status': status, 'binding': binding, 'body': body,
            'member': member, 'name': name, 'source': source, 'form': form}


def callable_return(receiver, left, right):
    """Callable returned by a complete named or member call, if present."""
    call = receiver._simple_call(left, right)
    if call is not None:
        body, env = receiver._call_environment(
            call[0], call[1], call[2], left)
        return _returned_expression(receiver, body, env)
    member_call = _simple_member_call(receiver, left, right)
    if member_call is None:
        return None
    called = receiver.member(member_call[0], member_call[1], left)
    args = receiver.split(
        receiver.mask, receiver.text, member_call[2] + 1,
        member_call[3] - 1)
    if called['form'] == 'get':
        return _invoke_callable(
            receiver, getter_value(receiver, called), args, left)
    if called['status'] != 'known' or called['body'] is None:
        return _target(called['status'])
    scope = receiver._scope_for(called['body'])
    if scope is None:
        return _target('unprovable')
    env = receiver._argument_environment(scope, args)
    return _returned_expression(receiver, called['body'], env)


def getter_value(receiver, value):
    """Resolve a getter's direct return, retaining unresolved provenance."""
    if value['status'] != 'known' or value['form'] != 'get':
        return value
    returned = _returned_expression(receiver, value['body'], {})
    if returned['status'] == 'unprovable':
        returned['form'] = 'get'
    return returned


def member_value(receiver, left, right, env=None):
    """Resolve every statically named hop in one member expression."""
    parsed = _member_chain(receiver, left, right)
    if parsed is None:
        return None
    owner, keys = parsed
    value = receiver.member(owner, keys[0], left, env=env)
    for key in keys[1:]:
        value = getter_value(receiver, value)
        if value['status'] != 'known':
            return value
        if value['binding'] is None:
            return _target('unprovable', form=value['form'])
        value = receiver._member_binding(
            value['binding'], key, left, frozenset(), env or {})
    return getter_value(receiver, value)


def _member_chain(receiver, left, right):
    found = re.match(r'[\w$]+', receiver.mask[left:right])
    if found is None:
        return None
    owner = found.group()
    cursor = left + found.end()
    keys = []
    while cursor < right:
        while cursor < right and receiver.mask[cursor].isspace():
            cursor += 1
        if receiver.mask[cursor:cursor + 1] == '.':
            cursor += 1
            while cursor < right and receiver.mask[cursor].isspace():
                cursor += 1
            found = re.match(r'[\w$]+', receiver.mask[cursor:right])
            if found is None:
                return None
            keys.append(found.group())
            cursor += found.end()
            continue
        if receiver.mask[cursor:cursor + 1] != '[':
            return None
        close = receiver.pair_end.get(cursor)
        if close is None or close > right:
            return None
        key = receiver._source_key(cursor, close)
        if key is None:
            return None
        keys.append(key)
        cursor = close
    return (owner, keys) if keys else None


def constructed_value(receiver, name, opening, close, key, position,
                      wanted=None):
    """Callable value or accessor supplied by one constructed receiver."""
    is_class, accessor = class_accessor(
        receiver, name, key, wanted, position)
    if is_class:
        if accessor is None:
            return _target('irrelevant')
        return _target('known', body=accessor, form=wanted)
    body, env = receiver._call_environment(name, opening, close, position)
    if body is None:
        return _target('unprovable')
    if key is None:
        return _target('unprovable')
    pattern = re.compile(
        r'\bthis\s*\.\s*' + re.escape(key) + r'\s*=(?!=|>)')
    found = list(pattern.finditer(receiver.mask, body[1], body[2]))
    if len(found) != 1:
        return _target('unprovable')
    start = found[0].end()
    end = min(receiver.expression_end(receiver.mask, start), body[2])
    return receiver.callable_value((start, end), env)


def callable_body(receiver, binding, position, seen=frozenset()):
    """Follow callable aliases to the body held at `position`."""
    if binding is None or binding in seen:
        return None
    span = receiver._latest(receiver.values.get(binding), position)
    if span is None:
        return None
    body = receiver.body_at(receiver.mask, span[0])
    if body is not None:
        return body
    value = receiver.callable_value(span)
    if value['body'] is not None:
        return value['body']
    return callable_body(
        receiver, value['binding'], span[0], seen | {binding})


def _invoke_callable(receiver, value, args, position):
    if value['status'] != 'known':
        return _target(value['status'], form=value['form'])
    body = value['body']
    if body is None:
        body = callable_body(receiver, value['binding'], position)
    if body is None:
        return _target('unprovable', form=value['form'])
    scope = receiver._scope_for(body)
    if scope is None:
        return _target('unprovable', form=value['form'])
    env = receiver._argument_environment(scope, args)
    return _returned_expression(receiver, body, env)


def _returned_expression(receiver, body, env):
    if body is None:
        return _target('unprovable')
    expression = receiver._returned_expression(body)
    if expression is None:
        return _target('unprovable')
    returned = receiver.callable_value(expression, env)
    source = receiver.mask[expression[0]:expression[1]]
    if (returned['status'] == 'unprovable'
            and any(re.search(rf'\b{re.escape(name)}\b', source)
                    for name in receiver.senders)):
        returned['form'] = 'callable-return'
    return returned


def _simple_member_call(receiver, left, right):
    found = re.match(
        r'([\w$]+)\s*\.\s*([\w$]+)\s*\(', receiver.mask[left:right])
    if found is None:
        return None
    opening = left + found.end() - 1
    close = receiver.pair_end.get(opening)
    if close != right:
        return None
    return found.group(1), found.group(2), opening, close
