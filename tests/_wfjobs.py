"""The jobs decode and file discovery both workflow-structure gates share.

Both gates read a workflow's jobs and must fail closed over what they
cannot classify, so one reader serves them: every job is decoded by the
complete mapping decoder, so a construct it refuses never reaches a gate
as an empty jobs set, and each job's header line travels with its decoded
fields so a refusal or a violation names file, line and literal text.
"""
from dataclasses import dataclass
from pathlib import Path

from _repo import ROOT
from _yamlread import _decoded_mapping_entry, _lines, _section
from _yamlscalar import YAMLReadError
from _yamlsteps import _decode_complete_value

WORKFLOW_EXTENSIONS = ('.yml', '.yaml')


@dataclass(frozen=True)
class Workflow:
    """One workflow file's decoded jobs and where each job begins."""

    path: Path
    jobs: dict
    starts: dict


def workflow_files(directory=None):
    """Every workflow file of both extensions, sorted, never an empty set."""
    root = ROOT / '.github' / 'workflows'
    if directory is not None:
        root = Path(directory)
    paths = sorted(
        path for path in root.iterdir()
        if path.is_file() and path.suffix in WORKFLOW_EXTENSIONS)
    if not paths:
        raise YAMLReadError(f'no workflow files under {root}')
    return paths


def jobs_mapping(workflow):
    """Return every decoded job mapping, keyed by its decoded job key."""
    lines = _lines(workflow)
    jobs = _decoded_mapping_entry(lines, 0, len(lines), -1, 'jobs')
    if jobs is None:
        return None
    return _jobs_value(lines, jobs)


def load(path):
    """Decode one workflow file's jobs, and locate each job's header."""
    source = path.read_text(encoding='utf-8')
    lines = _lines(source)
    jobs = _decoded_mapping_entry(lines, 0, len(lines), -1, 'jobs')
    if jobs is None:
        raise YAMLReadError(f'{path.name} declares no jobs mapping')
    text = lines[jobs.index][0].strip()
    body = _section(lines, jobs.index, jobs.indent)
    try:
        value = _jobs_value(lines, jobs)
    except YAMLReadError as error:
        raise YAMLReadError(
            f'{path.name}:{jobs.index + 1}: {text!r}: {error}') from error
    starts = {name: _header(lines, body, jobs.indent, name,
                            (jobs.index + 1, text))
              for name in value}
    return Workflow(path, value, starts)


def _jobs_value(lines, jobs):
    """Decode the jobs value, and refuse one that classifies no job."""
    body = _section(lines, jobs.index, jobs.indent)
    value = _decode_complete_value(
        lines, jobs.index, body[1], jobs.indent, 'jobs', jobs.rest,
        'workflow')
    if not isinstance(value, dict):
        raise YAMLReadError('jobs is not a mapping')
    for name, job in value.items():
        if not isinstance(job, dict):
            raise YAMLReadError(f'job {name!r} is not a mapping')
    if not value:
        raise YAMLReadError('the jobs mapping declares no job')
    return value


def _header(lines, body, jobs_indent, name, fallback):
    """Return one job's header line and text, or the jobs line's."""
    entry = _decoded_mapping_entry(lines, *body, jobs_indent, name)
    if entry is None:
        return fallback
    return entry.index + 1, lines[entry.index][0].strip()
