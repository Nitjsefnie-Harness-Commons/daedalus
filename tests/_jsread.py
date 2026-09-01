"""Reading shipped JavaScript without executing it.

Not a suite itself — run_tests.py only loads `test_*.py`.

Several contracts are properties of the source rather than of a run: which
message types a script sends, whether a value reaches innerHTML, which
argument a call passes. Answering those needs just enough of a reader to know
what is a comment, what is a string, and where a bracket closes — not a
JavaScript parser, and never eval.
"""
import re


def blank_js_comments(source):
    """Return `source` with comments blanked and string literals intact.

    A single forward walk, because a `//` inside a string literal does not
    start a comment. Regex literals are NOT modelled: one containing a quote
    or a comment opener would desynchronise this walk. The dashboard sources
    contain none, and the scanner below would report a violation rather than
    stay silent if that changed, which is the direction an unmodelled shape
    should fail in.
    """
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


def _js_regex_span(text, start):
    """Offset of the slash closing the regex literal opening at `start`,
    or None when no same-line body closes it: a raw line ending cannot
    occur in a regex body, so a slash reaching one is division."""
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


def _js_regex_opening(out):
    """Whether the `/` at `len(out)` opens a regex literal, read from the
    last token in the mask built so far. Comments and blanked spans are
    not tokens, so skipping blanked space reaches the real previous
    token; after `)` the matching `(` decides, because a regex may open
    an if/while/for/with head's body. `}` stays regex-allowed: statement
    heads follow blocks, and a wrong guess self-heals at the next line
    end because a body cannot contain one."""
    j = len(out) - 1
    while j >= 0 and out[j].isspace():
        j -= 1
    if j < 0:
        return True
    char = out[j]
    if char == ')':
        depth = 0
        while j >= 0:
            if out[j] == ')':
                depth += 1
            elif out[j] == '(':
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        if j < 0:
            return False
        k = j
        while k > 0 and out[k - 1].isspace():
            k -= 1
        word_end = k
        while k > 0 and (out[k - 1].isalnum() or out[k - 1] in '_$'):
            k -= 1
        return ''.join(out[k:word_end]) in _HEAD_KEYWORDS
    if char in ']"\'':
        return False
    if char in '+-' and j > 0 and out[j - 1] == char:
        return False
    if char in '_$' or char.isalnum():
        k = j
        while k > 0 and (out[k - 1].isalnum() or out[k - 1] in '_$'):
            k -= 1
        return ''.join(out[k:j + 1]) in _REGEX_KEYWORDS
    return True


def js_mask(text):
    """Blank string and comment contents, preserving positions and newlines,
    so structure (brackets, commas, colons) can be read without false hits
    from literal text. A template literal is walked with a nesting stack: its
    literal chunks blank like any string, while an interpolation is code, so
    it stays visible with its `${` and closing `}` intact and the brackets,
    commas and colons inside it count for depth. A regex literal is blanked
    like a string (delimiters kept, flags with it), read from the previous
    token, so its body never reaches the string, comment or interpolation
    states."""
    out = []
    i, n = 0, len(text)
    templates = []
    while i < n:
        char = text[i]
        if templates and templates[-1] == 0:
            if char == '`':
                templates.pop()
            elif char == '$' and text[i + 1:i + 2] == '{':
                templates[-1] = 1
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
        elif char == '/' and two != '/=':
            end = (_js_regex_span(text, i)
                   if _js_regex_opening(out) else None)
            if end is None:
                out.append('/')
                i += 1
                continue
            out.append('/')
            out.extend(re.sub(r'[^\n]', ' ', text[i + 1:end]))
            out.append('/')
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
            out.append(char)
            i += 1
    return ''.join(out)


def js_bracket_end(mask, open_pos):
    """Offset just past the bracket matching the one at `open_pos`."""
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
    """End at a depth-zero comma, semicolon, ASI newline, or delimiter."""
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
    """Split mask[start:end] on depth-0 commas. Emptiness is judged on the
    ORIGINAL text: a blanked string is a real argument, not a gap."""
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
    """Top-level entries of the object literal opening at `obj_start`:
    [(key, value_text_or_None_for_shorthand, key_offset)]. A spread has a
    None key and its expression as the value. Destructuring defaults
    (`tab = 'extension'` in a parameter object) are not entries."""
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
