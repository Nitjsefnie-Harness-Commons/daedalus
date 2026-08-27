"""Read complete scalar values from the small workflow YAML subset we use.

This module deliberately does not parse YAML generally.  It locates a named
job or step, decodes one plain, folded, or literal scalar, and refuses a shape
whose full value cannot be proved from the source.  A blank line inside a
block scalar is data, never an extraction boundary.
"""
from dataclasses import dataclass
import re

from _yamlscalar import (
    YAMLReadError,
    decode_inline_scalar as _decode_inline_scalar,
    split_mapping_field as _split_mapping_field,
)


@dataclass(frozen=True)
class _Entry:
    """One validated mapping or sequence entry."""

    index: int
    indent: int
    rest: str = ''


_BLOCK_HEADER = re.compile(r'^(?P<style>[>|])(?P<first>[1-9]?)'
                           r'(?P<chomp>[+-]?)(?P<second>[1-9]?)$')


def job_scalar(workflow, job, key):
    """Return a complete scalar named `key` on the named top-level job."""
    lines = _lines(workflow)
    jobs = _top_level_entry(lines, 'jobs')
    if jobs is None:
        return None
    jobs_indent, jobs_rest = _entry(lines[jobs])
    if jobs_rest.strip(' '):
        raise YAMLReadError('jobs is not a mapping')
    body = _section(lines, jobs, jobs_indent)
    _require_mapping_body(lines, *body, jobs_indent, 'jobs')
    job_entry = _mapping_entry(lines, *body, jobs_indent, job)
    if job_entry is None:
        return None
    job_index = job_entry.index
    job_indent = job_entry.indent
    job_rest = job_entry.rest
    if job_rest.strip(' '):
        raise YAMLReadError(f'job {job!r} is not a mapping')
    job_body = _section(lines, job_index, job_indent)
    _require_mapping_body(lines, *job_body, job_indent, f'job {job!r}')
    return _scalar_entry(lines, *job_body, job_indent, key)


def job_mapping(workflow, job, key):
    """Return one decoded scalar mapping on the named top-level job."""
    lines = _lines(workflow)
    jobs_entry = _decoded_mapping_entry(
        lines, 0, len(lines), -1, 'jobs')
    if jobs_entry is None:
        return None
    if jobs_entry.rest.strip(' '):
        raise YAMLReadError('jobs is not a mapping')
    jobs_body = _section(lines, jobs_entry.index, jobs_entry.indent)
    _require_mapping_body(
        lines, *jobs_body, jobs_entry.indent, 'jobs')
    job_entry = _decoded_mapping_entry(
        lines, *jobs_body, jobs_entry.indent, job)
    if job_entry is None:
        return None
    if job_entry.rest.strip(' '):
        raise YAMLReadError(f'job {job!r} is not a mapping')
    job_body = _section(lines, job_entry.index, job_entry.indent)
    _require_mapping_body(
        lines, *job_body, job_entry.indent, f'job {job!r}')
    mapping_entry = _decoded_mapping_entry(
        lines, *job_body, job_entry.indent, key)
    if mapping_entry is None:
        return None
    if mapping_entry.rest.strip(' '):
        raise YAMLReadError(f'{key} is not a mapping')
    mapping_body = _section(
        lines, mapping_entry.index, mapping_entry.indent)
    return _scalar_mapping(
        lines, *mapping_body, mapping_entry.indent, key)


def top_level_mapping(workflow, key):
    """Return one decoded scalar mapping at the workflow's top level."""
    lines = _lines(workflow)
    mapping_entry = _decoded_mapping_entry(
        lines, 0, len(lines), -1, key)
    if mapping_entry is None:
        return None
    if mapping_entry.rest.strip(' '):
        raise YAMLReadError(f'{key} is not a mapping')
    mapping_body = _section(
        lines, mapping_entry.index, mapping_entry.indent)
    return _scalar_mapping(
        lines, *mapping_body, mapping_entry.indent, key)


def step_mapping_scalar(workflow, job, step, mapping_name, key):
    """Return one scalar from a mapping on a named step."""
    lines = _lines(workflow)
    jobs = _decoded_mapping_entry(lines, 0, len(lines), -1, 'jobs')
    if jobs is None:
        return None
    if jobs.rest.strip(' '):
        raise YAMLReadError('jobs is not a mapping')
    jobs_body = _section(lines, jobs.index, jobs.indent)
    job_entry = _decoded_mapping_entry(
        lines, *jobs_body, jobs.indent, job)
    if job_entry is None:
        return None
    if job_entry.rest.strip(' '):
        raise YAMLReadError(f'job {job!r} is not a mapping')
    job_body = _section(lines, job_entry.index, job_entry.indent)
    steps = _decoded_mapping_entry(
        lines, *job_body, job_entry.indent, 'steps')
    if steps is None:
        return None
    if steps.rest.strip(' '):
        raise YAMLReadError(f'job {job!r} steps are not a sequence')
    steps_body = _section(lines, steps.index, steps.indent)
    _require_sequence_body(
        lines, *steps_body, steps.indent, f'job {job!r} steps')
    step_entry = _sequence_entry(
        lines, *steps_body, steps.indent, step)
    if step_entry is None:
        return None
    step_body = _section(lines, step_entry.index, step_entry.indent)
    _require_mapping_body(
        lines, *step_body, step_entry.indent, f'step {step!r}')
    mapping = _decoded_mapping_entry(
        lines, *step_body, step_entry.indent, mapping_name)
    if mapping is None:
        return None
    if mapping.rest.strip(' '):
        raise YAMLReadError(f'{mapping_name} is not a mapping')
    mapping_body = _section(lines, mapping.index, mapping.indent)
    _require_mapping_body(
        lines, *mapping_body, mapping.indent, mapping_name)
    value = _decoded_mapping_entry(
        lines, *mapping_body, mapping.indent, key)
    if value is None:
        return None
    value_body = _section(lines, value.index, value.indent)
    if any(_meaningful(lines[index]) for index in range(*value_body)):
        raise YAMLReadError(f'{mapping_name} value for {key!r} is nested')
    return _decode_inline_scalar(
        value.rest, f'{mapping_name} value for {key!r}')


def step_scalar(workflow, job, step, key):
    """Return a complete scalar named `key` on a named step in `job`."""
    lines = _lines(workflow)
    jobs = _top_level_entry(lines, 'jobs')
    if jobs is None:
        return None
    jobs_indent, jobs_rest = _entry(lines[jobs])
    if jobs_rest.strip(' '):
        raise YAMLReadError('jobs is not a mapping')
    jobs_body = _section(lines, jobs, jobs_indent)
    _require_mapping_body(lines, *jobs_body, jobs_indent, 'jobs')
    job_entry = _mapping_entry(lines, *jobs_body, jobs_indent, job)
    if job_entry is None:
        return None
    job_index = job_entry.index
    job_indent = job_entry.indent
    job_rest = job_entry.rest
    if job_rest.strip(' '):
        raise YAMLReadError(f'job {job!r} is not a mapping')
    job_body = _section(lines, job_index, job_indent)
    _require_mapping_body(lines, *job_body, job_indent, f'job {job!r}')
    steps_entry = _mapping_entry(lines, *job_body, job_indent, 'steps')
    if steps_entry is None:
        return None
    steps_index = steps_entry.index
    steps_indent = steps_entry.indent
    steps_rest = steps_entry.rest
    if steps_rest.strip(' '):
        raise YAMLReadError(f'job {job!r} steps are not a sequence')
    steps_body = _section(lines, steps_index, steps_indent)
    _require_sequence_body(lines, *steps_body, steps_indent,
                           f'job {job!r} steps')
    step_entry = _sequence_entry(lines, *steps_body, steps_indent, step)
    if step_entry is None:
        return None
    step_index = step_entry.index
    step_indent = step_entry.indent
    step_body = _section(lines, step_index, step_indent)
    _require_mapping_body(lines, *step_body, step_indent, f'step {step!r}')
    return _sequence_item_scalar(lines, step_entry, *step_body, key)


def step_scalars(workflow, job, key):
    """Return decoded values of `key` from every step in a named job."""
    lines = _lines(workflow)
    jobs = _decoded_mapping_entry(lines, 0, len(lines), -1, 'jobs')
    if jobs is None:
        return None
    if jobs.rest.strip(' '):
        raise YAMLReadError('jobs is not a mapping')
    jobs_body = _section(lines, jobs.index, jobs.indent)
    job_entry = _decoded_mapping_entry(
        lines, *jobs_body, jobs.indent, job)
    if job_entry is None:
        return None
    if job_entry.rest.strip(' '):
        raise YAMLReadError(f'job {job!r} is not a mapping')
    job_body = _section(lines, job_entry.index, job_entry.indent)
    steps = _decoded_mapping_entry(
        lines, *job_body, job_entry.indent, 'steps')
    if steps is None:
        return None
    if steps.rest.strip(' '):
        raise YAMLReadError(f'job {job!r} steps are not a sequence')
    steps_body = _section(lines, steps.index, steps.indent)
    _require_sequence_body(
        lines, *steps_body, steps.indent, f'job {job!r} steps')
    return _sequence_scalar_values(
        lines, *steps_body, steps.indent, key)


def _lines(workflow):
    """Split source while retaining whether each physical line ended."""
    if not isinstance(workflow, str):
        raise YAMLReadError('workflow must be a string')
    return [
        (line.rstrip('\r\n'), line.endswith(('\r', '\n')))
        for line in workflow.splitlines(keepends=True)
    ]


def _entry(line):
    """Return a mapping line's indent and text after its first colon."""
    text, _ended = line
    indent = len(text) - len(text.lstrip(' '))
    body = text[indent:]
    name, colon, rest = body.partition(':')
    if (not colon or not name or name != name.strip(' ')
            or rest and rest[0] != ' '):
        raise YAMLReadError(f'unsupported YAML mapping line: {text!r}')
    return indent, rest


def _meaningful(line):
    """Whether a line contributes YAML structure rather than whitespace."""
    text, _ended = line
    return bool(text.strip(' ')) and not text.lstrip(' ').startswith('#')


def _comment(line):
    """Whether a line is a YAML comment."""
    text, _ended = line
    return text.lstrip(' ').startswith('#')


def _indent(line):
    """Return a line's space indentation, rejecting tabs."""
    text, _ended = line
    count = len(text) - len(text.lstrip(' '))
    if '\t' in text[:count] or text[:1] == '\t':
        raise YAMLReadError('tabs in YAML indentation are unsupported')
    return count


def _top_level_entry(lines, name):
    """Find one top-level mapping entry, refusing duplicate declarations."""
    matches = []
    for index, line in enumerate(lines):
        if not _meaningful(line) or _indent(line) != 0:
            continue
        _entry(line)
        text, _ended = line
        key = text.partition(':')[0]
        if key == name:
            matches.append(index)
    if len(matches) > 1:
        raise YAMLReadError(f'duplicate top-level key: {name}')
    return matches[0] if matches else None


def _section(lines, start, parent_indent):
    """Return the half-open range for a mapping or sequence child."""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if _meaningful(line) and _indent(line) <= parent_indent:
            end = index
            break
    return start + 1, end


def _first_child(lines, start, end, parent_indent):
    """Return the first meaningful child line, if one exists."""
    for index in range(start, end):
        line = lines[index]
        if _meaningful(line) and _indent(line) > parent_indent:
            return index
    return None


def _require_mapping_body(lines, start, end, parent_indent, owner):
    """Refuse a sequence where a mapping body is required."""
    first = _first_child(lines, start, end, parent_indent)
    if first is not None:
        text, _ended = lines[first]
        if text[_indent(lines[first]):].startswith('- '):
            raise YAMLReadError(f'{owner} is not a mapping')


def _require_sequence_body(lines, start, end, parent_indent, owner):
    """Refuse a mapping or empty value where a sequence is required."""
    first = _first_child(lines, start, end, parent_indent)
    if first is None:
        raise YAMLReadError(f'{owner} is not a sequence')
    text, _ended = lines[first]
    if not text[_indent(lines[first]):].startswith('- '):
        raise YAMLReadError(f'{owner} is not a sequence')


def _mapping_entry(
        lines, start, end, parent_indent, name,
        child_indent=None) -> _Entry | None:
    """Find one child mapping entry and return its index, indent, and rest."""
    matches = []
    for index in range(start, end):
        line = lines[index]
        if not _meaningful(line):
            continue
        indent = _indent(line)
        if indent <= parent_indent:
            continue
        text, _ended = line
        field = text[indent:]
        if field.startswith('- '):
            continue
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue
        _entry(line)
        raw_key, rest = _split_mapping_field(field, 'mapping')
        key = _decode_inline_scalar(raw_key, 'mapping key')
        if key != name:
            continue
        matches.append(_Entry(index, indent, rest))
    if len(matches) > 1:
        raise YAMLReadError(f'duplicate mapping key: {name}')
    return matches[0] if matches else None


def _decoded_mapping_entry(
        lines, start, end, parent_indent, name) -> _Entry | None:
    """Find one child mapping field by its decoded scalar key."""
    child_indent = None
    matches = []
    for index in range(start, end):
        line = lines[index]
        if not _meaningful(line):
            continue
        indent = _indent(line)
        if indent <= parent_indent:
            continue
        if child_indent is None:
            child_indent = indent
        if indent != child_indent:
            continue
        text, _ended = line
        field = text[indent:]
        if field.startswith('- '):
            raise YAMLReadError('mapping body contains a sequence item')
        raw_key, rest = _split_mapping_field(field, 'mapping')
        key = _decode_inline_scalar(raw_key, 'mapping key')
        if key == name:
            matches.append(_Entry(index, indent, rest))
    if len(matches) > 1:
        raise YAMLReadError(f'duplicate mapping key: {name}')
    return matches[0] if matches else None


def _scalar_mapping(lines, start, end, parent_indent, owner):
    """Decode a nonempty mapping whose keys and values are inline scalars."""
    first = _first_child(lines, start, end, parent_indent)
    if first is None:
        raise YAMLReadError(f'{owner} is not a scalar mapping')
    child_indent = _indent(lines[first])
    values = {}
    for index in range(start, end):
        line = lines[index]
        if not _meaningful(line):
            continue
        indent = _indent(line)
        if indent <= parent_indent:
            continue
        if indent != child_indent:
            raise YAMLReadError(f'{owner} has a nested mapping value')
        text, _ended = line
        field = text[indent:]
        if field.startswith('- '):
            raise YAMLReadError(f'{owner} is not a mapping')
        raw_key, raw_value = _split_mapping_field(field, owner)
        key = _decode_inline_scalar(raw_key, f'{owner} key')
        if key in values:
            raise YAMLReadError(f'duplicate mapping key: {key}')
        values[key] = _decode_inline_scalar(
            raw_value, f'{owner} value for {key!r}')
    return values


def _sequence_scalar_values(lines, start, end, parent_indent, key):
    """Decode one scalar field wherever it occurs in a sequence mapping."""
    first = _first_child(lines, start, end, parent_indent)
    item_indent = _indent(lines[first])
    values = []
    seen = False
    for index in range(start, end):
        if not _meaningful(lines[index]):
            continue
        indent = _indent(lines[index])
        text, _ended = lines[index]
        field = text[indent:]
        if indent == item_indent and field.startswith('- '):
            seen = False
            field = field[2:]
            field_indent = item_indent + 2
        elif indent != item_indent + 2:
            continue
        else:
            field_indent = indent
        raw_key, raw_value = _split_mapping_field(field, 'step')
        field_key = _decode_inline_scalar(raw_key, 'step key')
        if field_key != key:
            if raw_value.strip(' ').startswith(("'", '"')):
                _decode_inline_scalar(raw_value, f'step value for {field_key}')
            continue
        if seen:
            raise YAMLReadError(f'duplicate mapping key: {key}')
        if raw_value.strip(' ')[:1] in ('>', '|'):
            entry = _Entry(index, field_indent, raw_value)
            _value_start, value_end = _section(lines, index, field_indent)
            values.append(_scalar_value(
                lines, entry, value_end, f'step {key}'))
        else:
            values.append(_decode_inline_scalar(raw_value, f'step {key}'))
        seen = True
    return values


def _sequence_entry(
        lines, start, end, parent_indent, name) -> _Entry | None:
    """Find one sequence item whose name mapping field matches."""
    item_indent = None
    items = []
    for index in range(start, end):
        line = lines[index]
        if not _meaningful(line):
            continue
        indent = _indent(line)
        text, _ended = line
        stripped = text[indent:]
        if indent <= parent_indent or not stripped.startswith('- '):
            continue
        if item_indent is None:
            item_indent = indent
        if indent != item_indent:
            continue
        items.append(index)

    matches = []
    for offset, index in enumerate(items):
        item_end = items[offset + 1] if offset + 1 < len(items) else end
        text, _ended = lines[index]
        fields = [text[item_indent + 2:]]
        for following in range(index + 1, item_end):
            line = lines[following]
            if not _meaningful(line):
                continue
            indent = _indent(line)
            if indent != item_indent + 2:
                continue
            text, _ended = line
            fields.append(text[indent:])

        step_name = None
        for field in fields:
            _entry((field, False))
            raw_key, rest = _split_mapping_field(field, 'step')
            key = _decode_inline_scalar(raw_key, 'step key')
            if key != 'name':
                continue
            if step_name is not None:
                raise YAMLReadError('duplicate mapping key: name')
            value = rest.strip(' ')
            if value.startswith('"') or value.startswith("'"):
                raise YAMLReadError('quoted step names are unsupported')
            if ' #' in value:
                raise YAMLReadError(
                    'step name has an unsupported inline comment')
            if value.startswith(('&', '*')):
                raise YAMLReadError(
                    'YAML anchors and aliases in step names are unsupported')
            if value.startswith('!'):
                raise YAMLReadError(
                    'YAML tags in step names are unsupported')
            step_name = value
        if step_name == name:
            matches.append(_Entry(index, item_indent))
    if len(matches) > 1:
        raise YAMLReadError(f'duplicate step name: {name}')
    return matches[0] if matches else None


def _sequence_item_scalar(lines, item, start, end, name):
    """Read a scalar from either field spelling in one sequence mapping."""
    text, _ended = lines[item.index]
    field = text[item.indent + 2:]
    raw_key, rest = _split_mapping_field(field, 'step')
    key = _decode_inline_scalar(raw_key, 'step key')
    inline = None
    if key == name:
        inline = _Entry(item.index, item.indent + 2, rest)
    nested = _mapping_entry(
        lines, start, end, item.indent, name, item.indent + 2)
    if inline is not None and nested is not None:
        raise YAMLReadError(f'duplicate mapping key: {name}')
    if inline is not None:
        _value_start, value_end = _section(
            lines, inline.index, inline.indent)
        return _scalar_value(lines, inline, value_end, name)
    return _scalar_value(lines, nested, end, name)


def _scalar_entry(lines, start, end, parent_indent, name):
    """Read one mapping scalar, returning None when its key is absent."""
    entry = _mapping_entry(lines, start, end, parent_indent, name)
    return _scalar_value(lines, entry, end, name)


def _scalar_value(lines, entry, end, name):
    """Decode the scalar at one already-located mapping entry."""
    if entry is None:
        return None
    index = entry.index
    key_indent = entry.indent
    rest = entry.rest
    value = rest.strip(' ')
    if not value:
        raise YAMLReadError(f'{name} has no scalar value')
    if value[:1] not in ('>', '|'):
        if value[:1] in ('[', '{'):
            raise YAMLReadError(f'{name} is not a scalar')
        if value[:1] in ('"', "'"):
            raise YAMLReadError(f'{name} has an unsupported quoted scalar')
        if ' #' in value:
            raise YAMLReadError(f'{name} has an unsupported inline comment')
        for following in range(index + 1, end):
            if (_meaningful(lines[following])
                    and _indent(lines[following]) > key_indent):
                raise YAMLReadError(
                    f'{name} has an unsupported multiline scalar')
        return value
    style, chomp, explicit = _parse_header(value, name)
    block_start = index + 1
    block_end = end
    for following in range(block_start, end):
        line = lines[following]
        if _meaningful(line) and _indent(line) <= key_indent:
            block_end = following
            break
        if _comment(line) and _indent(line) <= key_indent:
            block_end = following
            break
    return _decode_block(lines[block_start:block_end], key_indent,
                         style, chomp, explicit, name)


def _parse_header(value, name):
    """Parse a block scalar header and reject an incomplete shape."""
    header = value
    if ' #' in header:
        header = header.split(' #', 1)[0].rstrip(' ')
    match = _BLOCK_HEADER.fullmatch(header)
    if not match:
        raise YAMLReadError(f'{name} has an unsupported block scalar header')
    first = match.group('first')
    second = match.group('second')
    if first and second:
        raise YAMLReadError(f'{name} has two indentation indicators')
    explicit = int(first or second or 0)
    return match.group('style'), match.group('chomp') or '', explicit


def _decode_block(lines, parent_indent, style, chomp, explicit, name):
    """Decode a complete folded or literal block and apply its chomping."""
    if not lines:
        return ''
    if explicit:
        content_indent = parent_indent + explicit
    else:
        content_indent = parent_indent + 1
        for line in lines:
            text, _ended = line
            if text.strip(' '):
                content_indent = max(content_indent, _indent(line))
                break
            content_indent = max(content_indent, len(text))
    parts = []
    more_indented = []
    for line in lines:
        text, _ended = line
        if not text.strip(' ') and len(text) <= content_indent:
            parts.append('')
            more_indented.append(False)
            continue
        indent = _indent(line)
        if indent < content_indent:
            raise YAMLReadError(f'{name} block indentation is incomplete')
        parts.append(text[content_indent:])
        more_indented.append(indent > content_indent)

    if style == '|':
        value = '\n'.join(parts)
    else:
        value = parts[0]
        seen_content = bool(parts[0])
        last_content_more = more_indented[0] if parts[0] else False
        for index in range(1, len(parts)):
            previous = parts[index - 1]
            current = parts[index]
            if not previous:
                separator = '\n' if (
                    not current or not seen_content
                    or last_content_more or more_indented[index]) else ''
            elif (not current or more_indented[index - 1]
                  or more_indented[index]):
                separator = '\n'
            else:
                separator = ' '
            value += separator + current
            if current:
                seen_content = True
                last_content_more = more_indented[index]
    if lines[-1][1]:
        value += '\n'
    if chomp == '-':
        return value.rstrip('\n')
    if chomp == '+':
        return value
    return value.rstrip('\n') + (
        '\n' if value.endswith('\n') and any(parts) else '')
