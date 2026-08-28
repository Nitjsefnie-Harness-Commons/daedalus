"""Structural readers for the job graph in tests.yml."""
import re

from _repo import ROOT
from _yamlread import step_scalars


def _tests_yml():
    return (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')


def _job_section(workflow, job):
    """One job's source lines, from its header to the next job's."""
    lines = workflow.splitlines()
    start = next((index for index, line in enumerate(lines)
                  if line == f'  {job}:'), None)
    assert start is not None, f'tests.yml has no {job!r} job'
    end = next((index for index in range(start + 1, len(lines))
                if lines[index][:2] == '  ' and lines[index][2:3].strip()),
               len(lines))
    return lines[start:end]


def _job_needs(workflow, job):
    """The job names listed under `needs:` on the named job."""
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


def _job_output_step_ids(workflow, job):
    """Return step IDs referenced by a job's output expressions."""
    section = _job_section(workflow, job)
    output_lines = []
    in_outputs = False
    for line in section:
        if line.startswith('    outputs:'):
            in_outputs = True
            continue
        if in_outputs and line.strip() and not line.startswith('      '):
            break
        if in_outputs:
            output_lines.append(line)
    return set(re.findall(
        r'steps\.([A-Za-z0-9_-]+)\.outputs\.[A-Za-z0-9_-]+',
        '\n'.join(output_lines)))


def _job_if_expression(workflow, job):
    """Return the complete job-level if expression, including continuations."""
    section = _job_section(workflow, job)
    for index, line in enumerate(section):
        if not line.startswith('    if:'):
            continue
        parts = [line[len('    if:'):].strip()]
        for continuation in section[index + 1:]:
            if continuation.strip() and not continuation.startswith('     '):
                break
            if continuation.strip():
                parts.append(continuation.strip())
            if '}}' in continuation:
                break
        return ' '.join(parts)
    return None
