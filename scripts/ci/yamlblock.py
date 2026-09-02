"""Decode the block scalars the workflow YAML subset admits."""
import re

if __package__:
    # pylint: disable-next=relative-beyond-top-level
    from .yamlscalar import YAMLReadError
else:
    from yamlscalar import YAMLReadError


BLOCK_HEADER = re.compile(
    r'^(?P<style>[>|])(?P<first>[1-9]?)(?P<chomp>[+-]?)'
    r'(?P<second>[1-9]?)$')


def text_indent(text):
    """The indentation of one line, refusing a tab where spaces belong."""
    count = len(text) - len(text.lstrip(' '))
    if text[:1] == '\t':
        raise YAMLReadError('tabs in YAML indentation are unsupported')
    return count


def block_end(texts, start, end, header_indent, match):
    """The index past the block `match` heads, searching `start` to `end`."""
    explicit = int(match.group('first') or match.group('second') or 0)
    if match.group('first') and match.group('second'):
        raise YAMLReadError('workflow block has two indentation indicators')
    content_indent = header_indent + explicit
    if not explicit:
        for index in range(start, end):
            if not texts[index].strip(' '):
                continue
            content_indent = text_indent(texts[index])
            if content_indent <= header_indent:
                # Blank lines already passed are body, and keep
                # chomping decodes each of them as a break.
                return index
            break
        else:
            return end
    for index in range(start, end):
        if texts[index].strip(' ') \
                and text_indent(texts[index]) < content_indent:
            return index
    return end


def parse_block_header(value, owner):
    """The style, chomping indicator and explicit indentation of a header."""
    match = BLOCK_HEADER.fullmatch(value)
    if match is None:
        raise YAMLReadError(f'{owner} has an unsupported block header')
    first = match.group('first')
    second = match.group('second')
    if first and second:
        raise YAMLReadError(f'{owner} has two indentation indicators')
    explicit = int(first or second or 0)
    return match.group('style'), match.group('chomp') or '', explicit


def decode_block(lines, parent_indent, style, chomp, explicit, owner):
    """Decode a block body given as `(text, line ended)` pairs."""
    if not lines:
        return ''
    if explicit:
        content_indent = parent_indent + explicit
    else:
        content_indent = parent_indent + 1
        for text, _ended in lines:
            if text.strip(' '):
                content_indent = max(
                    content_indent, len(text) - len(text.lstrip(' ')))
                break
            content_indent = max(content_indent, len(text))
    parts = []
    more_indented = []
    for text, _ended in lines:
        if not text.strip(' ') and len(text) <= content_indent:
            parts.append('')
            more_indented.append(False)
            continue
        indent = len(text) - len(text.lstrip(' '))
        if indent < content_indent:
            raise YAMLReadError(f'{owner} block indentation is incomplete')
        parts.append(text[content_indent:])
        more_indented.append(indent > content_indent)
    if style == '|':
        value = '\n'.join(parts)
    else:
        value = _fold_block(parts, more_indented)
    if lines[-1][1]:
        value += '\n'
    if chomp == '-':
        return value.rstrip('\n')
    if chomp == '+':
        return value
    return value.rstrip('\n') + (
        '\n' if value.endswith('\n') and any(parts) else '')


def _fold_block(parts, more_indented):
    value = parts[0]
    seen_content = bool(parts[0])
    last_content_more = more_indented[0] if parts[0] else False
    for index in range(1, len(parts)):
        previous = parts[index - 1]
        current = parts[index]
        if not previous:
            separator = '\n' if (
                not current or not seen_content or last_content_more
                or more_indented[index]) else ''
        elif (not current or more_indented[index - 1]
              or more_indented[index]):
            separator = '\n'
        else:
            separator = ' '
        value += separator + current
        if current:
            seen_content = True
            last_content_more = more_indented[index]
    return value
