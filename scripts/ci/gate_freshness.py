#!/usr/bin/env python3
"""Publish a `gate freshness` verdict on every open pull request head.

A pull request whose head predates a NEW GATE can merge with every one of its
own checks green, and the merge then turns `main` red: the branch never ran the
gate that landed on `main` after it. PR 986 shipped that shape -- its head did
not contain the type-error ratchet added to the `pyright` job, so no run of
its own CI executed the ratchet and the merge failed it for the first time on
the already-merged tree.

A check computed only in the pull request's own CI is therefore not enough.
This script decides, for each open pull request head based on `main`, whether
the head contains every gate-defining commit on `main`, and publishes a
`gate freshness` check run onto that head through the Checks API. The
maintainer's ruleset requires that check, so only branches that predate a NEW
GATE are forced to rebase -- a branch that already contains the gates stays
green without rebasing.

THE GATE-DEFINING PATH SET. A file is gate-defining when a change to it can
change the verdict of a required check FOR THE SAME SOURCE TREE -- when it
decides *what a check computes*, not *what tree it computes it on*. A changed
TEST is not gate-defining: the source tree is exactly what a branch carries, so
a branch always runs the tests it brings with it. That boundary is why this is
a dozen paths and not "all of CI".

  .github/workflows/**       which checks run, and what each one does
  .github/ci-thresholds.json the recorded baselines every ratchet gates on
  scripts/ci/**             the gate implementations themselves
  pyrightconfig.json        the type checker's scope and settings
  pyrightconfig.tests.json  the type checker's scope over the test tree
  .pylintrc                 pylint's configuration
  setup.cfg                 pycodestyle's configuration (max-line-length)
  eslint.config.js          eslint's configuration
  pyproject.toml            [tool.coverage.*] -- what coverage measures
  run_tests.py              which suites the suites job runs
  requirements-dev.txt      the tools the gates install and run
  requirements-test.txt     the tools the gates install and run

The set is derived, not trusted: tests/test_gate_freshness.py reads every
`.github/workflows/*.yml` and requires every gate-configuration file those
workflows actually name to be listed here, so a gate file added to a workflow
later fails that suite instead of silently going unlisted.

`.github/ci-thresholds.json` is written by the coverage job's automatic
ratchet commit, so a coverage raise also red-flags open pull requests. The
entry stays because the recorded baselines are exactly what a ratchet gates
on; the cost is stated rather than quietly dropped.

THE DECISION, per (gate commit, head): one `compare/<gate>...<head>` request,
and the gate commit is an ancestor of the head IFF the merge base equals the
gate commit. Every enumerated gate commit is checked -- "the newest gate is a
descendant of the rest" is a claim about history shape, not a fact about this
repository. A 40-lowercase-hex merge base is validated before it is compared.

FORK HEADS. The primary `compare/<gate>...<head_sha>` already resolves a fork
head from the base repository (measured: PR 709's fork head answers HTTP 200).
The `owner:ref` spelling is belt-and-braces for a head the primary form
cannot reach -- a deleted fork, a ref that no longer exists -- and is only
tried after the primary form has already failed, so it costs nothing on the
healthy path. If BOTH spellings are unreadable, the head is published RED: a
freshness answer that cannot be computed is not evidence of freshness, and
failing open would reintroduce exactly this defect. The remedy -- rebase onto
`main` -- is the same one a genuinely stale branch needs.

READING FAILURES. A GLOBAL failure (the open-PR list, or a gate-commit lookup,
cannot be read, or the run would exceed its call bound) publishes NOTHING and
exits nonzero: there are no heads to write a verdict for, and an invented
verdict is worse than a missing one. A PER-HEAD failure (one head's compare is
unreadable) still writes that head's RED verdict, because that is precisely
the case where a stale green is waiting to be overwritten. Both print a loud
line to stderr.

THE BOUND. Per open pull request the worst case is, for each of G gate
commits, up to two compare requests (the fallback), plus one head revalidation,
one check-runs listing, a second head revalidation, and one write. The run
refuses LOUDLY -- publishing nothing, exiting nonzero -- if the worst case
exceeds the call budget, rather than truncating the head set: a run that
publishes verdicts for the heads it reached and none for the rest is the
silent-pass shape.
"""
import json
import os
import subprocess
import sys
from urllib.parse import quote

# The check name the maintainer's ruleset matches, and a stable external id so
# a re-run UPDATES the same check instead of accumulating duplicates.
NAME = 'gate freshness'
EXTERNAL_ID = 'daedalus-gate-freshness/v1'
APP_SLUG = 'github-actions'
BASE_BRANCH = 'main'

# THE SET, in the order of the justification above. The order is only for
# readability; every entry is checked against every head.
GATE_PATTERNS = (
    '.github/workflows/**',
    '.github/ci-thresholds.json',
    'scripts/ci/**',
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

_HEX40 = frozenset('0123456789abcdef')

# Per-head worst case that is not a compare: 1 revalidation + 1 check-runs
# listing + 1 revalidation + 1 write. Each gate commit adds up to 2 compares.
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

    Only the two shapes the set uses are implemented: a `/**` suffix (a
    directory prefix) and an exact path. A path is a repository-relative
    POSIX path as git records it, so it never carries a `..` component.
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

    One request per path. A path with no commit yet contributes nothing -- the
    API returning `[]` is a real answer, not a failure. Returns None when a
    lookup cannot be read at all (a GLOBAL failure).
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
        entries = payload if isinstance(payload, list) else []
        if not entries:
            continue
        sha = entries[0].get('sha') if isinstance(entries[0], dict) else None
        if not _hex40(sha):
            return None
        gates.append((pattern, sha))
    return gates


def merge_base(read, repository, gate, head):
    """The merge base of `gate` and `head`, or None if unreadable.

    Returns (merge_base, attempts) where each attempt is (request, outcome).
    Tries the primary `<head_sha>` spelling, then the `owner:ref` spelling
    only if the primary failed. A merge base that is not 40 lowercase hex is
    refused (a shape guard, distinct from the verdict).
    """
    attempts = []
    specs = [head['sha']]
    if head.get('owner') and head.get('ref'):
        specs.append(f"{head['owner']}:{head['ref']}")
    for spec in specs:
        target = f'repos/{repository}/compare/{gate}...{spec}'
        try:
            payload = _one(read, [
                'gh', 'api', '-H', 'Cache-Control: no-cache', target])
        except QueryError as error:
            attempts.append((target, f'failed: {error}'))
            continue
        sha = None
        if isinstance(payload, dict):
            base = payload.get('merge_base_commit')
            if isinstance(base, dict):
                sha = base.get('sha')
        if _hex40(sha):
            attempts.append((target, f'merge base {sha}'))
            return sha, attempts
        attempts.append((target, f'merge base {sha!r} is not 40 hex'))
    return None, attempts


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
            + head_count * (PER_HEAD_OVERHEAD + 2 * gate_count))


def process(read, repository, heads, gates, details_url, call_budget=None,
            dry_run=False):
    """Publish a verdict for each head. Returns (exit_code, published).

    `dry_run` computes and reports every verdict but publishes nothing, so the
    decision can be exercised over the real pull requests without writing a
    check run to any of them.
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
            publish(read, repository, head['sha'], verdict.conclusion,
                    verdict.title, verdict.summary, details_url)
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


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    dry_run = '--dry-run' in arguments
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    event = _event()
    name = os.environ.get('GITHUB_EVENT_NAME', '')
    if name == 'pull_request_target':
        pr = event.get('pull_request') or {}
        heads = select_heads([pr]) if pr else []
    else:
        try:
            pulls = _open_pulls(gh_read, repository)
        except QueryError as error:
            print('gate freshness: could not read the open pull request list; '
                  f'publishing nothing: {error}', file=sys.stderr)
            return 1
        heads = select_heads(pulls if isinstance(pulls, list) else [])
    gates = enumerate_gates(gh_read, repository)
    if gates is None:
        print('gate freshness: could not read a gate commit on main; '
              'publishing nothing', file=sys.stderr)
        return 1
    code, published = process(gh_read, repository, heads, gates,
                              _details_url(), _call_budget(), dry_run)
    verb = 'would publish' if dry_run else 'published'
    print(f'gate freshness: {verb} {len(published)} verdict(s) for '
          f'{len(heads)} open pull request(s) based on main')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
