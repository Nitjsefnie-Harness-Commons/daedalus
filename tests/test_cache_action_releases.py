#!/usr/bin/env python3
"""The upstream release verifier for the actions/cache pins.

These tests drive scripts/ci/cache_action_releases.py through its injected
`run` seam with bodies captured from the real endpoint; one test spans the
three modules so the scanner sees the pins the offline guard reviews.
"""
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(
    1, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from test_ci_pip_cache import _CACHE_JOBS  # noqa: E402
from test_workflow_cache_boundary import REVIEWED_CACHE_RELEASES  # noqa: E402

V610 = '55cc8345863c7cc4c66a329aec7e433d2d1c52a9'
V430 = '0057852bfaa89a56745cba8c7296529d2fc39830'
# python/cpython's v3.13.0 is an annotated tag; actions/cache has none.
CPYTHON_TAG = '3f27099d916c7b885e3daf1fabedcc119462014d'
CPYTHON_COMMIT = '60403a5409ff2c3f3b07dd2ca91a7a3e096839c7'

_GH_API = ['gh', 'api', '-H', 'Cache-Control: no-cache']
_REF = 'repos/actions/cache/git/ref/tags/'
_TAG = 'repos/actions/cache/git/tags/'

# Bodies as `gh api` returned them from the live endpoint, less the `url`
# fields (they name API hosts the release scanner refuses in the tree) and,
# on the tag object, the PGP signature and `verification` block.
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
_NOT_FOUND = json.dumps({'message': 'Not Found', 'status': '404'})
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
    '        uses: >-  # v6.1.0\n'
    f'          actions/cache/SAVE@{V610}\n'
    f"      - uses: 'actions/cache/restore@{V610}'  # v6.1.0\n"
    f'      - uses: "actions/cache/save@{V610}"  # v6.1.0\n'
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
    """A `run` double answering `gh api` by path; an unknown path raises
    what `check=True` raised for the real 404."""
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
    """Plain, list-item and folded `uses:`, any case, all three actions;
    a folded scalar's comment can only sit on its header line."""
    mod = _verifier()
    root = _workflow(tmp, _PLAIN)
    pins, refusals = mod.scan(root)
    assert refusals == [], refusals
    assert pins == [
        ('.github/workflows/tests.yml', 5, 'actions/cache/restore', V610,
         'v6.1.0'),
        ('.github/workflows/tests.yml', 6, 'Actions/Cache', V610, 'v6.1.0'),
        ('.github/workflows/tests.yml', 10, 'actions/cache/SAVE', V610,
         'v6.1.0'),
        ('.github/workflows/tests.yml', 12, 'actions/cache/restore', V610,
         'v6.1.0'),
        ('.github/workflows/tests.yml', 13, 'actions/cache/save', V610,
         'v6.1.0'),
    ], pins


def test_a_comment_inside_a_folded_scalar_is_content_and_refused(tmp):
    """Inside a block scalar `#` is content: YAML hands GitHub the whole
    line as the reference."""
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        '      - uses: >-\n'
        f'          actions/cache/save@{V610}  # v6.1.0\n'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        '.github/workflows/tests.yml:4: uses value cannot be classified: '
        f"'>-' then 'actions/cache/save@{V610}  # v6.1.0'"], refusals


def test_a_folded_scalar_that_keeps_going_is_refused(tmp):
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        '      - uses: >-  # v6.1.0\n'
        f'          actions/cache/save@{V610}\n'
        '          and-more\n'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        '.github/workflows/tests.yml:4: uses value cannot be classified: '
        f"'>-  # v6.1.0' then 'actions/cache/save@{V610}'"], refusals


def test_a_pin_continued_from_an_empty_uses_line_is_refused(tmp):
    """`uses:` then a deeper line is one plain scalar to YAML."""
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        '      - uses:\n'
        f'          actions/cache@{V610}  # v6.1.0\n'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        '.github/workflows/tests.yml:4: uses value cannot be classified: '
        f"'' then 'actions/cache@{V610}  # v6.1.0'"], refusals


def test_a_line_no_deeper_than_the_key_is_not_block_content(tmp):
    """A shallower line after an empty block is the next node to YAML,
    not the reference."""
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        '      - uses: >-  # v6.1.0\n'
        f'  actions/cache@{V610}:\n'
        '    runs-on: ubuntu-latest\n'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        '.github/workflows/tests.yml:4: uses value cannot be classified: '
        f"'>-  # v6.1.0' then 'actions/cache@{V610}:'"], refusals


def test_a_hash_glued_to_a_quoted_reference_is_not_a_comment(tmp):
    """A comment needs whitespace before its `#`."""
    mod = _verifier()
    root = _one_pin(tmp, f'"actions/cache@{V610}"#v6.1.0')
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        '.github/workflows/tests.yml:4: uses value cannot be classified: '
        f"'\"actions/cache@{V610}\"#v6.1.0'"], refusals


def test_an_unclassifiable_uses_naming_no_cache_action_is_left_alone(tmp):
    """The line grammar leaves it alone; the decoder still has to read
    the file, and refuses what it cannot."""
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        '      - uses: >-\n'
        '          actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n'
        '          and-more\n'
        f'      - uses: actions/cache@{V610}  # v6.1.0\n'))
    pins, refusals = mod.scan(root)
    assert [pin.line for pin in pins] == [7], pins
    assert refusals == [
        '.github/workflows/tests.yml: mapping has an unsupported mapping '
        'field'], refusals


def test_both_workflow_extensions_github_accepts_are_scanned(tmp):
    mod = _verifier()
    root = _workflow(tmp, _PLAIN, name='other.yaml')
    _workflow(tmp, 'jobs:\n  j:\n    runs-on: ubuntu-latest\n')
    pins, refusals = mod.scan(root)
    assert refusals == [], refusals
    assert [pin.path for pin in pins] == [
        '.github/workflows/other.yaml'] * 5, pins


def test_a_reference_the_grammar_did_not_account_for_is_refused(tmp):
    """A non-comment line spelling an `actions/cache…@` reference
    literally is a pin or a refusal; a spelling that hides the reference
    from the line grammar is the decoder cross-check's to catch."""
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        '      - uses:\n'
        '\n'
        f'          actions/cache/restore@{V610}\n'
        f'      - "uses": actions/cache@{V610}  # v6.1.0\n'
        f'      - {{uses: actions/cache/save@{V610}}}\n'
        '      - run: |\n'
        f'          echo actions/cache@{V610}\n'
        f'      - {{uses: Actions/Cache/Save@{V610}}}\n'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        f'.github/workflows/tests.yml:{line}: unclassified actions/cache '
        'reference' for line in (6, 7, 8, 10, 11)], refusals


def test_a_comment_line_naming_a_reference_is_not_refused(tmp):
    mod = _verifier()
    root = _workflow(tmp, _PLAIN + (
        f'      # - uses: actions/cache@{V610}  # v6.1.0\n'
        f'#actions/cache/save@{V610}\n'))
    pins, refusals = mod.scan(root)
    assert refusals == [], refusals
    assert len(pins) == 5, pins


def test_a_near_name_action_is_neither_a_pin_nor_refused(tmp):
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        f'      - uses: actions/cache-warmer@{V610}  # v1.0.0\n'
        f'      - uses: actions/cachex@{V610}  # v1.0.0\n'
        f'      - uses: actions/cache/x@{V610}  # v1.0.0\n'
        f'      - uses: >-  # v1.0.0\n'
        f'          x/actions/cache@{V610}\n'
        f'      - uses: actions/cache/restorer@{V610}  # v1.0.0\n'))
    assert mod.scan(root) == ([], []), mod.scan(root)


def test_the_refusal_filter_matches_the_action_name_case_insensitively(tmp):
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        '      - uses: >-\n'
        f'          Actions/Cache@{V610}  # v6.1.0\n'))
    _verified, refusals = mod.verify(root, _refusing_run)
    assert refusals == [
        '.github/workflows/tests.yml:4: uses value cannot be classified: '
        f"'>-' then 'Actions/Cache@{V610}  # v6.1.0'"], refusals


def test_a_workflow_that_is_not_utf8_is_refused_by_path(tmp):
    mod = _verifier()
    root = _workflow(tmp, _PLAIN)
    (root / '.github' / 'workflows' / 'legacy.yml').write_bytes(
        b'# caf\xe9\njobs: {}\n')
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == ['.github/workflows/legacy.yml: not UTF-8'], refusals


def test_a_block_header_that_keeps_a_newline_is_refused(tmp):
    """Only strip chomping (`>-`, `|-`) yields a bare reference; `>`, `|`
    and `+` leave a trailing newline in the value."""
    mod = _verifier()
    for header in ('>', '|', '>+', '|+'):
        root = _workflow(tmp, (
            'jobs:\n  j:\n    steps:\n'
            f'      - uses: {header}  # v6.1.0\n'
            f'          actions/cache@{V610}\n'))
        verified, refusals = mod.verify(root, _refusing_run)
        assert verified == [], (header, verified)
        assert refusals == [
            '.github/workflows/tests.yml:4: uses value cannot be '
            f"classified: '{header}  # v6.1.0' then 'actions/cache@{V610}'"
        ], (header, refusals)


def test_an_escaped_reference_is_refused_by_the_decoder_cross_check(tmp):
    """The line grammar sees no `@` or no `actions/cache`; the decoder
    sees the runnable reference, and the two must agree."""
    mod = _verifier()
    for spelling in (f'"actions/cache\\x40{V610}"',
                     f'"actions\\x2fcache@{V610}"',
                     f'"actions/cache\\u0040{V610}"'):
        root = _one_pin(tmp, f'{spelling}  # v6.1.0')
        verified, refusals = mod.verify(root, _refusing_run)
        assert verified == [], (spelling, verified)
        assert refusals == [
            f".github/workflows/tests.yml:4: decoded uses 'actions/cache@"
            f"{V610}' matches no recognised pin"], (spelling, refusals)


def test_an_escaped_reference_in_any_job_is_cross_checked(tmp):
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n'
        f'  first:\n    steps:\n      - uses: actions/cache@{V610}  # v6.1.0\n'
        f'  second:\n    steps:\n      - uses: "actions/cache\\x40{V610}"\n'
        f'  third:\n    steps:\n      - uses: "actions/cache\\x40{V610}"\n'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        f".github/workflows/tests.yml:{line}: decoded uses 'actions/cache@"
        f"{V610}' matches no recognised pin" for line in (7, 10)], refusals


def test_a_reference_split_by_a_quoted_line_continuation_is_refused(tmp):
    mod = _verifier()
    root = _workflow(tmp, (
        'jobs:\n  j:\n    steps:\n'
        '      - uses: "actions/\\\n'
        f'          cache@{V610}"  # v6.1.0\n'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        '.github/workflows/tests.yml: mapping has an unsupported mapping '
        'field'], refusals


def _one_pin(tmp, uses):
    return _workflow(tmp, f'jobs:\n  j:\n    steps:\n      - uses: {uses}\n')


def test_a_ref_that_is_not_a_full_lowercase_commit_is_refused(tmp):
    mod = _verifier()
    for ref in ('v4', V610[:-1], V610.upper(), 'main', V610 + 'x',
                'x' + V610, V610 + '#v6.1.0'):
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
    """One refused shape refuses the tree: no sibling pin is resolved."""
    mod = _verifier()
    root = _workflow(tmp, _PLAIN.replace('uses: >-  # v6.1.0', 'uses: >-'))
    verified, refusals = mod.verify(root, _refusing_run)
    assert verified == [], verified
    assert refusals == [
        f'.github/workflows/tests.yml:10: actions/cache/SAVE@{V610} '
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
    assert verified == [f'{V610} v6.1.0 5 pin(s)'], verified
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
        f"v3.13.0: tag object {CPYTHON_TAG} names a 'tag', not a commit "
        'at .github/workflows/tests.yml:4'], refusals
    assert len(calls) == 2, calls


def test_a_release_that_does_not_exist_is_refused_with_the_error(tmp):
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin('a' * 40, 'v99.99.99'))
    _calls, run = _upstream({})
    verified, refusals = mod.verify(root, run)
    assert verified == [], verified
    assert refusals == [
        'v99.99.99: gh api exited 1: gh: Not Found (HTTP 404) at '
        '.github/workflows/tests.yml:4'], refusals


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
    assert refusals == [
        "v6.1.0: response is not JSON: '<html>' at "
        '.github/workflows/tests.yml:4'], refusals


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
            f'v6.1.0: response names no commit or tag object: {body!r} '
            'at .github/workflows/tests.yml:4'], (body, refusals)


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
        'v99.99.99: gh api exited 1: gh: Not Found (HTTP 404) at '
        '.github/workflows/tests.yml:6'], refusals
    assert len(calls) == 3, calls


def _main(mod, root, stdout):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout, '')

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(subprocess, 'run', fake_run):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = mod.main([str(root)])
    return status, out.getvalue(), err.getvalue(), calls


def test_main_binds_gh_api_with_check_and_a_timeout(tmp):
    """check=True makes a 404 a CalledProcessError; the timeout keeps a
    hung API from holding the step."""
    mod = _verifier()
    root = _one_pin(tmp, _cache_pin(V610, 'v6.1.0'))
    status, out, err, calls = _main(mod, root, json.dumps(_LIGHTWEIGHT))
    assert status == 0, (status, err)
    assert out == f'{V610} v6.1.0 1 pin(s)\n', out
    assert err == '', err
    assert calls == [(
        [*_GH_API, _REF + 'v6.1.0'],
        {'capture_output': True, 'text': True, 'check': True,
         'timeout': 60})], calls


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
    """The online check verifies exactly the pins the offline guard
    reviews, never a quietly different set."""
    del tmp
    mod = _verifier()
    pins, refusals = mod.scan(ROOT)
    assert refusals == [], refusals
    assert len(pins) == 2 * len(_CACHE_JOBS), pins
    assert all(mod.shape_refusal(pin) is None for pin in pins), pins
    pairs = {(pin.ref, pin.comment) for pin in pins}
    assert pairs <= set(REVIEWED_CACHE_RELEASES.items()), pairs


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cacherel_')


if __name__ == '__main__':
    raise SystemExit(main())
