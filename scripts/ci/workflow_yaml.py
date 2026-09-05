"""Decode scalars and locate actual step mappings in workflow YAML."""
from dataclasses import dataclass

if __package__:
    # pylint: disable=relative-beyond-top-level
    from .workflow_yaml_scan import (
        field_indent as _field_indent,
        indent as _indent,
        lines as _lines,
        meaningful as _meaningful,
        node_parts as _node_parts,
        prepared_value as _prepared_value,
        property_only as _property_only,
        quote_is_open as _quote_is_open,
        scalar_regions as _scalar_regions,
        sequence_value as _sequence_value,
    )
    from .yamlanchor import (
        has_anchor_spelling,
        node_properties,
        parse_alias,
        validate_node_properties,
    )
    from .yamlblock import block_end, decode_block, parse_block_header
    from .yamlscalar import (
        YAMLReadError,
        decode_inline_scalar,
        split_mapping_field,
    )
else:
    from workflow_yaml_scan import (
        field_indent as _field_indent,
        indent as _indent,
        lines as _lines,
        meaningful as _meaningful,
        node_parts as _node_parts,
        prepared_value as _prepared_value,
        property_only as _property_only,
        quote_is_open as _quote_is_open,
        scalar_regions as _scalar_regions,
        sequence_value as _sequence_value,
    )
    from yamlanchor import (
        has_anchor_spelling,
        node_properties,
        parse_alias,
        validate_node_properties,
    )
    from yamlblock import block_end, decode_block, parse_block_header
    from yamlscalar import (
        YAMLReadError,
        decode_inline_scalar,
        split_mapping_field,
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
class _Entry:
    index: int
    indent: int
    rest: str = ''


def _split_field(text, owner):
    return split_mapping_field(text, owner, allow_tabs=True)


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


def _anchor_position(lines, index, name, parts):
    previous = -1
    previous_parts = None
    for candidate in range(index - 1, -1, -1):
        if not _meaningful(lines[candidate]):
            continue
        previous = candidate
        previous_parts = _node_parts(lines[candidate])
        if (previous_parts is not None
                or not _property_only(lines[candidate])):
            break
    if previous_parts is None:
        previous_value, previous_depth = '', 0
    else:
        _tokens, previous_value, previous_depth = _sequence_value(
            previous_parts[0])
    if parts is None:
        line_indent = _indent(lines[index])
        tokens = _prepared_value(
            lines[index].text[line_indent:])[0]
        if previous_parts is None:
            return None
        if f'&{name}' not in tokens or previous_value:
            return None
        if _indent(lines[previous]) >= line_indent:
            return None
        previous_key = previous_parts[2]
        if previous_key not in (None, '?'):
            return 'mapping value'
        if previous_key == '?':
            return 'mapping key'
        return 'nested sequence' if previous_depth else 'sequence item'
    raw_value, _indentation, raw_key, dashed = parts
    if raw_key == '?':
        position, tokens = 'mapping key', _prepared_value(raw_value)[0]
    elif raw_key is None:
        tokens, _value, depth = _sequence_value(raw_value)
        position = 'nested sequence item' if depth else 'sequence item'
    elif raw_key == '':
        return None
    else:
        nested = (
            dashed
            and (_field_indent(lines[index], True)
                 != _field_indent(lines[index], False))
        ) or (
            not dashed
            and previous >= 0
            and previous_depth
            and not previous_value
            and _indent(lines[previous]) < _indent(lines[index])
        )
        if nested:
            position = 'nested sequence item'
        elif dashed:
            position = 'sequence item'
        else:
            position = 'mapping key'
        tokens = _prepared_value(raw_key)[0]
    return position if f'&{name}' in tokens else None


def _alias_value(
        lines, texts, scalar, opaque, alias_index, name, owner):
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
        position = _anchor_position(lines, index, name, parts)
        if position is not None:
            raise YAMLReadError(
                f'{owner} has an unsupported YAML alias target on a '
                f'{position}: &{name}')
        if parts is None:
            continue
        raw_value, indent, raw_key, _dashed = parts
        if raw_key == '?': continue
        if raw_key is None:
            continue
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
            lines,
            texts,
            scalar,
            opaque,
            index,
            end,
            indent,
            prepared,
            target,
        )
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
            lines,
            texts,
            scalar,
            opaque,
            start,
            alias,
            owner,
        )
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
        field_end = fields[offset + 1][0] if offset + 1 < len(fields) else end
        raw_key, raw_value = _split_field(field, 'step')
        key = decode_inline_scalar(raw_key, 'step key')
        if key in values and key in ('name', 'id'):
            raise YAMLReadError(f'duplicate mapping key: {key}')
        if key in ('name', 'id'):
            values[key] = _step_value(
                lines,
                texts,
                scalar,
                opaque,
                field_index,
                field_end,
                field_indent,
                _prepared_value(raw_value),
                f'step {key}',
            )
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
            raise YAMLReadError('step sequence has inconsistent indentation')
    items = []
    for offset, index in enumerate(indices):
        item_end = indices[offset + 1] if offset + 1 < len(indices) else end
        fields = _decoded_step_fields(
            lines,
            texts,
            scalar,
            opaque,
            index,
            item_end,
            item_indent,
        )
        last = max(
            line_index
            for line_index in range(index, item_end)
            if line_index in scalar or _meaningful(lines[line_index])
        )
        items.append(StepItem(
            index=index,
            end_index=item_end,
            indent=item_indent,
            start=lines[index].start,
            end=lines[last].end,
            name=fields.get('name'),
            identity=fields.get('id'),
        ))
    return items
