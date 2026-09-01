#!/usr/bin/env python3
"""Track the HTML parser context needed by the version-region scanner."""
import re


_FOREIGN_BREAKOUT = frozenset(
    ('b big blockquote body br center code dd div dl dt em embed h1 h2 h3 '
     'h4 h5 h6 head hr i img li listing menu meta nobr ol p pre ruby s '
     'small span strong strike sub sup table tt u ul var').split())
_MATH_TEXT_INTEGRATION = frozenset(('mi', 'mo', 'mn', 'ms', 'mtext'))


def html_tag_name(text, start, end):
    """The lowercase name of an opening tag, or an empty string."""
    i = start + 1
    if i >= end or not text[i].isalpha():
        return ''
    name_start = i
    while i < end and (text[i].isalnum() or text[i] in '-:'):
        i += 1
    return text[name_start:i].lower()


def html_tag_context(text, start, end, contexts):
    tag_name = html_tag_name(text, start, end)
    current = contexts[-1][2] if contexts else 'html'
    html_context = current == 'html'
    closing = (html_tag_name(text, start + 1, end)
               if text[start:start + 2] == '</' else '')
    if closing:
        if not html_context and closing in ('br', 'p'):
            _pop_foreign(contexts)
            html_context = True
        else:
            for index in range(len(contexts) - 1, -1, -1):
                if contexts[index][0] == closing:
                    del contexts[index:]
                    break
        return tag_name, html_context, False
    math_exception = (contexts and contexts[-1][0] in
                      _MATH_TEXT_INTEGRATION
                      and contexts[-1][1] == 'math'
                      and tag_name in ('mglyph', 'malignmark'))
    annotation_svg = (contexts and contexts[-1][0] == 'annotation-xml'
                      and contexts[-1][1] == 'math' and tag_name == 'svg')
    if math_exception:
        current, html_context = 'math', False
    elif annotation_svg:
        current, html_context = 'html', True
    if (not html_context
            and _foreign_breakout(text, start, end, tag_name)):
        _pop_foreign(contexts)
        current, html_context = 'html', True
    if not tag_name or text[start:end].endswith('/>'):
        return tag_name, html_context, False
    opened_html = False
    if html_context and tag_name in ('svg', 'math'):
        contexts.append((tag_name, tag_name, tag_name))
    elif not html_context:
        child_context = current
        if (current == 'svg'
                and tag_name in ('foreignobject', 'desc', 'title')):
            child_context = 'html'
        elif (current == 'math'
              and tag_name in _MATH_TEXT_INTEGRATION):
            child_context = 'html'
        elif (current == 'math' and tag_name == 'annotation-xml'
              and (attribute_value(
                  text, start, end, 'encoding') or '').lower()
              in ('text/html', 'application/xhtml+xml')):
            child_context = 'html'
        opened_html = child_context == 'html'
        contexts.append((tag_name, current, child_context))
    return tag_name, html_context, opened_html


def _pop_foreign(contexts):
    while contexts and contexts[-1][2] != 'html':
        contexts.pop()


def _foreign_breakout(text, start, end, tag_name):
    return (tag_name in _FOREIGN_BREAKOUT
            or (tag_name == 'font' and any(
                attribute_value(text, start, end, name) is not None
                for name in ('color', 'face', 'size'))))


def attribute_value(text, start, end, wanted):
    quoted = []
    html_tag(text, start, quoted)
    pattern = re.compile(
        r'(?i)[\t\n\f\r ]' + re.escape(wanted)
        + r'(?=[\t\n\f\r />=])(?:[\t\n\f\r ]*=[\t\n\f\r ]*'
        + r'(?:"([^"]*)"|\'([^\']*)\'|([^\t\n\f\r >]+)))?')
    for match in pattern.finditer(text, start, end):
        if not any(begin < match.start() < stop
                   for begin, stop, _kind in quoted):
            return next((value for value in match.groups()
                         if value is not None), '')
    return None


def html_tag(text, start, regions):
    """Record the quoted attribute values of one tag."""
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
