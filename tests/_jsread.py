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


def js_mask(text):
    """Blank string and comment contents, preserving positions and newlines,
    so structure (brackets, commas, colons) can be read without false hits
    from literal text."""
    out = []
    i, n = 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == '//':
            j = text.find('\n', i)
            j = n if j == -1 else j
            out.append(' ' * (j - i))
            i = j
        elif two == '/*':
            j = text.find('*/', i + 2)
            j = n if j == -1 else j + 2
            out.append(re.sub(r'[^\n]', ' ', text[i:j]))
            i = j
        elif text[i] in '\'"`':
            j = i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                elif text[j] == text[i]:
                    j += 1
                    break
                else:
                    j += 1
            out.append(re.sub(r'[^\n]', ' ', text[i:j]))
            i = j
        else:
            out.append(text[i])
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
