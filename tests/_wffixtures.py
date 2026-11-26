"""The retry fixtures, the harness that runs audit.yml's block, and the
control that drives them, kept together: split, the control and the fixtures
it reads are free to disagree about what an outcome is.
"""
import os
import re
import subprocess
from pathlib import Path

import _util
from _repo import ROOT
from _wfgraph import _tests_yml
from _yamlread import step_scalar
from _yamlscalar import YAMLReadError

# Verbatim blocks from .github/workflows/tests.yml: the suites job's needs,
# and the changes job's outputs.
BLOCK_NEEDS = (
    '    needs:\n'
    '      - changes\n'
    '      - pycodestyle\n'
    '      - pylint\n'
    '      - pyright\n'
    '      - eslint\n'
    '      - actionlint\n'
    '      - plan\n')
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


def _audit_step(name):
    """One named step of audit.yml, decoded out of the workflow's own bytes."""
    workflow = (ROOT / '.github' / 'workflows' / 'audit.yml').read_text(
        encoding='utf-8')
    return step_scalar(workflow, 'pip-audit', name, 'run')


# One fixture per arm of audit.yml's retry block, with a value ON each
# predicate and one just PAST it, or "retries this" and "gives up on that"
# look alike. The bare-ServiceError and 401/403/404 rows split a narrow
# classifier from a broad one; the multi-line report is a real audit's shape.
# A row matching both arms pins the ORDER of the block's two tests: its retry
# predicate matches and its refusal one does not, so reading the retry test
# first runs it three times. The 429 rows pin what the 429 EXCEPTION admits.
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
    ('an unreachable OSV feed', 'ERROR:pip_audit._cli:Could not connect '
     "to OSV's vulnerability feed\n", 3, 1),
    ('a refused connection', 'NewConnectionError: Failed to establish a new '
     'connection: [Errno 111] Connection refused\n', 3, 1),
    ('a finding about refused connections', 'Found 1 known vulnerability in '
     'test/pkg\nConnection refused: Failed to establish a session\n', 1, 1),
    ('a failed name lookup', "NameResolutionError: Failed to resolve 'x.invali"
     "d' ([Errno -2] Name or service not known)\n", 3, 1),
    ('a failed pip upgrade', 'ERROR:pip_audit._cli:Failed to upgrade `pip`: '
     "['python3', '-m', 'pip', 'install', '--upgrade', 'pip']\n", 3, 1),
    ('a connect timeout', "ConnectTimeoutError: 'Connection to 10.255.255.1 "
     "timed out. (connect timeout=15)'\n", 3, 1),
    ('a finding about lookups and timeouts', 'Found 1 known vulnerability in '
     'test/pkg\nFailed to resolve test/pkg: connect timeout=0\n', 1, 1),
    ('a 404', 'HTTPError: 404 Client Error: Not Found\n', 1, 1),
    ('a 403', 'HTTPError: 403 Client Error: Forbidden\n', 1, 1),
    ('a 401', 'HTTPError: 401 Client Error: Unauthorized\n', 1, 1),
    ('a 400', 'HTTPError: 400 Client Error: Bad Request\n', 1, 1),
    # Longer than the retry arm's excerpt, which it pins: only the last lines
    # print per attempt, where an unbounded `cat` would print the whole report.
    ('a transport failure under a long report',
     'Checking 12 packages\nResolving dependencies\n'
     'Found 1 known vulnerability in test/pkg\n'
     'Upgrade test/pkg to 1.2.3 to fix it.\nVulnerability ID: PYSEC-2026-1\n'
     'Retrying the advisory feed\n'
     'HTTPError: 503 Service Unavailable\n', 3, 1),
    # Pins the ORDER: a refusal the index will not repeat beside a retry line.
    ('a 404 reported beside a transport line',
     'HTTPError: 404 Client Error: Not Found\n'
     'ServiceError: pypi.org returned 502\n', 1, 1),
    # Pins the 429 exception's SCOPE: a 429 that is no status, off a 4xx line.
    ('a 404 reported beside a 429 that is not a status',
     'HTTPError: 404 Client Error: Not Found\n'
     'Fixed-by: 6.0.429\n'
     'ConnectionError: connection reset by peer\n', 1, 1),
)


def audit_step_outcome(body, output, cwd, env, code=1):
    """Run audit.yml's own retry block over one pip-audit output.

    `pip-audit` and `sleep` become shell functions, so the block's OWN
    control flow answers: each run of the tool and its arguments
    (`runs.log`, not the block's "retrying" line), its exit, what it
    printed, and every sleep it took (`sleeps.log`), so a retry or backoff
    announced without being taken cannot pass for one.

    The block is handed to bash through `-c`, never `$( )`: macOS ships
    bash 3.2, whose here-doc handling inside a substitution differs.

    `env` is the environment the caller declares for these launches: `tmp`
    is not a tree [tool.coverage.paths] maps back, so the child must not
    inherit the collector this process is running under.
    """
    root = Path(cwd)
    (root / 'fixture.out').write_text(output, encoding='utf-8')
    (root / 'runs.log').write_text('', encoding='utf-8')
    (root / 'sleeps.log').write_text('', encoding='utf-8')
    if not (root / 'requirements-test.txt').exists():  # the step derives one
        (root / 'requirements-test.txt').write_bytes(
            (ROOT / 'requirements-test.txt').read_bytes())
    script = ('sleep() { echo "$1" >> sleeps.log; }\n'
              'pip-audit() { echo "x $*" >> runs.log; case "$*" in'
              ' *--disable-pip*) ;; *) cat fixture.out;; esac;'
              ' return "$AUDIT_CODE"; }\n') + body
    result = subprocess.run(
        [_util.workflow_bash(), '-e', '-c', script],
        capture_output=True, text=True, timeout=_AUDIT_RETRY_TIMEOUT,
        cwd=cwd,
        env=_util.child_coverage(
            'scrub', {**env, 'AUDIT_CODE': str(code)}))
    calls = (root / 'runs.log').read_text(encoding='utf-8').splitlines()
    runs = [call for call in calls if '--disable-pip' not in call]
    waits = (root / 'sleeps.log').read_text(encoding='utf-8').split()
    return {'runs': len(runs), 'args': calls, 'code': result.returncode,
            'sleeps': [int(value) for value in waits],
            'out': (result.stdout + result.stderr).strip()}


def assert_the_audit_covers_every_dependency_surface():
    """pip-audit reaches every requirements file and every extra: an
    unpinned manifest via the shared invocation, a hash-pinned one via the
    dedicated invocation (hash mode is resolution-wide and would fail every
    unpinned surface beside it), and zizmor's manifest — the documented
    exception its gate hash-verifies — via neither. Neither-both is this
    refusal."""
    workflow = (ROOT / '.github' / 'workflows' / 'audit.yml').read_text(
        encoding='utf-8')
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z', 'requirements*.txt'],
        capture_output=True, check=True)
    requirement_files = [
        os.fsdecode(path) for path in listed.stdout.split(b'\0') if path]
    assert requirement_files, 'no requirements file is tracked'
    dedicated_at = workflow.index('      - name: Audit the hash-pinned')
    shared, dedicated = workflow[:dedicated_at], workflow[dedicated_at:]
    for name in requirement_files:
        hashed = '--hash=sha256:' in (ROOT / name).read_text(
            encoding='utf-8')
        in_shared = f'--requirement {name}' in shared
        in_dedicated = f'--requirement {name}' in dedicated
        if name == 'requirements-zizmor.txt':  # the documented exception
            assert not in_shared and not in_dedicated, (name,)
        elif hashed:
            assert in_dedicated and not in_shared, (name, in_shared)
        else:
            assert in_shared and not in_dedicated, (name, in_dedicated)
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


def _audit_announcements(body):
    """Every line the block names an outcome with, each tagged by its stream.

    A refusal names itself on stderr; the retry arm names itself on stdout,
    the ONLY line telling a log reader a retry happened, so both are read.
    """
    return ([('refusal', text)
             for text in re.findall(r'^\s*echo "([^"]+)" >&2$', body,
                                    re.MULTILINE)]
            + [('retry', text)
               for text in re.findall(r'^\s*echo "([^"]+)"$', body,
                                      re.MULTILINE)])


def assert_the_audit_retry_is_narrow_and_ordered(tmp):
    """One executed fixture per arm of audit.yml's own retry block.

    The block is RUN rather than read: only a run tells a 404 the index will
    not repeat from a 502 beside it, or which of its two tests is read first.
    The runs, the exit, each attempt's announcement and its bounded excerpt
    are asserted; each outcome is named by the arm that owns it alone.
    """
    # Both blocks, the shared four-surface one and the hash-pinned one, carry
    # the same retry and run the same fixtures: unrun copies are unpinned.
    blocks = [_audit_step('Audit dependencies'),
              _audit_step('Audit the hash-pinned test toolchain')]
    for body in blocks:
        announcements = _audit_announcements(body)
        refusals = [t for kind, t in announcements if kind == 'refusal']
        announcing = [t for kind, t in announcements if kind == 'retry']
        assert len(set(refusals)) == len(refusals), (
            f'two outcomes refuse in the same words: {refusals}')
        assert len(announcing) == 1, (
            f'the block announces a retry in {len(announcing)} ways; the '
            f'retry arm must name the one line: {announcing}')
        marker = announcing[0]
        assert '$attempt' in marker, (
            f'the retry announcement names no attempt: {marker!r}; a '
            f'fixed string prints identically every attempt')
        assert marker not in refusals, (
            f'the retry announcement is also a refusal: {marker!r}')
        for label, output, runs, code in AUDIT_RETRY_FIXTURES:
            outcome = audit_step_outcome(
                body, output, cwd=tmp, env=_util.child_coverage('scrub'),
                code=code)
            assert (outcome['runs'], outcome['code']) == (runs, code), (
                f'{label}: {outcome}')
            assert output.strip() in outcome['out'], (
                f'{label}: the block ended without printing what the '
                f'tool wrote: {outcome}')
            printed = [t for t in refusals if t in outcome['out']]
            assert len(printed) == (0 if not code else 1), (
                f'{label}: printed {printed}; each outcome names itself '
                f'once')
            expected = list(range(1, runs + 1)) if runs > 1 else []
            announced = [attempt for attempt in range(1, runs + 1)
                         if marker.replace('$attempt', str(attempt))
                         in outcome['out']]
            assert announced == expected, (
                f'{label}: {runs} attempts; the retry arm announces '
                f'every one and no other arm announces anything: '
                f'{announced} of {expected}: {outcome}')
            if runs > 1:
                shown = outcome['out'].count
                tail_lines = output.strip().splitlines()
                assert all(shown(line) >= runs
                           for line in tail_lines[-5:]), (
                    f'{label}: each attempt showed the reader the tail '
                    f'of what the tool wrote: {outcome}')
                assert all(shown(line) <= 1
                           for line in tail_lines[:-5]), (
                    f'{label}: the excerpt is bounded: {outcome}')
            waits = outcome['sleeps']
            assert len(waits) >= runs - 1, (
                f'{label}: {runs} attempts, so every retry between them '
                f'has to wait: {outcome}')
            assert all(0 < first < second
                       for first, second in zip(waits, waits[1:])), (
                f'{label}: each attempt must wait longer than the one '
                f'before it: {waits}')
    # The hash-pinned run asks OSV and refuses a clean exit that skipped.
    outcome = audit_step_outcome(
        blocks[1], 'No known vulnerabilities found\nName Skip Reason\n'
        'test-pkg Dependency not found on PyPI and could not be audited: '
        'test-pkg (1.0+local)\n', cwd=tmp, env=_util.child_coverage('scrub'),
        code=0)
    assert (outcome['runs'], outcome['code']) == (1, 1), outcome
    assert '--vulnerability-service osv' in outcome['args'][0], outcome
    base = (Path(tmp) / 'pytest-base.txt').read_text(encoding='utf-8')
    assert re.fullmatch(r'pytest==[0-9.]+\n', base) and all(
        flag in outcome['args'][1] for flag in (
            'osv', '--no-deps --disable-pip', 'pytest-base.txt')), outcome
    wheel = 'pytest @ u/pytest-1+a-py3-none-any.whl\n'
    for manifest, runs in (('pytest==1\n', 0), (wheel * 2, 0), (wheel, 1)):
        (Path(tmp) / 'requirements-test.txt').write_text(manifest, 'utf-8')
        outcome = audit_step_outcome(
            blocks[1], '', cwd=tmp, env=_util.child_coverage('scrub'), code=0)
        assert (outcome['runs'], outcome['code']) == (runs, 1 - runs), outcome
