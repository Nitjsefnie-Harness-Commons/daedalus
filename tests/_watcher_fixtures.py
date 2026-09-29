"""The pull request, branch and answers every watcher-budget suite measures.

One idle pull request, answerable by this tree's watchers and by the base
commit's, is what makes the before/after comparison one method rather than
two: a base watcher answers from the REST surfaces and spends four and two
requests per poll, as it did. A suite that built its own copy of these
answers would be measuring a different subject from the bounds beside it,
so the answers live here and both suites take them from here.

Not a suite itself - `run_tests.py` only loads `test_*.py`.
"""
import json

PR = '195'
BRANCH = 'issue-997'
SHA = 'a' * 40

# The interval the measured watchers poll at, and what the hourly figure
# divides by. Nothing in the measurement itself reads a clock.
TICK = 2
# What one idle poll of either watcher may spend. Named so every doubled-poll
# control is checked against the bound the idle controls enforce.
IDLE_POLL_BOUND = 1
STAMP = '%Y-%m-%dT%H:%M:%SZ'
# What `gh` writes to stderr when it refuses a throttled query, and what
# the GraphQL entry beside it says. One wording, two carriers, so a
# control can separate them.
THROTTLED = 'API rate limit already exceeded for user ID 1.'


def page_info(has_next=False, cursor=None):
    return {'hasNextPage': has_next, 'endCursor': cursor}


def author(login):
    return {'login': login}


def pr_page(reviews=(), conversation=(), state='OPEN', is_draft=False,
            merged_at=None):
    """One page of the single query the comment watcher now makes."""
    return {'data': {'repository': {'pullRequest': {
        'state': state, 'isDraft': is_draft, 'mergedAt': merged_at,
        'reviews': {'pageInfo': page_info(), 'nodes': list(reviews)},
        'comments': {'pageInfo': page_info(),
                     'nodes': list(conversation)}}}}}


def review(rid, body='LGTM', comments=()):
    return {'databaseId': rid, 'body': body, 'state': 'APPROVED',
            'submittedAt': '2026-09-20T10:00:00Z', 'author': author('alice'),
            'comments': {'pageInfo': page_info(), 'nodes': list(comments)}}


def comment(rid, body='looks good', inline=False):
    node = {'databaseId': rid, 'body': body,
            'createdAt': '2026-09-20T10:01:00Z',
            'updatedAt': '2026-09-20T10:01:00Z', 'author': author('bob')}
    if inline:
        node['path'] = 'daedalus_bridge/result_store.py'
        node['line'] = 12
    return node


def ci_page(checks=(), sha=SHA):
    """One page of the single query the CI watcher now makes."""
    return {'data': {'repository': {'ref': {'target': {
        'oid': sha,
        'statusCheckRollup': {'contexts': {
            'pageInfo': page_info(),
            'nodes': [{'__typename': 'CheckRun', 'databaseId': node['id'],
                       'name': node['name'],
                       'conclusion': node['conclusion'],
                       'detailsUrl': node['url']} for node in checks]}}}}}}}


def check(rid, name, conclusion='SUCCESS'):
    return {'id': rid, 'name': name, 'conclusion': conclusion,
            'url': f'https://github.com/o/r/runs/{rid}'}


def runs_page(suites=()):
    """One page of the single query the wait and the hold now make."""
    return {'data': {'repository': {'object': {'checkSuites': {
        'pageInfo': page_info(), 'nodes': list(suites)}}}}}


def suite(rid, conclusion='SUCCESS', status='COMPLETED', workflow=11,
          started='2026-09-20T10:00:00Z', name=None):
    """One check suite of a workflow run, as the live schema reports it.

    `name` overrides the workflow's own name, which is how a fixture
    carries the gating workflow: since issue 1217 a run set with no run of
    it is an INCOMPLETE set, not a settled one, so a fixture standing in
    for a head whose matrix ran has to name that workflow or it is
    exercising a different state than it did before.
    """
    return {'status': status, 'conclusion': conclusion, 'createdAt': started,
            'workflowRun': {
                'databaseId': rid, 'createdAt': started,
                'url': f'https://github.com/o/r/actions/runs/{rid}',
                'file': {'path': '.github/workflows/ci.yml'},
                'workflow': {'databaseId': workflow,
                             'name': name or f'workflow {workflow}'}}}


def refusal_response(status=403, headers=None,
                     body='API rate limit exceeded.'):
    return {'status': status, 'headers': headers or {}, 'body': body}


def rate_limited_error(reset_at=None, retry_after=None):
    rate = {}
    if reset_at:
        rate['resetAt'] = reset_at
    if retry_after:
        rate['retryAfter'] = retry_after
    return {'status': 200, 'body': {'data': None, 'errors': [
        {'type': 'RATE_LIMITED', 'message': 'API rate limit exceeded.',
         'extensions': {'rateLimit': rate}}]}}


def spent_headers(reset_epoch, resource='graphql'):
    """The rate-limit headers a throttled answer carries, and nothing else."""
    return {'X-RateLimit-Limit': '5000', 'X-Ratelimit-Remaining': '0',
            'X-Ratelimit-Reset': str(reset_epoch),
            'X-Ratelimit-Resource': resource}


def spent_limit_response(reset_epoch, body=None, exit=1, stderr=None):
    """A refusal whose ONLY evidence is the spent rate-limit headers.

    Beside `refusal_response`, and the shape GitHub really sends for a
    throttled query (issue 1338): a status the transport is satisfied
    with, `X-Ratelimit-Remaining: 0` beside the reset it counts down to,
    and a body naming no limit at all. A reader that believes only the
    body, or that raises over the exit code before reading anything, does
    not see this one.

    `body` is whatever the transport managed to write, `None` for a JSON
    answer with no data in it; `stderr` is `gh`'s own complaint, and
    leaving it out models a run that exits nonzero in silence.
    """
    return {'status': 200, 'headers': spent_headers(reset_epoch),
            'body': {'data': None} if body is None else body,
            'exit': exit, 'stderr': stderr}


def throttled_query(reset_epoch=None, reset_at=None, exit=1, stderr=None,
                    kind: str | None = 'RATE_LIMIT',
                    code: str | None = 'graphql_rate_limit',
                    message=THROTTLED):
    """The 200 GitHub answers a throttled GraphQL query with.

    Beside `rate_limited_error`, and the refusal captured on 2026-09-29
    with `gh api -i graphql` (issue 1338): the transport succeeds, the
    entry names the limit in a `type` of `RATE_LIMIT` and a `code` of
    `graphql_rate_limit`, and `gh` exits 1 with the message on stderr.

    Every axis is separable, which is what lets a control for one of them
    be a control rather than a second copy of the same evidence: no
    `reset_epoch` leaves the header axis out, `kind`/`code` of `None`
    leave the body axis out, `stderr=''` leaves the complaint out and
    `exit=0` the exit code. What is left is the shape a reader believing
    only the named carriers can see, and nothing else.
    """
    rate = {}
    if reset_at:
        rate['resetAt'] = reset_at
    error = {'message': message, 'extensions': {'rateLimit': rate}}
    if kind is not None:
        error['type'] = kind
    if code is not None:
        error['code'] = code
    return {'status': 200, 'exit': exit, 'stderr': stderr,
            'headers': spent_headers(reset_epoch) if reset_epoch else {},
            'body': {'data': None, 'errors': [error]}}


def base_answers():
    """Answers for the REST surfaces the base watchers read; the specific
    paths come first, `pulls/195` alone matching all three comment surfaces.
    """
    return {
        'pulls/195/reviews': json.dumps(
            [{'id': 1, 'body': 'LGTM', 'user': {'login': 'alice'},
              'submitted_at': '2026-09-20T10:00:00Z'}]),
        'pulls/195/comments': json.dumps([]),
        'issues/195/comments': json.dumps([]),
        'pulls/195': json.dumps({'merged_at': None, 'state': 'open',
                                 'draft': False}),
        f'branches/{BRANCH}': json.dumps({'commit': {'sha': SHA}}),
        'check-runs': json.dumps({'check_runs': [
            {'id': 1, 'name': 'pylint', 'conclusion': 'success',
             'html_url': 'https://github.com/o/r/runs/1'}]}),
        'actions/runs': json.dumps({'workflow_runs': [
            {'id': 1, 'name': 'run 1', 'status': 'completed',
             'conclusion': 'success',
             'run_started_at': '2026-09-20T10:00:00Z',
             'workflow_id': 11,
             'html_url': 'https://github.com/o/r/actions/runs/1'}]}),
    }


def idle_answers():
    """The one idle pull request every budget suite measures against."""
    return {
        'reviews(first: 100': pr_page(),
        'statusCheckRollup': ci_page([check(1, 'pylint')]),
        'checkSuites': runs_page([suite(1, name='tests')]),
        **base_answers(),
    }
