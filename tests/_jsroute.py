"""Which key a JavaScript client writes the routing field under.

Not a suite itself — run_tests.py only loads `test_*.py`.

The JavaScript half of the same question, without a parser: the shared
reader in _jsread masks comments and strings, and what is left is walked for
the object literals a request body is built from.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _jsread import (js_bracket_end, js_expression_end,  # noqa: E402
                     js_mask, js_object_entries, js_split_top_level)
from _jsroute_calls import (discover_invocations,  # noqa: E402
                            parameter_bindings,
                            sender_candidate_bindings)
from _jsroute_timeline import (  # noqa: E402
    FunctionTimeline, InvocationReplay, declaration_bindings,
    function_reference, lexical_limits)
from _jsroute_state import build_sender_queries  # noqa: E402


# A name whose object exists but whose contents this scanner cannot prove.
# Distinct from an unknown name (a parameter, an import): unknown is silence,
# unprovable is a claim that something happened here and was not followed.
_JS_UNPROVABLE = object()


def _js_top_level(mask, start, end, char):
    """Offset of the first `char` at bracket depth 0 in the span, or None."""
    depth = 0
    for i in range(start, end):
        c = mask[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth < 0:
                return None
        elif c == char and depth == 0:
            return i
    return None


def _js_statement_end(mask, pos):
    """Offset of the `;` ending the statement that starts at `pos`.

    Falls back to the end of the line at depth 0, for the rare unterminated
    statement, and to the end of the file when neither is found.
    """
    depth = 0
    for i in range(pos, len(mask)):
        c = mask[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ';' and depth == 0:
            return i
        elif c == '\n' and depth == 0 and mask[pos:i].strip():
            return i
    return len(mask)


def _js_function_body_at(mask, start):
    """Body beginning at a concrete function value occurrence, or None."""
    start += len(mask[start:]) - len(mask[start:].lstrip())
    async_prefix = re.match(r'async\b\s*', mask[start:])
    if async_prefix:
        start += async_prefix.end()
    function = re.match(r'function(?:\s+[\w$]+)?\s*\(', mask[start:])
    if function:
        paren = start + function.end() - 1
        after = js_bracket_end(mask, paren)
        brace = mask.find('{', after)
        if brace != -1:
            return ('block', brace, js_bracket_end(mask, brace))
        return None
    if mask[start:start + 1] == '(':
        after = js_bracket_end(mask, start)
        arrow = re.match(r'\s*=>\s*', mask[after:])
        if not arrow:
            return None
        body = after + arrow.end()
    else:
        parameter = re.match(r'[\w$]+\s*=>\s*', mask[start:])
        if not parameter:
            return None
        body = start + parameter.end()
    if mask[body:body + 1] == '{':
        return ('block', body, js_bracket_end(mask, body))
    if mask[body:body + 1] == '(':
        return ('expr', body + 1, js_bracket_end(mask, body) - 1)
    return ('expr', body, js_expression_end(mask, body))


def js_tab_routing_violations(path, rel):
    """The dashboard side of the same contract: the fields object of an
    extCmd call, and a runCommand object carrying `type`, may contain `tab`
    only as the literal 'extension'. runCommand objects carrying `code` are
    eval sends and route by tab legitimately.

    Resolved before the call: local and transitive aliases of sender
    functions; inline object literals; names initialized by object literals;
    aliases of such names; ternary initializers whose branches both resolve;
    `Object.assign` writes; same-name direct property writes; tracked object
    spreads; literal computed keys; the third extCmd options argument; and a
    same-file helper return. Sender and callable aliases follow lexical
    bindings and optional states; known calls replay captured writes.

    Escaped objects, unresolved sender arguments, and unseen helper mutations
    are unprovable. Unseen parameter or import names stay unknown, and a
    simple alias to one inherits that silence.
    """
    text = path.read_text(encoding='utf-8')
    mask = js_mask(text)
    violations = []
    senders = ('extCmd', 'extcmd', 'runCommand')

    def line_of(pos):
        return text.count('\n', 0, pos) + 1

    def is_extension_literal(value):
        return value is not None and re.fullmatch(r'["\']extension["\']', value)

    def object_state(obj_start, named, depth):
        """Resolve relevant state from a literal and tracked object spreads."""
        state = {}
        for key, value, off in js_object_entries(mask, text, obj_start):
            if key is None:
                spread = value.strip()
                if spread.startswith('{'):
                    merged = object_state(mask.index('{', off), named, depth)
                elif re.fullmatch(r'[\w$]+', spread):
                    merged = named.get(spread)
                else:
                    merged = None
                if merged is _JS_UNPROVABLE:
                    return _JS_UNPROVABLE
                if merged is not None:
                    state.update(merged)
            else:
                state[key] = (line_of(off), value)
        return state

    def helper_state(name, call_pos, depth):
        """What a same-file helper returns, when it returns what it built."""
        if depth > 3:
            return _JS_UNPROVABLE
        binding = visible_binding(name, call_pos)
        limits = lexical_limits(scopes, scope_at(call_pos), call_pos)
        bodies = timeline.values_at(binding, limits=limits)
        if len(bodies) != 1 or bodies[0] is None:
            return _JS_UNPROVABLE
        body = bodies[0]
        kind, body_start, body_end = body
        inner = names_before(body_end, depth + 1, floor=body_start)
        if kind == 'expr':
            return resolve(body_start, body_end, inner, depth + 1)
        returns = list(re.finditer(r'\breturn\b',
                                   mask[body_start:body_end]))
        if not returns:
            return _JS_UNPROVABLE
        merged = {}
        for m in returns:
            expr_start = body_start + m.end()
            semi = mask.find(';', expr_start)
            expr_end = body_end if semi == -1 or semi > body_end else semi
            state = resolve(expr_start, expr_end, inner, depth + 1)
            if state is _JS_UNPROVABLE:
                return _JS_UNPROVABLE
            if state is None:
                continue
            merged.update(state)
        return merged

    def resolve(span_start, span_end, named, depth):
        """Resolve an expression span to a key state, None, or unprovable."""
        expr = mask[span_start:span_end].strip()
        raw = text[span_start:span_end].strip()
        if not expr:
            return None
        if expr.startswith('{'):
            return object_state(span_start + mask[span_start:span_end].index('{'),
                                named, depth)
        if re.fullmatch(r'[\w$]+', expr):
            if raw in ('null', 'undefined'):
                return None
            return named.get(expr)
        question = _js_top_level(mask, span_start, span_end, '?')
        if question is not None:
            colon = _js_top_level(mask, question + 1, span_end, ':')
            if colon is not None:
                left = resolve(question + 1, colon, named, depth)
                right = resolve(colon + 1, span_end, named, depth)
                if left is _JS_UNPROVABLE or right is _JS_UNPROVABLE:
                    return _JS_UNPROVABLE
                merged = dict(left or {})
                merged.update(right or {})
                return merged
        call = re.fullmatch(r'([\w$]+)\s*\(.*\)', expr, re.DOTALL)
        if call:
            call_pos = span_start + mask[span_start:span_end].index(
                call.group(1))
            return helper_state(call.group(1), call_pos, depth)
        return _JS_UNPROVABLE

    declarations = list(re.finditer(
        r'\b(const|let|var)\s+([\w$]+)\s*=', mask))
    bindings = []
    for m in re.finditer(r'\b([\w$]+)\s*=(?!=|>)', mask):
        if not mask[:m.start()].rstrip().endswith('.'):
            bindings.append(m)
    events = []  # (pos, kind, match)
    for m in declarations:
        events.append((m.start(), 'init', m))
    for m in bindings:
        events.append((m.start(), 'bind', m))
    for m in re.finditer(r'\b([\w$]+)\s*\.\s*tab(?![\w$])\s*=', mask):
        events.append((m.start(), 'prop', m))
    for m in re.finditer(r'\bObject\s*\.\s*assign\s*\(', mask):
        events.append((m.start(), 'assign', m))
    # The bracket form is found in the raw text because the mask blanks
    # string contents, making `['tab']` unreadable there — but then a match
    # that begins inside a blanked span is a mention in a string or comment,
    # not code. The mask preserves positions, so the two diverge at the very
    # first character of the name exactly when the mention is not real code.
    for m in re.finditer(r'\b([\w$]+)\s*\[\s*["\']tab["\']\s*\]\s*=', text):
        if mask[m.start()] == text[m.start()]:
            events.append((m.start(), 'prop', m))
    # A tracked object handed to any other call escapes: a helper that writes
    # through its parameter is invisible from here, so the object stops being
    # provable at that point rather than being trusted on its literal.
    for m in re.finditer(r'\b([\w$]+)\s*\(', mask):
        if m.group(1) in senders or m.group(1) in ('if', 'for', 'while',
                                                   'switch', 'catch',
                                                   'function', 'return'):
            continue
        # Object.assign's target is modelled above, so it is not an escape;
        # every other call taking the object is one, member calls included.
        if re.search(r'Object\s*\.\s*$', mask[:m.start()]):
            continue
        events.append((m.start(), 'escape', m))
    events.sort(key=lambda e: e[0])

    pair_end = {}
    pair_start = {}
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    for pos, char in enumerate(mask):
        if char in '([{':
            stack.append((char, pos))
        elif char in pairs and stack and stack[-1][0] == pairs[char]:
            _, opening = stack.pop()
            pair_end[opening] = pos + 1
            pair_start[pos] = opening

    def after_space(pos):
        while pos < len(mask) and mask[pos].isspace():
            pos += 1
        return pos

    scopes = [{'start': 0, 'end': len(mask), 'params': set(),
               'param_start': 0, 'param_order': [],
               'open': None, 'parent': None}]
    scope_keys = set()
    function_names = []
    function_occurrences = []
    method_positions = set()

    def add_scope(open_paren, body_open, params=None, param_start=None,
                  body=None, param_order=None):
        if body is None:
            body = ('block', body_open,
                    pair_end.get(body_open, len(mask)))
        inset = body[0] == 'block'
        key = (body[1] + inset, body[2] - inset)
        if key in scope_keys:
            return
        scope_keys.add(key)
        if params is None:
            params, param_order = parameter_bindings(
                mask, text, open_paren + 1, pair_end[open_paren] - 1,
                pair_end, _js_top_level, js_split_top_level)
            param_start = open_paren + 1
        scopes.append({'start': key[0], 'end': key[1],
                       'params': params, 'param_start': param_start,
                       'param_order': param_order,
                       'open': body_open, 'parent': None})

    for match in re.finditer(
            r'\bfunction(?:\s+([\w$]+))?\s*\(', mask):
        open_paren = mask.index('(', match.start(), match.end())
        body_open = after_space(pair_end.get(open_paren, len(mask)))
        if mask[body_open:body_open + 1] == '{':
            add_scope(open_paren, body_open)
            if match.group(1):
                function_names.append((match.group(1), match.start()))
                function_occurrences.append(
                    (match.group(1), match.start(),
                     _js_function_body_at(mask, match.start())))
    for open_paren, close in list(pair_end.items()):
        if mask[open_paren:open_paren + 1] != '(':
            continue
        arrow = re.match(r'\s*=>\s*', mask[close:])
        if arrow:
            body = _js_function_body_at(mask, open_paren)
            if body is not None:
                body_open = body[1] if body[0] == 'block' else None
                add_scope(open_paren, body_open, body=body)
    for match in re.finditer(r'\b([\w$]+)\s*=>', mask):
        body = _js_function_body_at(mask, match.start())
        if body is not None:
            body_open = body[1] if body[0] == 'block' else None
            params, param_order = parameter_bindings(
                mask, text, match.start(1), match.end(1), pair_end,
                _js_top_level, js_split_top_level)
            add_scope(
                None, body_open, params, match.start(), body, param_order)
    for match in re.finditer(r'\b([\w$]+)\s*\(', mask):
        if match.group(1) in ('if', 'for', 'while', 'switch', 'catch'):
            continue
        open_paren = mask.index('(', match.start())
        close = pair_end.get(open_paren, len(mask))
        body_open = after_space(close)
        prefix = mask[:match.start()].rstrip()
        previous = prefix[-1:] if prefix else ''
        if (mask[body_open:body_open + 1] == '{'
                and not prefix.endswith('function')
                and previous in '{,;}'):
            method_positions.add(match.start())
            add_scope(open_paren, body_open)

    for index in range(1, len(scopes)):
        scope = scopes[index]
        parents = [(candidate['end'] - candidate['start'], parent)
                   for parent, candidate in enumerate(scopes)
                   if parent != index
                   and candidate['param_start'] <= scope['param_start']
                   and scope['end'] <= candidate['end']]
        scope['parent'] = min(parents)[1] if parents else 0

    def scope_at(pos):
        found = [(scope['end'] - scope['param_start'], index)
                 for index, scope in enumerate(scopes)
                 if scope['param_start'] <= pos < scope['end']]
        return min(found)[1] if found else 0

    brace_ranges = [(opening + 1, end - 1)
                    for opening, end in pair_end.items()
                    if mask[opening:opening + 1] == '{']

    def lexical_range(pos, function_only=False):
        scope = scopes[scope_at(pos)]
        if function_only:
            return scope['start'], scope['end']
        found = [(end - start, start, end) for start, end in brace_ranges
                 if start <= pos < end]
        return min(found)[1:] if found else (0, len(mask))

    alias_bindings = {}

    def add_binding(name, bounds):
        binding = (name, *bounds)
        values = alias_bindings.setdefault(name, [])
        if binding not in values:
            values.append(binding)

    for scope in scopes[1:]:
        for name in scope['params']:
            add_binding(name, (scope['param_start'], scope['end']))
    for kind, name, pos in declaration_bindings(
            mask, text, js_split_top_level):
        add_binding(name, lexical_range(pos, function_only=kind == 'var'))
    for name, pos in function_names:
        add_binding(name, lexical_range(pos))

    def visible_binding(name, pos):
        found = [(end - start, -start, (item, start, end))
                 for item, start, end in alias_bindings.get(name, ())
                 if start <= pos < end]
        return min(found)[2] if found else None

    for match in bindings:
        name = match.group(1)
        if visible_binding(name, match.start()) is None:
            add_binding(name, lexical_range(match.start(), function_only=True))

    function_opens = {scope['open'] for scope in scopes[1:]}
    optional_ranges = []
    controls = {'if', 'for', 'while', 'switch', 'catch', 'else', 'try', 'do'}

    def word_before(pos):
        while pos > 0 and mask[pos - 1].isspace():
            pos -= 1
        end = pos
        while (pos > 0
               and (mask[pos - 1].isalnum() or mask[pos - 1] in '_$')):
            pos -= 1
        return mask[pos:end]

    for opening, end in pair_end.items():
        if mask[opening:opening + 1] != '{' or opening in function_opens:
            continue
        word = None
        before = opening - 1
        while before >= 0 and mask[before].isspace():
            before -= 1
        if before >= 0 and mask[before] == ')':
            open_paren = pair_start.get(before)
            if open_paren is not None:
                word = word_before(open_paren)
        else:
            word = word_before(opening)
        if word in controls:
            optional_ranges.append(
                (opening + 1, end - 1, scope_at(opening)))

    def alias_value(name, pos, states):
        binding = visible_binding(name, pos)
        return states.get(binding) if binding is not None else None

    def sender_state(span_start, span_end, states):
        """Resolve a sender binding, preserving unknown-name silence."""
        expr = mask[span_start:span_end].strip()
        if '=>' in expr or re.match(r'(?:async\s+)?function\b', expr):
            return None
        if re.fullmatch(r'[\w$]+', expr):
            if expr in senders:
                return expr
            return alias_value(expr, span_start, states)
        for name in re.findall(r'\b[\w$]+\b', expr):
            if (name in senders
                    or alias_value(name, span_start, states)
                    in (*senders, _JS_UNPROVABLE)):
                return _JS_UNPROVABLE
        return None

    def merged_sender(before, after):
        if before == after:
            return before
        if (before in (*senders, _JS_UNPROVABLE)
                or after in (*senders, _JS_UNPROVABLE)):
            return _JS_UNPROVABLE
        return None

    def optional_write(start, limit):
        if any(begin <= start < end and not begin <= limit < end
               and owner == scope_at(start)
               for begin, end, owner in optional_ranges):
            return True
        before = start - 1
        while before >= 0 and mask[before].isspace():
            before -= 1
        if before >= 0 and mask[before] == ')':
            open_paren = pair_start.get(before)
            if open_paren is not None:
                return word_before(open_paren) in controls
        return word_before(before + 1) in ('else', 'do')

    def bare_call(match):
        prefix = mask[:match.start()].rstrip()
        if prefix.endswith('.') or prefix.endswith('function'):
            return False
        open_paren = mask.index('(', match.start())
        after = after_space(pair_end.get(open_paren, len(mask)))
        return (match.start() not in method_positions
                and mask[after:after + 1] != '{')

    timeline = FunctionTimeline(optional_write)
    for function_name, definition, body in function_occurrences:
        binding = visible_binding(function_name, definition)
        if binding is not None:
            timeline.add(binding, scope_at(definition), binding[1],
                         definition, body)
    for match in bindings:
        binding = visible_binding(match.group(1), match.start())
        if binding is not None:
            value = _js_function_body_at(mask, match.end())
            alias = re.match(r'\s*([\w$]+)\s*(?=[,;)\n])',
                             mask[match.end():])
            source = (visible_binding(alias.group(1), match.start())
                      if value is None and alias else None)
            owner = scope_at(match.start())
            if source is not None:
                value = function_reference(source, match.start(), owner)
            timeline.add(binding, owner, match.start(), match.start(), value)
    member_bindings = {}
    for scope in scopes[1:]:
        for spec in scope['param_order']:
            for item in spec['bindings']:
                if item['rest'] != 'object':
                    continue
                binding = visible_binding(item['name'], scope['start'])
                if binding is not None:
                    member_bindings[binding] = item
    invocations = discover_invocations(
        mask, text, {'end': pair_end, 'start': pair_start}, bindings,
        {'binding': visible_binding, 'scope': scope_at,
         'member_bindings': member_bindings}, method_positions,
        {'top_level': _js_top_level, 'split': js_split_top_level,
         'body': _js_function_body_at}, senders)
    replay = InvocationReplay(
        timeline, scopes, events, invocations,
        {'mask': mask, 'text': text, 'body': _js_function_body_at,
         'top_level': _js_top_level,
         'expression_end': js_expression_end},
        scope_at, visible_binding, optional_write)

    reached_calls = {}
    reached_unknowns = []
    runtime_values = {}
    previous_call = -1
    for call in invocations:
        if call['scope'] != 0 or call['parent']:
            continue
        replay.clear_between(
            runtime_values, previous_call, call['order'], {0})
        _, reached, unknowns = replay.run(call, runtime_values)
        for record in reached:
            reached_calls.setdefault(
                record[0]['start'], []).append(record)
        reached_unknowns.extend(unknowns)
        previous_call = call['order']

    candidate_bindings = sender_candidate_bindings(
        scopes, bindings, mask, visible_binding, js_expression_end,
        senders)
    sender_queries = build_sender_queries(
        candidate_bindings, reached_calls, scopes, events,
        invocations, replay,
        {'scope_at': scope_at, 'visible_binding': visible_binding,
         'expression_end': js_expression_end, 'mask': mask,
         'sender_state': sender_state, 'optional_write': optional_write,
         'merged_sender': merged_sender, 'text': text,
         'top_level': _js_top_level, 'unprovable': _JS_UNPROVABLE})
    sender_answers = sender_queries['answers']
    call_queries = sender_queries['calls']
    record_senders = sender_queries['records']
    escape_queries = sender_queries['escapes']

    def sender_before(name, limit):
        query = escape_queries.get((limit, name))
        return sender_answers.get(query)

    def reached_sender(record):
        kind, value = record_senders[id(record)]
        return sender_answers[value] if kind == 'query' else value

    def names_before(limit, depth, floor=0):
        """Tracked state of named objects from the assignments before `limit`.

        Judging every call against end-of-file state let a later, unrelated
        `const f = {...}` erase an earlier violation, so the walk stops at
        the call. `floor` restricts the walk to one function body.
        """
        named = {}
        for start, kind, m in events:
            if start >= limit:
                break
            if start < floor:
                continue
            if kind == 'init':
                name = m.group(2)
                stmt_end = _js_statement_end(mask, m.end())
                named[name] = resolve(m.end(), stmt_end, named, depth)
            elif kind == 'bind':
                continue
            elif kind == 'assign':
                open_paren = mask.index('(', m.start())
                call_end = js_bracket_end(mask, open_paren)
                args = js_split_top_level(mask, text, open_paren + 1,
                                          call_end - 1)
                if not args:
                    continue
                target = mask[args[0][0]:args[0][1]].strip()
                if not re.fullmatch(r'[\w$]+', target):
                    continue
                state = named.get(target)
                if state is _JS_UNPROVABLE:
                    continue
                if state is None:
                    state = {}
                for span in args[1:]:
                    merged = resolve(span[0], span[1], named, depth)
                    if merged is _JS_UNPROVABLE:
                        state = _JS_UNPROVABLE
                        break
                    if merged:
                        state.update(merged)
                named[target] = state
            elif kind == 'escape':
                sender = (m.group(1) in senders
                          or sender_before(m.group(1), m.start()) is not None)
                if bare_call(m) and sender:
                    continue
                open_paren = mask.index('(', m.start())
                call_end = js_bracket_end(mask, open_paren)
                for span in js_split_top_level(mask, text, open_paren + 1,
                                               call_end - 1):
                    arg = mask[span[0]:span[1]].strip()
                    if re.fullmatch(r'[\w$]+', arg) and arg in named:
                        named[arg] = _JS_UNPROVABLE
            else:
                name = m.group(1)
                eq = mask.index('=', m.start())
                semi = mask.find(';', eq)
                semi = len(mask) if semi == -1 else semi
                state = named.get(name)
                if state is _JS_UNPROVABLE:
                    continue
                if state is None:
                    state = {}
                state['tab'] = (line_of(m.start()), text[eq + 1:semi].strip())
                named[name] = state
        return named

    def argument_state(span, named):
        start, end = span
        return resolve(start, end, named, 0)

    for call in reached_unknowns:
        violations.append(
            f'{rel}:{line_of(call["start"])}: an invocation target this '
            'guard cannot resolve')

    for call in invocations:
        call_name = call['name']
        if (call_name not in senders
                and call['binding'] not in candidate_bindings):
            continue
        runtime_records = reached_calls.get(call['start'])
        args = call['args']
        tab_entries = []
        unprovable = []
        if call_name in senders:
            sender = call_name
        elif runtime_records:
            runtime_senders = [reached_sender(record)
                               for record in runtime_records]
            sender = runtime_senders[0]
            for value in runtime_senders[1:]:
                sender = merged_sender(sender, value)
        else:
            sender = sender_answers[call_queries[id(call)]]
        if sender is None:
            continue
        relevant_args = []
        if sender in ('runCommand', _JS_UNPROVABLE) and args:
            relevant_args.append(args[0])
        if sender in ('extCmd', 'extcmd', _JS_UNPROVABLE):
            relevant_args.extend(args[1:3])
        needs_named = any(
            re.search(r'\.\.\.', mask[start:end])
            or re.fullmatch(r'\s*[\w$]+\s*', mask[start:end])
            or re.match(r'\s*[\w$]+\s*\(', mask[start:end])
            for start, end in relevant_args)
        named = names_before(call['start'], 0) if needs_named else {}
        if sender in ('runCommand', _JS_UNPROVABLE) and args:
            keys = argument_state(args[0], named)
            if keys is _JS_UNPROVABLE:
                unprovable.append(line_of(call['start']))
            # A `type` key makes this a typed send; eval sends carry `code`.
            elif keys and 'type' in keys and 'tab' in keys:
                tab_entries.append(keys['tab'])
        if sender in ('extCmd', 'extcmd', _JS_UNPROVABLE) and len(args) >= 2:
            fields = argument_state(args[1], named)
            if fields is _JS_UNPROVABLE:
                unprovable.append(line_of(call['start']))
            elif fields and 'tab' in fields:
                tab_entries.append(fields['tab'])
            if len(args) >= 3:
                opts = argument_state(args[2], named)
                if opts is _JS_UNPROVABLE:
                    unprovable.append(line_of(call['start']))
                elif opts and 'tab' in opts:
                    tab_entries.append(opts['tab'])
        for lineno, value in tab_entries:
            if not is_extension_literal(value):
                violations.append(
                    f'{rel}:{lineno}: `tab` in a typed command send')
        for lineno in unprovable:
            violations.append(
                f'{rel}:{lineno}: a command send whose fields this guard '
                'cannot resolve — inline the object or build it in a helper '
                'that returns one')
    return list(dict.fromkeys(violations))
