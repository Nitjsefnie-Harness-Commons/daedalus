#!/usr/bin/env python3
"""Publish a `gate freshness` verdict on every open pull request head.

For each open pull request head based on `main`, decide whether the head
contains every gate-defining commit on `main`, and publish a `gate freshness`
check run onto that head. A branch that already contains the gates stays green
without rebasing; only a branch that predates a new gate is forced to rebase.

THE GATE-DEFINING PATH SET. A file is gate-defining when a change to it can
change the verdict of a required check FOR THE SAME SOURCE TREE -- when it
decides *what a check computes*, not *what tree it computes it on*. A changed
TEST is not gate-defining: the tree is what a branch carries, so a branch
always runs the tests it brings with it.

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

A file the branch carries itself is deliberately NOT here; see
CARRIED_BY_THE_BRANCH below. The set is derived, not trusted: the suite reads
every `.github/workflows/*.yml` and requires every tracked file those
workflows execute or pass to a tool to be listed here (or declared
carried-by-the-branch), so a gate file added to a workflow later fails the
suite.

THE DECISION, per (gate commit, head): one `compare/<gate>...<head>` request.
The gate commit is an ancestor of the head IFF the merge base equals the gate
commit, validated as 40 lowercase hex. Every enumerated gate commit is checked
-- short-circuiting on the first miss would be an optimisation, not the proof.
A compare that cannot be read is NOT evidence of freshness: that head is
published RED, because that is exactly the case where a stale green is
waiting to be overwritten.

READING FAILURES. A GLOBAL failure (the open-PR list, a gate-commit lookup,
the call bound) publishes NOTHING and exits nonzero: an invented verdict is
worse than a missing one. A PER-HEAD failure (one head's compare unreadable,
or its write failing) still writes that head's RED verdict where it can, skips
it loudly where it cannot, and never lets one head's failure abandon the
rest. All print a loud line to stderr.

THE BOUND. Per open pull request the worst case is, for each of G gate
commits, one compare, plus one head revalidation, one check-runs listing, a
second head revalidation, and one write: G + 4. The run refuses LOUDLY --
publishing nothing, exiting nonzero -- if the worst case exceeds the call
budget, rather than truncating the head set, which is the silent-pass shape.
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

# Deliberately NOT gate-defining, because the branch reads them FROM ITS OWN
# TREE: the ratchet scripts that read them are already gate-defining above, so
# a branch carrying an older baseline measures its older tree
# self-consistently. And every baseline move is permissive (--tighten only
# lowers; a coverage-floor raise moves the floor up), so a stale branch's
# recorded baseline can never turn main red by merging. The derivation guard
# subtracts exactly this tuple: a new such file must be added here with its
# own reason, never slip through silently.
CARRIED_BY_THE_BRANCH = ('.github/ci-thresholds.json',)

_HEX40 = frozenset('0123456789abcdef')

# Per-head worst case that is not a compare: 1 revalidation + 1 listing
# + 1 revalidation + 1 write. Each gate commit adds one compare.
PER_HEAD_OVERHEAD = 4

DEFAULT_CALL_BUDGET = 1200


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
    """Whether `pattern` selects `path`.

    Only the two shapes the set uses: a `/**` suffix (a directory prefix) and
    an exact path. A path is repository-relative POSIX as git records it, so it
    never carries a `..` component.
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


def enumerate_gates(read, repository):
    """The newest commit on `main` touching each gate path, as (path, sha).

    One request per path. A path with no commit yet contributes nothing (the
    API returning `[]` is a real answer). A payload that is not a list at all,
    or a first entry with no readable sha, is a GLOBAL failure -- never "this
    path has no commits", which would silently drop a gate and turn every head
    green.
    """
    gates = []
    for pattern in GATE_PATTERNS:
        try:
            payload = _one(read, [
                'gh', 'api', '-H', 'Cache-Control: no-cache',
                f'repos/{repository}/commits?sha={BASE_BRANCH}&per_page=1'
                f'&path={quote(pattern, safe="/.*")}'])
        except QueryError:
            return None
        if not isinstance(payload, list):
            return None
        if not payload:
            continue
        sha = payload[0].get('sha') if isinstance(payload[0], dict) else None
        if not _hex40(sha):
            return None
        gates.append((pattern, sha))
    return gates


def merge_base(read, repository, gate, head):
    """The merge base of `gate` and `head`, or None if it cannot be read.

    One `compare/<gate>...<head_sha>` request; that spelling resolves a fork
    head from the base repository (measured against PR 709), so there is no
    second spelling to try. Returns (merge_base, attempts); each attempt is
    (request, outcome). A merge base that is not 40 lowercase hex is a shape
    failure, distinct from the verdict.
    """
    target = f'repos/{repository}/compare/{gate}...{head["sha"]}'
    try:
        payload = _one(read, [
            'gh', 'api', '-H', 'Cache-Control: no-cache', target])
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
        payload = _one(read, [
            'gh', 'api', '-H', 'Cache-Control: no-cache',
            f'repos/{repository}/pulls/{number}'])
    except QueryError:
        return None
    head = payload.get('head') if isinstance(payload, dict) else None
    sha = head.get('sha') if isinstance(head, dict) else None
    return sha if _hex40(sha) else None


def _existing_check_ids(read, repository, head_sha):
    """The ids of this check already on `head_sha`, filtered server-side."""
    listing = [
        'gh', 'api', '--method', 'GET', '-H', 'Cache-Control: no-cache',
        '--paginate',
        f'repos/{repository}/commits/{head_sha}/check-runs'
        f'?filter=all&per_page=100',
        '--jq', (f'.check_runs[] | select(.name == "{NAME}" and '
                 f'.external_id == "{EXTERNAL_ID}" and '
                 f'.app.slug == "{APP_SLUG}") | .id'),
    ]
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
    """Write the verdict, PATCHing an existing check or POSTing a new one.

    A failure overwrites a previously published success on the same head: a
    stale green left in place after a gate lands is the exact hole this issue
    is about.
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
            read(['gh', 'api', '-X', 'PATCH',
                  f'repos/{repository}/check-runs/{check_id}'] + fields)
    else:
        read(['gh', 'api', '-X', 'POST', f'repos/{repository}/check-runs',
              '-f', f'head_sha={head_sha}'] + fields)


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
    """Publish a verdict for each head. Returns (exit_code, published).

    A head whose write fails is skipped loudly and the run continues: one
    transient failure must not abandon the later heads, which include stale
    ones waiting for a red. `dry_run` computes and reports but publishes
    nothing.
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
    return _one(read, [
        'gh', 'api', '-H', 'Cache-Control: no-cache', '--paginate',
        f'repos/{repository}/pulls?state=open&base={BASE_BRANCH}'
        f'&per_page=100'])


def main(argv=None, read=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    dry_run = '--dry-run' in arguments
    read = gh_read if read is None else read
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    event = _event()
    name = os.environ.get('GITHUB_EVENT_NAME', '')
    if name == 'pull_request_target':
        pr = event.get('pull_request') or {}
        heads = select_heads([pr]) if pr else []
    else:
        try:
            pulls = _open_pulls(read, repository)
        except QueryError as error:
            print('gate freshness: could not read the open pull request list; '
                  f'publishing nothing: {error}', file=sys.stderr)
            return 1
        heads = select_heads(pulls if isinstance(pulls, list) else [])
    gates = enumerate_gates(read, repository)
    if gates is None:
        print('gate freshness: could not read a gate commit on main; '
              'publishing nothing', file=sys.stderr)
        return 1
    code, published = process(read, repository, heads, gates,
                              _details_url(), _call_budget(), dry_run)
    verb = 'would publish' if dry_run else 'published'
    print(f'gate freshness: {verb} {len(published)} verdict(s) for '
          f'{len(heads)} open pull request(s) based on main')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
