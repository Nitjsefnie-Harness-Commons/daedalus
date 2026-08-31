"""Decode complete step mappings from the bounded workflow YAML subset."""
# pylint: disable=protected-access
import re

import _yamlread as _reader
from _yamlscalar import (
    YAMLReadError,
    _strip_inline_comment,
    decode_inline_scalar,
    split_flow_collection,
    split_flow_items,
    split_mapping_field,
)
from workflow_yaml import workflow_step_items


_EXPRESSION = re.compile(r'^\$\{\{ [A-Za-z0-9_.*()[\]]+ \}\}$')
_PLAIN_WITH_SPACES = re.compile(
    r'^[A-Za-z0-9_.][-A-Za-z0-9_.@/+$() ]*$')


def step_mappings(workflow, job):
    """Return every complete mapping in the named job's step sequence."""
    lines = _reader._lines(workflow)
    items = workflow_step_items(workflow, job)
    if items is None:
        return None
    return [
        _decode_step(lines, item.index, item.end_index, item.indent)
        for item in items
    ]


def workflow_mapping(workflow):
    """Return the complete recursively decoded workflow mapping."""
    lines = _reader._lines(workflow)
    return _decode_complete_mapping(
        lines, 0, len(lines), -1, 'workflow')


def complete_job_mapping(workflow, job):
    """Return every recursively decoded field in one named job."""
    lines = _reader._lines(workflow)
    jobs = _reader._decoded_mapping_entry(
        lines, 0, len(lines), -1, 'jobs')
    if jobs is None:
        return None
    if jobs.rest.strip(' '):
        raise YAMLReadError('jobs is not a mapping')
    jobs_body = _reader._section(
        lines, jobs.index, jobs.indent)
    _reader._require_mapping_body(
        lines, *jobs_body, jobs.indent, 'jobs')
    job_entry = _reader._decoded_mapping_entry(
        lines, *jobs_body, jobs.indent, job)
    if job_entry is None:
        return None
    if job_entry.rest.strip(' '):
        raise YAMLReadError(f'job {job!r} is not a mapping')
    job_body = _reader._section(
        lines, job_entry.index, job_entry.indent)
    _reader._require_mapping_body(
        lines, *job_body, job_entry.indent, f'job {job!r}')
    return _decode_complete_mapping(
        lines, *job_body, job_entry.indent, f'job {job!r}')


def _decode_step(lines, index, end, item_indent):
    """Decode every field in one sequence-item mapping."""
    text, _ended = lines[index]
    field_indent = item_indent + 2
    fields = [(index, text[field_indent:])]
    for following in range(index + 1, end):
        if not _reader._meaningful(
                lines[following]):
            continue
        indent = _reader._indent(
            lines[following])
        if indent < field_indent:
            raise YAMLReadError('step mapping has inconsistent indentation')
        if indent == field_indent:
            text, _ended = lines[following]
            fields.append((following, text[indent:]))
    values = {}
    for offset, (field_index, field) in enumerate(fields):
        field_end = fields[offset + 1][0] if offset + 1 < len(fields) else end
        raw_key, raw_value = split_mapping_field(field, 'step')
        key = decode_inline_scalar(raw_key, 'step key')
        if key in values:
            raise YAMLReadError(f'duplicate mapping key: {key}')
        values[key] = _decode_value(
            lines, field_index, field_end, field_indent, key, raw_value)
    return values


def _decode_value(lines, index, end, indent, key, raw_value):
    """Decode one step scalar or nested scalar mapping."""
    value = raw_value.strip(' ')
    if not value:
        return _decode_scalar_mapping(
            lines, index + 1, end, indent, f'step {key}')
    if value[:1] in ('>', '|'):
        entry = _reader._Entry(
            index, indent, raw_value)
        return _reader._scalar_value(
            lines, entry, end, f'step {key}')
    child = _reader._first_child(
        lines, index + 1, end, indent)
    if child is not None:
        raise YAMLReadError(f'step {key} has nested scalar content')
    return _decode_inline_value(raw_value, f'step value for {key!r}')


def _decode_scalar_mapping(lines, start, end, parent_indent, owner):
    """Decode one nonempty nested mapping of inline scalar values."""
    first = _reader._first_child(
        lines, start, end, parent_indent)
    if first is None:
        raise YAMLReadError(f'{owner} is not a scalar mapping')
    child_indent = _reader._indent(
        lines[first])
    fields = []
    for index in range(start, end):
        if not _reader._meaningful(
                lines[index]):
            continue
        indent = _reader._indent(lines[index])
        if indent <= parent_indent:
            continue
        if indent < child_indent:
            raise YAMLReadError(f'{owner} has inconsistent indentation')
        if indent != child_indent:
            continue
        text, _ended = lines[index]
        field = text[indent:]
        if field.startswith('- '):
            raise YAMLReadError(f'{owner} is not a mapping')
        fields.append((index, field))
    values = {}
    for offset, (index, field) in enumerate(fields):
        field_end = fields[offset + 1][0] if offset + 1 < len(fields) else end
        raw_key, raw_value = split_mapping_field(field, owner)
        key = decode_inline_scalar(raw_key, f'{owner} key')
        if key in values:
            raise YAMLReadError(f'duplicate mapping key: {key}')
        value = raw_value.strip(' ')
        if value[:1] in ('>', '|'):
            entry = _reader._Entry(
                index, child_indent, raw_value)
            values[key] = _reader._scalar_value(
                lines, entry, field_end, f'{owner} value for {key!r}')
            continue
        child = _reader._first_child(
            lines, index + 1, field_end, child_indent)
        if child is not None:
            raise YAMLReadError(f'{owner} has a nested mapping value')
        values[key] = _decode_inline_value(
            raw_value, f'{owner} value for {key!r}')
    return values


def _decode_inline_value(raw_value, owner):
    """Decode the inline subset plus one complete Actions expression."""
    value = raw_value.strip(' ')
    if (_EXPRESSION.fullmatch(value)
            or _PLAIN_WITH_SPACES.fullmatch(value)):
        return value
    return decode_inline_scalar(raw_value, owner)


def _decode_complete_mapping(lines, start, end, parent_indent, owner):
    """Decode every direct field and recursively decode its value."""
    first = _reader._first_child(lines, start, end, parent_indent)
    if first is None:
        raise YAMLReadError(f'{owner} is not a mapping')
    field_indent = _reader._indent(lines[first])
    fields = []
    for index in range(start, end):
        if not _reader._meaningful(lines[index]):
            continue
        indent = _reader._indent(lines[index])
        if indent < field_indent:
            raise YAMLReadError(f'{owner} has inconsistent indentation')
        if indent != field_indent:
            continue
        text, _ended = lines[index]
        field = text[indent:]
        if field.startswith('- '):
            raise YAMLReadError(f'{owner} is not a mapping')
        fields.append((index, field))
    values = {}
    for offset, (index, field) in enumerate(fields):
        field_end = fields[offset + 1][0] if offset + 1 < len(
            fields) else end
        raw_key, raw_value = split_mapping_field(field, owner)
        key = decode_inline_scalar(raw_key, f'{owner} key')
        if key in values:
            raise YAMLReadError(f'duplicate mapping key: {key}')
        values[key] = _decode_complete_value(
            lines, index, field_end, field_indent, key, raw_value,
            owner)
    return values


def _decode_complete_value(
        lines, index, end, indent, key, raw_value, owner):
    """Decode one scalar, mapping, or sequence value recursively."""
    value = _strip_inline_comment(raw_value.strip(' '))
    if value[:1] in ('>', '|'):
        entry = _reader._Entry(index, indent, raw_value)
        return _reader._scalar_value(
            lines, entry, end, f'{owner} {key}')
    child = _reader._first_child(lines, index + 1, end, indent)
    if value:
        if child is not None:
            raise YAMLReadError(
                f'{owner} {key} has nested scalar content')
        return _decode_complete_inline(
            raw_value, f'{owner} value for {key!r}')
    if child is None:
        raise YAMLReadError(f'{owner} {key} has no value')
    child_indent = _reader._indent(lines[child])
    text, _ended = lines[child]
    if text[child_indent:].startswith('- '):
        return _decode_complete_sequence(
            lines, index + 1, end, indent, f'{owner} {key}')
    return _decode_complete_mapping(
        lines, index + 1, end, indent, f'{owner} {key}')


def _decode_complete_sequence(lines, start, end, parent_indent, owner):
    """Decode every item in one block sequence."""
    first = _reader._first_child(lines, start, end, parent_indent)
    if first is None:
        raise YAMLReadError(f'{owner} is not a sequence')
    item_indent = _reader._indent(lines[first])
    items = []
    for index in range(start, end):
        if not _reader._meaningful(lines[index]):
            continue
        indent = _reader._indent(lines[index])
        if indent < item_indent:
            raise YAMLReadError(f'{owner} has inconsistent indentation')
        if indent != item_indent:
            continue
        text, _ended = lines[index]
        field = text[indent:]
        if not field.startswith('- '):
            raise YAMLReadError(f'{owner} contains a non-item')
        items.append(index)
    decoded = []
    for offset, index in enumerate(items):
        item_end = items[offset + 1] if offset + 1 < len(
            items) else end
        text, _ended = lines[index]
        raw_value = text[item_indent + 2:]
        try:
            split_mapping_field(raw_value, owner)
        except YAMLReadError:
            mapping_item = False
        else:
            mapping_item = True
        if not mapping_item:
            child = _reader._first_child(
                lines, index + 1, item_end, item_indent)
            if child is not None:
                raise YAMLReadError(
                    f'{owner} scalar item has nested content')
            decoded.append(_decode_complete_inline(
                raw_value, f'{owner} item'))
            continue
        decoded.append(_decode_complete_sequence_mapping(
            lines, index, item_end, item_indent, owner))
    return decoded


def _decode_complete_sequence_mapping(
        lines, index, end, item_indent, owner):
    """Decode one mapping carried by a block-sequence item."""
    text, _ended = lines[index]
    field_indent = item_indent + 2
    fields = [(index, text[field_indent:])]
    for following in range(index + 1, end):
        if not _reader._meaningful(lines[following]):
            continue
        indent = _reader._indent(lines[following])
        if indent < field_indent:
            raise YAMLReadError(
                f'{owner} mapping has inconsistent indentation')
        if indent == field_indent:
            text, _ended = lines[following]
            fields.append((following, text[indent:]))
    values = {}
    for offset, (field_index, field) in enumerate(fields):
        field_end = fields[offset + 1][0] if offset + 1 < len(
            fields) else end
        raw_key, raw_value = split_mapping_field(field, owner)
        key = decode_inline_scalar(raw_key, f'{owner} key')
        if key in values:
            raise YAMLReadError(f'duplicate mapping key: {key}')
        values[key] = _decode_complete_value(
            lines, field_index, field_end, field_indent, key,
            raw_value, owner)
    return values


def _decode_flow_collection(opening, body, owner):
    """Decode one flow sequence or mapping into the same value as a block."""
    if not body.strip(' '):
        return [] if opening == '[' else {}
    items = split_flow_items(body, owner)
    if opening == '[':
        return [_decode_complete_inline(item, f'{owner} item')
                for item in items]
    values = {}
    for item in items:
        raw_key, raw_value = split_mapping_field(item.strip(' '), owner)
        key = decode_inline_scalar(raw_key, f'{owner} key')
        if key in values:
            raise YAMLReadError(f'duplicate mapping key: {key}')
        values[key] = _decode_complete_inline(
            raw_value, f'{owner} value for {key!r}')
    return values


def _decode_complete_inline(raw_value, owner):
    """Decode one bounded inline value used by complete mappings."""
    value = _strip_inline_comment(raw_value.strip(' '))
    flow = split_flow_collection(value, owner)
    if flow is not None:
        return _decode_flow_collection(*flow, owner)
    if value.startswith(("'", '"')):
        return decode_inline_scalar(value, owner)
    if not value or value.startswith(('&', '*', '!', '@', '`')):
        raise YAMLReadError(f'{owner} has an unsupported plain scalar')
    if ': ' in value or '\t' in value or any(
            ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff
            for char in value):
        raise YAMLReadError(f'{owner} has an unsupported plain scalar')
    return value
