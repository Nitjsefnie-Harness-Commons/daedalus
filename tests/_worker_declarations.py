"""Declaration inventory for classic scripts sharing one worker global."""
import re

from _jsread import (js_bracket_end, js_in_for_header, js_local_var_ranges,
                     js_mask, js_split_top_level,
                     js_var_declaration_end)

_JS_IDENTIFIER = r'[A-Za-z_$][\w$]*'
_TOP_LEVEL_DECLARATION = re.compile(
    rf'(?P<function>(?:async\s+)?function\s+'
    rf'(?P<function_name>{_JS_IDENTIFIER})\s*\()'
    rf'|(?P<binding>(?:const|let|var)\b)'
    rf'|(?P<class>class\s+(?P<class_name>{_JS_IDENTIFIER})\b)')
_CONTROL_HEADER = re.compile(
    r'(?<![\w$.])(?:if|for|while|with)\s*\(')
_STATEMENT_CONTINUATION = frozenset('([{=,:.?+-*/%&|^!~<>')
_REGEX_PREFIX_WORDS = frozenset({
    'await', 'case', 'delete', 'do', 'else', 'in', 'instanceof', 'new',
    'of', 'return', 'throw', 'typeof', 'void', 'yield',
})
_CONTROL_WORDS = frozenset({'catch', 'for', 'if', 'switch', 'while', 'with'})


def _quoted_end(source, start):
    quote = source[start]
    index = start + 1
    while index < len(source):
        if source[index] == '\\':
            index += 2
        elif source[index] == quote:
            return index + 1
        else:
            index += 1
    return len(source)


def _regex_end(source, start):
    index = start + 1
    in_class = False
    while index < len(source):
        char = source[index]
        if char in '\r\n':
            return None
        if char == '\\':
            index += 2
            continue
        if char == '[':
            in_class = True
        elif char == ']':
            in_class = False
        elif char == '/' and not in_class:
            index += 1
            while index < len(source) and source[index].isalpha():
                index += 1
            return index
        index += 1
    return None


def _declaration_mask(source):
    """Blank regex literals for this declaration reader only."""
    mask = list(js_mask(source))
    index = 0
    expression_start = True
    pending_control = False
    control_parentheses = []
    while index < len(source):
        char = source[index]
        two = source[index:index + 2]
        if char.isspace():
            index += 1
            continue
        if two == '//':
            newline = source.find('\n', index + 2)
            index = len(source) if newline < 0 else newline
            continue
        if two == '/*':
            closing = source.find('*/', index + 2)
            index = len(source) if closing < 0 else closing + 2
            continue
        if char in '\'"`':
            index = _quoted_end(source, index)
            expression_start = False
            pending_control = False
            continue
        identifier = re.match(_JS_IDENTIFIER, source[index:])
        if identifier:
            word = identifier.group()
            index += len(word)
            pending_control = word in _CONTROL_WORDS
            expression_start = word in _REGEX_PREFIX_WORDS
            continue
        if char.isdigit():
            number = re.match(r'[\w.]+', source[index:])
            index += len(number.group())
            expression_start = False
            pending_control = False
            continue
        if char == '(':
            control_parentheses.append(pending_control)
            pending_control = False
            expression_start = True
            index += 1
            continue
        if char == ')':
            expression_start = (
                control_parentheses.pop() if control_parentheses else False)
            pending_control = False
            index += 1
            continue
        if char == '/' and expression_start:
            end = _regex_end(source, index)
            if end is not None:
                for offset in range(index, end):
                    if mask[offset] != '\n':
                        mask[offset] = ' '
                index = end
                expression_start = False
                pending_control = False
                continue
        if char == '/':
            index += 2 if source[index:index + 2] == '/=' else 1
            expression_start = True
            pending_control = False
            continue
        if char in ')]':
            expression_start = False
        elif char == '}':
            expression_start = True
        elif char in '([{,;:?=.+-*%&|^!~<>':
            expression_start = True
        pending_control = False
        index += 1
    return ''.join(mask)


def _top_level_positions(mask):
    top_level = []
    braces = 0
    brackets = 0
    parentheses = 0
    for char in mask:
        top_level.append(braces == brackets == parentheses == 0)
        if char == '{':
            braces += 1
        elif char == '}':
            braces -= 1
        elif char == '[':
            brackets += 1
        elif char == ']':
            brackets -= 1
        elif char == '(':
            parentheses += 1
        elif char == ')':
            parentheses -= 1
    return top_level


def _previous_nonspace(mask, start):
    previous = start - 1
    while previous >= 0 and mask[previous].isspace():
        previous -= 1
    return previous


def _control_header_ends_at(mask, closing):
    for control in _CONTROL_HEADER.finditer(mask, 0, closing + 1):
        opening = mask.find('(', control.start(), control.end())
        if js_bracket_end(mask, opening) == closing + 1:
            return True
    return False


def _starts_statement(mask, start):
    previous = _previous_nonspace(mask, start)
    if previous < 0 or mask[previous] in ';}':
        return True
    if mask[previous] == ')' and _control_header_ends_at(mask, previous):
        return False
    line_start = mask.rfind('\n', 0, start) + 1
    if mask[line_start:start].strip():
        return False
    return mask[previous] not in _STATEMENT_CONTINUATION


def _starts_var_statement(mask, start):
    if _starts_statement(mask, start) or js_in_for_header(mask, start):
        return True
    previous = _previous_nonspace(mask, start)
    if previous >= 0 and mask[previous] in '{:':
        return True
    if (previous >= 0 and mask[previous] == ')'
            and _control_header_ends_at(mask, previous)):
        return True
    prefix = mask[:start].rstrip()
    return re.search(r'(?<![\w$])(?:do|else)\s*$', prefix) is not None


def _first_top_level(mask, start, end, wanted):
    depth = 0
    for index in range(start, end):
        char = mask[index]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif depth == 0 and char in wanted:
            return index
    return None


def _binding_pattern_names(mask, start, end):
    while start < end and mask[start].isspace():
        start += 1
    while end > start and mask[end - 1].isspace():
        end -= 1
    if mask.startswith('...', start):
        return _binding_pattern_names(mask, start + 3, end)
    default = _first_top_level(mask, start, end, '=')
    if default is not None:
        end = default
        while end > start and mask[end - 1].isspace():
            end -= 1
    identifier = re.fullmatch(_JS_IDENTIFIER, mask[start:end])
    if identifier:
        return [(identifier.group(), start)]
    if start >= end or mask[start] not in '[{':
        return []

    closing = js_bracket_end(mask, start) - 1
    names = []
    for item_start, item_end in js_split_top_level(
            mask, mask, start + 1, closing):
        if mask[start] == '{':
            colon = _first_top_level(mask, item_start, item_end, ':')
            if colon is not None:
                item_start = colon + 1
        names.extend(_binding_pattern_names(mask, item_start, item_end))
    return names


def _binding_declarations(mask, declaration_start, binding_kind):
    statement_end = js_var_declaration_end(
        mask, declaration_start) if binding_kind == 'var' else None
    if statement_end is None:
        statement_end = _first_top_level(
            mask, declaration_start, len(mask), ';')
    if statement_end is None:
        statement_end = len(mask)
    declarations = []
    for start, end in js_split_top_level(
            mask, mask, declaration_start, statement_end):
        for name, name_start in _binding_pattern_names(mask, start, end):
            declarations.append((name, 'binding', name_start))
    return declarations


def top_level_declarations(source):
    """Return declarations that bind in a classic script's global scope."""
    mask = _declaration_mask(source)
    top_level = _top_level_positions(mask)
    local_var_ranges = js_local_var_ranges(mask)
    declarations = []
    for match in _TOP_LEVEL_DECLARATION.finditer(mask):
        if match.lastgroup == 'binding':
            binding_kind = match.group('binding')
            locally_scoped = any(
                start < match.start() < end
                for start, end in local_var_ranges)
            if binding_kind == 'var':
                if locally_scoped or not _starts_var_statement(
                        mask, match.start()):
                    continue
            elif (not top_level[match.start()]
                  or not _starts_statement(mask, match.start())):
                continue
            declarations.extend(_binding_declarations(
                mask, match.end(), binding_kind))
            continue
        if not top_level[match.start()]:
            continue
        if not _starts_statement(mask, match.start()):
            continue
        kind = match.lastgroup.removesuffix('_name')
        name = match.group(f'{kind}_name')
        declarations.append((name, kind, match.start()))
    return declarations


def top_level_reassigns(source, name, after):
    """Whether a later worker-global assignment replaces `name`."""
    mask = _declaration_mask(source)
    top_level = _top_level_positions(mask)
    assignment = re.compile(
        rf'(?<![\w$.]){re.escape(name)}\s*=(?!=|>)')
    return any(
        match.start() > after and top_level[match.start()]
        for match in assignment.finditer(mask)
    )
