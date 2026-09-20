#!/usr/bin/env python3
"""The upstream release verifier for the actions/cache pins.

scripts/ci/cache_action_releases.py is the online half of the cache-pin
guard: the offline suites pin every cached job to REVIEWED_CACHE_RELEASES,
and this script is what makes that mapping true, by resolving each pinned
release through the GitHub API. These tests drive its pure core through the
injected `run` seam with bodies captured from the real endpoint, and one
test spans the three modules to prove the scanner sees the same pins the
offline guard sees.
"""
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from test_ci_pip_cache import _CACHE_JOBS  # noqa: E402
from test_workflow_cache_boundary import REVIEWED_CACHE_RELEASES  # noqa: E402

V610 = '55cc8345863c7cc4c66a329aec7e433d2d1c52a9'
V430 = '0057852bfaa89a56745cba8c7296529d2fc39830'
# python/cpython's v3.13.0 is an annotated tag: the ref names a tag object,
# which names the commit.
CPYTHON_TAG = '3f27099d916c7b885e3daf1fabedcc119462014d'
CPYTHON_COMMIT = '60403a5409ff2c3f3b07dd2ca91a7a3e096839c7'

_GH_API = ['gh', 'api', '-H', 'Cache-Control: no-cache']
_REF = 'repos/actions/cache/git/ref/tags/'
_TAG = 'repos/actions/cache/git/tags/'

# Bodies as `gh api` returned them from the live endpoint, less their `url`
# fields, which name API hosts the release scanner refuses in the tree.
_LIGHTWEIGHT = {
    'ref': 'refs/tags/v6.1.0',
    'node_id': 'MDM6UmVmMjE1NTY2NDYyOnJlZnMvdGFncy92Ni4xLjA=',
    'object': {'sha': V610, 'type': 'commit'},
}
_ANNOTATED_REF = {
    'ref': 'refs/tags/v3.13.0',
    'node_id': 'MDM6UmVmODE1OTg5NjE6cmVmcy90YWdzL3YzLjEzLjA=',
    'object': {'sha': CPYTHON_TAG, 'type': 'tag'},
}
_ANNOTATED_TAG = {
    'node_id': 'TA_kwDOBN0Z8doAKDNmMjcwOTlkOTE2YzdiODg1ZTNkYWYxZmFiZWRjYzEx'
               'OTQ2MjAxNGQ',
    'sha': CPYTHON_TAG,
    'tagger': {'name': 'Thomas Wouters', 'email': 'thomas@python.org',
               'date': '2024-10-07T05:02:14Z'},
    'object': {'sha': CPYTHON_COMMIT, 'type': 'commit'},
    'tag': 'v3.13.0',
    'message': 'Python 3.13.0\n',
}
_NOT_FOUND = json.dumps({
    'message': 'Not Found',
    'documentation_url': 'docs: rest/git/refs#get-a-reference',
    'status': '404',
})
_NOT_FOUND_STDERR = 'gh: Not Found (HTTP 404)\n'

_PLAIN = (
    'jobs:\n'
    '  one:\n'
    '    steps:\n'
    '      - name: Restore\n'
    f'        uses: actions/cache/restore@{V610}  # v6.1.0\n'
    f'      - uses: Actions/Cache@{V610}  # v6.1.0\n'
    '      - uses: >-\n'
    '          actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n'
    '      - name: Save\n'
    '        uses: >-\n'
    f'          actions/cache/SAVE@{V610}  # v6.1.0\n'
)


def _verifier():
    return _util.load(
        ROOT / 'scripts' / 'ci' / 'cache_action_releases.py',
        'cache_releases_mod')


def _workflow(tmp, text, name='tests.yml'):
    directory = Path(tmp) / '.github' / 'workflows'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding='utf-8')
    return Path(tmp)


def _refusing_run(argv):
    raise AssertionError(f'a request was made: {argv}')


def _upstream(responses):
    """A `run` double answering `gh api` by path, 404 for the rest.

    `responses` maps a request path to a body (a mapping, serialized, or
    a string handed back as-is). A path it lacks raises exactly what
    `check=True` raises for the real 404: the JSON error body on stdout,
    the one-line summary on stderr, exit status 1.
    """
    calls = []

    def run(argv):
        calls.append(argv)
        assert argv[:4] == _GH_API and len(argv) == 5, argv
        if argv[4] not in responses:
            raise subprocess.CalledProcessError(
                1, argv, output=_NOT_FOUND, stderr=_NOT_FOUND_STDERR)
        body = responses[argv[4]]
        return body if isinstance(body, str) else json.dumps(body)

    return calls, run


def test_zero_workflow_files_is_a_refusal_not_a_clean_run(tmp):
    mod = _verifier()
    verified, refusals = mod.verify(Path(tmp), _refusing_run)
    assert verified == [], verified
    assert refusals == [
        f'no workflow files under {Path(tmp) / ".github" / "workflows"}'
    ], refusals


def test_every_cache_family_pin_shape_is_recognised(tmp):
    """Plain, list-item and folded `uses:`, any case, all three actions."""
    mod = _verifier()
    root = _workflow(tmp, _PLAIN)
    pins = mod.scan(root)
    assert pins == [
        ('.github/workflows/tests.yml', 5, 'actions/cache/restore', V610,
         'v6.1.0'),
        ('.github/workflows/tests.yml', 6, 'Actions/Cache', V610, 'v6.1.0'),
        ('.github/workflows/tests.yml', 11, 'actions/cache/SAVE', V610,
         'v6.1.0'),
    ], pins


def _one_pin(tmp, uses):
    return _workflow(tmp, f'jobs:\n  j:\n    steps:\n      - uses: {uses}\n')


def test_a_ref_that_is_not_a_full_lowercase_commit_is_refused(tmp):
    mod = _verifier()
    for ref in ('v4', V610[:-1], V610.upper(), 'main'):
        root = _one_pin(tmp, f'actions/cache@{ref}  # v6.1.0')
        verified, refusals = mod.verify(root, _refusing_run)
        assert verified == [], (ref, verified)
        assert refusals == [
            f'.github/workflows/tests.yml:4: actions/cache@{ref} is not '
            'pinned to a 40-hex commit'], (ref, refusals)


def test_a_pin_without_a_release_comment_is_refused(tmp):
    mod = _verifier()
    root = _one_pin(tmp, f'actions/cache/restore@{V610}')
    _verified, refusals = mod.verify(root, _refusing_run)
    assert refusals == [
        f'.github/workflows/tests.yml:4: actions/cache/restore@{V610} '
        'carries no release comment'], refusals


def test_a_comment_that_is_not_a_full_release_is_refused(tmp):
    mod = _verifier()
    for comment in ('v6', '6.1.0', 'v6.1.0-rc1', 'v6.1', 'version v6.1.0'):
        root = _one_pin(tmp, f'actions/cache/save@{V610}  # {comment}')
        _verified, refusals = mod.verify(root, _refusing_run)
        assert refusals == [
            f'.github/workflows/tests.yml:4: release comment {comment!r} '
            'is not vX.Y.Z'], (comment, refusals)


def test_one_malformed_pin_stops_every_request(tmp):
    """A refused shape never reaches a request path, and no healthy pin
    beside it is resolved either: the tree is refused as a whole."""
    mod = _verifier()
    root = _workflow(tmp, _PLAIN.replace(
        f'actions/cache/SAVE@{V610}  # v6.1.0', f'actions/cache/SAVE@{V610}'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        f'.github/workflows/tests.yml:11: actions/cache/SAVE@{V610} '
        'carries no release comment'], refusals


def _cache_pin(sha, tag, action='actions/cache'):
    return f'{action}@{sha}  # {tag}'


def test_a_lightweight_tag_verifies_the_commit_it_names(tmp):
    """One request per distinct (ref, tag) pair, however many pins."""
    mod = _verifier()
    root = _workflow(tmp, _PLAIN)
    calls, run = _upstream({_REF + 'v6.1.0': _LIGHTWEIGHT})
    verified, refusals = mod.verify(root, run)
    assert refusals == [], refusals
    assert verified == [f'{V610} v6.1.0 3 pin(s)'], verified
    assert calls == [_GH_API + [_REF + 'v6.1.0']], calls


def test_an_annotated_tag_is_dereferenced_exactly_once(tmp):
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin(CPYTHON_COMMIT, 'v3.13.0'))
    calls, run = _upstream({_REF + 'v3.13.0': _ANNOTATED_REF,
                            _TAG + CPYTHON_TAG: _ANNOTATED_TAG})
    verified, refusals = mod.verify(root, run)
    assert refusals == [], refusals
    assert verified == [f'{CPYTHON_COMMIT} v3.13.0 1 pin(s)'], verified
    assert calls == [_GH_API + [_REF + 'v3.13.0'],
                     _GH_API + [_TAG + CPYTHON_TAG]], calls


def test_a_tag_object_that_names_no_commit_is_refused(tmp):
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin(CPYTHON_COMMIT, 'v3.13.0'))
    tag = dict(_ANNOTATED_TAG, object={'sha': CPYTHON_TAG, 'type': 'tag'})
    calls, run = _upstream({_REF + 'v3.13.0': _ANNOTATED_REF,
                            _TAG + CPYTHON_TAG: tag})
    verified, refusals = mod.verify(root, run)
    assert verified == [], verified
    assert refusals == [
        f"v3.13.0: tag object {CPYTHON_TAG} names a 'tag', not a commit"
    ], refusals
    assert len(calls) == 2, calls


def test_a_release_that_does_not_exist_is_refused_with_the_error(tmp):
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin('a' * 40, 'v99.99.99'))
    _calls, run = _upstream({})
    verified, refusals = mod.verify(root, run)
    assert verified == [], verified
    assert refusals == [
        'v99.99.99: gh api exited 1: gh: Not Found (HTTP 404)'], refusals


def test_a_commit_under_the_wrong_release_is_refused_with_upstream(tmp):
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin(V430, 'v6.1.0', 'actions/cache/save'))
    _calls, run = _upstream({_REF + 'v6.1.0': _LIGHTWEIGHT})
    verified, refusals = mod.verify(root, run)
    assert verified == [], verified
    assert refusals == [
        f'v6.1.0: upstream is {V610}, pinned {V430} at '
        '.github/workflows/tests.yml:4'], refusals


def test_a_response_that_is_not_json_is_refused(tmp):
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin(V610, 'v6.1.0'))
    _calls, run = _upstream({_REF + 'v6.1.0': '<html>'})
    verified, refusals = mod.verify(root, run)
    assert verified == [], verified
    assert refusals == ["v6.1.0: response is not JSON: '<html>'"], refusals


def test_a_response_without_the_ref_fields_is_refused(tmp):
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin(V610, 'v6.1.0'))
    for body in ([], {'object': {'sha': V610}},
                 {'object': {'sha': V610[:-1], 'type': 'commit'}},
                 {'object': {'sha': V610, 'type': 'blob'}}):
        _calls, run = _upstream({_REF + 'v6.1.0': body})
        verified, refusals = mod.verify(root, run)
        assert verified == [], (body, verified)
        assert refusals == [
            f'v6.1.0: response names no commit or tag object: {body!r}'
        ], (body, refusals)


def test_every_resolution_refusal_is_reported(tmp):
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        f'      - uses: {_cache_pin(V430, "v6.1.0")}\n'
        f'      - uses: {_cache_pin(V610, "v6.1.0")}\n'
        f'      - uses: {_cache_pin(V430, "v99.99.99")}\n'))
    calls, run = _upstream({_REF + 'v6.1.0': _LIGHTWEIGHT})
    verified, refusals = mod.verify(root, run)
    assert verified == [f'{V610} v6.1.0 1 pin(s)'], verified
    assert refusals == [
        f'v6.1.0: upstream is {V610}, pinned {V430} at '
        '.github/workflows/tests.yml:4',
        'v99.99.99: gh api exited 1: gh: Not Found (HTTP 404)'], refusals
    assert len(calls) == 3, calls


def _main(mod, root, stdout):
    """Run main() against `root` with subprocess.run answering `stdout`."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(subprocess, 'run') as run:
        run.return_value = subprocess.CompletedProcess([], 0, stdout, '')
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = mod.main([str(root)])
    return status, out.getvalue(), err.getvalue(), run.call_args_list


def test_main_binds_gh_api_with_check_and_a_timeout(tmp):
    """main() runs the real `gh` with the seam every test above drove:
    captured text, check=True (so a 404 arrives as CalledProcessError),
    and a bound so a hung API fails the step rather than holding it."""
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin(V610, 'v6.1.0'))
    status, out, err, calls = _main(mod, root, json.dumps(_LIGHTWEIGHT))
    assert status == 0, (status, err)
    assert out == f'{V610} v6.1.0 1 pin(s)\n', out
    assert err == '', err
    assert calls == [mock.call(
        [*_GH_API, _REF + 'v6.1.0'], capture_output=True, text=True,
        check=True, timeout=60)], calls


def test_main_reports_every_refusal_and_exits_nonzero(tmp):
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        f'      - uses: actions/cache@{V610}\n'
        '      - uses: actions/cache/restore@v4  # v4.3.0\n'))
    status, out, err, calls = _main(mod, root, json.dumps(_LIGHTWEIGHT))
    assert status == 1, status
    assert out == '', out
    assert err == (
        f'.github/workflows/tests.yml:4: actions/cache@{V610} carries no '
        'release comment\n'
        '.github/workflows/tests.yml:5: actions/cache/restore@v4 is not '
        'pinned to a 40-hex commit\n'), err
    assert calls == [], calls


def test_the_real_tree_pins_are_the_ones_the_offline_guard_reviews(tmp):
    """The scanner sees exactly the pins test_ci_pip_cache pins, and they
    name only reviewed releases: the assertion that spans the three
    modules, so the online check cannot quietly verify a different set."""
    del tmp
    mod = _verifier()
    pins = mod.scan(ROOT)
    assert len(pins) == 2 * len(_CACHE_JOBS), pins
    assert all(mod.shape_refusal(pin) is None for pin in pins), pins
    pairs = {(pin.ref, pin.comment) for pin in pins}
    assert pairs <= set(REVIEWED_CACHE_RELEASES.items()), pairs


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cacherel_')


if __name__ == '__main__':
    raise SystemExit(main())
