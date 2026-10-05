"""The run-shaped answers `test_ci_wait_published.py` measures against.

That suite drives the real `ci_wait.py` against the fake `gh` in
`_fake_gh.py`, and both halves of its question - a run list that is
acceptable, and a published check run that is not - are asked of
answers built here rather than at each call site, so the two cannot
drift apart into two subjects. `RUNS_QUERY` is the selection the
answers are keyed on, `page_info` the pagination shape `runs_page`
writes them with; `test_gh_rate_limit.py` consumes both as "an answer
that delivered". Not a suite; `run_tests.py` loads `test_*.py` only.
"""

SHA = 'a' * 40


def page_info(has_next=False, cursor=None):
    return {'hasNextPage': has_next, 'endCursor': cursor}

# The fragment a runs answer is keyed on, and the reason it is NOT
# `checkSuites`: `_fake_gh` answers whichever fragment the request
# carries, so keying on the connection answers a query that asks for
# no check runs at all. Keyed on the SELECTION, a query with the
# `checkRuns` clause deleted finds no fixture and is refused - the only
# thing that authenticates the query text, the change's own central
# regression being a query that asks for less.


RUNS_QUERY = 'checkRuns(first: 100)'


def runs_page(suites=()):
    """One page of the one query `ci_wait.py` makes on a head."""
    return {'data': {'repository': {'object': {'checkSuites': {
        'pageInfo': page_info(), 'nodes': list(suites)}}}}}


def suite(rid, conclusion: str | None = 'SUCCESS', status='COMPLETED',
          workflow: int | None = 11, started='2026-09-20T10:00:00Z',
          name=None, check_runs=None):
    """One check suite of a workflow run, as the live schema reports it.
    `name` overrides the workflow's own name, how a fixture carries the
    gating workflow: since issue 1217 a run set with no run of it is an
    INCOMPLETE set, not a settled one. `check_runs` are the suite's own
    check runs; `workflow=None` leaves the suite with no `workflowRun`
    at all, which is what a suite published through the Checks API
    arrives in."""
    # Annotated, because the suite gains a `workflowRun` and a
    # `checkRuns` it does not start with: inferred from the three
    # string keys it would be a `dict[str, str]` and both of those
    # assignments would be a type error rather than a value.
    node: dict[str, object] = {
        'status': status, 'conclusion': conclusion, 'createdAt': started}
    if workflow is not None:
        node['workflowRun'] = {
            'databaseId': rid, 'createdAt': started,
            'url': f'https://github.com/o/r/actions/runs/{rid}',
            'file': {'path': '.github/workflows/ci.yml'},
            'workflow': {'databaseId': workflow,
                         'name': name or f'workflow {workflow}'}}
    if check_runs is not None:
        node['checkRuns'] = {'nodes': list(check_runs)}
    return node
