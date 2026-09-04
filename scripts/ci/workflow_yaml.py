"""Decode scalars and locate actual step mappings in workflow YAML."""
from dataclasses import dataclass

if __package__:
    # pylint: disable=relative-beyond-top-level
    from .yamlblock import block_end, decode_block, parse_block_header
    from .yamlanchor import (
        has_anchor_spelling, node_properties, parse_alias,
        validate_node_properties,
    )
    from .yamlscalar import (
        YAMLReadError,
        _strip_inline_comment,
        decode_inline_scalar,
        split_mapping_field,
        text_indent,
    )
else:
    from yamlblock import block_end, decode_block, parse_block_header
    from yamlanchor import (
        has_anchor_spelling, node_properties, parse_alias,
        validate_node_properties,
    )
    from yamlscalar import (
        YAMLReadError,
        _strip_inline_comment,
        decode_inline_scalar,
        split_mapping_field,
        text_indent,
    )


@dataclass(frozen=True)
class StepItem:
    """One actual mapping item in a named job's steps sequence."""

    index: int
    end_index: int
    indent: int
    start: int
    end: int
    name: str | None
    identity: str | None


@dataclass(frozen=True)
class _Line:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class _Entry:
    index: int
    indent: int
    rest: str = ''


def _lines(workflow):
    if not isinstance(workflow, str):
        raise YAMLReadError('workflow must be a string')
    lines = []
    offset = 0
    if workflow[:1] == '\ufeff':
        workflow = workflow[1:]
        offset = 1
    for raw in workflow.splitlines(keepends=True):
        lines.append(_Line(raw.rstrip('\r\n'), offset, offset + len(raw)))
        offset += len(raw)
    return lines


def _indent(line):
    return text_indent(line.text)


def _meaningful(line):
    return bool(line.text.strip(' ')) and line.text.lstrip(' ')[:1] != '#'


def _split_field(text, owner):
    return split_mapping_field(text, owner, allow_tabs=True)


def _field_indent(line):
    """Where a mapping field starts, past any sequence dash on its line."""
    indent = _indent(line)
    after_dash = line.text[indent + 1:]
    if line.text[indent:indent + 1] == '-' and after_dash[:1] in (' ', '\t'):
        indent += 1 + len(after_dash) - len(after_dash.lstrip(' \t'))
    return indent


def _quote_is_open(value):
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


def _continued_quote_end(lines, start, end, quote):
    for index in range(start, end):
        value = lines[index].text
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


def _prepared_value(raw_value):
    """A field value's property tokens and its comment-stripped content.

    The properties come off first: a quote opening after an anchor or a
    tag is a quote, not plain text, so its `#` is not a comment.
    """
    tokens, bare = node_properties(raw_value.lstrip(' \t'))
    return tokens, _strip_inline_comment(bare.strip(' \t'))


def _node_parts(line):
    indent = _indent(line)
    body = line.text[indent:]
    dashed = body.startswith('-') and body[1:2] in (' ', '\t')
    if dashed:
        body = body[1:].lstrip(' \t')
    try:
        field = _split_field(body, 'mapping')
    except YAMLReadError:
        field = None
    if field is not None:
        key, value = field
        position = _field_indent(line) if dashed else indent
        return value, position, key, dashed
    if dashed:
        return body, indent, None, True
    return None


def _flow_property_end(text, start):
    if text.startswith('!<', start):
        return text.find('>', start + 2) + 1 or len(text)
    return next((i for i in range(start + 1, len(text))
                 if text[i].isspace() or text[i] in ',[]{}'), len(text))


def _flow_end(lines, start, end, value):
    depth, quote = 0, None
    node_start = True
    for index in range(start, end):
        text = value if index == start else lines[index].text
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
            if node_start and char in ("'", '"'):
                quote = char
            elif char in '[{':
                depth, node_start = depth + 1, True
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


def _scalar_regions(lines, texts):
    """Find scalar continuations and opaque flow-collection extents."""
    scalar = set()
    opaque = set()
    index = 0
    while index < len(lines):
        if not _meaningful(lines[index]) or index in scalar:
            index += 1
            continue
        parts = _node_parts(lines[index])
        if parts is None:
            index += 1
            continue
        raw_value, parent_indent, raw_key, _dashed = parts
        _tokens, value = _prepared_value(raw_value)
        header_owner = (f'mapping field {raw_key.strip()!r}'
                        if raw_key is not None else 'sequence item')
        header = parse_block_header(value, header_owner)
        if header is not None:
            stop = block_end(
                texts, index + 1, len(texts), parent_indent, header)
            scalar.update(range(index + 1, stop))
            index = stop
            continue
        quote = _quote_is_open(value)
        if quote is not None:
            stop = _continued_quote_end(
                lines, index + 1, len(lines), quote)
            scalar.update(range(index + 1, stop))
            index = stop
            continue
        if value.startswith(('[', '{')):
            stop = _flow_end(lines, index, len(lines), value)
            opaque.update(range(index, stop))
            scalar.update(range(index + 1, stop))
            index = stop
            continue
        index += 1
    return scalar, opaque


def _section(lines, scalar, start, parent_indent):
    for index in range(start + 1, len(lines)):
        if index in scalar:
            continue
        if (_meaningful(lines[index])
                and _indent(lines[index]) <= parent_indent):
            return start + 1, index
    return start + 1, len(lines)


def _first_child(lines, scalar, start, end, parent_indent):
    for index in range(start, end):
        if _meaningful(lines[index]) and _indent(lines[index]) > parent_indent:
            return index
    return None


def _mapping_entry(lines, scalar, start, end, parent_indent, name):
    child_indent = None
    matches = []
    for index in range(start, end):
        if index in scalar or not _meaningful(lines[index]):
            continue
        indent = _indent(lines[index])
        if indent <= parent_indent:
            continue
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue
        field = lines[index].text[indent:]
        if field.startswith('-'):
            raise YAMLReadError('mapping body contains a sequence item')
        raw_key, rest = _split_field(field, 'mapping')
        key = decode_inline_scalar(raw_key, 'mapping key')
        if key == name:
            matches.append(_Entry(index, indent, rest))
    if len(matches) > 1:
        raise YAMLReadError(f'duplicate mapping key: {name}')
    return matches[0] if matches else None


def _mapping_body(lines, scalar, entry, owner):
    if entry is None:
        return None
    if entry.rest.strip(' \t'):
        raise YAMLReadError(f'{owner} is not a mapping')
    body = _section(lines, scalar, entry.index, entry.indent)
    first = _first_child(lines, scalar, *body, entry.indent)
    if first is not None:
        field = lines[first].text[_indent(lines[first]):]
        if field.startswith('-'):
            raise YAMLReadError(f'{owner} is not a mapping')
    return body


def _alias_value(lines, texts, scalar, opaque, alias_index, name, owner):
    target = f'{owner} alias &{name}'
    for index in range(alias_index - 1, -1, -1):
        if (index in opaque
                and has_anchor_spelling(lines[index].text, name)):
            raise YAMLReadError(
                f'{owner} has an unsupported YAML alias target in a flow '
                f'collection: &{name}')
        if index in scalar or not _meaningful(lines[index]):
            continue
        parts = _node_parts(lines[index])
        if parts is None:
            continue
        raw_value, indent, raw_key, dashed = parts
        if raw_key is not None:
            key_tokens, _key = node_properties(raw_key.lstrip(' \t'))
            if f'&{name}' in key_tokens:
                position = 'sequence item' if dashed else 'mapping key'
                raise YAMLReadError(
                    f'{owner} has an unsupported YAML alias target on a '
                    f'{position}: &{name}')
        if raw_key is None:
            tokens, content = _prepared_value(raw_value)
            if not dashed or f'&{name}' not in tokens:
                continue
            raise YAMLReadError(
                f'{owner} has an unsupported YAML alias target on a '
                f'sequence item: &{name}')
        tokens, content = _prepared_value(raw_value)
        if f'&{name}' not in tokens:
            continue
        _body, end = _section(lines, scalar, index, indent)
        if not content:
            first = _first_child(lines, scalar, index + 1, end, indent)
            shape = 'scalar'
            if first is not None:
                body = lines[first].text[_indent(lines[first]):]
                if body.startswith('-') and body[1:2] in (' ', '\t'):
                    shape = 'sequence'
                elif body.startswith(('[', '{')):
                    shape = 'flow collection'
                elif _node_parts(lines[first]) is not None:
                    shape = 'mapping'
            if first is None:
                raise YAMLReadError(
                    f'{owner} has an unsupported alias to an empty node: '
                    f'&{name}')
            raise YAMLReadError(
                f'{owner} has an unsupported alias to a nested '
                f'{shape}: &{name}')
        prepared = (tokens, content)
        return _step_value(
            lines, texts, scalar, opaque, index, end, indent, prepared, target)
    raise YAMLReadError(f'{owner} has an unknown YAML alias: &{name}')


def _step_value(
        lines, texts, scalar, opaque, start, end, indent, prepared, owner):
    tokens, content = prepared
    validate_node_properties(tokens, owner)
    alias = parse_alias(content, owner)
    if tokens and content.startswith('*'):
        raise YAMLReadError(
            f'{owner} has an alias carrying node properties: '
            f'{" ".join(tokens)} {content}')
    if alias is not None:
        return _alias_value(
            lines, texts, scalar, opaque, start, alias, owner)
    if not content:
        raise YAMLReadError(f'{owner} has no scalar value')
    header = parse_block_header(content, owner)
    if header is not None:
        stop = block_end(texts, start + 1, end, indent, header)
        body = [(line.text, line.end > line.start + len(line.text))
                for line in lines[start + 1:stop]]
        return decode_block(body, indent, header, owner)
    if content.startswith(("'", '"')):
        if _quote_is_open(content) is not None:
            raise YAMLReadError(f'{owner} has an unsupported multiline scalar')
        return decode_inline_scalar(content, owner)
    if content.startswith(('[', '{', '@', '`')):
        raise YAMLReadError(f'{owner} has an unsupported scalar')
    if '\t' in content or any(
            ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff
            for char in content):
        raise YAMLReadError(f'{owner} has an unsupported scalar')
    if any(_meaningful(lines[index]) and _indent(lines[index]) > indent
           for index in range(start + 1, end)):
        raise YAMLReadError(f'{owner} has an unsupported multiline scalar')
    return content


def _step_fields(lines, scalar, index, end, item_indent):
    body = lines[index].text[item_indent:]
    if not body.startswith('-') or body[1:2] not in (' ', '\t'):
        raise YAMLReadError('step sequence contains a non-item')
    field_indent = _field_indent(lines[index])
    fields = [(index, lines[index].text[field_indent:])]
    for following in range(index + 1, end):
        if following in scalar or not _meaningful(lines[following]):
            continue
        indent = _indent(lines[following])
        if indent < field_indent:
            raise YAMLReadError('step mapping has inconsistent indentation')
        if indent == field_indent:
            fields.append((following, lines[following].text[indent:]))
    return field_indent, fields


def _decoded_step_fields(
        lines, texts, scalar, opaque, index, end, item_indent):
    field_indent, fields = _step_fields(lines, scalar, index, end, item_indent)
    if fields:
        first_index, first_field = fields[0]
        tokens, first_field = node_properties(first_field)
        if tokens:
            validate_node_properties(tokens, 'step mapping')
            fields = ([(first_index, first_field)] if first_field else []) + \
                fields[1:]
    values = {}
    for offset, (field_index, field) in enumerate(fields):
        field_end = fields[offset + 1][0] if offset + 1 < len(
            fields) else end
        raw_key, raw_value = _split_field(field, 'step')
        key = decode_inline_scalar(raw_key, 'step key')
        if key in values and key in ('name', 'id'):
            raise YAMLReadError(f'duplicate mapping key: {key}')
        if key in ('name', 'id'):
            values[key] = _step_value(
                lines, texts, scalar, opaque, field_index, field_end,
                field_indent, _prepared_value(raw_value), f'step {key}')
        else:
            values[key] = None
    return values


def workflow_step_items(workflow, job):
    """Return actual mapping items in the named job's steps sequence."""
    lines = _lines(workflow)
    texts = [line.text for line in lines]
    scalar, opaque = _scalar_regions(lines, texts)
    jobs = _mapping_entry(lines, scalar, 0, len(lines), -1, 'jobs')
    jobs_body = _mapping_body(lines, scalar, jobs, 'jobs')
    if jobs is None or jobs_body is None:
        return None
    job_entry = _mapping_entry(lines, scalar, *jobs_body, jobs.indent, job)
    if job_entry is None:
        return None
    job_body = _mapping_body(lines, scalar, job_entry, f'job {job!r}')
    if job_body is None:
        return None
    steps = _mapping_entry(lines, scalar, *job_body, job_entry.indent, 'steps')
    if steps is None:
        return None
    if steps.rest.strip(' \t'):
        raise YAMLReadError(f'job {job!r} steps are not a sequence')
    start, end = _section(lines, scalar, steps.index, steps.indent)
    first = _first_child(lines, scalar, start, end, steps.indent)
    if first is None:
        raise YAMLReadError(f'job {job!r} steps are not a sequence')
    item_indent = _indent(lines[first])
    indices = []
    for index in range(start, end):
        if index in scalar or not _meaningful(lines[index]):
            continue
        indent = _indent(lines[index])
        if indent == item_indent:
            body = lines[index].text[indent:]
            if not body.startswith('-') or body[1:2] not in (' ', '\t'):
                raise YAMLReadError('step sequence contains a non-item')
            indices.append(index)
        elif indent < item_indent:
            raise YAMLReadError(
                'step sequence has inconsistent indentation')
    items = []
    for offset, index in enumerate(indices):
        item_end = indices[offset + 1] if offset + 1 < len(
            indices) else end
        fields = _decoded_step_fields(
            lines, texts, scalar, opaque, index, item_end, item_indent)
        last = max(line_index for line_index in range(index, item_end)
                   if line_index in scalar or _meaningful(lines[line_index]))
        items.append(StepItem(
            index=index, end_index=item_end, indent=item_indent,
            start=lines[index].start, end=lines[last].end,
            name=fields.get('name'), identity=fields.get('id')))
    return items
