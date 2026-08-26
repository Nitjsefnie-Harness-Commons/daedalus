"""Decode complete step mappings from the bounded workflow YAML subset."""
# pylint: disable=protected-access
import re

import _yamlread as _reader
from _yamlscalar import (
    YAMLReadError,
    decode_inline_scalar,
    split_mapping_field,
)


_EXPRESSION = re.compile(r'^\$\{\{ [A-Za-z0-9_.*()[\]]+ \}\}$')
_PLAIN_WITH_SPACES = re.compile(
    r'^[A-Za-z0-9_.][-A-Za-z0-9_.@/+$() ]*$')


def step_mappings(workflow, job):
    """Return every complete mapping in the named job's step sequence."""
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
    steps = _reader._decoded_mapping_entry(
        lines, *job_body, job_entry.indent, 'steps')
    if steps is None:
        return None
    if steps.rest.strip(' '):
        raise YAMLReadError(f'job {job!r} steps are not a sequence')
    steps_body = _reader._section(
        lines, steps.index, steps.indent)
    _reader._require_sequence_body(
        lines, *steps_body, steps.indent, f'job {job!r} steps')
    return _decode_step_sequence(lines, *steps_body, steps.indent)


def _decode_step_sequence(lines, start, end, parent_indent):
    """Decode each mapping item in one validated step sequence."""
    first = _reader._first_child(
        lines, start, end, parent_indent)
    item_indent = _reader._indent(lines[first])
    items = []
    for index in range(start, end):
        if not _reader._meaningful(
                lines[index]):
            continue
        indent = _reader._indent(lines[index])
        text, _ended = lines[index]
        field = text[indent:]
        if indent == item_indent:
            if not field.startswith('- '):
                raise YAMLReadError('step sequence contains a non-item')
            items.append(index)
        elif indent < item_indent:
            raise YAMLReadError('step sequence has inconsistent indentation')
    decoded = []
    for offset, index in enumerate(items):
        item_end = items[offset + 1] if offset + 1 < len(items) else end
        decoded.append(_decode_step(lines, index, item_end, item_indent))
    return decoded


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
