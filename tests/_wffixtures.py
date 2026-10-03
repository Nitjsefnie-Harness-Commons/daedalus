"""The retry fixtures, the harness that runs audit.yml's block, and the
control that drives them.

The matrix is data and the harness is a fixture, and the control is here
because it is the one assertion in the repository that exists to drive that
matrix: it has no fixture of its own to place, and splitting it from the
fixtures it reads leaves the two halves free to disagree about what an
outcome is.
"""
import os
import re
import subprocess
from pathlib import Path

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


def _audit_step(name):
    """One named step of audit.yml, decoded out of the workflow's own bytes."""
    workflow = (ROOT / '.github' / 'workflows' / 'audit.yml').read_text(
        encoding='utf-8')
    return step_scalar(workflow, 'pip-audit', name, 'run')


# One fixture per arm of audit.yml's retry block, with a value ON each
# predicate and one just PAST it: a single-valued fixture set makes "retries
# this" and "gives up on that" indistinguishable, so the boundary is unpinned
# while the outcomes look pinned hard. The bare-ServiceError row and the
# 401/403/404 rows are the ones a narrow classifier and a broad one disagree
# on, and the multi-line report is the shape a real audit prints.
#
# The ORDER of the block's two tests is the narrowness it claims, and a row
# that matches both arms is the only thing that pins it: one whose retry
# predicate also matches while its refusal predicate does not, so a block
# reading the retry test first runs it three times and a block reading the
# refusal test first reports at once. The 429 rows pin the 429 EXCEPTION,
# which is a different property — they discriminate what the exception
# admits, not which test is read first.
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
    # The row longer than the retry arm's excerpt window, so that window is
    # pinned too: on a report this size the excerpt's lines are printed once
    # per attempt and everything above them only in the report the last one
    # ends with. An unbounded `cat` there prints the whole report per
    # attempt, and the shorter rows cannot tell the two apart.
    ('a transport failure under a long report',
     'Checking 12 packages\n'
     'Resolving dependencies\n'
     'Found 1 known vulnerability in test/pkg\n'
     'Upgrade test/pkg to 1.2.3 to fix it.\n'
     'Vulnerability ID: PYSEC-2026-1\n'
     'Retrying the advisory feed\n'
     'HTTPError: 503 Service Unavailable\n', 3, 1),
    # The row that pins the ORDER: a refusal the index will not repeat, on
    # one line, beside a transport failure the block must retry.
    ('a 404 reported beside a transport line',
     'HTTPError: 404 Client Error: Not Found\n'
     'ServiceError: pypi.org returned 502\n', 1, 1),
    # The row that pins the 429 exception's SCOPE: the substring is there
    # and it is not a status, on a line the 4xx test never matched.
    ('a 404 reported beside a 429 that is not a status',
     'HTTPError: 404 Client Error: Not Found\n'
     'Fixed-by: 6.0.429\n'
     'ConnectionError: connection reset by peer\n', 1, 1),
)


def audit_step_outcome(body, output, cwd, code=1):
    """Run audit.yml's own retry block over one pip-audit output.

    `pip-audit` and `sleep` become shell functions, so the block's OWN
    control flow answers: how many times it ran the tool, what it exited
    with, what it printed, and how long it waited between attempts.
    `runs.log` counts invocations rather than the block's own "retrying"
    line, so a retry it announced without taking cannot pass for one it
    took; `sleeps.log` records the argument of every sleep, so a backoff the
    block printed without taking cannot pass for one it took either.

    The block arrives as a decoded `run:` scalar on a script this builds and
    is handed to bash through `-c`, never through `$( )`: macOS ships bash
    3.2, whose here-doc handling inside a substitution differs.
    """
    root = Path(cwd)
    (root / 'fixture.out').write_text(output, encoding='utf-8')
    (root / 'runs.log').write_text('', encoding='utf-8')
    (root / 'sleeps.log').write_text('', encoding='utf-8')
    script = ('sleep() { echo "$1" >> sleeps.log; }\n'
              'pip-audit() { echo x >> runs.log; cat fixture.out;'
              ' return "$AUDIT_CODE"; }\n') + body
    result = subprocess.run(
        ['bash', '-e', '-c', script], capture_output=True, text=True,
        timeout=_AUDIT_RETRY_TIMEOUT, cwd=cwd,
        env={**os.environ, 'AUDIT_CODE': str(code)})
    runs = len((root / 'runs.log').read_text(encoding='utf-8').split())
    waits = (root / 'sleeps.log').read_text(encoding='utf-8').split()
    return {'runs': runs, 'code': result.returncode,
            'sleeps': [int(value) for value in waits],
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


def _audit_announcements(body):
    """Every line the block names an outcome with, each tagged by its stream.

    A refusal names itself on stderr and ends the job; the retry arm names
    itself on stdout with the tail of what the tool wrote, and that line is
    the ONLY way a reader of the log learns a retry happened. Both are read
    here, because a reader that took the stderr one alone reports the block as
    covering its outcomes while the line the retry path prints — the one the
    retry path prints at all — is asserted by nothing.
    """
    return ([('refusal', text)
             for text in re.findall(r'^\s*echo "([^"]+)" >&2$', body,
                                    re.MULTILINE)]
            + [('retry', text)
               for text in re.findall(r'^\s*echo "([^"]+)"$', body,
                                      re.MULTILINE)])


def assert_the_audit_retry_is_narrow_and_ordered(tmp):
    """One executed fixture per arm of audit.yml's own retry block.

    Every predicate gets a value it accepts and one just past it, because a
    single-valued set makes "retries this" and "gives up on that" look the
    same while pinning neither. The block is RUN rather than read: only a run
    can tell a 404 the index will not repeat out of a 502 beside it, and only
    a run can tell which of the block's two tests was read first.

    What it PRINTS is asserted too, because the harness collects it: how many
    times the tool ran and what the block exited with are two of the facts a
    reader of this log actually gets, and the others are what tell a genuine
    finding from an index error, a retried transport failure from a backoff
    the block never took, and a refusal from a clean run. Every one of the
    block's five outcomes is named by exactly the arm that owns it and by no
    other — including the retry, which is announced per attempt and shows the
    tail of what the tool wrote each time.
    """
    body = _audit_step('Audit dependencies')
    announcements = _audit_announcements(body)
    refusals = [text for kind, text in announcements if kind == 'refusal']
    announcing = [text for kind, text in announcements if kind == 'retry']
    assert len(set(refusals)) == len(refusals), (
        f'two outcomes refuse in the same words: {refusals}')
    assert len(announcing) == 1, (
        f'the block announces a retry in {len(announcing)} ways, so "the '
        f'retry arm announced itself" names no single line: {announcing}')
    marker = announcing[0]
    assert marker not in refusals, (
        f'the retry announcement is also a refusal text, so no output of the '
        f'block tells the two apart: {marker!r}')
    for label, output, runs, code in AUDIT_RETRY_FIXTURES:
        outcome = audit_step_outcome(body, output, cwd=tmp, code=code)
        assert (outcome['runs'], outcome['code']) == (runs, code), (
            f'{label}: {outcome}')
        assert output.strip() in outcome['out'], (
            f'{label}: the block ended without printing what the tool wrote: '
            f'{outcome}')
        printed = [text for text in refusals if text in outcome['out']]
        assert len(printed) == (0 if not code else 1), (
            f'{label}: printed {printed}, and each outcome names itself once')
        expected = list(range(1, runs + 1)) if runs > 1 else []
        announced = [attempt for attempt in range(1, runs + 1)
                     if marker.replace('$attempt', str(attempt))
                     in outcome['out']]
        assert announced == expected, (
            f'{label}: {runs} attempts, and the retry arm has to announce '
            f'every one of them and no other arm announces anything; it '
            f'announced {announced} of {expected}: {outcome}')
        if runs > 1:
            lines = output.strip().splitlines()
            shown = outcome['out'].count
            assert all(shown(line) >= runs for line in lines[-5:]), (
                f'{label}: each attempt showed the reader the tail of what '
                f'the tool wrote, so those lines appear once per attempt, '
                f'not only in the report the last one ends with: {outcome}')
            assert all(shown(line) <= 1 for line in lines[:-5]), (
                f'{label}: the excerpt is bounded, so the lines above it '
                f'reach the log only in the final report: {outcome}')
        waits = outcome['sleeps']
        assert len(waits) >= runs - 1, (
            f'{label}: {runs} attempts, so every retry between them has to '
            f'wait, and these are the waits it took: {outcome}')
        assert all(0 < first < second
                   for first, second in zip(waits, waits[1:])), (
            f'{label}: each attempt must wait longer than the one before it, '
            f'and these are the waits it took: {waits}')
