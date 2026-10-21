#!/usr/bin/env python3
"""Publish gate freshness for open heads based on main.

Snapshot main before enumerating GATE_PATTERNS. Compare every gate commit to
its head; only a 40-hex merge base equal to the gate proves freshness. Missing
gates require a rebase. An unreadable compare is RED, never evidence of access
denial. Revalidate heads before deciding and publishing; moved heads skip.
Global listing, main-read, gate-enumeration and budget failures fail push;
per-head problems skip loudly. Event jobs fail for RED. Dry runs only compute.

THE CHECK CARRIER. PATCH only completed checks bearing EXTERNAL_ID, choosing
the newest carrier and retiring completed owned non-carriers. An empty listing
permits POST; runner-owned checks require a run. API-created checks were
observed in the oldest app suite, invisible once superseded (issue #1655).
Push keeps fresh or already-red heads and dispatches other stale heads where
permitted. Dispatched jobs execute main's fetched source; a snapshot marker
prevents recursion and keeps its gate lookups on that same snapshot.

ORDERING. Keep a verdict whose stamped main our snapshot does not contain.
Unmarked failures cannot become success without ordering evidence. Two
writers can still race between the ordering read and write; an older verdict
may win until the next event. There is no post-write repair.

THE BOUND. Estimate L + 4 + H * (G + 7): L path lookups and up to four setup
reads (main, open PRs, bootstrap source and repeated PR list), then G compares,
two revalidations, listing, write, ordering compare, workflow read and dispatch
per head. Alternative paths are both reserved. Extra owned ids add retirement
PATCHes; pagination is one logical read, with no bound on ids or pages.
At 1.5 s/call, 1200 calls take 30 minutes; the 35-minute timeout leaves slack.
Oversized estimates refuse without truncating heads. Workflow discovery misses
module invocations and five tool configs explicitly carried in GATE_PATTERNS.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

NAME = 'gate freshness'
NAME_RETIRED = 'superseded gate freshness'
EXTERNAL_ID = 'daedalus-gate-freshness/v1'
APP_SLUG = 'github-actions'
BASE_BRANCH = 'main'
MAIN_SOURCE = 'GATE_FRESHNESS_MAIN_SOURCE'

GATE_PATTERNS = (
    '.github/workflows/**', '.github/ci-thresholds.json',
    '.github/journey-budget.json', 'scripts/ci/**',
    'scripts/check_versions.py', '.gitleaks.toml',
    'pyrightconfig.json', 'pyrightconfig.tests.json', '.pylintrc',
    'setup.cfg', 'eslint.config.js', 'pyproject.toml', 'run_tests.py',
    'requirements-dev.txt',
    'requirements-test.txt', 'requirements-release.txt',
    'requirements-pip-audit.txt', 'requirements-zizmor.txt',
)

_HEX40 = frozenset('0123456789abcdef')

# Revalidations, listing, write/order compare, workflow read and dispatch.
PER_HEAD_OVERHEAD = 7

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


def enumerate_gates(read, repository, main_sha):
    """Newest commit at the snapshot for each path, as (path, sha)."""
    gates = []
    for pattern in GATE_PATTERNS:
        request = _request_path(pattern)
        target = (f'repos/{repository}/commits?sha={main_sha}&per_page=1'
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
    """Compare every gate; stop only on an unreadable comparison."""
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
    """Read all suites; require objects with integer id and string status."""
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
    """Return (posted/patched/kept/needs-run, newest check needing a run)."""
    checks = _existing_checks(read, repository, head_sha)
    if checks is None: raise QueryError('could not read existing check runs')
    fields = ['-f', f'name={NAME}', '-f', 'status=completed',
              '-f', f'conclusion={conclusion}',
              '-f', f'details_url={details_url}',
              '-f', f'output[title]={title}',
              '-f', f'output[summary]={summary}\nmain {main_sha}']
    writable = [c for c in checks if c.get('external_id') == EXTERNAL_ID
                and c['status'] == 'completed']
    carrier = max(writable, key=lambda c: c['id'], default=None)
    if carrier is None:
        if checks:
            return 'needs-run', max(checks, key=lambda c: c['id'])
        read(_api(f'repos/{repository}/check-runs', '-X', 'POST',
                  '-f', f'head_sha={head_sha}', *fields,
                  '-f', f'external_id={EXTERNAL_ID}'))
        return 'posted', None
    outcome = 'kept'
    if _may_replace(read, repository, carrier, main_sha, conclusion):
        read(_api(f'repos/{repository}/check-runs/{carrier["id"]}',
                  '-X', 'PATCH', *fields))
        outcome = 'patched'
    for check in writable:
        if check['id'] != carrier['id']:
            read(_api(f'repos/{repository}/check-runs/{check["id"]}',
                      '-X', 'PATCH', '-f', f'name={NAME_RETIRED}'))
    return outcome, None


def _raw_file(read, repository, path, sha):
    return read(_api(f'repos/{repository}/contents/{path}?ref={sha}',
                     '-H', 'Accept: application/vnd.github.raw+json'))


def _keep_or_dispatch(read, repository, head, verdict):
    if verdict.conclusion == 'success': return
    checks = _existing_checks(read, repository, head['sha'])
    if checks is None: raise QueryError('could not read existing check runs')
    newest = max(checks, key=lambda c: c['id'], default={})
    if newest.get('conclusion') == 'failure': return
    if head['owner'] != repository.split('/')[0]:
        reason = 'cross-repository head has no dispatch ref here'
    else:
        workflow = _raw_file(read, repository,
                             '.github/workflows/gate-freshness.yml',
                             head['sha'])
        if 'workflow_dispatch' in workflow:
            read(_api(f'repos/{repository}/actions/workflows/'
                      'gate-freshness.yml/dispatches', '-X', 'POST',
                      '-f', f'ref={head["ref"]}'))
            return
        reason = 'head workflow has no workflow_dispatch trigger'
    raise QueryError(reason)


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
    return len(GATE_PATTERNS) + 4 + head_count * (
        PER_HEAD_OVERHEAD + gate_count)


def process(read, repository, heads, gates, details_url, main_sha,
            call_budget=None, dry_run=False, may_dispatch=False):
    """Return (exit_code, published); failed heads do not abandon siblings."""
    budget = (DEFAULT_CALL_BUDGET if call_budget is None else call_budget)
    needed = required_calls(len(heads), len(gates))
    if needed > budget:
        print('gate freshness: refusing to run -- '
              f'{needed} estimated calls exceed the budget of {budget}; '
              'publishing nothing', file=sys.stderr)
        return 1, []
    published, failed = [], 0
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
            elif may_dispatch:
                _keep_or_dispatch(read, repository, head, verdict)
            else:
                publish(read, repository, head['sha'], verdict.conclusion,
                        verdict.title, verdict.summary, details_url, main_sha)
        except QueryError as error:
            print(f'gate freshness: could not {phase} pull request '
                  f'{head["number"]}: {error}; skipping', file=sys.stderr)
            failed += not may_dispatch
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


def _main_source(read, repository, main_sha, arguments, run):
    source = _raw_file(read, repository, 'scripts/ci/gate_freshness.py',
                       main_sha)
    with tempfile.TemporaryDirectory(prefix='gate-freshness-') as directory:
        path = Path(directory) / 'gate_freshness.py'
        path.write_text(source, encoding='utf-8')
        try:
            return run([sys.executable, str(path), *arguments],
                       env={**os.environ, MAIN_SOURCE: main_sha},
                       check=False).returncode
        except (subprocess.SubprocessError, OSError) as error:
            raise QueryError(f'could not run main gate source: {error}') \
                from error


def main(argv=None, read=None, run=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    dry_run = '--dry-run' in arguments
    read = gh_read if read is None else read
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    event = _event()
    name = os.environ.get('GITHUB_EVENT_NAME', '')
    try:
        if name == 'pull_request_target':
            pr = event.get('pull_request') or {}
            head = pr.get('head') if isinstance(pr, dict) else None
            if not isinstance(head, dict) or not _hex40(head.get('sha')):
                raise QueryError('the pull_request_target event carried no '
                                 'usable pull_request.head')
            heads = select_heads([pr])
        else:
            try:
                pulls = _open_pulls(read, repository)
            except QueryError as error:
                raise QueryError('could not read the open pull request list: '
                                 f'{error}') from error
            if not isinstance(pulls, list):
                raise QueryError('the open pull request list did not decode '
                                 'as a list')
            heads = select_heads(pulls)
            if name == 'workflow_dispatch':
                heads = [h for h in heads
                         if h['sha'] == os.environ.get('GITHUB_SHA')][:1]
                if not heads: return 0
        marker = os.environ.get(MAIN_SOURCE, '')
        bootstrapped = name == 'workflow_dispatch' and _hex40(marker)
        main_sha = marker if bootstrapped else main_head(read, repository)
        if name == 'workflow_dispatch' and not bootstrapped:
            return _main_source(read, repository, main_sha, arguments,
                                subprocess.run if run is None else run)
        gates = enumerate_gates(read, repository, main_sha)
    except QueryError as error:
        print(f'gate freshness: {error}; publishing nothing', file=sys.stderr)
        return 1
    code, published = process(read, repository, heads, gates,
                              _details_url(), main_sha, _call_budget(),
                              dry_run, may_dispatch=name == 'push')
    if name in ('pull_request_target', 'workflow_dispatch') and not dry_run:
        if any(v.conclusion != 'success' for v in published): code = 1
    verb = 'would publish' if dry_run else 'published'
    print(f'gate freshness: {verb} {len(published)} verdict(s) for '
          f'{len(heads)} open pull request(s) based on main; resolved '
          f'{len(gates)}/{len(GATE_PATTERNS)} gate paths')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
