#!/usr/bin/env python3
"""Publish a `gate freshness` verdict on every open pull request head.

For each open pull request head based on `main`, decide whether the head
contains every gate-defining commit on `main`, and publish a `gate freshness`
check run onto that head. A branch already containing the gates stays green
without rebasing; only one predating a new gate is forced to rebase.

THE GATE-DEFINING PATH SET. A file is gate-defining when a change to it can
change a required check's verdict FOR THE SAME SOURCE TREE -- it decides *what
a check computes*, not *what tree it computes it on*. A changed TEST is not:
the tree is what a branch carries, so a branch runs the tests it brings.

  .github/workflows/**       which checks run, and what each one does
  scripts/ci/**             the gate implementations under the CI directory
  scripts/check_versions.py the version gate run on every push to main
  .gitleaks.toml            the configuration secrets.yml scans with
  pyrightconfig.json        the type checker's scope and settings
  pyrightconfig.tests.json  the type checker's scope over the test tree
  .pylintrc                 pylint's configuration
  setup.cfg                 pycodestyle's configuration (max-line-length)
  eslint.config.js          eslint's configuration
  pyproject.toml            [tool.coverage.*] -- what coverage measures
  run_tests.py              which suites the suites job runs
  requirements-dev.txt      the tools the gates install and run
  requirements-test.txt     the tools the gates install and run

A file the branch carries itself is NOT here; see CARRIED_BY_THE_BRANCH below.
The set is derived only as far as a workflow NAMES a file: the suite reads
every `.github/workflows/*.yml` and requires every tracked file a workflow
invokes by path or passes to a tool to be listed here (or declared
carried-by-the-branch), so a gate file added later fails the suite. The REACH
LIMIT is real and stated:
`pyrightconfig.json`, `pyrightconfig.tests.json`, `setup.cfg`,
`eslint.config.js` and `pyproject.toml` are read by tool DISCOVERY (a bare
`pyright` / `eslint` /
`pycodestyle` invocation, or a heredoc), never named, so the derivation cannot
see them; those five are hand-held above, each with its reason. A `python3 -m
module` invocation is likewise not seen.

THE DECISION, per (gate commit, head): one `compare/<gate>...<head>` request.
The gate commit is an ancestor of the head IFF the merge base equals the gate
commit, validated as 40 lowercase hex. Every enumerated gate commit is checked
-- short-circuiting on the first miss would be an optimisation, not the proof.
A compare that cannot be read is NOT evidence of freshness: that head is
published RED, the case where a stale green is waiting to be overwritten.

READING FAILURES. A GLOBAL failure (the open-PR list, a gate-commit lookup, the
call bound, a route that cannot establish its head) publishes NOTHING and exits
nonzero: an invented verdict is worse than a missing one. A PER-HEAD failure
(one head's compare unreadable, or its write failing) still writes that head's
RED verdict where it can, skips it loudly where it cannot, and never abandons
the rest. All print a loud line to stderr.

THE BOUND. Per open pull request the worst case is, for each of G gate
commits, one compare, plus one head revalidation, one check-runs listing, a
second head revalidation, and one write: G + PER_HEAD_OVERHEAD. The run refuses
LOUDLY -- publishing nothing, exiting nonzero -- if the worst case exceeds the
call budget, rather than truncating the head set, which is the silent-pass
shape. The budget is a CALL count; the timeout is sized against it at
ASSUMED_SECONDS_PER_CALL (below), and a test pins that relation.
"""
import json
import os
import subprocess
import sys
from urllib.parse import quote

NAME = 'gate freshness'
EXTERNAL_ID = 'daedalus-gate-freshness/v1'
APP_SLUG = 'github-actions'
BASE_BRANCH = 'main'

# THE SET. Order is readability only; every entry is checked against every
# head.
GATE_PATTERNS = (
    '.github/workflows/**',
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
    'requirements-test.txt',
)

# And every baseline move is permissive (--tighten only lowers; a
# coverage-floor raise moves the floor up), so a stale branch's baseline can
# never turn main red by merging. The derivation guard subtracts exactly this
# tuple: a new such file must be added here with its own reason.
# The two `timed` suites are carried under a second mechanism:
# `timed-timings.yml` RUNS them to verify the refresh before it commits, so
# the workflow-invokes-a-gate sweep finds them. They verify this branch.
CARRIED_BY_THE_BRANCH = ('.github/ci-thresholds.json',
                         'tests/test_timed_planner.py',
                         'tests/test_timed_refresh.py')

_HEX40 = frozenset('0123456789abcdef')

# Per-head worst case that is not a compare: 2 revalidations, 1 listing, 1
# write. Each gate commit adds one compare.
PER_HEAD_OVERHEAD = 4

DEFAULT_CALL_BUDGET = 1200

# The per-call rate the timeout is sized against (see THE BOUND). Measured
# live at ~1.2 s/call; 1.5 s is the conservative figure the timeout clears.
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
    """Whether `pattern` selects `path`: a `/**` directory prefix or an exact
    path. A path is repository-relative POSIX, so it carries no `..`.
    """
    if pattern.endswith('/**'):
        return path.startswith(pattern[:-2])
    if any(char in pattern for char in '*?['):
        raise ValueError(f'unsupported pattern shape: {pattern!r}')
    return pattern == path


def is_gate_defining(path):
    """Whether a change to `path` can change a required check's verdict."""
    return any(matches(pattern, path) for pattern in GATE_PATTERNS)


def _one(read, argv):
    """One JSON object/array through the read seam, parsed."""
    try:
        return json.loads(read(argv))
    except ValueError as error:
        raise QueryError(f'unparseable gh output: {error}') from error


def _api(url, *extra):
    """The one `gh api` call shape. Every read routes through here so the
    `Cache-Control: no-cache` convention has a single site to hold."""
    return ['gh', 'api', '-H', 'Cache-Control: no-cache', url, *extra]


def _request_path(pattern):
    """The path the commits endpoint understands for `pattern`. GitHub's
    `commits?path=` is a DIRECTORY-PREFIX filter and does NOT expand a `**`
    glob: sending `.github/workflows/**` answers `[]` even though the directory
    has commits. The endpoint is sent the BARE directory; `pattern` keeps its
    `/**` spelling for the internal matcher, and the two cannot disagree
    because this is the single translation between them.
    """
    if pattern.endswith('/**'):
        return pattern[:-3]
    return pattern


def _gate_error(pattern, cause):
    """The GLOBAL failure an unreadable or empty gate lookup raises."""
    return QueryError(f'the gate commit lookup for {pattern!r} {cause}')


def enumerate_gates(read, repository):
    """The newest commit on `main` touching each gate path, as (path, sha).

    One request per path, built from `_request_path`. Every failure mode is a
    GLOBAL failure that NAMES the pattern and is raised, never a silent skip:
    unreadable, not a list, no readable sha, OR empty. Empty is anomalous (all
    patterns have commits on main) and treating it as "no commits" would drop a
    gate and turn every head green -- the exact defect this module prevents."""
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
    """The merge base of `gate` and `head`, or None if it cannot be read.

    One `compare/<gate>...<head_sha>` request; that spelling resolves a fork
    head (measured against PR 709), so there is no second spelling. Returns
    (merge_base, attempts); a merge base that is not 40 lowercase hex is a
    shape failure, distinct from the verdict.
    """
    target = f'repos/{repository}/compare/{gate}...{head["sha"]}'
    try:
        payload = _one(read, _api(target))
    except QueryError as error:
        return None, [(target, f'failed: {error}')]
    sha = None
    if isinstance(payload, dict):
        base = payload.get('merge_base_commit')
        if isinstance(base, dict):
            sha = base.get('sha')
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
    """The pull request's current head sha, or None if it cannot be read."""
    try:
        payload = _one(read, _api(f'repos/{repository}/pulls/{number}'))
    except QueryError:
        return None
    head = payload.get('head') if isinstance(payload, dict) else None
    sha = head.get('sha') if isinstance(head, dict) else None
    return sha if _hex40(sha) else None


def _existing_check_ids(read, repository, head_sha):
    """The ids of this check already on `head_sha`, filtered server-side."""
    listing = _api(
        f'repos/{repository}/commits/{head_sha}/check-runs'
        f'?filter=all&per_page=100',
        '--method', 'GET', '--paginate', '--jq',
        (f'.check_runs[] | select(.name == "{NAME}" and '
         f'.external_id == "{EXTERNAL_ID}" and '
         f'.app.slug == "{APP_SLUG}") | .id'))
    try:
        raw = read(listing)
    except QueryError:
        return None
    ids = []
    for line in raw.splitlines():
        line = line.strip()
        if line and line.isdigit():
            ids.append(line)
    return ids


def publish(read, repository, head_sha, conclusion, title, summary,
            details_url):
    """Write the verdict, PATCHing an existing check or POSTing a new one. A
    failure overwrites a previously published success on the same head: a stale
    green left in place is the exact hole this issue is about.
    """
    ids = _existing_check_ids(read, repository, head_sha)
    if ids is None:
        raise QueryError('could not read the existing check runs')
    fields = [
        '-f', f'name={NAME}',
        '-f', 'status=completed',
        '-f', f'conclusion={conclusion}',
        '-f', f'external_id={EXTERNAL_ID}',
        '-f', f'details_url={details_url}',
        '-f', f'output[title]={title}',
        '-f', f'output[summary]={summary}',
    ]
    if ids:
        for check_id in ids:
            read(_api(f'repos/{repository}/check-runs/{check_id}',
                      '-X', 'PATCH', *fields))
    else:
        read(_api(f'repos/{repository}/check-runs', '-X', 'POST',
                  '-f', f'head_sha={head_sha}', *fields))


def _pr_head(pr):
    head = pr.get('head') or {}
    repo = head.get('repo') or {}
    owner = (repo.get('owner') or {}).get('login')
    return {'number': pr.get('number'), 'sha': head.get('sha'),
            'owner': owner, 'ref': head.get('ref'),
            'base': (pr.get('base') or {}).get('ref')}


def select_heads(pulls):
    """Keep only open pull requests based on `main` (the ruleset's branch)."""
    heads = []
    for pr in pulls:
        head = _pr_head(pr)
        if head['base'] == BASE_BRANCH and _hex40(head['sha']):
            heads.append(head)
    return heads


def required_calls(head_count, gate_count):
    """Worst-case `gh api` calls: enumeration plus per-head work."""
    return (len(GATE_PATTERNS)
            + head_count * (PER_HEAD_OVERHEAD + gate_count))


def process(read, repository, heads, gates, details_url, call_budget=None,
            dry_run=False):
    """Publish a verdict for each head. Returns (exit_code, published). A head
    whose write fails is skipped loudly and the run continues: one transient
    failure must not abandon the later heads, which include stale ones waiting
    for a red. `dry_run` computes and reports but publishes nothing.
    """
    budget = (DEFAULT_CALL_BUDGET if call_budget is None else call_budget)
    needed = required_calls(len(heads), len(gates))
    if needed > budget:
        print('gate freshness: refusing to run -- '
              f'{needed} worst-case calls exceed the budget of {budget}; '
              'publishing nothing', file=sys.stderr)
        return 1, []
    published = []
    skipped = 0
    for head in heads:
        if current_head(read, repository, head['number']) != head['sha']:
            print(f'gate freshness: head of pull request '
                  f'{head["number"]} moved; skipping', file=sys.stderr)
            skipped += 1
            continue
        verdict = head_verdict(read, repository, head, gates)
        if current_head(read, repository, head['number']) != head['sha']:
            print(f'gate freshness: head of pull request '
                  f'{head["number"]} moved; skipping', file=sys.stderr)
            skipped += 1
            continue
        if dry_run:
            print(f'PR #{head["number"]} {head["sha"][:12]} '
                  f'{verdict.conclusion:>7} [{verdict.kind}] '
                  f'{verdict.summary}')
        else:
            try:
                publish(read, repository, head['sha'], verdict.conclusion,
                        verdict.title, verdict.summary, details_url)
            except QueryError as error:
                print(f'gate freshness: could not publish the verdict for '
                      f'pull request {head["number"]}: {error}; skipping',
                      file=sys.stderr)
                skipped += 1
                continue
        published.append(verdict)
    return (1 if skipped else 0), published


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
        if not isinstance(pr, dict) or not pr.get('head'):
            # A route that cannot establish WHICH pull request it is deciding
            # must fail closed. Treating a missing event as "no heads" would
            # publish nothing and exit 0, i.e. "fresh", and the ruleset would
            # let it merge -- the silent-pass shape, refused here.
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
    except QueryError as error:
        print(f'gate freshness: {error}; publishing nothing', file=sys.stderr)
        return 1
    code, published = process(read, repository, heads, gates,
                              _details_url(), _call_budget(), dry_run)
    verb = 'would publish' if dry_run else 'published'
    print(f'gate freshness: {verb} {len(published)} verdict(s) for '
          f'{len(heads)} open pull request(s) based on main; resolved '
          f'{len(gates)}/{len(GATE_PATTERNS)} gate paths')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
