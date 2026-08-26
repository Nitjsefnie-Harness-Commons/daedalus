"""Execute decoded workflow shell steps in contract tests."""
import shlex
import shutil
import subprocess
from pathlib import Path


def run_step(workdir, step, env, *, workflow=None, job=None):
    """Run one decoded workflow step through its declared shell template."""
    workflow = {} if workflow is None else workflow
    job = {} if job is None else job
    script = step.get('run')
    assert isinstance(script, str), f'step has no run script: {step!r}'
    script_path = Path(workdir) / 'workflow-step.sh'
    script_path.write_text(script, encoding='utf-8')
    template = _effective_shell(workflow, job, step)
    assert isinstance(template, str), f'invalid shell template: {template!r}'
    assert template.count('{0}') == 1, template
    command = [
        part.replace('{0}', str(script_path))
        for part in shlex.split(template)
    ]
    program = command[0]
    executable = shutil.which(program)
    if executable is None:
        raise FileNotFoundError(
            f'workflow shell executable not found on PATH: {program}')
    command[0] = executable
    child_env = {
        name: value for name, value in _effective_environment(
            workdir, workflow, job, step, env).items()
        if not name.startswith('COVERAGE_')
    }
    return subprocess.run(
        command, cwd=workdir, env=child_env,
        capture_output=True, text=True, timeout=60)


def _effective_shell(workflow, job, step):
    """Resolve run shell precedence across workflow, job, then step."""
    template = 'bash -e {0}'
    for container in (workflow, job):
        defaults = container.get('defaults', {})
        assert isinstance(defaults, dict), defaults
        run_defaults = defaults.get('run', {})
        assert isinstance(run_defaults, dict), run_defaults
        template = run_defaults.get('shell', template)
    return step.get('shell', template)


def _effective_environment(workdir, workflow, job, step, env):
    """Resolve workflow and job env beneath the caller's step env."""
    child_env = dict(env)
    for container in (workflow, job):
        container_env = container.get('env', {})
        assert isinstance(container_env, dict), container_env
        for name, value in container_env.items():
            assert isinstance(name, str) and isinstance(value, str), (
                name, value)
            child_env[name] = _resolve_environment_value(workdir, value)
    step_env = step.get('env', {})
    assert isinstance(step_env, dict), step_env
    for name, value in step_env.items():
        assert isinstance(name, str) and isinstance(value, str), (
            name, value)
        child_env[name] = env.get(
            name, _resolve_environment_value(workdir, value))
    return child_env


def _resolve_environment_value(workdir, value):
    """Resolve the workspace expression used by inherited test env."""
    return value.replace('${{ github.workspace }}', str(Path(workdir)))


def recorded_writes(calls):
    """Return recorded POST and PATCH calls from a GitHub stub log."""
    return [
        line for line in calls.read_text(encoding='utf-8').splitlines()
        if '"-X"' in line and ('"POST"' in line or '"PATCH"' in line)
    ]
