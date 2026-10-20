#!/usr/bin/env python3
"""Publish a `gate freshness` verdict for open heads based on main.

The event's own job carries its verdict; the Checks API updates other heads.
GATE_PATTERNS names paths that change what a gate computes for a fixed tree,
including the ratcheted threshold and journey counts. Tests change the tree,
not the gate definition. Directory patterns use literal prefixes; config files
read by tool discovery are explicitly listed because workflow scans miss them.

For every gate path, enumerate its latest main commit, then read main's head
once. Compare each gate commit to each PR head: its 40-hex merge base must be
the gate itself. Revalidate the PR head before comparing and before writing.
A readable moved head is skipped; unreadable identity or comparison fails.
Global read/budget failures publish nothing; per-head failures preserve work
for other heads and fail the run. A dry run computes without publishing.

THE CHECK CARRIER. GitHub chooses suites from repository and SHA. API-created
checks were observed in the oldest app suite; callers cannot move them when
it is superseded. A job check belongs to its run's suite, so the job carries
the context. Select the newest check, PATCH only if completed, otherwise leave
it to its runner. Retire completed non-carriers, including after a skipped
verdict write. Without a carrier, POST may still land in a superseded suite.

ORDERING. A writer never replaces a verdict it has observed to rest on a main
its own snapshot does not contain. Every verdict summary ends with main's SHA;
unmarked failures are not turned success without ordering evidence. This is
not atomic: two invocations reading the carrier before either writes can still
race, and an older snapshot can win. The window is one ordering compare wide,
not a whole run; an older red can still replace a newer green, blocking the
head until its next event. No retry or repair follows a write.

THE BOUND. Estimate L + 1 + H * (G + 5): L gate-path lookups and one main-head
read, then G compares, two revalidations, one listing, one write and a
conditional ordering compare per head. Extra completed ids cost retirement
PATCHes; pagination counts as one logical read. Neither ids nor pages are
bounded by the estimate. At 1.5 s/call, 1200 calls take 30 minutes; the
35-minute timeout provides residual slack under that assumption. An
oversized estimate refuses without truncating the head set.
"""
import json
import os
import subprocess
import sys
from urllib.parse import quote

NAME = 'gate freshness'
NAME_RETIRED = 'superseded gate freshness'
EXTERNAL_ID = 'daedalus-gate-freshness/v1'
APP_SLUG = 'github-actions'
BASE_BRANCH = 'main'

GATE_PATTERNS = (
    '.github/workflows/**',
    '.github/ci-thresholds.json',
    '.github/journey-budget.json',
    'scripts/ci/**',
    'scripts/check_versions.py',
    '.gitleaks.toml',
    'pyrightconfig.json',
    'pyrightconfig.tests.json',
    '.pylintrc',
    'setup.cfg',
    'eslint.config.js',
    'pyproject.toml',
    'run_tests.py',
    'requirements-dev.txt',
    'requirements-test.txt', 'requirements-release.txt',
    'requirements-pip-audit.txt', 'requirements-zizmor.txt',
)

_HEX40 = frozenset('0123456789abcdef')

# Per head: two revalidations, listing, write, conditional ordering compare.
PER_HEAD_OVERHEAD = 5

DEFAULT_CALL_BUDGET = 1200

ASSUMED_SECONDS_PER_CALL = 1.5


class QueryError(RuntimeError):
    """A `gh api` call that could not be read."""


class Verdict:
    """What to publish for one head, and why."""

    def __init__(self, conclusion, kind, title, summary, paths=()):
        self.conclusion = conclusion
        self.kind = kind
        self.title = title
        self.summary = summary
        self.paths = list(paths)


def _hex40(value):
    return (isinstance(value, str) and len(value) == 40
            and all(char in _HEX40 for char in value))


def matches(pattern, path):
    """Match a directory-prefix pattern or exact repository-relative path."""
    if pattern.endswith('/**'):
        return path.startswith(pattern[:-2])
    if any(char in pattern for char in '*?['):
        raise ValueError(f'unsupported pattern shape: {pattern!r}')
    return pattern == path


def is_gate_defining(path):
    """Whether a change to `path` can change a required check's verdict."""
    return any(matches(pattern, path) for pattern in GATE_PATTERNS)


def _one(read, argv):
    try:
        return json.loads(read(argv))
    except ValueError as error:
        raise QueryError(f'unparseable gh output: {error}') from error


def _api(url, *extra):
    """Build a gh request with the shared no-cache header."""
    return ['gh', 'api', '-H', 'Cache-Control: no-cache', url, *extra]


def _request_path(pattern):
    """Use a directory prefix: the commits endpoint does not expand globs."""
    if pattern.endswith('/**'):
        return pattern[:-3]
    return pattern


def _gate_error(pattern, cause):
    return QueryError(f'the gate commit lookup for {pattern!r} {cause}')


def enumerate_gates(read, repository):
    """The newest commit on `main` touching each gate path, as (path, sha)."""
    gates = []
    for pattern in GATE_PATTERNS:
        request = _request_path(pattern)
        target = (f'repos/{repository}/commits?sha={BASE_BRANCH}&per_page=1'
                  f'&path={quote(request, safe="/.*")}')
        try:
            payload = _one(read, _api(target))
        except QueryError as error:
            raise _gate_error(pattern, f'could not be read: {error}') \
                from error
        if not isinstance(payload, list):
            raise _gate_error(pattern, 'did not decode as a list')
        if not payload:
            cause = (f'(requested as {request!r}) resolved to no commit on '
                     f'main; refusing to under-count the gate set')
            raise _gate_error(pattern, cause)
        sha = payload[0].get('sha') if isinstance(payload[0], dict) else None
        if not _hex40(sha):
            raise _gate_error(pattern, 'returned an unreadable sha')
        gates.append((pattern, sha))
    return gates


def merge_base(read, repository, gate, head):
    """The merge base of `gate` and `head`, or None if it cannot be read."""
    target = f'repos/{repository}/compare/{gate}...{head["sha"]}'
    try:
        payload = _one(read, _api(target))
    except QueryError as error:
        return None, [(target, f'failed: {error}')]
    base = (payload.get('merge_base_commit')
            if isinstance(payload, dict) else None)
    sha = base.get('sha') if isinstance(base, dict) else None
    if _hex40(sha):
        return sha, [(target, f'merge base {sha}')]
    return None, [(target, f'merge base {sha!r} is not 40 hex')]


def _remedy():
    return 'Rebase onto main so the head contains every gate-defining commit.'


def _unreadable_summary(head, gate, attempts):
    tried = '; '.join(f'{target} -> {outcome}' for target, outcome in attempts)
    return (f'The freshness of head {head["sha"][:12]} could not be computed: '
            f'the compare against gate commit {gate[:12]} could not be read '
            f'({tried}). A compare that cannot be read is not evidence of '
            f'freshness, and a 404 is not evidence of access denial. '
            f'{_remedy()}')


def _stale_summary(head, missing):
    first = missing[0]
    return (f'Head {head["sha"][:12]} does not contain gate commit '
            f'{first[1][:12]} (last change to {first[0]}). {_remedy()}')


def head_verdict(read, repository, head, gates):
    """Decide one head against every gate commit. All are checked."""
    missing = []
    for path, gate in gates:
        base, attempts = merge_base(read, repository, gate, head)
        if base is None:
            return Verdict('failure', 'unreadable',
                           'gate freshness could not be computed',
                           _unreadable_summary(head, gate, attempts))
        if base != gate:
            missing.append((path, gate))
    if missing:
        return Verdict('failure', 'stale', 'gate freshness: rebase required',
                       _stale_summary(head, missing), [p for p, _ in missing])
    return Verdict('success', 'fresh', 'gate freshness: up to date',
                   f'Head {head["sha"][:12]} contains every gate-defining '
                   f'commit on main.')


def current_head(read, repository, number):
    """Read a 40-hex current head sha; raise QueryError on read failure."""
    payload = _one(read, _api(f'repos/{repository}/pulls/{number}'))
    head = payload.get('head') if isinstance(payload, dict) else None
    sha = head.get('sha') if isinstance(head, dict) else None
    if not _hex40(sha):
        raise QueryError('could not read a 40-hex current head sha')
    return sha


def main_head(read, repository):
    """Read the invocation's main snapshot, or fail without a verdict."""
    payload = _one(read, _api(f'repos/{repository}/commits/{BASE_BRANCH}'))
    sha = payload.get('sha') if isinstance(payload, dict) else None
    if not _hex40(sha):
        raise QueryError('could not read the main snapshot')
    return sha


def _existing_checks(read, repository, head_sha):
    """Read projected JSON check records, refusing malformed rows."""
    listing = _api(
        f'repos/{repository}/commits/{head_sha}/check-runs'
        '?filter=all&per_page=100', '--method', 'GET', '--paginate',
        '--jq', (f'.check_runs[] | select(.name == "{NAME}" and '
                 f'.app.slug == "{APP_SLUG}") | '
                 '{id, status, conclusion, external_id, '
                 'summary: .output.summary}'))
    try:
        raw = read(listing)
    except QueryError:
        return None
    checks = []
    for line in raw.splitlines():
        try:
            check = json.loads(line)
        except ValueError as error:
            raise QueryError('malformed existing check listing') from error
        if (not isinstance(check, dict)
                or not isinstance(check.get('id'), int)
                or isinstance(check['id'], bool)
                or not isinstance(check.get('status'), str)):
            raise QueryError('malformed existing check listing')
        checks.append(check)
    return checks


def _may_replace(read, repository, carrier, main_sha, conclusion):
    summary = carrier.get('summary')
    marker = summary.rsplit('\n', 1)[-1] if isinstance(summary, str) else ''
    theirs = marker[5:] if marker.startswith('main ') else None
    theirs = theirs if _hex40(theirs) else None
    if theirs and theirs != main_sha:
        base, _attempts = merge_base(
            read, repository, theirs, {'sha': main_sha})
        if base is None:
            raise QueryError('could not read the main ordering compare')
        return base == theirs
    return not (theirs is None and carrier.get('conclusion') == 'failure'
                and conclusion == 'success')


def publish(read, repository, head_sha, conclusion, title, summary,
            details_url, main_sha):
    """Write onto the newest check; leave an unfinished one to its runner.

    Never replace a verdict observed to rest on a main ours does not contain.
    Two reads before either write can still race: an older red or green may
    win. The window is one ordering compare wide, not a whole run. A head's
    job check is its carrier; a POST fallback can remain in a superseded suite.
    """
    checks = _existing_checks(read, repository, head_sha)
    if checks is None:
        raise QueryError('could not read the existing check runs')
    fields = [
        '-f', f'name={NAME}',
        '-f', 'status=completed',
        '-f', f'conclusion={conclusion}',
        '-f', f'details_url={details_url}',
        '-f', f'output[title]={title}',
        '-f', f'output[summary]={summary}\nmain {main_sha}',
    ]
    # Only POST sets external_id; PATCH preserves the runner UUID.
    carrier = max(checks, key=lambda check: check['id']) if checks else None
    if carrier is None:
        read(_api(f'repos/{repository}/check-runs', '-X', 'POST',
                  '-f', f'head_sha={head_sha}', *fields,
                  '-f', f'external_id={EXTERNAL_ID}'))
    elif carrier['status'] == 'completed' and _may_replace(
            read, repository, carrier, main_sha, conclusion):
        read(_api(f'repos/{repository}/check-runs/{carrier["id"]}',
                  '-X', 'PATCH', *fields))
    # An unfinished carrier gets its verdict from its job's exit code.
    for check in checks:
        if carrier is not None:
            if check['id'] == carrier['id']:
                continue
        if check['status'] == 'completed':
            read(_api(f'repos/{repository}/check-runs/{check["id"]}',
                      '-X', 'PATCH', '-f', f'name={NAME_RETIRED}'))


def _pr_head(pr):
    head = pr.get('head') or {}
    repo = head.get('repo') or {}
    owner = (repo.get('owner') or {}).get('login')
    return {'number': pr.get('number'), 'sha': head.get('sha'),
            'owner': owner, 'ref': head.get('ref'),
            'base': (pr.get('base') or {}).get('ref')}


def select_heads(pulls):
    heads = []
    for pr in pulls:
        head = _pr_head(pr)
        if head['base'] == BASE_BRANCH and _hex40(head['sha']):
            heads.append(head)
    return heads


def required_calls(head_count, gate_count):
    return len(GATE_PATTERNS) + 1 + head_count * (
        PER_HEAD_OVERHEAD + gate_count)


def process(read, repository, heads, gates, details_url, main_sha,
            call_budget=None, dry_run=False):
    """Return (exit_code, published); failed heads do not abandon siblings."""
    budget = (DEFAULT_CALL_BUDGET if call_budget is None else call_budget)
    needed = required_calls(len(heads), len(gates))
    if needed > budget:
        print('gate freshness: refusing to run -- '
              f'{needed} estimated calls exceed the budget of {budget}; '
              'publishing nothing', file=sys.stderr)
        return 1, []
    published = []
    failed = 0
    for head in heads:
        phase = 'read the head of'
        try:
            if current_head(read, repository, head['number']) != head['sha']:
                print(f'gate freshness: head of pull request '
                      f'{head["number"]} moved; skipping', file=sys.stderr)
                continue
            verdict = head_verdict(read, repository, head, gates)
            if current_head(read, repository, head['number']) != head['sha']:
                print(f'gate freshness: head of pull request '
                      f'{head["number"]} moved; skipping', file=sys.stderr)
                continue
            phase = 'publish the verdict for'
            if dry_run:
                print(f'PR #{head["number"]} {head["sha"][:12]} '
                      f'{verdict.conclusion:>7} [{verdict.kind}] '
                      f'{verdict.summary}')
            else:
                publish(read, repository, head['sha'], verdict.conclusion,
                        verdict.title, verdict.summary, details_url, main_sha)
        except QueryError as error:
            print(f'gate freshness: could not {phase} pull request '
                  f'{head["number"]}: {error}; skipping', file=sys.stderr)
            failed += 1
            continue
        published.append(verdict)
    return (1 if failed else 0), published


def gh_read(argv):
    """The `gh api` seam: return stdout, raise QueryError on any failure."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=60)
    except (subprocess.SubprocessError, OSError) as error:
        raise QueryError(f'gh failed: {error}') from error
    if proc.returncode != 0:
        detail = proc.stderr.strip()[:200] or f'gh exit {proc.returncode}'
        raise QueryError(detail)
    return proc.stdout


def _call_budget():
    raw = os.environ.get('GATE_FRESHNESS_MAX_CALLS', '')
    return int(raw) if raw.isdigit() else DEFAULT_CALL_BUDGET


def _details_url():
    server = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    run = os.environ.get('GITHUB_RUN_ID', '')
    return f'{server}/{repo}/actions/runs/{run}'


def _event():
    path = os.environ.get('GITHUB_EVENT_PATH')
    if not path:
        return {}
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _open_pulls(read, repository):
    return _one(read, _api(
        f'repos/{repository}/pulls?state=open&base={BASE_BRANCH}'
        f'&per_page=100', '--paginate'))


def main(argv=None, read=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    dry_run = '--dry-run' in arguments
    read = gh_read if read is None else read
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    event = _event()
    name = os.environ.get('GITHUB_EVENT_NAME', '')
    if name == 'pull_request_target':
        pr = event.get('pull_request') or {}
        head = pr.get('head') if isinstance(pr, dict) else None
        if not isinstance(head, dict) or not _hex40(head.get('sha')):
            # An unreadable event head must not produce an empty green run.
            print('gate freshness: the pull_request_target event carried no '
                  'usable pull_request.head; publishing nothing',
                  file=sys.stderr)
            return 1
        heads = select_heads([pr])
    else:
        try:
            pulls = _open_pulls(read, repository)
        except QueryError as error:
            print('gate freshness: could not read the open pull request list; '
                  f'publishing nothing: {error}', file=sys.stderr)
            return 1
        if not isinstance(pulls, list):
            # A non-list answer at HTTP 200 (an error object) is not "no open
            # pull requests": it publishes zero verdicts and exits 0, which is
            # the silent-pass shape. Refuse, as enumerate_gates refuses.
            print('gate freshness: the open pull request list did not decode '
                  'as a list; publishing nothing', file=sys.stderr)
            return 1
        heads = select_heads(pulls)
    try:
        gates = enumerate_gates(read, repository)
        main_sha = main_head(read, repository)
    except QueryError as error:
        print(f'gate freshness: {error}; publishing nothing', file=sys.stderr)
        return 1
    code, published = process(read, repository, heads, gates,
                              _details_url(), main_sha, _call_budget(),
                              dry_run)
    if name == 'pull_request_target' and not dry_run:
        if any(v.conclusion != 'success' for v in published):
            code = 1
    verb = 'would publish' if dry_run else 'published'
    print(f'gate freshness: {verb} {len(published)} verdict(s) for '
          f'{len(heads)} open pull request(s) based on main; resolved '
          f'{len(gates)}/{len(GATE_PATTERNS)} gate paths')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
