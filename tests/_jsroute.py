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
from _jsread import (js_bracket_end, js_mask,  # noqa: E402
                     js_object_entries, js_split_top_level)


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


def _js_function_body(mask, name):
    """`(kind, start, end)` of a same-file function's body, or None.

    `kind` is 'block' for a braced body (start is the `{`) and 'expr' for a
    concise arrow body (the span is the expression). Only the first
    declaration of a name is read; a file that declares one twice gets the
    first, which is the conservative half of a shape nothing here uses.
    """
    patterns = (
        r'\bfunction\s+' + re.escape(name) + r'\s*\(',
        r'\b(?:const|let|var)\s+' + re.escape(name)
        + r'\s*=\s*(?:async\s+)?function\s*[\w$]*\s*\(',
        r'\b(?:const|let|var)\s+' + re.escape(name)
        + r'\s*=\s*(?:async\s*)?\(',
    )
    for pattern in patterns:
        match = re.search(pattern, mask)
        if not match:
            continue
        paren = match.end() - 1
        after = js_bracket_end(mask, paren)
        arrow = re.match(r'\s*=>\s*', mask[after:])
        if arrow:
            body = after + arrow.end()
            if mask[body:body + 1] == '{':
                return ('block', body, js_bracket_end(mask, body))
            if mask[body:body + 1] == '(':
                return ('expr', body + 1, js_bracket_end(mask, body) - 1)
            semi = mask.find(';', body)
            return ('expr', body, len(mask) if semi == -1 else semi)
        brace = mask.find('{', after)
        if brace != -1:
            return ('block', brace, js_bracket_end(mask, brace))
    return None


def js_tab_routing_violations(path, rel):
    """The dashboard side of the same contract: the fields object of an
    extCmd call, and a runCommand object carrying `type`, may contain `tab`
    only as the literal 'extension'. runCommand objects carrying `code` are
    eval sends and route by tab legitimately.

    Resolved before the call: inline object literals, names initialized by
    object literals, aliases of such names, ternary initializers whose
    branches both resolve, `Object.assign` writes, same-name direct property
    writes, tracked object spreads, literal computed keys, the third extCmd
    options argument, and a call to a same-file helper that returns an
    object it built.

    An object that escapes into another call is marked unprovable from that
    point, and an unprovable object reaching a sender is a violation: a
    helper that writes `target.tab` through a parameter cannot be followed,
    so it is reported rather than trusted. An unresolvable sender argument
    is reported for the same reason. A name this scanner never saw assigned
    — a parameter, an import — stays unknown rather than unprovable, which
    is the one silence left.
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

    def helper_state(name, depth):
        """What a same-file helper returns, when it returns what it built."""
        if depth > 3:
            return _JS_UNPROVABLE
        body = _js_function_body(mask, name)
        if body is None:
            return _JS_UNPROVABLE
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
            return helper_state(call.group(1), depth)
        return _JS_UNPROVABLE

    events = []  # (pos, kind, match)
    for m in re.finditer(r'\b(?:const|let|var)\s+([\w$]+)\s*=', mask):
        events.append((m.start(), 'init', m))
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
                name = m.group(1)
                stmt_end = _js_statement_end(mask, m.end())
                named[name] = resolve(m.end(), stmt_end, named, depth)
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

    for m in re.finditer(r'\b(?:extCmd|extcmd|runCommand)\s*\(', mask):
        # `function extCmd(type, fields = {}, opts = {})` is where a sender is
        # declared, not where one is called; its parameter list is not a
        # payload and resolving it would report the definition of every
        # sender as unprovable.
        if mask[:m.start()].rstrip().endswith('function'):
            continue
        call_name = re.match(r'[\w$]+', mask[m.start():]).group(0)
        open_paren = mask.index('(', m.start())
        call_end = js_bracket_end(mask, open_paren)
        args = js_split_top_level(mask, text, open_paren + 1, call_end - 1)
        tab_entries = []
        unprovable = []
        named = names_before(m.start(), 0)
        if call_name == 'runCommand':
            if not args:
                continue
            keys = argument_state(args[0], named)
            if keys is _JS_UNPROVABLE:
                unprovable.append(line_of(m.start()))
            # A `type` key makes this a typed send; eval sends carry `code`.
            elif keys and 'type' in keys and 'tab' in keys:
                tab_entries.append(keys['tab'])
        else:  # extCmd(type, fields, ...)
            if len(args) < 2:
                continue
            fields = argument_state(args[1], named)
            if fields is _JS_UNPROVABLE:
                unprovable.append(line_of(m.start()))
            elif fields and 'tab' in fields:
                tab_entries.append(fields['tab'])
            if len(args) >= 3:
                opts = argument_state(args[2], named)
                if opts is _JS_UNPROVABLE:
                    unprovable.append(line_of(m.start()))
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
