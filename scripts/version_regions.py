#!/usr/bin/env python3
"""The region scanners the version checker filters decoys by (#316).

A version pattern inside a comment, a string, a template literal's text or a
regex literal is not a binding. Text no region covers is code, and that is
what the checker reads as a site, so each scanner has to hold two properties
at once: a decoy must land inside a region, and the code around it must stay
outside one. A scanner that swallows executable code hides a binding the
runtime does bind; a scanner that marks code as a string hides the one true
site.
"""
import io
import string
import tokenize


def regions_for(path, text):
    """(start, end, kind) regions for one file's text, chosen by extension."""
    if path.endswith('.js'):
        return _javascript_regions(text)
    if path.endswith('.py'):
        return _python_regions(text, path)
    if path.endswith('.json'):
        return _quoted_regions(text, {'"'})
    if path.endswith('.html'):
        return _html_regions(text)
    return []


def is_decoy(match, regions, allow_comments):
    """Whether `match` starts inside a region, so it is not a site.

    A comment region filters only when the site has not opted in: the one
    `@version` header lives in a line comment by design. A match beginning at
    a region's first character stays code, which is what keeps a quoted key
    or a `<tag` a site.
    """
    position = match.start()
    for start, end, kind in regions:
        if start < position < end:
            if kind != 'comment' or not allow_comments:
                return True
    return False


def _quoted_end(text, start):
    """Where the JSON string opening at `start` ends: `\"` is content."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == '\\':
            i += 2
        elif text[i] == quote:
            return i + 1
        else:
            i += 1
    return len(text)


def _quoted_regions(text, delimiters):
    regions = []
    i = 0
    while i < len(text):
        if text[i] not in delimiters:
            i += 1
            continue
        end = _quoted_end(text, i)
        regions.append((i, end, 'string'))
        i = end
    return regions


# A `/` after one of these tokens is division; after anything else — a value,
# an operand keyword, an opening `{`, or an operator — it opens a regex
# literal. The decision reads the previous token alone, so it cannot see
# grammar context: a member access spelled like an operand keyword
# (`obj.return / 2`) is misread as a regex. Either misreading costs one line,
# never more: a regex stops at a raw newline and a string at one too, so text
# past the line is classified as written.
_JS_DIVISION_BEFORE = frozenset({'value', 'end', ')', ']', '}'})
_JS_OPERAND_KEYWORDS = frozenset({'await', 'case', 'delete', 'do', 'else',
                                  'in', 'instanceof', 'new', 'of', 'return',
                                  'throw', 'typeof', 'void', 'yield'})
_JS_WORD = frozenset(string.ascii_letters + string.digits + '_$')
_JS_REGEX_FLAGS = frozenset('dgimsuvy')


# Every scanner answers (start, end, kind) spans for the constructs a version
# pattern inside is not a binding in; text no span covers is code.

def _javascript_regions(text, html_comments=False):
    """Regions for JavaScript comments, quoted strings, template literals and
    regex literals. A regex literal is its own kind: a version spelled inside
    one is a pattern and not a site, but the literal has to end where
    JavaScript ends it so the code after it is not swallowed.
    """
    regions = []
    _scan_javascript(text, 0, regions, False, html_comments)
    return regions


def _scan_javascript(text, start, regions, substitution, html_comments):
    """Scan JavaScript code from `start` into `regions`, answering where the
    scan stopped: the end of the text, or — inside a template substitution —
    the `}` that closes it. A substitution is scanned recursively as the code
    it is, so a binding spelled inside one still counts.
    """
    end = len(text)
    i = start
    depth = 0
    previous = ''
    while i < end:
        ch = text[i]
        if ch in ' \t\r\n':
            i += 1
        elif (text.startswith('//', i) or text.startswith('/*', i)
              or html_comments and text.startswith('<!--', i)):
            line = text[i + 1] == '/' or text.startswith('<!--', i)
            closer, skip = ('\n', 0) if line else ('*/', 2)
            close = text.find(closer, i + 2)
            close = end if close == -1 else close + skip
            regions.append((i, close, 'comment'))
            i = close
        elif ch in '\'"':
            i = _js_string(text, i, regions)
            previous = 'end'
        elif ch == '`':
            i = _js_template(text, i, regions, html_comments)
            previous = 'end'
        elif ch == '/' and previous not in _JS_DIVISION_BEFORE:
            i = _js_regex(text, i, regions)
            previous = 'end'
        elif ch in _JS_WORD:
            word = i
            while i < end and text[i] in _JS_WORD:
                i += 1
            previous = ('keyword' if text[word:i] in _JS_OPERAND_KEYWORDS
                        else 'value')
        elif ch == '{':
            depth += 1
            i += 1
            previous = '{'
        elif substitution and depth == 0 and ch == '}':
            return i
        elif ch == '}':
            depth = max(depth - 1, 0)
            i += 1
            previous = '}'
        else:
            i += 1
            previous = ch if ch in ')]}' else ''
    return end


def _js_string(text, start, regions):
    """Record one quoted string; answer the index just past it. A raw newline
    leaves it: JavaScript has no line-spanning quoted string, so a stray
    quote costs the rest of its line rather than the rest of the file.
    """
    end = len(text)
    quote = text[start]
    i = start + 1
    while i < end:
        ch = text[i]
        if ch == '\\':
            i = min(i + 2, end)
        elif ch == quote:
            regions.append((start, i + 1, 'string'))
            return i + 1
        elif ch == '\n':
            break
        else:
            i += 1
    regions.append((start, i, 'string'))
    return i


def _js_template(text, start, regions, html_comments):
    """Record one template literal: its own text is string, and each
    `${...}` substitution is scanned recursively as the code it is.
    """
    end = len(text)
    i = start + 1
    chunk = start
    while i < end:
        ch = text[i]
        if ch == '`':
            regions.append((chunk, i + 1, 'string'))
            return i + 1
        if ch == '\\':
            i = min(i + 2, end)
        elif text.startswith('${', i):
            if i > chunk:
                regions.append((chunk, i, 'string'))
            close = _scan_javascript(
                text, i + 2, regions, True, html_comments)
            if close == end:
                return end
            i = chunk = close + 1
        else:
            i += 1
    regions.append((chunk, end, 'string'))
    return end


def _js_regex(text, start, regions):
    """Record one regex literal: a version inside it is a pattern and not a
    site, but the literal has to end where JavaScript ends it so the code
    after it is not swallowed. A `/` inside a character class closes nothing.
    """
    end = len(text)
    i = start + 1
    in_class = False
    while i < end:
        ch = text[i]
        if ch == '\\':
            i = min(i + 2, end)
        elif ch == '[':
            in_class = True
            i += 1
        elif ch == ']':
            in_class = False
            i += 1
        elif ch == '/' and not in_class:
            i += 1
            while i < end and text[i] in _JS_REGEX_FLAGS:
                i += 1
            break
        elif ch == '\n':
            break
        else:
            i += 1
    regions.append((start, i, 'regex'))
    return i


def _html_regions(text):
    """Regions for HTML comments, attributes and JavaScript in script tags.

    Quotes delimit only inside a tag, so an apostrophe in ordinary text
    opens nothing, and no backslash escapes anything.
    """
    regions = []
    end = len(text)
    i = 0
    while i < end:
        if text.startswith('<!--', i):
            close = text.find('-->', i + 4)
            close = end if close == -1 else close + 3
            regions.append((i, close, 'comment'))
            i = close
            continue
        if (text[i] == '<' and i + 1 < end
                and (text[i + 1].isalpha() or text[i + 1] in '!/?')):
            tag_start = i
            i = _html_tag(text, tag_start, regions)
            if _html_tag_name(text, tag_start, i) == 'script':
                script_end = _html_script_end(text, i)
                for start, stop, kind in _javascript_regions(
                        text[i:script_end], html_comments=True):
                    regions.append((i + start, i + stop, kind))
                i = script_end
            continue
        i += 1
    return regions


def _html_tag_name(text, start, end):
    """The lowercase name of an opening tag, or an empty string."""
    i = start + 1
    if i >= end or not text[i].isalpha():
        return ''
    name_start = i
    while i < end and (text[i].isalnum() or text[i] in '-:'):
        i += 1
    return text[name_start:i].lower()


def _html_script_end(text, start):
    """The real script end tag after `start`, or the document end."""
    state = 'data'
    i = start
    while i < len(text):
        ch = text[i]
        if state == 'data':
            if text.startswith('<!--', i):
                state = 'escaped-dash-dash'
                i += 4
                continue
            if _html_script_name(text, i, '</script') is not None:
                return i
        elif state.startswith('escaped'):
            if _html_script_name(text, i, '</script') is not None:
                return i
            if _html_script_name(text, i, '<script') is not None:
                state = 'double-escaped'
            elif ch == '<':
                state = 'escaped'
            elif state == 'escaped':
                state = 'escaped-dash' if ch == '-' else state
            elif state == 'escaped-dash':
                state = ('escaped-dash-dash' if ch == '-'
                         else 'escaped')
            elif ch == '>':
                state = 'data'
            elif ch != '-':
                state = 'escaped'
        elif _html_script_name(text, i, '</script') is not None:
            state = 'escaped'
        elif ch == '<':
            state = 'double-escaped'
        elif state == 'double-escaped':
            state = 'double-escaped-dash' if ch == '-' else state
        elif state == 'double-escaped-dash':
            state = ('double-escaped-dash-dash' if ch == '-'
                     else 'double-escaped')
        elif ch == '>':
            state = 'data'
        elif ch != '-':
            state = 'double-escaped'
        i += 1
    return len(text)


def _html_script_name(text, start, token):
    """The index after a script token's name when HTML recognizes it."""
    after = start + len(token)
    if (text[start:after].lower() == token
            and after < len(text) and text[after] in '\t\n\f />'):
        return after
    return None


def _html_tag(text, start, regions):
    """Record the quoted attribute values of one tag; a value ends at the
    first matching quote, and a quote left open runs the tag past `>`.
    """
    end = len(text)
    quote = None
    opening = 0
    i = start + 1
    while i < end:
        ch = text[i]
        if quote is not None:
            if ch == quote:
                regions.append((opening, i + 1, 'string'))
                quote = None
        elif ch in '"\'':
            quote = ch
            opening = i
        elif ch == '>':
            return i + 1
        i += 1
    return end


def _python_regions(text, path):
    """(start, end, kind) for every Python comment and string token.

    A source no interpreter can tokenize is refused rather than read as one
    with no regions: a rewrite mode must not treat text inside a broken
    string as the binding it never was.
    """
    starts = [0]
    for line in text.split('\n'):
        starts.append(starts[-1] + len(line) + 1)
    regions = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in tokens:
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            start = starts[min(tok.start[0] - 1, len(starts) - 1)]
            end = starts[min(tok.end[0] - 1, len(starts) - 1)]
            regions.append((start + tok.start[1],
                            end + tok.end[1],
                            'comment' if tok.type == tokenize.COMMENT
                            else 'string'))
    except (IndentationError, SyntaxError, tokenize.TokenError) as exc:
        raise SystemExit(
            f'FAIL: cannot tokenize {path}: {exc} — refused rather than read '
            f'as a file with no regions to filter by') from exc
    return regions
