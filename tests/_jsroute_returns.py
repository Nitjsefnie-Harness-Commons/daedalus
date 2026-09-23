"""Direct callable returns resolved from receiver expressions."""
import re

from _jsroute_keys import class_accessor
from _jsroute_source import function_body_at


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
    if member_call[1] == 'bind':
        bound = _bound_member(
            receiver, left, member_call[2], member_call[3])
        if bound is not None:
            return bound
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
        returned = _this_member(receiver, value['body']) or returned
    if returned['status'] == 'unprovable':
        returned['form'] = 'get'
    return returned


def _enclosing_literal(receiver, position):
    """Innermost object-literal `{` around `position`, or None.

    Function bodies are stepped past and a brace in block position is
    refused, so `this` names the literal the method sits in and any
    other container fails closed.
    """
    depth = 0
    for cursor in range(position - 1, -1, -1):
        char = receiver.mask[cursor]
        if char in ')]}':
            depth += 1
        elif char in '([{':
            if depth == 0:
                if char != '{':
                    return None
                body = ('block', cursor, receiver.pair_end.get(cursor))
                if body[2] is not None and receiver._scope_for(
                        body) is not None:
                    continue
                before = cursor - 1
                while before >= 0 and receiver.mask[before].isspace():
                    before -= 1
                if before < 0 or receiver.mask[before] not in '=(,[:!':
                    return None
                return cursor
            depth -= 1
    return None


def sibling_member(receiver, key, position):
    """Member of the object literal whose scope holds `position`."""
    opening = _enclosing_literal(receiver, position)
    if opening is None:
        return None
    return receiver._member_from_span(
        (opening, receiver.pair_end[opening]), key, position,
        frozenset(), {})


def _this_member(receiver, getter_body):
    """Sibling member a `this.<key>` return inside the getter names."""
    expression = receiver._returned_expression(getter_body)
    if expression is None:
        return None
    left, right = receiver._unwrap(expression)
    found = re.fullmatch(r'this\s*\.\s*([\w$]+)',
                         receiver.mask[left:right])
    if found is None:
        return None
    return sibling_member(receiver, found.group(1), left)


def folded_return(receiver, value, seen=frozenset()):
    """Callable a returned expression chain bottoms out at.

    Evaluating a function expression runs nothing, so a call of the
    chain's value reaches the innermost function's own body, and a
    returned `this.<key>` read names the literal's own member.
    """
    while (value['status'] == 'known' and value['body'] is not None
           and value['body'][0] == 'expr'
           and value['body'][1] not in seen):
        position = value['body'][1]
        span = value['body'][1:]
        if function_body_at(receiver.mask, position) is not None:
            value = receiver.callable_value(span)
        else:
            value = _member_callable(receiver, span)
        seen = seen | {position}
    return value


def _member_callable(receiver, span):
    """Callable a returned member expression evaluates to."""
    value = receiver.callable_value(span)
    if value['status'] == 'unprovable':
        left, right = receiver._unwrap(span)
        found = re.fullmatch(r'this\s*\.\s*([\w$]+)',
                             receiver.mask[left:right])
        if found is not None:
            return sibling_member(receiver, found.group(1), left) or value
    return value


def _bound_member(receiver, left, opening, close):
    """Callable a `<name>.bind(...)` result invokes: the name's own.

    None when an argument could run code: bind's arguments evaluate
    before the bound callable exists, so what they execute is the
    call's own and the answer stays fail-closed.
    """
    found = re.match(r'([\w$]+)\s*\.', receiver.mask[left:opening])
    if found is None:
        return None
    arguments = receiver.mask[opening + 1:close - 1]
    if re.search(r'[\w$]\s*\(|(?<![=!<>])=(?!=)', arguments) is not None:
        return None
    owner = receiver.callable_value((left, left + found.end(1)))
    if owner['status'] != 'known':
        return None
    body = invoked_body(receiver, owner, left)
    if body is None:
        return None
    return _target('known', body=body)


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


def chained_member(receiver, owner_end, key, position):
    """Target and chain head of the last key on a spelled member chain.

    The call site asks only when the key's owner names no binding: the
    hops between the head binding and the key are object-literal
    members, which the receiver index reads one hop at a time.
    """
    hops = []
    cursor = owner_end
    while True:
        pos = cursor - 1
        while pos >= 0 and receiver.mask[pos].isspace():
            pos -= 1
        end = pos + 1
        stop = pos
        while stop >= 0 and (receiver.mask[stop].isalnum()
                             or receiver.mask[stop] in '_$'):
            stop -= 1
        name = receiver.mask[stop + 1:end]
        if not re.fullmatch(r'[\w$]+', name):
            return None
        hops.append(name)
        head_start = stop + 1
        if stop < 0 or receiver.mask[stop] != '.':
            break
        cursor = stop
    if len(hops) < 2:
        return None
    binding = receiver.visible_binding(hops[-1], position)
    if binding is None:
        return None
    span, _ = receiver._latest_value(
        receiver.values.get(binding), position)
    for hop in reversed(hops[:-1]):
        if span is None:
            return None
        left, right = receiver._unwrap(span)
        if receiver.mask[left:left + 1] != '{':
            return None
        status, span, _ = receiver._property_span(
            (left, right), hop, position, frozenset(), {})
        if status != 'known':
            return None
    left, right = receiver._unwrap(span)
    if receiver.mask[left:left + 1] != '{':
        return None
    status, value, form = receiver._property_span(
        (left, right), key, position, frozenset(), {})
    if status != 'known':
        return None
    if form != 'data':
        return _target('known', body=value, form=form), head_start
    return receiver.callable_value(value), head_start


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
    status, accessor, form = class_accessor(receiver, name, key, wanted)
    if status == 'known':
        return _target('known', body=accessor, form=form)
    if status == 'absent':
        return _target('irrelevant')
    if status == 'opaque':
        return _target('unprovable')
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


def invoked_body(receiver, value, position):
    """Body a known callable value runs when called at `position`."""
    if value['status'] != 'known':
        return None
    if value['body'] is not None:
        return value['body']
    return callable_body(receiver, value['binding'], position)


def _invoke_callable(receiver, value, args, position):
    if value['status'] != 'known':
        return _target(value['status'], form=value['form'])
    body = invoked_body(receiver, value, position)
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
