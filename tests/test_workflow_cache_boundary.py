#!/usr/bin/env python3
"""Fail-closed inventory of statically classified cache-writing jobs.
Direct shell detection is bounded literal scanning; arbitrary shell, checked-in
scripts, constructed names, and downloaded code are not interpreted."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfgraph import _job_names, _tests_yml  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402


_CACHE_WRITING_JOBS = frozenset((
    'suites', 'coverage-matrix', 'coverage',
    'pycodestyle', 'pylint', 'pyright',
))


_REVIEWED_REFS = {
    'actions/cache': frozenset((
        'v4', 'v4.3.0', 'v6', 'v6.1.0',
        '0057852bfaa89a56745cba8c7296529d2fc39830',
        '55cc8345863c7cc4c66a329aec7e433d2d1c52a9',
    )),
    'actions/setup-go': frozenset((
        'v6', '924ae3a1cded613372ab5595356fb5720e22ba16',
    )),
    'actions/setup-node': frozenset((
        'v7', '820762786026740c76f36085b0efc47a31fe5020',
    )),
    'actions/setup-python': frozenset((
        'v7', '5fda3b95a4ea91299a34e894583c3862153e4b97',
    )),
    'docker/setup-buildx-action': frozenset((
        'v3', '8d2750c68a42422c14e847fe6c8ac0403b4cbd6f',
    )),
    'astral-sh/setup-uv': frozenset((
        'v7', '37802adc94f370d6bfd71619e3f0bf239e1f3b78',
    )),
    'swatinem/rust-cache': frozenset((
        'v2', '6323deb102c322ba6fcbdcafc7e3dddab59af2b6',
    )),
    'docker/build-push-action': frozenset((
        'v6', '10e90e3645eae34f1e60eeb005ba3a3d33f178e8',
    )),
}

_REVIEWED_NONCACHE_REFS = {
    'actions/checkout': '3d3c42e5aac5ba805825da76410c181273ba90b1',
    'actions/upload-artifact':
        '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
    'actions/download-artifact':
        '3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c',
}

_DECISIVE_CONTROLS = {
    'astral-sh/setup-uv': (('enable-cache', 'auto'),
                           ('save-cache', 'true')),
    'swatinem/rust-cache': (('save-if', 'true'),
                            ('cache-provider', 'github')),
}

_DIRECT_ENV_NAMES = (
    'ACTIONS_CACHE_URL', 'ACTIONS_RESULTS_URL',
    'ACTIONS_RUNTIME_TOKEN',
)
_MAX_RUN_LITERAL = 65536
_IDENTIFIER = r'A-Za-z0-9_'


def _boundary_error(job, step_number, detail):
    return AssertionError(
        f"cache boundary cannot classify job {job!r} step {step_number}: "
        f'{detail}')


def _split_action(uses, job, step_number):
    """Return a literal lower-case action identity and its unchanged ref."""
    if not isinstance(uses, str):
        raise _boundary_error(
            job, step_number, f'uses is not a literal string: {uses!r}')
    if '${{' in uses or '}}' in uses:
        raise _boundary_error(
            job, step_number, f'expression-valued uses {uses!r}')
    if not uses or uses != uses.strip() or uses.count('@') != 1:
        raise _boundary_error(
            job, step_number, f'malformed action reference {uses!r}')
    action, ref = uses.split('@', 1)
    if (not action or not ref or any(
            char.isspace() or ord(char) < 0x20
            or 0x7f <= ord(char) <= 0x9f for char in uses)):
        raise _boundary_error(
            job, step_number, f'malformed action reference {uses!r}')
    parts = action.split('/')
    if len(parts) < 2 or any(not part for part in parts):
        raise _boundary_error(
            job, step_number, f'malformed action reference {uses!r}')
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in parts):
        raise _boundary_error(
            job, step_number, f'malformed action reference {uses!r}')
    if any(char.isspace() or ord(char) < 0x20
           or 0x7f <= ord(char) <= 0x9f for char in ref):
        raise _boundary_error(
            job, step_number, f'malformed action reference {uses!r}')
    return action.casefold(), ref


def _literal_control(inputs, name, default, job, step_number, action):
    """Read one case-insensitive literal action input."""
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise _boundary_error(
            job, step_number, f'{action} inputs are not a literal mapping')
    if any(not isinstance(key, str) for key in inputs):
        raise _boundary_error(
            job, step_number, f'{action} input key is not a literal string')
    matches = [value for key, value in inputs.items()
               if isinstance(key, str) and key.casefold() == name.casefold()]
    if len(matches) > 1:
        raise _boundary_error(
            job, step_number,
            f"{action} input {name!r} is duplicated case-insensitively")
    value = default if not matches else matches[0]
    if value is None and not matches:
        return None
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if not isinstance(value, str):
        raise _boundary_error(
            job, step_number,
            f"{action} input {name!r} is not a literal scalar")
    if '${{' in value or '}}' in value:
        raise _boundary_error(
            job, step_number,
            f"{action} input {name!r} is expression-valued")
    if any(ord(char) < 0x20 or 0x7f <= ord(char) <= 0x9f
           for char in value):
        raise _boundary_error(
            job, step_number,
            f"{action} input {name!r} contains control characters")
    return value.strip().casefold()


def _gha_cache_destination(value, job, step_number, owner):
    """Whether a cache-to input has a comma-delimited type=gha field."""
    values = value if isinstance(value, list) else [value]
    if not values or any(not isinstance(item, str) for item in values):
        raise _boundary_error(
            job, step_number,
            f'{owner} cache-to is not a literal scalar sequence')
    if any(any((ord(char) < 0x20 and char not in '\r\n\t')
               or 0x7f <= ord(char) <= 0x9f for char in item)
           for item in values):
        raise _boundary_error(
            job, step_number, f'{owner} cache-to contains control characters')
    fields = []
    for item in values:
        fields.extend(field.strip() for line in item.splitlines()
                      for field in line.split(','))
    if any(field.casefold() == 'type=gha' for field in fields):
        return True
    if any('${{' in item or '}}' in item or '${' in item or re.search(
            r'(?<![A-Za-z0-9_])\$[A-Za-z_][A-Za-z0-9_]*', item)
            for item in values):
        raise _boundary_error(
            job, step_number,
            f'{owner} cache-to has a dynamic destination')
    return False


def _token_marker(name):
    return re.compile(
        rf'(?<![{_IDENTIFIER}]){re.escape(name)}(?![{_IDENTIFIER}])',
        re.IGNORECASE)


_DIRECT_ENV_PATTERNS = tuple(
    (name, _token_marker(name)) for name in _DIRECT_ENV_NAMES)
_DIRECT_PATH = re.compile(r'/_apis/artifactcache/', re.IGNORECASE)
_DIRECT_SERVICE = re.compile(
    r'(?<![' + _IDENTIFIER + r'.-])github\.actions\.results\.api\.v1\.'
    r'CacheService(?![' + _IDENTIFIER + r'.-])', re.IGNORECASE)
_DIRECT_TOOLKIT = re.compile(
    (r'(?<![' + _IDENTIFIER + r'])@actions/cache(?![' + _IDENTIFIER
     + r'.-])'), re.IGNORECASE)
_BUILDX_CACHE_TO = re.compile(
    r'(?<![' + _IDENTIFIER + r'-])--cache-to(?:=|\s+)'
    r'(?P<destination>"[^"]*"|\'[^\']*\'|[^\s]+)', re.IGNORECASE)


def _direct_cache_run(run):
    """Return a reason for a bounded literal direct cache writer, or None."""
    if not isinstance(run, str):
        raise AssertionError('cache boundary run is not a literal string')
    if len(run) > _MAX_RUN_LITERAL:
        raise AssertionError(
            f'cache boundary run exceeds {_MAX_RUN_LITERAL} literal bytes')
    if any((ord(char) < 0x20 and char not in '\r\n\t')
           or 0x7f <= ord(char) <= 0x9f for char in run):
        raise AssertionError('cache boundary run contains control characters')
    scanned = run.replace('\\\r\n', ' ').replace('\\\n', ' ')
    for name, pattern in _DIRECT_ENV_PATTERNS:
        if pattern.search(run):
            return f'direct cache service environment {name}'
    if _DIRECT_PATH.search(run):
        return 'direct artifact-cache API path'
    if _DIRECT_SERVICE.search(run):
        return 'direct Results CacheService API'
    if _DIRECT_TOOLKIT.search(run):
        return 'direct @actions/cache toolkit use'
    matches = tuple(_BUILDX_CACHE_TO.finditer(scanned))
    if matches:
        for match in matches:
            destination = match.group('destination').strip('"\' ;|&')
            fields = [field.strip() for field in destination.split(',')]
            if any(field.casefold() == 'type=gha' for field in fields):
                return 'Buildx cache-to type=gha exporter'
        if any('${{' in match.group('destination')
               or '${' in match.group('destination')
               or re.search(r'(?<![A-Za-z0-9_])\$[A-Za-z_][A-Za-z0-9_]*',
                            match.group('destination'))
               for match in matches):
            raise AssertionError(
                'cache boundary Buildx cache-to has a dynamic destination')
        return None
    return None


def _reviewed_noncache_action(action, ref, job, step_number):
    expected = _REVIEWED_NONCACHE_REFS.get(action)
    if expected is None:
        return False
    if ref != expected:
        raise AssertionError(
            f"cache boundary has no reviewed manifest for job {job!r} "
            f"step {step_number} action {action!r} ref {ref!r}")
    return True


def _require_reviewed_writer_ref(action, ref, job, step_number):
    if ref not in _REVIEWED_REFS.get(action, ()):
        raise AssertionError(
            f"cache boundary has no reviewed manifest for job {job!r} "
            f"step {step_number} action {action!r} ref {ref!r}")


def _direct_reason(run, job, step_number):
    try:
        return _direct_cache_run(run)
    except AssertionError as error:
        raise _boundary_error(job, step_number, str(error)) from error


def _decisive_opt_out(inputs, controls, context):
    errors = []
    for key, default in controls:
        disabled = 'warpbuild' if key == 'cache-provider' else 'false'
        try:
            if _literal_control(inputs, key, default, *context) == disabled:
                return True
        except AssertionError as error: errors.append(error)
    if errors: raise errors[0]
    return False


def _cache_write_reason(step, job, step_number):
    """Return a writer reason, prove no writer with None, or refuse."""
    if not isinstance(step, dict):
        raise _boundary_error(job, step_number, 'step is not a mapping')
    has_uses = 'uses' in step
    uses = step.get('uses')
    run = step.get('run')
    if 'run' in step and run is not None and not isinstance(run, str):
        raise _boundary_error(job, step_number, 'run is not a literal string')
    if 'run' in step and run is None:
        raise _boundary_error(job, step_number, 'run has no literal value')
    if has_uses:
        action, ref = _split_action(uses, job, step_number)
        inputs = step.get('with', {})
        if inputs is None:
            inputs = {}
        elif not isinstance(inputs, dict):
            raise _boundary_error(
                job, step_number, f'{action} inputs are not a mapping')
        if _reviewed_noncache_action(action, ref, job, step_number):
            return _direct_reason(run, job, step_number) \
                if run is not None else None

        if action == 'actions/cache':
            _require_reviewed_writer_ref(action, ref, job, step_number)
            reason = 'actions/cache combined post-save'
        elif action.startswith('actions/cache/'):
            _require_reviewed_writer_ref(
                'actions/cache', ref, job, step_number)
            reason = None if action == 'actions/cache/restore' \
                else f'{action} cache writer'
        elif action == 'actions/setup-go':
            cache = _literal_control(
                inputs, 'cache', 'true', job, step_number, action)
            if cache == 'false':
                _require_reviewed_writer_ref(action, ref, job, step_number)
                reason = None
            else:
                reason = 'setup-go cache post-save'
        elif action == 'actions/setup-node':
            cache = _literal_control(
                inputs, 'cache', None, job, step_number, action)
            if cache is not None:
                reason = 'setup-node explicit package-manager cache'
            else:
                package_cache = _literal_control(
                    inputs, 'package-manager-cache', 'true', job,
                    step_number, action)
                if package_cache == 'false':
                    _require_reviewed_writer_ref(
                        action, ref, job, step_number)
                    reason = None
                else:
                    reason = 'setup-node package-manager cache post-save'
        elif action == 'actions/setup-python':
            cache = _literal_control(
                inputs, 'cache', None, job, step_number, action)
            if cache is None:
                _require_reviewed_writer_ref(action, ref, job, step_number)
            reason = 'setup-python package-manager cache' \
                if cache is not None else None
        elif action == 'docker/setup-buildx-action':
            cache = _literal_control(
                inputs, 'cache-binary', 'true', job, step_number, action)
            if cache == 'false':
                _require_reviewed_writer_ref(action, ref, job, step_number)
                reason = None
            else:
                reason = 'setup-buildx binary cache'
        elif action in _DECISIVE_CONTROLS:
            if _decisive_opt_out(
                    inputs, _DECISIVE_CONTROLS[action],
                    (job, step_number, action)):
                _require_reviewed_writer_ref(action, ref, job, step_number)
                reason = None
            else:
                reason = f'{action.rsplit("/", 1)[-1]} cache post-save'
        elif action == 'docker/build-push-action':
            _require_reviewed_writer_ref(action, ref, job, step_number)
            has_cache_to = 'cache-to' in inputs
            cache_to = inputs.get('cache-to')
            reason = None
            if has_cache_to and _gha_cache_destination(
                    cache_to, job, step_number, action):
                reason = 'Buildx cache-to type=gha exporter'
        elif '/setup-' in action:
            if not isinstance(inputs, dict):
                raise _boundary_error(
                    job, step_number, f'{action} inputs are not a mapping')
            names = {name.casefold() for name in inputs
                     if isinstance(name, str)}
            if 'cache' not in names:
                raise _boundary_error(
                    job, step_number,
                    f"no cache policy for action {action + '@' + ref!r}; "
                    'inspect its primary action.yml')
            reason = f'{action} cache input'
        else:
            raise _boundary_error(
                job, step_number,
                f"no cache policy for action {action + '@' + ref!r}; "
                'inspect its primary action.yml')
        if run is not None:
            direct = _direct_reason(run, job, step_number)
            if direct is not None:
                return direct
        return reason
    if run is not None:
        return _direct_reason(run, job, step_number)
    return None


def _cache_writing_jobs(workflow):
    """Return statically classified cache-writing jobs."""
    writers = set()
    for job in _job_names(workflow):
        mapping = complete_job_mapping(workflow, job)
        if 'uses' in mapping:
            raise AssertionError(
                f"cache boundary cannot classify reusable job {job!r}: "
                'job-level uses requires pending-recursive-inspection')
        steps = mapping.get('steps')
        if steps is None:
            continue
        if not isinstance(steps, list):
            raise AssertionError(
                f"cache boundary cannot classify job {job!r}: steps is not "
                'a sequence')
        for step_number, step in enumerate(steps, 1):
            if _cache_write_reason(step, job, step_number) is not None:
                writers.add(job)
    return writers


def _real_step(uses=None, run=None, inputs=None):
    assert (uses is None) != (run is None)
    if uses is not None:
        lines = [f'      - uses: {uses}']
    else:
        lines = ['      - run: |']
        lines.extend(f'          {line}' for line in run.splitlines())
    if inputs:
        lines.append('        with:')
        for key, value in inputs.items():
            if '\n' in value:
                lines.append(f'          {key}: |')
                lines.extend(f'            {line}'
                             for line in value.splitlines())
            else:
                lines.append(f'          {key}: {value}')
    return '\n'.join(lines) + '\n'


def _insert_wheel_step(workflow, step):
    marker = '  wheel:\n'
    start = workflow.index(marker)
    steps = workflow.index('    steps:\n', start) + len('    steps:\n')
    return workflow[:steps] + step + workflow[steps:]


def _refuses(call, *args, contains=None):
    try:
        call(*args)
    except AssertionError as error:
        message = str(error)
        if contains is not None:
            assert contains in message, message
        return message
    raise AssertionError(f'{call.__name__} accepted the planted ambiguity')


def _assert_writer_inventory(workflow):
    actual = _cache_writing_jobs(workflow)
    assert actual == set(_CACHE_WRITING_JOBS), (
        f"unrecorded cache-writing jobs: "
        f"{sorted(actual - _CACHE_WRITING_JOBS)!r}; "
        f"recorded cache-writing jobs gone quiet: "
        f"{sorted(_CACHE_WRITING_JOBS - actual)!r}")


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
        ('actions/cache@v4', {}, True),
        ('actions/cache/save@v4', {}, True),
        ('actions/cache/restore@v4', {}, False),
    )
    for uses, inputs, expected in cases:
        actual = _cache_write_reason(
            {'uses': uses, 'with': inputs}, 'sample', 1)
        assert (actual is not None) is expected, (uses, actual)


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
        ('astral-sh/setup-uv@v7', {}, True),
        ('astral-sh/setup-uv@v7', {'enable-cache': 'true'}, True),
        ('astral-sh/setup-uv@v7', {'enable-cache': 'false'}, False),
        ('astral-sh/setup-uv@v7', {'save-cache': 'false'}, False),
        (uv, {'enable-cache': 'false', 'save-cache': '${{ x }}'}, False),
        (uv, {'enable-cache': '${{ x }}', 'save-cache': 'false'}, False),
        ('Swatinem/rust-cache@v2', {}, True),
        ('Swatinem/rust-cache@v2', {'save-if': 'true'}, True),
        ('Swatinem/rust-cache@v2', {'save-if': 'false'}, False),
        ('Swatinem/rust-cache@v2', {'cache-provider': 'warpbuild'}, False),
        (rust, {'save-if': 'false', 'cache-provider': '${{ x }}'}, False),
        (rust, {'save-if': '${{ x }}', 'cache-provider': 'warpbuild'}, False),
        ('docker/build-push-action@v6', {'cache-from': 'type=gha'}, False),
        ('docker/build-push-action@v6', {'cache-to': 'type=gha'}, True),
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
        assert (actual is not None) is expected, (uses, inputs, actual)


def test_generic_setup_cache_input_remains_conservative(tmp):
    assert _cache_write_reason(
        {'uses': 'example/setup-thing@v1', 'with': {'cache': 'false'}},
        'sample', 1) is not None
    _refuses(
        _cache_write_reason,
        {'uses': 'example/setup-thing@v1', 'with': {}}, 'sample', 1,
        contains="no cache policy for action 'example/setup-thing@v1'")


def test_unknown_and_dynamic_actions_fail_with_exact_context(tmp):
    del tmp
    _refuses(
        _cache_write_reason,
        {'uses': '${{ matrix.action }}'}, 'wheel', 1,
        contains=("cache boundary cannot classify job 'wheel' step 1: "
                  "expression-valued uses '${{ matrix.action }}'"))
    _refuses(
        _cache_write_reason, {'uses': 'owner/action@v1'}, 'wheel', 1,
        contains="no cache policy for action 'owner/action@v1'")
    _refuses(
        _cache_write_reason, {'uses': 'actions/checkout@deadbeef'},
        'wheel', 1, contains="action 'actions/checkout' ref 'deadbeef'")
    _refuses(
        _cache_write_reason, {'uses': 'actions/setup-python@deadbeef'},
        'wheel', 1, contains="action 'actions/setup-python' ref 'deadbeef'")
    _refuses(
        _cache_write_reason, {'uses': 'docker/build-push-action@deadbeef'},
        'wheel', 1,
        contains="action 'docker/build-push-action' ref 'deadbeef'")


def test_local_docker_and_empty_actions_are_not_silently_cache_free(tmp):
    del tmp
    for uses in ('./local-action', 'docker://alpine:3.20', None):
        _refuses(_cache_write_reason, {'uses': uses}, 'wheel', 1,
                 contains='cache boundary cannot classify')


def test_action_family_delimiters_do_not_widen_cache_or_setup_names(tmp):
    del tmp
    for uses in (
            'actions/cache-warmer@v4',
            'actions/setupfoo@v1',
            'pnpm/action-setup-x@v1'):
        _refuses(
            _cache_write_reason,
            {'uses': uses, 'with': {'cache': 'pip'}}, 'wheel', 1,
            contains='no cache policy')


def test_controls_needed_for_opt_out_must_be_literal(tmp):
    del tmp
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


def test_malformed_controls_and_destinations_fail_closed(tmp):
    del tmp
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


def test_direct_cache_markers_are_token_bounded(tmp):
    del tmp
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
    del tmp
    for run in (
            'docker buildx build --cache-to type=${TYPE} .',
            'docker buildx build --cache-to "$CACHE_DEST" .',
            'docker buildx build --cache-to type=${{ matrix.type }} .'):
        _refuses(_direct_cache_run, run, contains='dynamic destination')
    _refuses(_direct_cache_run, 'x' * 65537, contains='65536')


def test_real_workflow_mutations_are_seen_by_the_writer_inventory(tmp):
    del tmp
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


def test_real_workflow_unknown_and_expression_mutations_refuse(tmp):
    del tmp
    workflow = _tests_yml()
    for step, expected in (
            (_real_step(uses='${{ matrix.action }}'),
             "expression-valued uses '${{ matrix.action }}'"),
            (_real_step(uses='owner/action@v1'),
             "no cache policy for action 'owner/action@v1'")):
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
        "unrecorded cache-writing jobs: ['eslint']; "
        "recorded cache-writing jobs gone quiet: []")


def test_production_cache_steps_keep_restore_and_save_separate(tmp):
    del tmp
    workflow = _tests_yml()
    for job in ('suites', 'coverage-matrix', 'coverage'):
        steps = complete_job_mapping(workflow, job)['steps']
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
