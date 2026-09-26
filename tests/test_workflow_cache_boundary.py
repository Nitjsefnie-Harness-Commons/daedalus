#!/usr/bin/env python3
"""Direct shell detection is bounded literal scanning; arbitrary shell,
checked-in scripts, constructed names, and downloaded code are not interpreted.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wffixtures import _refuses  # noqa: E402
from _wfgraph import _tests_yml  # noqa: E402
from _workflow_cache_boundary import (  # noqa: E402
    _CACHE_WRITING_JOBS,
    _assert_writer_inventory,
    _cache_to_records,
    _cache_write_reason,
    _cache_writing_jobs,
    _insert_wheel_step,
    _real_step,
)


def test_only_the_recorded_jobs_write_the_actions_cache(tmp):
    _assert_writer_inventory(_tests_yml())


def test_steps_less_local_job_is_empty_but_reusable_job_is_pending(tmp):
    local = 'jobs:\n  local:\n    runs-on: ubuntu-latest\n'
    assert _cache_writing_jobs(local) == set()
    reusable = 'jobs:\n  called:\n    uses: ./.github/workflows/other.yml\n'
    _refuses(_cache_writing_jobs, reusable,
             contains='pending-recursive-inspection')


def test_actions_cache_combined_and_save_write_restore_reads(tmp):
    cases = (
        ('actions/cache@v4', 'actions/cache combined post-save'),
        ('actions/cache/save@v4', 'actions/cache/save cache writer'),
        ('actions/cache/restore@v4', None),
    )
    for uses, expected in cases:
        actual = _cache_write_reason({'uses': uses, 'with': {}}, 'sample', 1)
        assert actual == expected, (uses, actual)


def test_manifest_backed_cache_action_controls(tmp):
    uv = 'astral-sh/setup-uv@v7'
    rust = 'Swatinem/rust-cache@v2'
    cases = (
        ('actions/setup-go@v6', {}, True),
        ('actions/setup-go@v6', {'cache': 'true'}, True),
        ('actions/setup-go@v6', {'cache': 'false'}, False),
        ('actions/setup-node@v7', {}, True),
        ('actions/setup-node@v7', {'package-manager-cache': 'true'}, True),
        ('actions/setup-node@v7', {'package-manager-cache': 'false'}, False),
        ('actions/setup-node@v7', {'cache': 'npm'}, True),
        ('docker/setup-buildx-action@v3', {}, True),
        ('docker/setup-buildx-action@v3', {'cache-binary': 'true'}, True),
        ('docker/setup-buildx-action@v3', {'cache-binary': 'false'}, False),
        (uv, {}, 'setup-uv cache post-save'),
        (uv, {'enable-cache': 'true'}, 'setup-uv cache post-save'),
        ('astral-sh/setup-uv@v7', {'enable-cache': 'false'}, False),
        ('astral-sh/setup-uv@v7', {'save-cache': 'false'}, False),
        (uv, {'enable-cache': 'false', 'save-cache': '${{ x }}'}, False),
        (uv, {'enable-cache': '${{ x }}', 'save-cache': 'false'}, False),
        (rust, {}, 'rust-cache post-save'),
        (rust, {'save-if': 'true'}, 'rust-cache post-save'),
        ('Swatinem/rust-cache@v2', {'save-if': 'false'}, False),
        ('Swatinem/rust-cache@v2', {'cache-provider': 'warpbuild'}, False),
        (rust, {'save-if': 'false', 'cache-provider': '${{ x }}'}, False),
        (rust, {'save-if': '${{ x }}', 'cache-provider': 'warpbuild'}, False),
        ('docker/build-push-action@v6', {'cache-from': 'type=gha'}, False),
        ('docker/build-push-action@v6', {'cache-to': 'type=gha'}, True),
        ('docker/build-push-action@v6', {'CACHE-TO': 'type=gha'}, True),
        ('docker/build-push-action@v6', {'Cache-To': 'type=gha'}, True),
        ('docker/build-push-action@v6',
         {'cache-to': 'type=local\ntype=gha'}, True),
        ('docker/build-push-action@v6',
         {'cache-to': ['type=local', 'type=gha']}, True),
        ('docker/build-push-action@v6', {'cache-to': 'type=gha2'}, False),
        ('actions/setup-python@v7', {}, False),
        ('actions/setup-python@v7', {'cache': 'pip'}, True),
    )
    for uses, inputs, expected in cases:
        actual = _cache_write_reason(
            {'uses': uses, 'with': inputs}, 'sample', 1)
        assert (actual == expected if isinstance(expected, str)
                else (actual is not None) is expected), (uses, inputs, actual)


def test_generic_setup_cache_input_remains_conservative(tmp):
    assert _cache_write_reason(
        {'uses': 'example/setup-thing@v1', 'with': {'cache': 'false'}},
        'sample', 1) is not None
    _refuses(
        _cache_write_reason,
        {'uses': 'example/setup-thing@v1', 'with': {}}, 'sample', 1,
        contains="no cache policy for action 'example/setup-thing@v1'")


def test_unknown_and_dynamic_actions_fail_with_exact_context(tmp):
    for uses, expected in (
            ('${{ matrix.action }}', "cache boundary cannot classify job "
             "'wheel' step 1: expression-valued uses '${{ matrix.action }}'"),
            ('owner/action@v1', "cache boundary cannot classify job "
             "'wheel' step 1: no cache policy for action 'owner/action@v1'; "
             "inspect its primary action.yml"),
            ('actions/checkout@deadbeef', "cache boundary has no reviewed "
             "manifest for job 'wheel' step 1 action 'actions/checkout' "
             "ref 'deadbeef'"),
            ('actions/setup-python@deadbeef', "cache boundary has no reviewed "
             "manifest for job 'wheel' step 1 action 'actions/setup-python' "
             "ref 'deadbeef'"),
            ('docker/build-push-action@deadbeef',
             "cache boundary has no reviewed manifest for job 'wheel' step 1 "
             "action 'docker/build-push-action' ref 'deadbeef'"),
            ('actions/cache/unknown@v4', "cache boundary cannot classify job "
             "'wheel' step 1: unknown actions/cache sub-action "
             "'actions/cache/unknown'"),
            ('actions/cache/save@deadbeef', "cache boundary has no reviewed "
             "manifest for job 'wheel' step 1 action 'actions/cache' "
             "ref 'deadbeef'")):
        assert _refuses(_cache_write_reason, {'uses': uses}, 'wheel', 1,
                        contains=expected) == f'AssertionError: {expected}'


def test_local_docker_and_empty_actions_are_not_silently_cache_free(tmp):
    for uses in ('./local-action', 'docker://alpine:3.20', None):
        _refuses(_cache_write_reason, {'uses': uses}, 'wheel', 1,
                 contains='cache boundary cannot classify')


def test_action_family_delimiters_do_not_widen_cache_or_setup_names(tmp):
    for uses in (
            'actions/cache-warmer@v4',
            'actions/setupfoo@v1',
            'pnpm/action-setup-x@v1'):
        _refuses(
            _cache_write_reason,
            {'uses': uses, 'with': {'cache': 'pip'}}, 'wheel', 1,
            contains='no cache policy')


def test_controls_needed_for_opt_out_must_be_literal(tmp):
    for uses, key in (
            ('actions/setup-go@v6', 'cache'),
            ('actions/setup-node@v7', 'package-manager-cache'),
            ('docker/setup-buildx-action@v3', 'cache-binary'),
            ('astral-sh/setup-uv@v7', 'enable-cache'),
            ('Swatinem/rust-cache@v2', 'save-if')):
        _refuses(
            _cache_write_reason,
            {'uses': uses, 'with': {key: '${{ inputs.off }}'}},
            'wheel', 2, contains=f"input '{key}' is expression-valued")


def test_csv_cache_exporters_are_inventoried_on_the_real_workflow(tmp):
    # Re-check Util.getInputList(cache-to, {ignoreComma: true}) when bumping
    # docker/actions-toolkit@0.62.1 in the reviewed build-push action pin.
    target = Path(tmp) / '.github/workflows/tests.yml'
    target.parent.mkdir(parents=True)
    original = (ROOT / '.github/workflows/tests.yml').read_bytes()
    lf = original.replace(b'\r\n', b'\n')
    for original in (lf, lf.replace(b'\n', b'\r\n')):
        target.write_bytes(original)
        assert target.read_bytes() == original
        _assert_writer_inventory(target.read_text(encoding='utf-8'))
        for value, writer in (
                ('"type=gha"', True), ('"type=gha",mode=max', True),
                ('"type=gha,mode=max"', True),
                ('"type=local', True), ('unknown', True),
                ('type=local', False), ('"type=local"', False),
                ('"type=local",mode=max', False)):
            step = _real_step(
                uses='docker/build-push-action@'
                     '10e90e3645eae34f1e60eeb005ba3a3d33f178e8',
                inputs={'cache-to': "'" + value + "'"})
            changed = _insert_wheel_step(original.decode('utf-8'), step)
            try:
                target.write_bytes(changed.encode('utf-8'))
                planted = target.read_text(encoding='utf-8')
                if writer:
                    _refuses(
                        _assert_writer_inventory, planted,
                        contains="unrecorded cache-writing jobs: ['wheel']")
                    assert _cache_writing_jobs(planted) == (
                        _CACHE_WRITING_JOBS | {'wheel'}), value
                else:
                    _assert_writer_inventory(planted)
            finally:
                target.write_bytes(original)
            assert target.read_bytes() == original
            _assert_writer_inventory(target.read_text(encoding='utf-8'))


def test_cache_csv_decoding_keeps_commas_and_unescapes_doubled_quotes(tmp):
    del tmp
    for value, expected in (
            ('"type=gha"', ['type=gha']),
            ('"type=gha",mode=max', ['type=gha,mode=max']),
            ('"type=gha,mode=max"', ['type=gha,mode=max']),
            ('"type=local","dest=a""b"', ['type=local,dest=a"b']),
            ('type=local\n\n"type=gha"', ['type=local', 'type=gha']),
            ('"type=local', None)):
        assert _cache_to_records(value) == expected, value


def test_malformed_controls_and_destinations_fail_closed(tmp):
    _refuses(
        _cache_write_reason,
        {'uses': 'actions/setup-go@v6', 'with': {'cache': ['false']}},
        'wheel', 3, contains='not a literal scalar')
    _refuses(
        _cache_write_reason,
        {'uses': 'docker/build-push-action@v6',
         'with': {'cache-to': 'type=${{ inputs.kind }}'}},
        'wheel', 3, contains='dynamic destination')
    _refuses(
        _cache_write_reason,
        {'uses': 'docker/build-push-action@v6', 'with': {'cache-to': None}},
        'wheel', 3, contains='cache-to is not a literal')
    for keys in (('cache-to', 'CACHE-TO'), ('cache-to', 'Cache-To')):
        _refuses(
            _cache_write_reason,
            {'uses': 'docker/build-push-action@v6',
             'with': {key: 'type=gha' for key in keys}},
            'wheel', 3, contains='duplicated case-insensitively')


def test_direct_cache_markers_are_token_bounded(tmp):
    from _workflow_cache_boundary import (  # noqa: PLC0415
        _direct_cache_run)

    positives = (
        'curl "$ACTIONS_CACHE_URL/_apis/artifactcache/cache"',
        'curl "/_apis/artifactcache/cache"',
        'curl "$ACTIONS_RESULTS_URL"',
        'curl -H "$ACTIONS_RUNTIME_TOKEN" /cache',
        'github.actions.results.api.v1.CacheService/GetCacheEntry',
        "node -e \"require('@actions/cache')\"",
        'docker buildx build --cache-to type=gha,mode=max .',
        'docker buildx build --cache-to \\\n type=gha .',
    )
    for run in positives:
        assert _direct_cache_run(run) is not None, run
    negatives = (
        'echo MY_ACTIONS_CACHE_URL_BACKUP',
        'echo ACTIONS_CACHE_URL_BACKUP',
        'echo github.actions.results.api.v1.CacheServiceX',
        "echo '@actions/cacheable'",
        'docker buildx build --cache-to type=local --cache-from type=gha .',
        'docker buildx build --cache-from type=gha .',
        'docker buildx build --cache-to type=gha2 .',
        'docker buildx build --cache-from type=gha --cache-to type=local .',
        'docker buildx build --cache-to type=gh .',
    )
    for run in negatives:
        assert _direct_cache_run(run) is None, run


def test_direct_dynamic_buildx_destination_is_indeterminate(tmp):
    from _workflow_cache_boundary import (  # noqa: PLC0415
        _direct_cache_run)

    for run in (
            'docker buildx build --cache-to type=${TYPE} .',
            'docker buildx build --cache-to "$CACHE_DEST" .',
            'docker buildx build --cache-to type=${{ matrix.type }} .'):
        _refuses(_direct_cache_run, run, contains='dynamic destination')
    _refuses(_direct_cache_run, 'x' * 65537, contains='65536')


def test_real_workflow_mutations_are_seen_by_the_writer_inventory(tmp):
    workflow = _tests_yml()
    positives = (
        _real_step(uses='actions/setup-go@v6'),
        _real_step(uses='actions/setup-node@v7'),
        _real_step(uses='docker/setup-buildx-action@v3'),
        _real_step(uses='astral-sh/setup-uv@v7',
                   inputs={'enable-cache': 'true'}),
        _real_step(uses='Swatinem/rust-cache@v2'),
        _real_step(uses='docker/build-push-action@v6',
                   inputs={'cache-to': 'type=gha'}),
        _real_step(run='curl "$ACTIONS_CACHE_URL/_apis/artifactcache/cache"'),
        _real_step(run='curl "$ACTIONS_RESULTS_URL"'),
        _real_step(run='curl "$ACTIONS_RUNTIME_TOKEN"'),
        _real_step(run='github.actions.results.api.v1.CacheService/Get'),
        _real_step(run="node -e \"require('@actions/cache')\""),
        _real_step(run='docker buildx build --cache-to type=gha .'),
    )
    for step in positives:
        assert 'wheel' in _cache_writing_jobs(
            _insert_wheel_step(workflow, step)), step
    uppercase = _insert_wheel_step(
        workflow, _real_step(uses='docker/build-push-action@v6',
                             inputs={'CACHE-TO': 'type=gha'}))
    _refuses(_assert_writer_inventory, uppercase,
             contains="unrecorded cache-writing jobs: ['wheel']")


def test_real_workflow_unknown_and_expression_mutations_refuse(tmp):
    workflow = _tests_yml()
    for step, expected in (
            (_real_step(uses='${{ matrix.action }}'),
             "expression-valued uses '${{ matrix.action }}'"),
            (_real_step(uses='owner/action@v1'),
             "no cache policy for action 'owner/action@v1'"),
            (_real_step(uses='actions/cache/unknown@v4'),
             "unknown actions/cache sub-action 'actions/cache/unknown'")):
        _refuses(_cache_writing_jobs, _insert_wheel_step(workflow, step),
                 contains=expected)


def test_eslint_opt_out_keeps_the_production_set_closed(tmp):
    workflow = _tests_yml()
    _assert_writer_inventory(workflow)
    line = '          package-manager-cache: false\n'
    assert line in workflow
    without_opt_out = workflow.replace(line, '', 1)
    message = _refuses(_assert_writer_inventory, without_opt_out)
    assert message == (
        "AssertionError: unrecorded cache-writing jobs: ['eslint']; "
        "recorded cache-writing jobs gone quiet: []")


def test_production_cache_steps_keep_restore_and_save_separate(tmp):
    from _yamlsteps import complete_job_mapping  # noqa: PLC0415

    workflow = _tests_yml()
    for job in ('suites', 'coverage-matrix', 'coverage'):
        mapping = complete_job_mapping(workflow, job)
        assert mapping is not None, job
        steps = mapping['steps']
        restores = [step for step in steps if step.get('uses', '').startswith(
            'actions/cache/restore@')]
        saves = [step for step in steps if step.get('uses', '').startswith(
            'actions/cache/save@')]
        assert len(restores) == len(saves) == 1, (job, restores, saves)
        assert _cache_write_reason(restores[0], job, 1) is None
        assert _cache_write_reason(saves[0], job, 1) is not None


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cacheboundary_'))
