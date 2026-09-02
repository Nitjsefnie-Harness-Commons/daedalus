"""Reading shipped JavaScript without executing it; not a suite itself, and
never eval."""
import re


def blank_js_comments(source):
    """Return `source` with comments blanked and strings intact. One
    forward walk, because a `//` inside a string starts no comment;
    regex literals are unmodelled, and the scanner reports one rather
    than stay silent."""
    out = []
    index, end = 0, len(source)
    quote = None
    while index < end:
        char = source[index]
        if quote:
            out.append(char)
            if char == '\\' and index + 1 < end:
                out.append(source[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in '\'"`':
            quote = char
            out.append(char)
            index += 1
            continue
        if char == '/' and source[index + 1:index + 2] == '/':
            while index < end and source[index] != '\n':
                out.append(' ')
                index += 1
            continue
        if char == '/' and source[index + 1:index + 2] == '*':
            while index < end and source[index:index + 2] != '*/':
                out.append('\n' if source[index] == '\n' else ' ')
                index += 1
            out.append('  ')
            index += 2
            continue
        out.append(char)
        index += 1
    return ''.join(out)


_REGEX_KEYWORDS = {
    'await', 'case', 'delete', 'do', 'else', 'in', 'instanceof', 'new',
    'of', 'return', 'throw', 'typeof', 'void', 'yield'}
_HEAD_KEYWORDS = {'if', 'while', 'for', 'with'}
_BLOCK_HEADS = {'else', 'do'}
_DECIMAL_DIGITS = frozenset('0123456789')


def _js_regex_span(text, start):
    """Offset of the slash closing the regex literal opening at `start`, or
    None when no same-line body closes it: a line ending cannot occur in a
    regex body, so a slash reaching one is division."""
    in_class = False
    j = start + 1
    n = len(text)
    while j < n:
        char = text[j]
        if char == '\\':
            if text[j + 1:j + 2] in ('', '\n', '\r'):
                return None
            j += 2
            continue
        if char in '\n\r':
            return None
        if in_class:
            in_class = char != ']'
        elif char == '[':
            in_class = True
        elif char == '/':
            return j
        j += 1
    return None


def _js_word_before(out, index):
    start = index
    while start > 0 and (out[start - 1].isalnum() or out[start - 1] in '_$'):
        start -= 1
    return ''.join(out[start:index])


def _js_matching_open(out, close):
    depth = 0
    for j in range(close, -1, -1):
        if out[j] in ')]}':
            depth += 1
        elif out[j] in '([{':
            depth -= 1
            if depth == 0:
                return j
    return -1


def _js_object_brace(out, brace):
    """An `{` in operand position is a literal, and the tokens that put one
    there are the regex-allowing ones; the bracket or colon in front
    decides the rest."""
    k = brace
    while k > 0 and out[k - 1].isspace():
        k -= 1
    if k == 0:
        return False
    char = out[k - 1]
    if char == ')' or (char == '>' and k > 1 and out[k - 2] == '='):
        return False
    if char in '_$' or char.isalnum():
        word = _js_word_before(out, k)
        return word not in _BLOCK_HEADS and (
            word in _REGEX_KEYWORDS or word == '$')
    if char in '([':
        return True
    if char == '{':
        return _js_object_brace(out, k - 1)
    if char == ':':
        return _js_colon_brace(out, k - 1)
    return char in '=,?+-*/%<>&|^~!'


def _js_clause_head_word(out, start):
    """Whether the word at `start` heads a `case` clause or only names a
    member or key."""
    k = start
    while k > 0 and out[k - 1].isspace():
        k -= 1
    if k == 0:
        return True
    if out[k - 1] in '.,':
        return False
    return not (out[k - 1] == '{' and _js_object_brace(out, k - 1))


def _js_colon_brace(out, colon):
    """Whether the `{` behind the `:` at `colon` is an object literal.
    A colon also spells a label or a case/default clause, whose `{` Node
    follows with a regex. Reading back at depth zero, the first `?` with
    no other colon on its side is a ternary — `?.` is a chain only where
    no decimal digit follows, so `cond?.5:` is a conditional. A `;` bounds
    the walk, which would otherwise turn quadratic on label-heavy files."""
    depth, crossed = 0, 0
    j = colon - 1
    while j >= 0:
        char = out[j]
        if char in ')]}':
            depth += 1
        elif char in '([{':
            if depth == 0:
                if char != '{':
                    return True
                return _js_object_brace(out, j)
            depth -= 1
        elif depth == 0:
            if char == ';':
                return False
            if char == ':':
                crossed += 1
            elif char == '?':
                after = out[j + 1]
                if after == '?' or (j and out[j - 1] == '?'):
                    j -= 1
                    continue
                if after == '.' and out[j + 2] not in _DECIMAL_DIGITS:
                    j -= 1
                    continue
                if crossed == 0:
                    return True
                crossed -= 1
            elif char in '_$' or char.isalnum():
                word = _js_word_before(out, j + 1)
                start = j + 1 - len(word)
                if word == 'case' and _js_clause_head_word(
                        out, start):
                    return False
                j -= len(word)
                continue
        j -= 1
    return False


def _js_regex_opening(out, regex_closed):
    """Whether the `/` at `len(out)` opens a regex literal, from the last
    mask token. A wrong guess does not self-heal at the line
    end: a body swallowing a string's opening quote leaves the string
    state open past it. After `/` `regex_closed` splits the spellings — a
    closing slash is division, a division operator reopens regex position
    — and a blanked literal leaves no previous token to read."""
    j = len(out) - 1
    while j >= 0 and out[j].isspace():
        j -= 1
    if j < 0:
        return True
    char = out[j]
    if char == '/':
        return not regex_closed
    if char == ')':
        head = _js_matching_open(out, j)
        if head < 0:
            return False
        while head > 0 and out[head - 1].isspace():
            head -= 1
        word = _js_word_before(out, head)
        dot = head - len(word)
        while dot > 0 and out[dot - 1].isspace():
            dot -= 1
        if dot > 0 and out[dot - 1] == '.':
            return False
        return word in _HEAD_KEYWORDS
    if char in ']"\'':
        return False
    if char in '+-' and j > 0 and out[j - 1] == char:
        return False
    if char in '_$' or char.isalnum():
        return _js_word_before(out, j + 1) in _REGEX_KEYWORDS
    if char == '}':
        brace = _js_matching_open(out, j)
        if brace < 0:
            return True
        return not _js_object_brace(out, brace)
    return True


def js_mask(text):
    """Blank string and comment contents, preserving positions and newlines.
    A regex literal is blanked like a string, read from the previous token;
    `out` holds one character per element, which the readers index
    backwards."""
    out = []
    i, n = 0, len(text)
    templates = []
    blanked_operand = False
    regex_closed = False
    while i < n:
        char = text[i]
        if templates and templates[-1] == 0:
            if char == '`':
                templates.pop()
                blanked_operand = True
            elif char == '$' and text[i + 1:i + 2] == '{':
                templates[-1] = 1
                blanked_operand = False
                out.append('$')
                out.append('{')
                i += 2
                continue
            elif char == '\\' and i + 1 < n:
                out.append(' ')
                out.append(text[i + 1] if text[i + 1] == '\n' else ' ')
                i += 2
                continue
            out.append(char if char == '\n' else ' ')
            i += 1
            continue
        two = text[i:i + 2]
        if two == '//':
            j = text.find('\n', i)
            j = n if j == -1 else j
            out.extend(' ' * (j - i))
            i = j
        elif two == '/*':
            j = text.find('*/', i + 2)
            j = n if j == -1 else j + 2
            out.extend(re.sub(r'[^\n]', ' ', text[i:j]))
            i = j
        elif char == '/':
            end = (_js_regex_span(text, i)
                   if not blanked_operand
                   and _js_regex_opening(out, regex_closed) else None)
            if end is None:
                out.append('/')
                blanked_operand = False
                regex_closed = False
                i += 1
                continue
            out.append('/')
            out.extend(re.sub(r'[^\n]', ' ', text[i + 1:end]))
            out.append('/')
            blanked_operand = False
            regex_closed = True
            i = end + 1
            while i < n and (text[i].isalnum() or text[i] in '_$'):
                out.append(' ')
                i += 1
        elif char in '\'"':
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                elif text[j] == char:
                    j += 1
                    break
                else:
                    j += 1
            out.extend(re.sub(r'[^\n]', ' ', text[i:j]))
            blanked_operand = True
            i = j
        elif char == '`':
            templates.append(0)
            out.append(' ')
            i += 1
        else:
            if templates:
                if char in '([{':
                    templates[-1] += 1
                elif char in ')]}':
                    if char == '}' and templates[-1] == 1:
                        templates[-1] = 0
                    else:
                        templates[-1] -= 1
            if not char.isspace():
                blanked_operand = False
            out.append(char)
            i += 1
    return ''.join(out)


def js_bracket_end(mask, open_pos):
    depth = 0
    for i in range(open_pos, len(mask)):
        if mask[i] in '([{':
            depth += 1
        elif mask[i] in ')]}':
            depth -= 1
            if depth == 0:
                return i + 1
    return len(mask)


def js_expression_end(mask, start):
    depth = 0
    previous_continues = '+-*/%&|^!?:.=<>~([{'
    next_continues = '.([?*/%&|^!:<>=~'
    word_continues_before = {
        'await', 'delete', 'in', 'instanceof', 'new', 'typeof', 'void',
        'yield'}
    word_continues_after = {'in', 'instanceof'}
    for pos in range(start, len(mask)):
        char = mask[pos]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            if depth == 0:
                return pos
            depth -= 1
        elif depth == 0 and char in ',;':
            return pos
        elif depth == 0 and char == '\n':
            before = mask[start:pos].rstrip()
            if not before:
                continue
            after = mask[pos + 1:].lstrip()
            if before[-1] in previous_continues:
                continue
            if after[:1] in next_continues:
                continue
            before_word = re.search(r'([\w$]+)$', before)
            after_word = re.match(r'([\w$]+)', after)
            if (before_word
                    and before_word.group(1) in word_continues_before):
                continue
            if after_word and after_word.group(1) in word_continues_after:
                continue
            if after.startswith(('+', '-')) and not after.startswith(
                    ('++', '--')):
                continue
            return pos
    return len(mask)


def js_split_top_level(mask, text, start, end):
    """Split mask[start:end] on depth-0 commas; emptiness is judged on the
    original text, where a blanked string is a real argument."""
    spans, depth, seg = [], 0, start
    for i in range(start, end):
        c = mask[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ',' and depth == 0:
            spans.append((seg, i))
            seg = i + 1
    spans.append((seg, end))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def js_object_entries(mask, text, obj_start):
    """Entries of the object literal at `obj_start`, as (key, value or None
    for shorthand, key offset); a spread's key is None."""
    obj_end = js_bracket_end(mask, obj_start)
    entries = []
    for s, e in js_split_top_level(mask, text, obj_start + 1, obj_end - 1):
        seg_text = text[s:e]
        stripped = seg_text.strip()
        if stripped.startswith('...'):
            spread_at = s + seg_text.index('...') + 3
            while spread_at < e and text[spread_at].isspace():
                spread_at += 1
            entries.append((None, text[spread_at:e].strip(), spread_at))
            continue
        seg_mask = mask[s:e]
        depth, colon, equals = 0, None, None
        for i, c in enumerate(seg_mask):
            if c in '([{':
                depth += 1
            elif c in ')]}':
                depth -= 1
            elif depth == 0 and c == ':' and colon is None:
                colon = i
            elif depth == 0 and c == '=' and equals is None:
                equals = i
        if equals is not None and (colon is None or equals < colon):
            continue
        if colon is None:
            m = re.match(r'\s*([\w$]+)', seg_text)
            if m:
                entries.append((m.group(1), None, s + m.start(1)))
            continue
        key_text = seg_text[:colon].strip()
        quoted = re.fullmatch(r'["\']([^"\']+)["\']', key_text)
        computed = re.fullmatch(
            r'\[\s*(["\'])([^"\']+)\1\s*\]', key_text)
        key = quoted.group(1) if quoted else (
            computed.group(2) if computed else key_text)
        entries.append((key,
                        seg_text[colon + 1:].strip(), s))
    return entries
