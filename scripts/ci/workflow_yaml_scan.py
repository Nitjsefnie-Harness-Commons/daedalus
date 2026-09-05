"""Locate scalar and flow regions in workflow YAML text."""
from dataclasses import dataclass

if __package__:
    # pylint: disable=relative-beyond-top-level
    from .yamlblock import block_end, parse_block_header
    from .yamlanchor import node_properties
    from .yamlscalar import (
        YAMLReadError,
        _strip_inline_comment,
        split_mapping_field,
        text_indent,
    )
else:
    from yamlblock import block_end, parse_block_header
    from yamlanchor import node_properties
    from yamlscalar import (
        YAMLReadError,
        _strip_inline_comment,
        split_mapping_field,
        text_indent,
    )


@dataclass(frozen=True)
class Line:
    """One source line with its original character offsets."""

    text: str
    start: int
    end: int


def lines(workflow):
    """Split a workflow into source lines, removing one leading BOM."""
    if not isinstance(workflow, str):
        raise YAMLReadError('workflow must be a string')
    result = []
    offset = 0
    if workflow[:1] == '\ufeff':
        workflow = workflow[1:]
        offset = 1
    for raw in workflow.splitlines(keepends=True):
        result.append(Line(raw.rstrip('\r\n'), offset, offset + len(raw)))
        offset += len(raw)
    return result


def indent(line):
    """Return the indentation of one source line."""
    return text_indent(line.text)


def meaningful(line):
    """Return whether a line carries non-comment YAML content."""
    return bool(line.text.strip(' ')) and line.text.lstrip(' ')[:1] != '#'


def field_indent(line, nested=False):
    """Return where a mapping field starts, past a sequence dash."""
    result = indent(line)
    while (
            line.text[result:result + 1] == '-'
            and line.text[result + 1:result + 2] in (' ', '\t')
            and (nested or result == indent(line))):
        spacing = line.text[result + 1:]
        result += 1 + len(spacing) - len(spacing.lstrip(' \t'))
    return result


def quote_is_open(value):
    """Return the opening quote when a scalar continues on another line."""
    value = value.lstrip(' \t')
    if value[:1] not in ("'", '"'):
        return None
    quote = value[0]
    index = 1
    while index < len(value):
        char = value[index]
        if quote == "'" and char == "'":
            if value[index:index + 2] == "''":
                index += 2
                continue
            return None
        if quote == '"' and char == '\\':
            index += 2
            continue
        if char == quote:
            return None
        index += 1
    return quote


def continued_quote_end(source, start, end, quote):
    """Return the line after a quote's closing line."""
    for index in range(start, end):
        value = source[index].text
        cursor = 0
        while cursor < len(value):
            char = value[cursor]
            if quote == "'" and char == "'":
                if value[cursor:cursor + 2] == "''":
                    cursor += 2
                    continue
                return index + 1
            if quote == '"' and char == '\\':
                cursor += 2
                continue
            if char == quote:
                return index + 1
            cursor += 1
    raise YAMLReadError('workflow has an incomplete quoted scalar')


def prepared_value(raw_value):
    """Return node properties and comment-stripped scalar content."""
    tokens, bare = node_properties(raw_value.lstrip(' \t'))
    return tokens, _strip_inline_comment(bare.strip(' \t'))


def property_only(line):
    """Return whether a line contains only node properties."""
    tokens, value = prepared_value(line.text[indent(line):])
    return bool(tokens) and not value


def sequence_value(raw, dashed=True):
    """Strip nested sequence dashes and explicit-key indicators."""
    tokens, value = prepared_value(raw)
    depth = 0
    while (
            dashed and (value == '-' or value.startswith(('- ', '-\t')))
            or (value[:1] == '?' and value[1:2] in ('', ' ', '\t'))):
        is_dash = value == '-' or value.startswith(('- ', '-\t'))
        depth += int(is_dash)
        tokens, value = prepared_value(value[1:].lstrip(' \t'))
    return tokens, value, depth


def node_parts(line):
    """Return `(value, indent, key, dashed)` for a visible YAML node."""
    line_indent = indent(line)
    body = line.text[line_indent:]
    dashed = body.startswith('-') and body[1:2] in (' ', '\t')
    if dashed:
        body = body[1:].lstrip(' \t')
    candidate = (line.text[field_indent(line, True):]
                 if dashed else body)
    position = field_indent(line, True) if dashed else line_indent
    if candidate[:1] == '?' and candidate[1:2] in ('', ' ', '\t'):
        return candidate[1:].lstrip(' \t'), position, '?', dashed
    if candidate[:1] == ':' and candidate[1:2] in ('', ' ', '\t'):
        return candidate[1:].lstrip(' \t'), position, '', dashed
    try:
        field = split_mapping_field(candidate, 'mapping', allow_tabs=True)
    except YAMLReadError:
        field = None
    if field is not None:
        key, value = field
        return value, position, key, dashed
    if body == '-' and not dashed:
        return '', line_indent, None, True
    if dashed:
        return body, line_indent, None, True
    return None


def _block_scalar_end(source, texts, index, parent_indent, value, owner,
                      dashed):
    """Return a block or multiline quote's end, if the value opens one."""
    _tokens, value, depth = sequence_value(value)
    header = parse_block_header(value, owner)
    header_index = index
    candidate_depth = 0
    header_indent = parent_indent
    header_parts = None
    if header is None and not value:
        header_index = next(
            (i for i in range(index + 1, len(source))
             if meaningful(source[i])),
            len(source),
        )
        while (
                header_index < len(source)
                and indent(source[header_index]) > parent_indent
                and property_only(source[header_index])):
            header_index = next(
                (i for i in range(header_index + 1, len(source))
                 if meaningful(source[i])),
                len(source),
            )
        if (header_index >= len(source)
                or indent(source[header_index]) < parent_indent):
            return None
        header_parts = node_parts(source[header_index]) if dashed else None
        if header_parts is None:
            header_indent = parent_indent
            candidate = source[header_index].text[
                indent(source[header_index]):]
        else:
            header_indent = header_parts[1]
            candidate = header_parts[0]
        _tokens, value, candidate_depth = sequence_value(
            candidate,
            dashed if header_parts is None else header_parts[3],
        )
        header = parse_block_header(value, owner)
    if header is not None:
        if depth:
            block_indent = indent(source[index])
            block_line = source[index]
        else:
            block_indent = parent_indent
            block_line = source[index]
        if header_index != index and dashed and header_parts is not None:
            block_indent = header_indent
            depth = candidate_depth
            block_line = source[header_index]
        for _ in range(depth):
            spacing = block_line.text[block_indent + 1:]
            block_indent += 1 + len(spacing) - len(spacing.lstrip(' \t'))
        return header_index, block_end(
            texts,
            header_index + 1,
            len(texts),
            block_indent,
            header,
        )
    quote = quote_is_open(value)
    if quote is not None:
        return header_index, continued_quote_end(
            source, header_index + 1, len(source), quote)
    return None


def _flow_property_end(text, start):
    if text.startswith('!<', start):
        return text.find('>', start + 2) + 1 or len(text)
    return next(
        (i for i in range(start + 1, len(text))
         if text[i].isspace() or text[i] in ',[]{}'),
        len(text),
    )


def _flow_end(source, start, end, value):
    """Return the line after a flow collection closes."""
    depth = 0
    quote = None
    node_start = True
    for index in range(start, end):
        text = value if index == start else source[index].text
        cursor = 0
        while cursor < len(text):
            char = text[cursor]
            if quote:
                if quote == "'" and text[cursor:cursor + 2] == "''":
                    cursor += 2
                    continue
                if quote == '"' and char == '\\':
                    cursor += 2
                    continue
                if char == quote:
                    quote = None
                cursor += 1
                continue
            if char == '#' and (cursor == 0 or text[cursor - 1].isspace()):
                break
            if node_start and char in '&!':
                cursor = _flow_property_end(text, cursor)
                continue
            if (node_start and char == '?'
                    and text[cursor + 1:cursor + 2] in ('', ' ', '\t')):
                cursor += 1
                continue
            if node_start and char in ("'", '"'):
                quote = char
            elif char in '[{':
                depth += 1
                node_start = True
            elif char in ']}':
                depth -= 1
                if depth <= 0:
                    return index + 1
                node_start = False
            elif char == ',':
                node_start = True
            elif char == ':' and text[cursor + 1:cursor + 2] in (
                    '', ' ', '\t', "'", '"', '&', '!'):
                node_start = True
            elif not char.isspace():
                node_start = False
            cursor += 1
    return end


def _flow_start(source, index, end, raw_value, dashed):
    _tokens, value, _depth = sequence_value(raw_value)
    if not value and dashed:
        candidate = next(
            (i for i in range(index + 1, len(source))
             if meaningful(source[i])),
            len(source),
        )
    else:
        candidate = index
    if candidate >= len(source):
        return None
    if candidate != index:
        _tokens, value, _depth = sequence_value(
            source[candidate].text[indent(source[candidate]):])
    if not value.startswith(('[', '{')):
        return None
    return candidate, _flow_end(source, candidate, end, value)


def scalar_regions(source, texts):
    """Find scalar continuations and opaque flow-collection extents."""
    scalar = set()
    opaque = set()
    index = 0
    while index < len(source):
        if not meaningful(source[index]) or index in scalar:
            index += 1
            continue
        parts = node_parts(source[index])
        if parts is None:
            index += 1
            continue
        raw_value, parent_indent, raw_key, dashed = parts
        if raw_key == '?':
            owner = 'mapping key'
        elif raw_key == '':
            owner = 'mapping value'
        elif raw_key is not None:
            owner = f'mapping field {raw_key.strip()!r}'
        else:
            owner = 'sequence item'
        stop = _block_scalar_end(
            source, texts, index, parent_indent, raw_value, owner, dashed)
        if stop is not None:
            header_index, stop = stop
            scalar.update(range(header_index + 1, stop))
            index = stop
            continue
        flow = _flow_start(
            source,
            index,
            len(source),
            raw_value,
            dashed or raw_key is not None,
        )
        if flow is not None:
            flow_index, stop = flow
            opaque.update(range(flow_index, stop))
            scalar.update(range(flow_index + 1, stop))
            index = stop
            continue
        index += 1
    return scalar, opaque
