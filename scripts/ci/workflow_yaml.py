"""Decode scalars and locate actual step mappings in workflow YAML."""
from dataclasses import dataclass

if __package__:
    # pylint: disable=relative-beyond-top-level
    from .yamlblock import block_end, decode_block, parse_block_header
    from .yamlanchor import ALIAS, node_properties, strip_node_properties
    from .yamlscalar import (
        YAMLReadError,
        _strip_inline_comment,
        decode_inline_scalar,
        split_mapping_field,
        text_indent,
    )
else:
    from yamlblock import block_end, decode_block, parse_block_header
    from yamlanchor import ALIAS, node_properties, strip_node_properties
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
    for raw in workflow.splitlines(keepends=True):
        lines.append(_Line(raw.rstrip('\r\n'), offset, offset + len(raw)))
        offset += len(raw)
    return lines


def _indent(line):
    return text_indent(line.text)


def _meaningful(line):
    return (bool(line.text.strip(' '))
            and not line.text.lstrip(' ').startswith('#'))


def _split_field(text, owner):
    return split_mapping_field(text, owner, allow_tabs=True)


def _field_indent(line):
    """Where a mapping field starts, past any sequence dash on its line."""
    indent = _indent(line)
    after_dash = line.text[indent + 1:]
    if line.text[indent:indent + 1] == '-' and after_dash[:1] in (' ', '\t'):
        indent += 1 + len(after_dash) - len(after_dash.lstrip(' \t'))
    return indent


def _line_field(text):
    body = text.lstrip(' ')
    if body.startswith('-') and body[1:2] in (' ', '\t'):
        body = body[1:].lstrip(' \t')
    try:
        return _split_field(body, 'mapping')
    except YAMLReadError:
        return None


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


def _scalar_lines(lines):
    texts = [line.text for line in lines]
    scalar = set()
    index = 0
    while index < len(lines):
        if not _meaningful(lines[index]):
            index += 1
            continue
        field = _line_field(lines[index].text)
        if field is None:
            index += 1
            continue
        _key, raw_value = field
        _tokens, value = _prepared_value(raw_value)
        header = parse_block_header(value, 'workflow')
        if header is not None:
            stop = block_end(
                texts, index + 1, len(texts),
                _field_indent(lines[index]), header)
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
        index += 1
    return scalar


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


def _alias_value(lines, scalar, alias_index, name, owner):
    """The value of the last `&name` node defined before `alias_index`."""
    target = f'{owner} alias &{name}'
    for index in range(alias_index - 1, -1, -1):
        if index in scalar or not _meaningful(lines[index]):
            continue
        field = _line_field(lines[index].text)
        if field is None:
            continue
        raw_value = field[1]
        tokens, content = _prepared_value(raw_value)
        if f'&{name}' not in tokens:
            continue
        indent = _field_indent(lines[index])
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
                elif _line_field(body) is not None:
                    shape = 'mapping'
            raise YAMLReadError(
                f'{owner} has an unsupported alias to a nested '
                f'{shape}: &{name}')
        return _step_value(
            lines, scalar, index, end, indent, raw_value, target)
    raise YAMLReadError(f'{owner} has an unknown YAML alias: &{name}')


def _step_value(lines, scalar, start, end, indent, raw_value, owner):
    tokens, content = _prepared_value(raw_value)
    if tokens and content.startswith('*'):
        raise YAMLReadError(
            f'{owner} has an alias carrying node properties: '
            f'{" ".join(tokens)} {content}')
    value = strip_node_properties(raw_value.strip(' \t'), owner)
    if value.startswith('*'):
        alias = ALIAS.fullmatch(value)
        if alias is None:
            raise YAMLReadError(f'{owner} has a malformed YAML alias')
        return _alias_value(lines, scalar, start, alias.group('name'), owner)
    if not value:
        raise YAMLReadError(f'{owner} has no scalar value')
    header = parse_block_header(value, owner)
    if header is not None:
        texts = [line.text for line in lines]
        stop = block_end(texts, start + 1, end, indent, header)
        body = [
            (line.text, line.end > line.start + len(line.text))
            for line in lines[start + 1:stop]
        ]
        return decode_block(body, indent, header, owner)
    if value.startswith(("'", '"')):
        if _quote_is_open(value) is not None:
            raise YAMLReadError(f'{owner} has an unsupported multiline scalar')
        return decode_inline_scalar(value, owner)
    if value.startswith(('*', '[', '{', '@', '`')):
        raise YAMLReadError(f'{owner} has an unsupported scalar')
    if '\t' in value or any(
            ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff
            for char in value):
        raise YAMLReadError(f'{owner} has an unsupported scalar')
    if any(_meaningful(lines[index]) and _indent(lines[index]) > indent
           for index in range(start + 1, end)):
        raise YAMLReadError(f'{owner} has an unsupported multiline scalar')
    return value


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


def _decoded_step_fields(lines, scalar, index, end, item_indent):
    field_indent, fields = _step_fields(
        lines, scalar, index, end, item_indent)
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
                lines, scalar, field_index, field_end, field_indent,
                raw_value, f'step {key}')
        else:
            values[key] = None
    return values


def workflow_step_items(workflow, job):
    """Return actual mapping items in the named job's steps sequence."""
    lines = _lines(workflow)
    scalar = _scalar_lines(lines)
    jobs = _mapping_entry(lines, scalar, 0, len(lines), -1, 'jobs')
    jobs_body = _mapping_body(lines, scalar, jobs, 'jobs')
    if jobs_body is None:
        return None
    job_entry = _mapping_entry(
        lines, scalar, *jobs_body, jobs.indent, job)
    job_body = _mapping_body(
        lines, scalar, job_entry, f'job {job!r}')
    if job_body is None:
        return None
    steps = _mapping_entry(
        lines, scalar, *job_body, job_entry.indent, 'steps')
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
            lines, scalar, index, item_end, item_indent)
        content = [
            line_index for line_index in range(index, item_end)
            if line_index in scalar or _meaningful(lines[line_index])
        ]
        items.append(StepItem(
            index=index,
            end_index=item_end,
            indent=item_indent,
            start=lines[index].start,
            end=lines[content[-1]].end,
            name=fields.get('name'),
            identity=fields.get('id')))
    return items
