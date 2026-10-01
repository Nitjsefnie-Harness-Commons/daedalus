"""The open pull requests whose head is one commit SHA.

Its own module rather than another query in `gh_client.py`, which measured
490 lines at the commit this branch starts from against the 500-line
production ceiling the size policy enforces: ten lines of headroom, and the
query and its two filters are more than ten lines. The whole of a subject
lives here, and `gh_client` stays the transport it already is for every
watcher in this directory. That ceiling is why reading a refusal is
`gh_rate_limit`'s subject and not this one's (issue 1338): one reader for
the evidence is the thing worth protecting, and it does not fit beside the
request that produces it.

Both filters are load-bearing, and `origin/main` is why. That tip is an
ancestor of the head of a branch whose pull request has been merged, so the
API answers it with that MERGED pull request; and a branch is merged while
its own earlier commits are still queried. Unfiltered, every commit on the
line to a merged head looks like an open pull request of its own, which is
how a wait on `main` would come to read as a pull request that blocks it.
No control in this tree exercises that, and the pull request body records
what a broken filter costs when it is measured: a wrong refusal naming the
wrong pull request, never a green.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_client  # noqa: E402

HEAD_PULL_REQUESTS_QUERY = '''query HeadPullRequests(
    $owner: String!, $name: String!, $sha: GitObjectID!) {
  repository(owner: $owner, name: $name) {
    object(oid: $sha) { ... on Commit {
      associatedPullRequests(first: 20) { nodes {
        number state mergeable mergeStateStatus headRefOid } } } } } }'''


def head_pull_requests(owner, name, sha):
    """The open pull requests whose head is exactly this SHA.

    A SHA the repository does not have answers with a null object, which is
    a head with no pull request rather than a failed query: `ci_wait.py`
    reads that as no pull request and hands the head to its grace.

    The all-zero OID is the one input that does not, and it is the one
    input a caller cannot have meant: GitHub answers it with `data: null`
    and no errors array, which is a body `gh_client.graphql` refuses as a
    failed read. A caller that supplied it gets a `QueryError`, which
    `ci_wait.py` reports once and then carries on to its grace.
    """
    page = gh_client.graphql(HEAD_PULL_REQUESTS_QUERY,
                             {'owner': owner, 'name': name, 'sha': sha})
    found = gh_client.nodes(
        page, ('repository', 'object', 'associatedPullRequests'))
    return [pull for pull in found
            if pull.get('headRefOid') == sha and pull.get('state') == 'OPEN']
