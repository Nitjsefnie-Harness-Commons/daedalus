#!/usr/bin/env python3
"""The workflow checkout reader and the pin that keeps its refs trusted.

These tests exercise the reader separately from speed measurement, including
the workflow pin that protects the checkout ref expression.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
import _wfcheckout  # noqa: E402


_FORBIDDEN_CHECKOUT_NAMES = ('head', 'branch', 'ref', 'sha', 'commit')


def test_checkout_reader_returns_structured_checkout_refs(tmp):
    """The reader exposes checkout refs from the workflow structure."""
    del tmp
    workflow = (
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: \u00a0${{ github.sha }}\u00a0\n'
        '            \u00a0\n'
        '  test:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '  aligned:\n'
        '    steps:\n'
        '      -    uses: actions/checkout@v4\n'
        '           with:\n'
        '             ref: ${{ steps.baseline.outputs.ref }}\n')
    assert _wfcheckout.checkout_refs(workflow) == [
        ('build', '\u00a0${{ github.sha }}\u00a0 \u00a0'),
        ('aligned', '${{ steps.baseline.outputs.ref }}')]


def test_checkout_reader_skips_unwalked_flow_values(tmp):
    """Flow syntax outside a checkout path is not inspected."""
    del tmp
    workflow = (
        'name: [main]\n'
        'on:\n'
        '  branches: [main]\n'
        'jobs:\n'
        '  build:\n'
        '    env: {BROKEN: [still, irrelevant]}\n'
        '    steps:\n'
        '      - uses: actions/setup-python@v4\n'
        '        with: {ref: [not, a, checkout]}\n')
    assert _wfcheckout.checkout_refs(workflow) == []


def test_checkout_reader_skips_balanced_multiline_flow_values(tmp):
    """Balanced flow values outside the checkout path are unwalked."""
    del tmp
    checkout = (
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: safe\n')
    cases = (
        (
            'root on',
            'name: root on\n'
            'on: [\n'
            '  push,\n'
            '  pull_request,\n'
            ']\n' + checkout,
        ),
        (
            'root env',
            'name: root env\n'
            'on: push\n'
            'env: {\n'
            '  SAFE_REF: main,\n'
            '}\n' + checkout,
        ),
        (
            'job strategy',
            'name: job strategy\n'
            'on: push\n'
            'jobs:\n'
            '  build:\n'
            '    strategy: {\n'
            '      fail-fast: false,\n'
            '    }\n'
            '    runs-on: ubuntu-latest\n'
            '    steps:\n'
            '      - uses: actions/checkout@v4\n'
            '        with:\n'
            '          ref: safe\n',
        ),
        (
            'step env',
            'name: step env\n'
            'on: push\n'
            'jobs:\n'
            '  build:\n'
            '    runs-on: ubuntu-latest\n'
            '    steps:\n'
            '      - env: {\n'
            "          SAFE_REF: don't,\n"
            '        }\n'
            '        run: echo "$SAFE_REF"\n'
            '      - uses: actions/checkout@v4\n'
            '        with:\n'
            '          ref: safe\n',
        ),
        (
            'non-checkout with',
            'name: action with\n'
            'on: push\n'
            'jobs:\n'
            '  build:\n'
            '    runs-on: ubuntu-latest\n'
            '    steps:\n'
            '      - uses: actions/setup-python@'
            '5fda3b95a4ea91299a34e894583c3862153e4b97\n'
            '        with: {\n'
            '          python-version: "3.12",\n'
            '        }\n'
            '      - uses: actions/checkout@v4\n'
            '        with:\n'
            '          ref: safe\n',
        ),
    )
    failures = []
    for name, workflow in cases:
        try:
            actual = _wfcheckout.checkout_refs(workflow)
        except _wfcheckout.YAMLReadError as error:
            failures.append((name, str(error)))
        else:
            if actual != [('build', 'safe')]:
                failures.append((name, actual))
    assert failures == [], failures


def test_checkout_reader_never_omits_indented_jobs_for_a_quoted_decoy(tmp):
    """An unsupported root shape must not make a real checkout disappear."""
    del tmp
    workflow = (
        '  jobs:\n'
        '    real:\n'
        '      runs-on: ubuntu-latest\n'
        '      steps:\n'
        '        - uses: actions/checkout@'
        '3d3c42e5aac5ba805825da76410c181273ba90b1\n'
        '          with:\n'
        '            ref: hidden\n'
        '  on: push\n'
        '  name: "decoy\n'
        'jobs:\n'
        '  decoy:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - run: echo decoy"\n')
    try:
        refs = _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError:
        refs = None
    assert refs != [], 'a valid checkout cannot be silently omitted'
    assert refs is None or refs == [('real', 'hidden')], refs


def _assert_yaml_refusal(workflow, wording):
    try:
        _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError as error:
        message = str(error)
        assert wording in message, message
        assert 'line ' in message, message
    else:
        raise AssertionError(f'expected refusal mentioning {wording!r}')


def test_checkout_reader_refuses_a_nonzero_root_mapping_indent(tmp):
    """Classify the root indent before skipping nested-looking lines."""
    del tmp
    workflow = (
        '  jobs:\n'
        '    real:\n'
        '      runs-on: ubuntu-latest\n'
        '      steps:\n'
        '        - uses: actions/checkout@v4\n'
        '          with:\n'
        '            ref: hidden\n'
        '  on: push\n')
    _assert_yaml_refusal(workflow, 'nonzero root mapping indentation')


def test_checkout_reader_never_omits_jobs_before_an_nbsp_decoy(tmp):
    """A BOM and Unicode-space decoy cannot redirect the root scan."""
    del tmp
    workflow = (
        '\ufeffjobs:\n  real:\n    runs-on: ubuntu-latest\n'
        '    steps:\n      - uses: actions/checkout@v4\n'
        '        with:\n          ref: hidden\n'
        '\u00a0jobs:\n  decoy:\n    runs-on: ubuntu-latest\n'
        '    steps:\n      - run: echo decoy\n')
    try:
        refs = _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError:
        refs = None
    assert refs != [], 'a real checkout cannot be silently omitted'
    assert refs is None or refs == [('real', 'hidden')], refs


def test_checkout_reader_never_omits_jobs_from_a_root_merge(tmp):
    """A nested quoted decoy cannot hide jobs supplied by a root merge."""
    del tmp
    workflow = (
        'holder:\n  defaults: &defaults\n    jobs:\n'
        '      real:\n        runs-on: ubuntu-latest\n'
        '        steps:\n          - uses: actions/checkout@v4\n'
        '            with:\n              ref: hidden\n'
        '<<: *defaults\nname:\n  - "decoy\njobs:\n'
        '  decoy:\n    runs-on: ubuntu-latest\n'
        '    steps:\n      - run: echo decoy"\n')
    try:
        refs = _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError:
        refs = None
    assert refs != [], 'a real checkout cannot be silently omitted'
    assert refs is None or refs == [('real', 'hidden')], refs


def test_checkout_reader_refuses_unsupported_walked_constructs(tmp):
    """Unsupported syntax on the walked path fails closed with its location."""
    del tmp
    cases = (
        ('name: no jobs\n', 'top-level jobs mapping'),
        ('jobs:\n', 'jobs value is not a block mapping'),
        ('jobs:\nname: workflow\n',
         'jobs value is not a block mapping'),
        ('jobs:\n  build:\n', 'job value is not a block mapping'),
        ('jobs:\n  build:\n    steps:\n',
         'steps value is not a block sequence'),
        ('jobs:\n  build:\n    steps:\n      -\n',
         'step is not a mapping sequence item'),
        ('\tjobs:\n', 'tab in indentation'),
        ('jobs: {build: {steps: []}}\n', 'flow mapping'),
        ('jobs:\n  build:\n    steps: []\n', 'flow sequence'),
        ('jobs:\n  build:\n    steps:\n'
         '      - {uses: actions/checkout@v4}\n', 'flow mapping'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with: {ref: point}\n', 'flow mapping'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with: {\n'
         '          ref: point,\n'
         '        }\n', 'flow mapping'),
        ('jobs:\n  build:\n    steps: &saved\n', 'anchor'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: *saved\n', 'alias'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: !str point\n', 'explicit tag'),
        ('jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n'
         '        with:\n          ref:\u00a0hidden\n',
         'mapping entry without a colon'),
        ('jobs:\n  build:\n    steps:\n'
         '      - ? uses\n'
         '        : actions/checkout@v4\n', 'explicit key'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          <<: *defaults\n', 'merge key'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: "\\q"\n', 'unknown double-quote escape'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: "\\UFFFFFFFF"\n',
         'invalid double-quoted escape'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: {point: value}\n', 'flow mapping'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: [point]\n', 'flow sequence'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: |0\n', 'unsupported block scalar header'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: |2\n'
         '           point\n', 'inconsistent block scalar indentation'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         "          ref: 'point' tail\n",
         'trailing text after single-quoted scalar'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: "point\n', 'unterminated double-quoted scalar'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref:\n'
         '            path: value\n', 'mapping where scalar was required'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref:\n'
         '            - point\n', 'sequence where scalar was required'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        uses: actions/checkout@v4\n', 'duplicate uses key'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: one\n'
         '        with:\n', 'duplicate with key'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: one\n'
         '          ref: two\n', 'duplicate ref key'),
        ('jobs:\n  build:\n    steps:\n'
         '    steps:\n', 'duplicate steps key'),
        ('jobs:\n  build:\n    steps:\n'
         '      - <<: *defaults\n', 'merge key'),
        ('jobs:\n  build:\n    steps:\n'
         '\t  - uses: actions/checkout@v4\n', 'tab in indentation'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: point\n'
         '---\njobs:\n', 'multiple YAML documents'),
        ('jobs:\n  build:\n    steps:\n'
         '      - uses: actions/checkout@v4\n'
         '        with:\n'
         '          ref: point\n'
         'jobs:\n', 'second top-level jobs mapping'),
    )
    for workflow, wording in cases:
        _assert_yaml_refusal(workflow, wording)


def test_checkout_reader_refuses_a_duplicate_top_level_jobs_with_location(tmp):
    """A duplicate jobs mapping cannot be resolved by choosing one."""
    del tmp
    workflow = (
        'jobs:\n'
        '  first:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        'jobs:\n'
        '  second:\n')
    _assert_yaml_refusal(workflow, 'second top-level jobs mapping')


def test_checkout_reader_refuses_duplicate_decoded_job_keys(tmp):
    """Two spellings of one YAML job key cannot produce a result union."""
    del tmp
    failures = []
    for second_key in ('build', '"bu\\x69ld"'):
        workflow = (
            'jobs:\n'
            '  build:\n'
            '    steps:\n'
            '      - uses: actions/checkout@v4\n'
            '        with:\n'
            '          ref: first\n'
            f'  {second_key}:\n'
            '    steps:\n'
            '      - uses: actions/checkout@v4\n'
            '        with:\n'
            '          ref: second\n')
        try:
            actual = _wfcheckout.checkout_refs(workflow)
        except _wfcheckout.YAMLReadError as error:
            if 'duplicate job key' not in str(error):
                failures.append((second_key, str(error)))
        else:
            failures.append((second_key, actual))
    assert failures == [], failures


def test_checkout_pin_refuses_schema_equivalent_job_keys_before_refs(tmp):
    """Typed job keys cannot union checkout refs before duplicate checks."""
    del tmp
    workflow = (
        'name: schema-equivalent jobs\n'
        'on: push\n'
        'jobs:\n'
        '  yes:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - id: baseline\n'
        '        run: echo safe\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: ${{ steps.baseline.outputs.ref }}\n'
        '  true:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - id: baseline\n'
        '        run: echo safe\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: ${{ steps.baseline.outputs.point }}\n')
    try:
        _assert_checkout_refs_safe(workflow)
    except AssertionError as error:
        message = str(error)
        assert 'plain scalar is not provably a string' in message, message
        assert 'outputs.ref' not in message, message
    else:
        raise AssertionError('expected schema-typed job key refusal')


def test_checkout_reader_refuses_a_hex_escaped_duplicate_jobs_key(tmp):
    """A hex-escaped jobs sibling cannot hide the real mapping."""
    del tmp
    path = ROOT / '.github' / 'workflows' / 'speed.yml'
    workflow = path.read_text(encoding='utf-8')
    decoy = ('jobs:\n  decoy:\n    runs-on: ubuntu-latest\n'
             '    steps:\n      - run: echo decoy\n')
    mutated = workflow.replace('jobs:\n', decoy + '"jo\\x62s":\n', 1)
    assert mutated != workflow
    _assert_yaml_refusal(mutated, 'second top-level jobs mapping')


def test_checkout_reader_refuses_a_unicode_escaped_duplicate_jobs_key(tmp):
    """A Unicode-escaped jobs sibling cannot hide the real mapping."""
    del tmp
    path = ROOT / '.github' / 'workflows' / 'speed.yml'
    workflow = path.read_text(encoding='utf-8')
    decoy = ('jobs:\n  decoy:\n    runs-on: ubuntu-latest\n'
             '    steps:\n      - run: echo decoy\n')
    mutated = workflow.replace('jobs:\n', decoy + '"jo\\u0062s":\n', 1)
    assert mutated != workflow
    _assert_yaml_refusal(mutated, 'second top-level jobs mapping')


def test_checkout_reader_refuses_an_explicit_top_level_jobs_key(tmp):
    """An explicit top-level jobs key cannot silently hide checkout steps."""
    del tmp
    path = ROOT / '.github' / 'workflows' / 'speed.yml'
    workflow = path.read_text(encoding='utf-8')
    mutated = workflow.replace('jobs:\n', '? jobs\n:\n', 1)
    assert mutated != workflow
    _assert_yaml_refusal(mutated, 'explicit key')


def test_checkout_reader_refuses_a_complex_explicit_root_key(tmp):
    """A compact mapping cannot masquerade as an explicit scalar key."""
    del tmp
    workflow = (
        '? name: workflow\n'
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: hidden\n')
    _assert_yaml_refusal(workflow, 'explicit key')


def _assert_checkout_refs_safe(workflow, workflow_name='fixture.yml'):
    """Apply the pin's conservative expression contract to one workflow."""
    offenders = []
    expressions = re.compile(r'\$\{\{(.*?)\}\}', re.DOTALL)
    identifier = r'[A-Za-z_][A-Za-z0-9_-]*'
    access = rf'{identifier}(?:\.{identifier})+'
    github_access = rf'github(?:\.{identifier})+'
    access_pattern = re.compile(
        rf'(?<![A-Za-z0-9_-]){access}(?![A-Za-z0-9_-])')
    github_only = re.compile(
        rf'{github_access}(?:\s*\|\|\s*{github_access})*')
    try:
        refs = _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError as error:
        raise AssertionError(f'{workflow_name}: {error}') from error
    for job, ref in refs:
        matches = list(expressions.finditer(ref))
        if ref.count('${{') != len(matches):
            raise AssertionError(
                f'{workflow_name}, job {job}: unterminated expression in '
                f'{ref!r}')
        for match in matches:
            expression = match.group(1).strip()
            if github_only.fullmatch(expression):
                continue
            expression_offenders = []
            for dotted in access_pattern.findall(expression):
                segments = dotted.split('.')
                if segments[0] == 'github':
                    continue
                for segment in segments[1:]:
                    if segment == 'outputs':
                        continue
                    if any(word in spelling
                           for spelling in (segment, segment.lower())
                           for word in _FORBIDDEN_CHECKOUT_NAMES):
                        expression_offenders.append(
                            f'{workflow_name}, job {job}: {segment}')
            offenders.extend(expression_offenders)
            if expression_offenders:
                continue
            if not re.fullmatch(access, expression):
                raise AssertionError(
                    f'{workflow_name}, job {job}: cannot decompose '
                    f'expression {expression!r}')
    assert not offenders, (
        'a checkout takes its ref from a non-github expression named '
        f'{offenders}, which an analyser reads as an untrusted head')


def test_checkout_pin_checks_all_contexts_and_identifier_segments(tmp):
    """The pin checks step/job ids and every non-github context uniformly."""
    del tmp
    expressions = (
        '${{ steps.find_base_ref.outputs.point }}',
        '${{ needs.release_commit.outputs.point }}',
        '${{ jobs.build_branch.outputs.point }}',
        '${{ env.base_ref }}',
        '${{ secrets.base_ref }}',
        '${{ inputs.base_ref }}',
        '${{ matrix.base_ref }}',
        '${{ vars.base_ref }}',
        ('${{ steps.baseline.outputs.point }}\u00a0'
         '# ${{ steps.baseline.outputs.ref }}'),
    )
    for expression in expressions:
        workflow = (
            'jobs:\n'
            '  release_commit:\n'
            '    steps:\n'
            '      - run: echo release\n'
            '  build:\n'
            '    steps:\n'
            '      - uses: actions/checkout@v4\n'
            '        with:\n'
            f'          ref: {expression}\n')
        try:
            _assert_checkout_refs_safe(workflow)
        except AssertionError as error:
            assert any(word in str(error)
                       for word in _FORBIDDEN_CHECKOUT_NAMES), str(error)
            assert 'job build' in str(error), str(error)
        else:
            raise AssertionError(f'expected refusal for {expression}')


def test_checkout_pin_checks_contexts_after_a_github_access(tmp):
    """A leading GitHub access cannot exempt later dotted accesses."""
    del tmp
    path = ROOT / '.github' / 'workflows' / 'speed.yml'
    speed = path.read_text(encoding='utf-8')
    original = '${{ steps.baseline.outputs.point }}'
    assert original in speed
    needs = (
        'name: mixed contexts\n'
        'on: push\n'
        'jobs:\n'
        '  release_commit:\n'
        '    runs-on: ubuntu-latest\n'
        '    outputs:\n'
        '      point: ${{ steps.value.outputs.point }}\n'
        '    steps:\n'
        '      - id: value\n'
        '        run: echo point=main >> "$GITHUB_OUTPUT"\n'
        '  build:\n'
        '    needs: release_commit\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: ${{ github.sha || '
        'needs.release_commit.outputs.point }}\n')
    cases = (
        (
            '${{ github.sha || steps.baseline.outputs.ref }}',
            speed.replace(
                original,
                '${{ github.sha || steps.baseline.outputs.ref }}', 1),
            'ref',
        ),
        (
            '${{ github.sha || env.base_ref }}',
            speed.replace(
                original, '${{ github.sha || env.base_ref }}', 1),
            'base_ref',
        ),
        (
            '${{ github.sha || needs.release_commit.outputs.point }}',
            needs,
            'release_commit',
        ),
    )
    failures = []
    for expression, workflow, offender in cases:
        try:
            _assert_checkout_refs_safe(workflow)
        except AssertionError as error:
            message = str(error)
            if offender not in message or 'job ' not in message:
                failures.append((expression, message))
        else:
            failures.append((expression, 'pin accepted the checkout ref'))
    assert failures == [], failures


def test_checkout_pin_skips_github_and_rejects_non_reference_expressions(tmp):
    """GitHub refs are excluded; expressions the pin cannot decompose fail."""
    del tmp
    github = (
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: ${{ github.event.pull_request.base.sha }}\n')
    _assert_checkout_refs_safe(github)

    for expression in (
            "${{ format('{0}', steps.baseline.outputs.point) }}",
            '${{ steps.baseline.outputs.point || github.sha }}',
            '${{ steps.baseline.outputs.point[0] }}',
            '${{ true }}'):
        workflow = github.replace(
            '${{ github.event.pull_request.base.sha }}', expression)
        try:
            _assert_checkout_refs_safe(workflow)
        except AssertionError as error:
            assert expression[3:-3].strip() in str(error), str(error)
            assert 'job build' in str(error), str(error)
        else:
            raise AssertionError(f'expected refusal for {expression}')


def test_checkout_refs_from_step_outputs_avoid_the_analyser_heuristic(tmp):
    """No checkout's `ref:` may come from a name that reads like a head.

    CodeQL's untrusted-checkout query (code-scanning alert #82) reads an
    actions/checkout step as pulling a pull request's untrusted head when
    its `ref:` comes from a `steps.*` output whose name contains `head`,
    `branch`, `ref`, `sha` or `commit`. The baseline checkout in speed.yml
    pulls the pull request's base SHA or a release tag — trusted code — so
    the output carrying it is named `point`; naming it `ref` was what drew
    the alert. This pins the name class, so a rename back goes red here
    rather than in the next default-branch analysis.

    The pin is a conservative superset of the analyser heuristic's checked
    identifier-name class for non-`github` dotted expressions: every segment
    is checked, including step and needed-job ids and `secrets`, `inputs`,
    `matrix` and `vars` contexts.
    `vars` is intentionally over-approximated because CodeQL does not model
    it. The query's regexpMatch is case-sensitive, so `BASE_REF` would evade
    the analyser itself; the check tests each name as written and lowercased
    because a human-readable spelling stays one rename away from the alert.
    """
    workflows = sorted((ROOT / '.github' / 'workflows').glob('*.yml'))
    workflows += sorted((ROOT / '.github' / 'workflows').glob('*.yaml'))
    for path in workflows:
        text = path.read_text(encoding='utf-8')
        _assert_checkout_refs_safe(text, path.name)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
