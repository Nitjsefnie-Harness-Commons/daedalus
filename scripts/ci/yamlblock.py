"""Decode the block scalars the workflow YAML subset admits."""
import re
from typing import NamedTuple

if __package__:
    # pylint: disable=relative-beyond-top-level
    from .yamlscalar import YAMLReadError, text_indent
else:
    from yamlscalar import YAMLReadError, text_indent


class BlockHeader(NamedTuple):
    style: str
    chomp: str
    explicit: int


_HEADER = re.compile(
    r'^(?P<style>[>|])(?P<first>[0-9]?)(?P<chomp>[+-]?)'
    r'(?P<second>[0-9]?)$')


def parse_block_header(value, owner):
    """The header record of a block scalar, or None for other scalars.

    `0` is no indentation indicator: a YAML header's indicator must be
    1-9, so `|0` refuses here rather than passing for a plain scalar and
    sending its body to the multiline check.
    """
    match = _HEADER.fullmatch(value)
    if match is None:
        return None
    first = match.group('first')
    second = match.group('second')
    if first and second:
        raise YAMLReadError(f'{owner} has two indentation indicators')
    if first == '0' or second == '0':
        raise YAMLReadError(f'{owner} has an unsupported block header')
    return BlockHeader(
        match.group('style'), match.group('chomp') or '',
        int(first or second or 0))


def block_end(texts, start, end, header_indent, header):
    """The index past the lines one block `header` owns."""
    content_indent = header_indent + header.explicit
    if not header.explicit:
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


def decode_block(lines, parent_indent, header, owner):
    """Decode a block body given as `(text, line ended)` pairs."""
    style, chomp, explicit = header
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
