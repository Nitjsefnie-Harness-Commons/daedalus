"""Fixtures, and the assertion bodies, shared by the workflow-reading suites.

The retry fixtures and the harness that runs audit.yml's own retry block
live here because the matrix is data and the harness is a fixture; the two
relocated assertion bodies are here because test_ci_workflows.py is at its
700-line ceiling and _actionlint.py is at its own.
"""
import os
import re
import subprocess
from pathlib import Path

from _repo import ROOT
from _yamlscalar import YAMLReadError
from _wfgraph import _tests_yml
from _yamlsteps import complete_job_mapping

# Verbatim blocks from .github/workflows/tests.yml: the suites job's needs,
# and the changes job's outputs.
BLOCK_NEEDS = (
    '    needs:\n'
    '      - changes\n'
    '      - pycodestyle\n'
    '      - pylint\n'
    '      - pyright\n'
    '      - eslint\n'
    '      - actionlint\n')
BLOCK_OUTPUTS = (
    '    outputs:\n'
    '      matrix: ${{ steps.classify.outputs.matrix }}\n'
    '      docs_only: ${{ steps.classify.outputs.docs_only }}\n'
    '      workflows: ${{ steps.classify.outputs.workflows }}\n')


def _real(tmp, source, name='tests.yml'):
    path = os.path.join(tmp, name)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(source)
    with open(path, encoding='utf-8', newline='') as handle:
        return handle.read()


def _replaced(old, new, name='tests.yml'):
    """Return a shipped workflow with one real block swapped for a rewrite."""
    workflow = _tests_yml() if name == 'tests.yml' else (
        ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8')
    assert old in workflow, old
    mutated = workflow.replace(old, new, 1)
    assert mutated != workflow
    return mutated


def _refuses(call, *args, contains=None):
    """Return the message from the refusal `call` must raise."""
    try:
        call(*args)
    except (AssertionError, ValueError, YAMLReadError) as error:
        message = f'{type(error).__name__}: {error}'
        if contains is not None:
            assert contains in message, message
        return message
    raise AssertionError(f'{call.__name__} accepted the planted defect')


def _value_error(call):
    """The ValueError `call` raised, or None when it raised none.

    The None is the assertion, not an accident: a caller comparing the
    return against the message it expects goes red on an acceptance,
    where `_refuses` raises on one.
    """
    try:
        call()
    except ValueError as error:
        return str(error)
    return None


def _probe_workflow(tmp, name, source):
    root = Path(tmp) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / 'probe.yml').write_text(source, encoding='utf-8')
    return root


_AUDIT_RETRY_TIMEOUT = 120

# One fixture per arm of audit.yml's retry block, with a value ON each
# predicate and one just PAST it: a single-valued fixture set makes "retries
# this" and "gives up on that" indistinguishable, so the boundary is unpinned
# while the outcomes look pinned hard. The bare-ServiceError row and the
# 401/403/404 rows are the ones a narrow classifier and a broad one disagree
# on, and the multi-line report is the shape a real audit prints.
#
# (label, what pip-audit wrote, how many times the block ran it, its exit).
AUDIT_RETRY_FIXTURES = (
    ('clean', 'No known vulnerabilities found\n', 1, 0),
    ('one real finding', 'Found 1 known vulnerability in test/pkg\n'
     'Upgrade test/pkg to 1.2.3 to fix it.\n', 1, 1),
    ('a multi-line findings report',
     'Found 2 known vulnerabilities in 1 package\n\n'
     'Name: test/pkg\n'
     'Version: 1.0.0\n'
     'Fixed-by: 1.2.3\n'
     'Vulnerability ID: PYSEC-2026-1\n\n'
     'To fix all vulnerabilities, run:\n'
     'pip install test/pkg==1.2.3\n', 1, 1),
    ('ServiceError carrying a 503',
     'pip_audit._service.interface.ServiceError: pypi.org returned 503 '
     'Server Error\n', 3, 1),
    ('a bare ServiceError', 'ServiceError:\n', 3, 1),
    ('requests 500', 'HTTPError: 500 Server Error: Internal Server Error\n',
     3, 1),
    ('requests 502', 'HTTPError: 502 Bad Gateway\n', 3, 1),
    ('requests 503', 'HTTPError: 503 Service Unavailable\n', 3, 1),
    ('requests 504', 'HTTPError: 504 Gateway Timeout\n', 3, 1),
    ('a 429 in the requests spelling',
     'HTTPError: 429 Client Error: Too Many Requests\n', 3, 1),
    ('a 429 without "Client Error"',
     'HTTPError: 429 Too Many Requests\n', 3, 1),
    ('an SSLError', 'urllib3.exceptions.SSLError: certificate verify '
     'failed\n', 3, 1),
    ('a reset connection',
     'ConnectionResetError: Connection reset by peer\n', 3, 1),
    ('an aborted connection', 'ConnectionAbortedError: Software caused '
     'connection abort\n', 3, 1),
    ('a read timeout', 'ReadTimeout: HTTPConnectionPool(host=pypi.org): '
     'Read timed out.\n', 3, 1),
    ('a DNS failure', 'Temporary failure in name resolution\n', 3, 1),
    ('an unreachable advisory feed', 'Could not connect to PyPI or the '
     'vulnerability feed\n', 3, 1),
    ('a 404', 'HTTPError: 404 Client Error: Not Found\n', 1, 1),
    ('a 403', 'HTTPError: 403 Client Error: Forbidden\n', 1, 1),
    ('a 401', 'HTTPError: 401 Client Error: Unauthorized\n', 1, 1),
    ('a 400', 'HTTPError: 400 Client Error: Bad Request\n', 1, 1),
)


def audit_step_outcome(body, output, code=1, cwd=None):
    """Run audit.yml's own retry block over one pip-audit output.

    `pip-audit` and `sleep` become shell functions, so the block's OWN
    control flow answers: how many times it ran the tool, what it exited
    with, and what it printed. `runs.log` counts invocations rather than
    the block's own "retrying" line, so a retry it announced without taking
    cannot pass for one it took.

    The block arrives as a decoded `run:` scalar on a script this builds and
    is handed to bash through `-c`, never through `$( )`: macOS ships bash
    3.2, whose here-doc handling inside a substitution differs.
    """
    root = Path(cwd)
    (root / 'fixture.out').write_text(output, encoding='utf-8')
    (root / 'runs.log').write_text('', encoding='utf-8')
    script = ('sleep() { :; }\n'
              'pip-audit() { echo x >> runs.log; cat fixture.out;'
              ' return "$AUDIT_CODE"; }\n') + body
    result = subprocess.run(
        ['bash', '-e', '-c', script], capture_output=True, text=True,
        timeout=_AUDIT_RETRY_TIMEOUT, cwd=cwd,
        env={**os.environ, 'AUDIT_CODE': str(code)})
    runs = len((root / 'runs.log').read_text(encoding='utf-8').split())
    return {'runs': runs, 'code': result.returncode,
            'out': (result.stdout + result.stderr).strip()}


def assert_the_audit_covers_every_dependency_surface():
    """pip-audit is handed every unpinned requirements file and every extra.

    The published wheel declares no dependencies, so `pip-audit .` over this
    project collects zero packages — an audit that can never fire. A surface
    added to the tree without being added to the invocation would leave the
    gate green while going unchecked, so the one class allowed to be absent
    is pinned here too: a HASH-PINNED manifest cannot share one resolution
    with the unpinned ones, and a manifest that claims to be hash-pinned
    without a hash is the opposite defect, so exactly one of the two holds.
    """
    workflow = (ROOT / '.github' / 'workflows' / 'audit.yml').read_text(
        encoding='utf-8')
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z', 'requirements*.txt'],
        capture_output=True, check=True)
    requirement_files = [
        os.fsdecode(path) for path in listed.stdout.split(b'\0') if path]
    assert requirement_files, 'no requirements file is tracked'
    for name in requirement_files:
        hashed = '--hash=sha256:' in (ROOT / name).read_text(
            encoding='utf-8')
        named = f'--requirement {name}' in workflow
        assert named != hashed, (
            f'{name}: hash-pinned={hashed}, audited={named}. A hash-pinned '
            f'manifest cannot share the audit\'s one resolution and is '
            f'excluded; every other manifest is named --requirement there.')

    # The extras are read out of pyproject.toml rather than listed, so a
    # second extra cannot escape the audit by nobody remembering it here.
    assert "['optional-dependencies'].values()" in workflow, workflow
    generated = re.search(r'> (\S+-requirements\.txt)', workflow)
    assert generated, 'the workflow generates no extras file'
    assert f'--requirement {generated.group(1)}' in workflow, (
        generated.group(1))
    # An empty generated file narrows the gate in silence: pip-audit accepts
    # it, the other surfaces still report clean, and the only third-party code
    # that runs in production goes unaudited.
    assert f'! -s {generated.group(1)}' in workflow, workflow


def assert_the_cache_release_step_is_shaped_as_declared():
    """Decoded scalars, not substrings, so a dropped env or a narrowed
    condition cannot hide behind a lookalike; the step's own comment says
    why it exists."""
    steps = complete_job_mapping(_tests_yml(), 'actionlint')['steps']
    matches = [
        (index, step) for index, step in enumerate(steps)
        if step.get('name')
        == 'Verify the actions/cache release annotations upstream']
    assert len(matches) == 1, matches
    index, step = matches[0]
    assert step.get('run') == 'python3 scripts/ci/cache_action_releases.py', (
        step)
    assert step.get('if') == '${{ !cancelled() }}', step
    assert step.get('env') == {'GH_TOKEN': '${{ github.token }}'}, step
    zizmor = [i for i, s in enumerate(steps) if s.get('name') == 'zizmor']
    assert zizmor and index > zizmor[0], (index, zizmor)
