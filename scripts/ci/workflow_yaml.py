"""Decode scalars and locate actual step mappings in workflow YAML."""
from dataclasses import dataclass
import re

if __package__:
    # pylint: disable-next=relative-beyond-top-level
    from .yamlscalar import (
        YAMLReadError,
        _strip_inline_comment,
        decode_inline_scalar,
        split_mapping_field,
    )
else:
    from yamlscalar import (
        YAMLReadError,
        _strip_inline_comment,
        decode_inline_scalar,
        split_mapping_field,
    )


_BLOCK_HEADER = re.compile(
    r'^(?P<style>[>|])(?P<first>[1-9]?)(?P<chomp>[+-]?)'
    r'(?P<second>[1-9]?)$')


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
    count = len(line.text) - len(line.text.lstrip(' '))
    if line.text[:1] == '\t':
        raise YAMLReadError('tabs in YAML indentation are unsupported')
    return count


def _meaningful(line):
    return (bool(line.text.strip(' '))
            and not line.text.lstrip(' ').startswith('#'))


def _split_field(text, owner):
    return split_mapping_field(text, owner, allow_tabs=True)


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


def _block_end(lines, start, end, header_indent, match):
    explicit = int(match.group('first') or match.group('second') or 0)
    if match.group('first') and match.group('second'):
        raise YAMLReadError('workflow block has two indentation indicators')
    content_indent = header_indent + explicit
    if not explicit:
        for index in range(start, end):
            if not lines[index].text.strip(' '):
                continue
            content_indent = _indent(lines[index])
            if content_indent <= header_indent:
                return start
            break
        else:
            return end
    for index in range(start, end):
        if lines[index].text.strip(' ') \
                and _indent(lines[index]) < content_indent:
            return index
    return end


def _scalar_lines(lines):
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
        value = _strip_inline_comment(raw_value.strip(' \t'))
        match = _BLOCK_HEADER.fullmatch(value)
        if match is not None:
            stop = _block_end(
                lines, index + 1, len(lines), _indent(lines[index]), match)
            scalar.update(range(index + 1, stop))
            index = stop
            continue
        quote = _quote_is_open(raw_value)
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
        if index in scalar:
            continue
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


def _step_value(lines, start, end, indent, raw_value, owner):
    value = _strip_inline_comment(raw_value.strip(' \t'))
    if not value:
        raise YAMLReadError(f'{owner} has no scalar value')
    match = _BLOCK_HEADER.fullmatch(value)
    if match is not None:
        stop = _block_end(lines, start + 1, end, indent, match)
        body = [
            (line.text, line.end > line.start + len(line.text))
            for line in lines[start + 1:stop]
        ]
        style, chomp, explicit = _parse_header(value, owner)
        return _decode_block(
            body, indent, style, chomp, explicit, owner)
    if value.startswith(("'", '"')):
        return decode_inline_scalar(value, owner)
    if value.startswith(('&', '*', '!', '[', '{', '@', '`')):
        raise YAMLReadError(f'{owner} has an unsupported scalar')
    if '\t' in value or any(
            ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff
            for char in value):
        raise YAMLReadError(f'{owner} has an unsupported scalar')
    return value


def _parse_header(value, owner):
    match = _BLOCK_HEADER.fullmatch(value)
    if match is None:
        raise YAMLReadError(f'{owner} has an unsupported block header')
    first = match.group('first')
    second = match.group('second')
    if first and second:
        raise YAMLReadError(f'{owner} has two indentation indicators')
    explicit = int(first or second or 0)
    return match.group('style'), match.group('chomp') or '', explicit


def _decode_block(lines, parent_indent, style, chomp, explicit, owner):
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


def _step_fields(lines, scalar, index, end, item_indent):
    body = lines[index].text[item_indent:]
    if not body.startswith('-') or body[1:2] not in (' ', '\t'):
        raise YAMLReadError('step sequence contains a non-item')
    after_dash = body[1:]
    separation = len(after_dash) - len(after_dash.lstrip(' \t'))
    field_indent = item_indent + 1 + separation
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
                lines, field_index, field_end, field_indent,
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
