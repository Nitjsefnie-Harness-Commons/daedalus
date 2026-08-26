"""Execute decoded workflow shell steps in contract tests."""
import shlex
import subprocess
from pathlib import Path


def run_step(workdir, step, env):
    """Run one decoded workflow step through its declared shell template."""
    script = step.get('run')
    assert isinstance(script, str), f'step has no run script: {step!r}'
    script_path = Path(workdir) / 'workflow-step.sh'
    script_path.write_text(script, encoding='utf-8')
    template = step.get('shell', 'bash -e {0}')
    assert isinstance(template, str), f'invalid shell template: {template!r}'
    assert template.count('{0}') == 1, template
    command = [
        part.replace('{0}', str(script_path))
        for part in shlex.split(template)
    ]
    child_env = {
        name: value for name, value in env.items()
        if not name.startswith('COVERAGE_')
    }
    return subprocess.run(
        command, cwd=workdir, env=child_env,
        capture_output=True, text=True, timeout=60)


def recorded_writes(calls):
    """Return recorded POST and PATCH calls from a GitHub stub log."""
    return [
        line for line in calls.read_text(encoding='utf-8').splitlines()
        if '"-X"' in line and ('"POST"' in line or '"PATCH"' in line)
    ]
