#!/usr/bin/env python3
"""The head's pull requests, and the two filters that decide what is one.

`gh_head_prs.py` answers a question only `ci_wait.py` asks, so its controls
live beside it rather than in the suite that asks. Split out of
`tests/test_ci_wait_gate.py` when that file reached the 700-line test
ceiling; `scripts/ci/size_baseline.py`'s remedy for a file over its ceiling
is to relocate, not to raise the number.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402

SOURCE = (_util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
          / 'gh_head_prs.py')


def _head_prs():
    return _util.load(SOURCE, 'gh_head_prs_contract')


def _pr_page(pull_requests, null_object=False):
    """One page of the associated-pull-requests query."""
    obj = (None if null_object
           else {'associatedPullRequests': {'nodes': list(pull_requests)}})
    return {'data': {'repository': {'object': obj}}}


def _pull(number, state='OPEN', mergeable='MERGEABLE',
          merge_state='CLEAN', head='a' * 40):
    return {'number': number, 'state': state, 'mergeable': mergeable,
            'mergeStateStatus': merge_state, 'headRefOid': head}


def test_a_merged_pull_request_of_another_head_is_not_this_heads(tmp):
    """Issue 1217: the origin/main tip is an ancestor of a merged branch's
    head, and the API answers that tip with that MERGED pull request. Both
    filters are load-bearing - headRefOid for the ancestor, state for the
    pull request that is already merged - and unfiltered every ancestor of
    a merged branch would look like an open pull request of its own.

    The third fixture is the one the other two could not hold: it is this
    head's own pull request, already merged, and it differs from an accepted
    one in the state limb alone. The first two vary state and headRefOid
    together, so the headRefOid clause answers both and nothing would say
    the state clause is there at all.
    """
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([
        _pull(1139, state='MERGED', head='f' * 40)])})
    with fake.activate():
        assert mod.head_pull_requests('o', 'r', 'a' * 40) == []
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([
        _pull(1139, state='OPEN', head='f' * 40)])})
    with fake.activate():
        assert mod.head_pull_requests('o', 'r', 'a' * 40) == []
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([
        _pull(1122, state='MERGED')])})
    with fake.activate():
        assert mod.head_pull_requests('o', 'r', 'a' * 40) == []


def test_the_open_pull_request_of_the_head_is_answered(tmp):
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([
        _pull(1122), _pull(1139, state='MERGED', head='f' * 40)])})
    with fake.activate():
        found = mod.head_pull_requests('o', 'r', 'a' * 40)
    assert [pull['number'] for pull in found] == [1122]
    payload = json.loads(fake.calls()[0]['request'])
    assert payload['variables']['sha'] == 'a' * 40, payload['variables']


def test_a_commit_with_no_pull_request_reads_as_none(tmp):
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page([])})
    with fake.activate():
        assert mod.head_pull_requests('o', 'r', 'a' * 40) == []


def test_an_unknown_sha_reads_as_no_pull_request(tmp):
    """A SHA the repository does not have answers with a null object, which
    is a head with no pull request rather than a failed query.

    A non-zero OID on purpose. Measured against this repository, an
    unresolvable OID of any shape answers `data.repository.object: null` -
    which is the body pinned here - and the all-zero OID is the one input
    that does not: it answers `data: null` with no errors array, which the
    next control pins instead. Naming it here would have made this control
    assert a shape its own input never produces.
    """
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {'associatedPullRequests': _pr_page(
        [], null_object=True)})
    with fake.activate():
        assert mod.head_pull_requests(
            'o', 'r', 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef') == []


def test_the_all_zero_oid_is_a_failed_query_rather_than_an_empty_answer(tmp):
    """The one unknown SHA that does not come back as a null object.

    GitHub answers the all-zero OID with `data: null` and no errors array,
    and a body carrying no data is a failed read as far as `gh_client` is
    concerned - the same answer a truncated or errored response gets. It is
    a `QueryError` and not an empty list, because a caller that supplied
    the all-zero OID is owed a refusal rather than a confident `[]`: the
    wait reports it once and carries on to its grace, and a wrong answer
    here would tell it the head has no pull request.
    """
    mod = _head_prs()
    fake = _fake_gh.FakeGh(tmp, {
        'associatedPullRequests': {'status': 200, 'body': {'data': None}}})
    with fake.activate():
        try:
            mod.head_pull_requests('o', 'r', '0' * 40)
        except mod.gh_client.QueryError as failure:
            assert 'no data' in str(failure), failure
        else:
            raise AssertionError(
                'a body carrying no data must fail the read, not answer []')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='headprs_')


if __name__ == '__main__':
    raise SystemExit(main())
