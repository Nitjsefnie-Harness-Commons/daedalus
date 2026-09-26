"""Which workflow jobs may write the Actions cache, and why one may not.

Not a suite itself — run_tests.py only loads `test_*.py`.

The whole engine moved here out of tests/test_workflow_cache_boundary.py,
which tests/test_static_guard_regressions.py imported whole for six of
these names. That suite keeps its own tests and imports back what they
use, and `REVIEWED_CACHE_RELEASES` now has its only home here: the two
pip-cache suites read it here rather than through that suite.
"""
import csv
import io
import re

from _wfgraph import _job_names  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402


_CACHE_WRITING_JOBS = frozenset((
    'suites', 'coverage-matrix', 'coverage',
    'pycodestyle', 'pylint', 'pyright',
))


# The pip-cache suite shares this review point for pinned `uses:` comments.
REVIEWED_CACHE_RELEASES = {
    '0057852bfaa89a56745cba8c7296529d2fc39830': 'v4.3.0',
    '55cc8345863c7cc4c66a329aec7e433d2d1c52a9': 'v6.1.0',
}

_REVIEWED_REFS = {
    'actions/cache': frozenset((
        'v4', 'v6', *REVIEWED_CACHE_RELEASES,
        *REVIEWED_CACHE_RELEASES.values())),
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
    'actions/upload-artifact': '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
    'actions/download-artifact': '3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c',
}

_DECISIVE_CONTROLS = {
    'astral-sh/setup-uv': ((('enable-cache', 'auto'),
                            ('save-cache', 'true')),
                           'setup-uv cache post-save'),
    'swatinem/rust-cache': ((('save-if', 'true'),
                             ('cache-provider', 'github')),
                            'rust-cache post-save'),
}

_DIRECT_ENV_NAMES = (
    'ACTIONS_CACHE_URL', 'ACTIONS_RESULTS_URL', 'ACTIONS_RUNTIME_TOKEN',
)
_MAX_RUN_LITERAL = 65536
_IDENTIFIER = r'A-Za-z0-9_'


def _boundary_error(job, step_number, detail):
    return AssertionError(
        f"cache boundary cannot classify job {job!r} step {step_number}: "
        f'{detail}')


def _split_action(uses, job, step_number):
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


def _cache_to_records(value):
    try:
        records = csv.reader(io.StringIO(value.strip(), newline=''),
                             strict=True)
        return [','.join(record).strip() for record in records
                if record and any(record)]
    except csv.Error:
        return None


def _gha_cache_destination(value, job, step_number, owner):
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
        records = _cache_to_records(item)
        if records is None:
            return True
        fields.extend(field.strip() for record in records
                      for field in record.split(','))
    if any(field.casefold() == 'type=gha' for field in fields):
        return True
    if any('${{' in item or '}}' in item or '${' in item or re.search(
            r'(?<![A-Za-z0-9_])\$[A-Za-z_][A-Za-z0-9_]*', item)
            for item in values):
        raise _boundary_error(
            job, step_number,
            f'{owner} cache-to has a dynamic destination')
    return any(not re.fullmatch(r'[-A-Za-z0-9_]+=[^\s\"\']+', field)
               for field in fields)


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
            if action not in ('actions/cache/restore', 'actions/cache/save'):
                raise _boundary_error(
                    job, step_number,
                    f'unknown actions/cache sub-action {action!r}')
            _require_reviewed_writer_ref(
                'actions/cache', ref, job, step_number)
            reason = (None if action == 'actions/cache/restore'
                      else 'actions/cache/save cache writer')
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
                    inputs, _DECISIVE_CONTROLS[action][0],
                    (job, step_number, action)):
                _require_reviewed_writer_ref(action, ref, job, step_number)
                reason = None
            else:
                reason = _DECISIVE_CONTROLS[action][1]
        elif action == 'docker/build-push-action':
            _require_reviewed_writer_ref(action, ref, job, step_number)
            cache_to_keys = [key for key in inputs
                             if isinstance(key, str)
                             and key.casefold() == 'cache-to']
            if len(cache_to_keys) > 1:
                raise _boundary_error(
                    job, step_number,
                    f"{action} input 'cache-to' is duplicated "
                    'case-insensitively')
            has_cache_to = bool(cache_to_keys)
            cache_to = inputs.get(cache_to_keys[0]) if has_cache_to else None
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
    writers = set()
    for job in _job_names(workflow):
        mapping = complete_job_mapping(workflow, job)
        if mapping is None:
            continue
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
        assert run is not None, 'a step is either a uses or a run'
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
    workflow = workflow.replace('\r\n', '\n')
    marker = '  wheel:\n'
    start = workflow.index(marker)
    steps = workflow.index('    steps:\n', start) + len('    steps:\n')
    return workflow[:steps] + step + workflow[steps:]


def _assert_writer_inventory(workflow):
    actual = _cache_writing_jobs(workflow)
    assert actual == set(_CACHE_WRITING_JOBS), (
        f"unrecorded cache-writing jobs: "
        f"{sorted(actual - _CACHE_WRITING_JOBS)!r}; "
        f"recorded cache-writing jobs gone quiet: "
        f"{sorted(_CACHE_WRITING_JOBS - actual)!r}")
