"""Structural readers for the job graph in tests.yml."""
import contextlib
import io
import json
import os
import subprocess

import _util
from _ghexpr import ExpressionError, evaluate, evaluate_if, sole_context_path
from _repo import ROOT
from _yamlread import (
    _decoded_mapping_entry, _first_child, _indent, _lines, _meaningful,
    _section, job_scalar, step_scalars,
)
from _yamlscalar import (
    _strip_inline_comment, decode_inline_scalar, flow_depth,
    split_mapping_field,
)
from _yamlsteps import complete_job_mapping


def _tests_yml():
    return (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')


def _run_script(script, needs, through_bash=False):
    """Run an extracted workflow script and capture its result."""
    env = {**os.environ, 'NEEDS_JSON': json.dumps(needs)}
    if through_bash:
        bash = _util.workflow_bash()
        return subprocess.run([bash, '-c', script], env=env,
                              capture_output=True, text=True, timeout=60)
    source = '\n'.join(script.splitlines()[1:-1])
    stdout, stderr = io.StringIO(), io.StringIO()
    code = 0
    previous = os.environ.get('NEEDS_JSON')
    os.environ['NEEDS_JSON'] = env['NEEDS_JSON']
    try:
        with contextlib.redirect_stdout(stdout):
            with contextlib.redirect_stderr(stderr):
                try:
                    exec(source, {})  # pylint: disable=exec-used
                except SystemExit as error:
                    code = error.code or 0
    finally:
        if previous is None:
            del os.environ['NEEDS_JSON']
        else:
            os.environ['NEEDS_JSON'] = previous
    return subprocess.CompletedProcess(
        [], code, stdout.getvalue(), stderr.getvalue())


def aggregate_expected(results, allowed):
    return all(
        result in allowed[name]
        for name, result in results.items())


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

    A bare scalar, a block sequence and a flow sequence all name the same
    dependencies, so all three decode to the same list; a shape that is not
    a name or a list of names is refused rather than read.
    """
    source = _field_source(workflow, job, 'needs')
    if source is None:
        return []
    decoded = complete_job_mapping(source, job)
    assert decoded is not None, job
    needs = decoded.get('needs')
    if isinstance(needs, str):
        return [needs]
    if not isinstance(needs, list) or not all(
            isinstance(name, str) for name in needs):
        raise ValueError(f'job {job!r} needs is not a list of job names')
    return needs


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


def _job_field_end(section, index, raw_value):
    """Where one job field's lines stop, past any collection it opens.

    A flow collection holds its own lines open, so a continuation sitting
    at the field's own indentation still belongs to the field.
    """
    value = _strip_inline_comment(raw_value.strip(' '))
    depth = flow_depth(value) if value[:1] in ('[', '{') else 0
    for following in range(index + 1, len(section)):
        candidate = section[following]
        if not _line_meaningful(candidate):
            continue
        if depth <= 0 and _line_indent(candidate) <= 4:
            return following
        depth += flow_depth(_strip_inline_comment(candidate.strip(' ')))
    return len(section)


def _job_field_lines(workflow, job, field):
    """Return one direct job field and its continuation lines."""
    section = _job_section(workflow, job)
    found = None
    index = 1
    while index < len(section):
        line = section[index]
        if not _line_meaningful(line) or _line_indent(line) != 4:
            index += 1
            continue
        raw_key, raw_value = split_mapping_field(
            line[4:], f'job {job!r}')
        key = decode_inline_scalar(raw_key, f'job {job!r} key')
        end = _job_field_end(section, index, raw_value)
        if key == field:
            if found is not None:
                raise ValueError(f'duplicate job field: {field}')
            found = section[index:end]
        index = end
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
    assert decoded is not None, job
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

    The reference is parsed rather than pattern-matched, so parentheses and
    spacing do not change which step an output names; anything that is not
    one complete `steps.<id>.outputs.<name>` lookup is refused.
    """
    outputs = _job_output_mapping(workflow, job)
    if outputs is None:
        return set()
    references = set()
    for name, value in outputs.items():
        if not isinstance(value, str):
            raise ValueError(
                f'job {job!r} output {name!r} is not a scalar expression')
        path = _output_reference(value)
        if path is None:
            raise ValueError(
                f'job {job!r} output {name!r} has unsupported expression: '
                f'{value!r}')
        references.add(path[1])
    return references


def _output_reference(value):
    """Return the step lookup one output expression names, or None."""
    expression = value.strip()
    if not (expression.startswith('${{') and expression.endswith('}}')):
        return None
    try:
        path = sole_context_path(expression)
    except ExpressionError:
        return None
    if (path is None or len(path) != 4 or path[0] != 'steps'
            or path[2] != 'outputs'):
        return None
    return path


def _job_if_expression(workflow, job):
    """Return the complete job-level if expression, including continuations."""
    source = _field_source(workflow, job, 'if')
    if source is None:
        return None
    return job_scalar(source, job, 'if')


def _aggregate_script(workflow):
    """The aggregate job's run block, dedented, ready for bash."""
    section = '\n'.join(_job_section(workflow, 'aggregate'))
    _, marker, after = section.partition('        run: |\n')
    assert marker, 'aggregate has no run block shaped as this test expects'
    lines = []
    for line in after.splitlines():
        if line.strip() and not line.startswith('          '):
            break
        lines.append(line[10:])
    return '\n'.join(lines)


def _run_aggregate(workflow, results, through_bash=False):
    """Run the real aggregate script against one `needs` result mapping."""
    needs = {name: {'result': result} for name, result in results.items()}
    return _run_script(_aggregate_script(workflow), needs, through_bash)


def _job_condition_runs(workflow, job, outputs=None, *, context=None):
    """Use the shared expression semantics at a real job boundary."""
    expression = _job_if_expression(workflow, job)
    assert expression is not None, job
    if context is None:
        context = {
            'github': {'event_name': 'push'},
            'status': {
                'success': True, 'failure': False, 'cancelled': False,
            },
            'needs': {'changes': {'outputs': outputs}},
        }
    return evaluate_if(expression, context)


def _actionlint_runs(workflow, event_name, workflows):
    """Evaluate actionlint's real condition for one event and output."""
    expression = _job_if_expression(workflow, 'actionlint')
    assert expression is not None
    context = {
        'github': {'event_name': event_name},
        'needs': {'changes': {'outputs': {'workflows': workflows}}},
    }
    return evaluate(expression, context)
