"""Structural readers for the job graph in tests.yml."""
import re

from _repo import ROOT
from _yamlread import (
    _decoded_mapping_entry, _first_child, _indent, _lines, _meaningful,
    _section, job_scalar, step_scalars,
)
from _yamlscalar import decode_inline_scalar, split_mapping_field
from _yamlsteps import complete_job_mapping


def _tests_yml():
    return (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')


def _job_section(workflow, job):
    """One job's source lines, from its header to the next job's."""
    lines = workflow.splitlines()
    start = None
    for index, line in enumerate(lines):
        if not _line_meaningful(line) or _line_indent(line) != 2:
            continue
        raw_key, rest = split_mapping_field(line[2:], 'jobs')
        name = decode_inline_scalar(raw_key, 'job key')
        if name == job:
            if rest.strip(' '):
                raise ValueError(f'job {job!r} is not a mapping')
            start = index
            break
    assert start is not None, f'tests.yml has no {job!r} job'
    end = next((index for index in range(start + 1, len(lines))
                if (lines[index][:2] == '  '
                    and not lines[index].lstrip().startswith('#')
                    and lines[index][2:3].strip())),
               len(lines))
    return lines[start:end]


def _job_needs(workflow, job):
    """The job names listed under `needs:` on the named job.

    Workflows in this repository use one canonical source spelling; these
    tests pin it, so a failure means the spelling changed rather than the
    guard broke.
    """
    names = []
    in_needs = False
    for line in _job_section(workflow, job)[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if in_needs:
            if stripped.startswith('- '):
                names.append(stripped[2:])
                continue
            return names
        if line.startswith('    needs:'):
            inline = stripped[len('needs:'):].strip()
            if inline:
                return [inline]
            in_needs = True
    return names


def _job_step_ids(workflow, job):
    """Return IDs carried by steps in a job."""
    return set(step_scalars(workflow, job, 'id') or ())


def _job_names(workflow):
    """Return every decoded job key in the workflow's jobs mapping."""
    lines = _lines(workflow)
    jobs = _decoded_mapping_entry(lines, 0, len(lines), -1, 'jobs')
    if jobs is None:
        return ()
    if jobs.rest.strip(' '):
        raise ValueError('jobs is not a mapping')
    start, end = _section(lines, jobs.index, jobs.indent)
    first = _first_child(lines, start, end, jobs.indent)
    if first is None:
        return ()
    job_indent = _indent(lines[first])
    names = []
    for index in range(start, end):
        line = lines[index]
        if not _meaningful(line) or _indent(line) != job_indent:
            continue
        text, _ended = line
        field = text[job_indent:]
        if field.startswith('- '):
            raise ValueError('jobs mapping contains a sequence item')
        raw_key, rest = split_mapping_field(field, 'jobs')
        name = decode_inline_scalar(raw_key, 'job key')
        if rest.strip(' '):
            raise ValueError(f'job {name!r} is not a mapping')
        if name in names:
            raise ValueError(f'duplicate job key: {name}')
        names.append(name)
    return tuple(names)


def _line_indent(line):
    """Return a source line's space indentation."""
    return len(line) - len(line.lstrip(' '))


def _line_meaningful(line):
    """Whether a raw source line carries YAML structure or data."""
    return bool(line.strip(' ')) and not line.lstrip(' ').startswith('#')


def _job_field_lines(workflow, job, field):
    """Return one direct job field and its continuation lines."""
    section = _job_section(workflow, job)
    found = None
    for index, line in enumerate(section[1:], 1):
        if not _line_meaningful(line) or _line_indent(line) != 4:
            continue
        raw_key, _raw_value = split_mapping_field(
            line[4:], f'job {job!r}')
        key = decode_inline_scalar(raw_key, f'job {job!r} key')
        if key != field:
            continue
        if found is not None:
            raise ValueError(f'duplicate job field: {field}')
        end = len(section)
        for following in range(index + 1, len(section)):
            candidate = section[following]
            if (_line_meaningful(candidate)
                    and _line_indent(candidate) <= 4):
                end = following
                break
        found = section[index:end]
    return found


def _field_source(workflow, job, field):
    """Build a minimal workflow containing one direct job field."""
    lines = _job_field_lines(workflow, job, field)
    if lines is None:
        return None
    return '\n'.join(('jobs:', f'  {job}:', *lines)) + '\n'


def _job_output_mapping(workflow, job):
    """Decode a job's outputs mapping, or return None when it is absent."""
    source = _field_source(workflow, job, 'outputs')
    if source is None:
        return None
    decoded = complete_job_mapping(source, job)
    outputs = decoded.get('outputs')
    if not isinstance(outputs, dict):
        raise ValueError(f'job {job!r} outputs is not a mapping')
    return outputs


def _job_names_with_outputs(workflow):
    """Return every job that declares an outputs mapping."""
    return tuple(job for job in _job_names(workflow)
                 if _job_output_mapping(workflow, job) is not None)


def _job_output_step_ids(workflow, job):
    """Return step IDs referenced by a job's output expressions.

    Workflows in this repository use one canonical source spelling; these
    tests pin it, so a failure means the spelling changed rather than the
    guard broke.
    """
    outputs = _job_output_mapping(workflow, job)
    if outputs is None:
        return set()
    references = set()
    pattern = re.compile(
        r'^\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.'
        r'[A-Za-z0-9_-]+\s*\}\}$')
    for name, value in outputs.items():
        if not isinstance(value, str):
            raise ValueError(
                f'job {job!r} output {name!r} is not a scalar expression')
        match = pattern.fullmatch(value)
        if match is None:
            raise ValueError(
                f'job {job!r} output {name!r} has unsupported expression: '
                f'{value!r}')
        references.add(match.group(1))
    return references


def _job_if_expression(workflow, job):
    """Return the complete job-level if expression, including continuations.

    Workflows in this repository use one canonical source spelling; these
    tests pin it, so a failure means the spelling changed rather than the
    guard broke.
    """
    source = _field_source(workflow, job, 'if')
    if source is None:
        return None
    field_lines = _job_field_lines(workflow, job, 'if')
    assert field_lines is not None
    first = field_lines[0]
    _raw_key, raw_value = split_mapping_field(first[4:], f'job {job!r}')
    if (raw_value.strip(' ')[:1] not in ('>', '|', "'", '"')
            and any(_line_meaningful(line) for line in field_lines[1:])):
        parts = [raw_value.strip()]
        parts.extend(line.strip() for line in field_lines[1:]
                     if _line_meaningful(line))
        source = '\n'.join((
            'jobs:', f'  {job}:', f'    if: {" ".join(parts)}')) + '\n'
    return job_scalar(source, job, 'if')
