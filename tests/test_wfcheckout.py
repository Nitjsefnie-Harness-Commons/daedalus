#!/usr/bin/env python3
"""The workflow checkout reader and the pin that keeps its refs trusted.

These tests exercise the reader separately from speed measurement, including
the workflow pin that protects the checkout ref expression.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
import _wfcheckout  # noqa: E402
from test_checkout_pin import _assert_checkout_refs_safe  # noqa: E402


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


def test_checkout_reader_matches_action_name_case_insensitively(tmp):
    """Checkout owner/repository spelling follows GitHub case rules."""
    del tmp
    workflow = (
        'name: checkout identity oracle\n'
        'on: push\n'
        'jobs:\n'
        '  lower:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@lower-version\n'
        '        with:\n'
        '          ref: lower-ref\n'
        '  mixed:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: Actions/Checkout@Mixed-Version\n'
        '        with:\n'
        '          ref: Mixed-Ref\n'
        '  upper:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: ACTIONS/CHECKOUT@UPPER-VERSION\n'
        '        with:\n'
        '          ref: UPPER-REF\n')
    assert _wfcheckout.checkout_refs(workflow) == [
        ('lower', 'lower-ref'),
        ('mixed', 'Mixed-Ref'),
        ('upper', 'UPPER-REF'),
    ]


def test_checkout_reader_accepts_indentless_step_sequences(tmp):
    """A mapping value may use a same-indent block sequence."""
    del tmp
    workflow = (
        'name: indentless sequence oracle\n'
        'on: push\n'
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '    - run: echo prepare\n'
        '    - uses: actions/checkout@v4\n'
        '      with:\n'
        '        ref: safe\n'
        '    timeout-minutes: 5\n')
    try:
        actual = _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError as error:
        raise AssertionError(str(error)) from error
    assert actual == [('build', 'safe')]


def test_checkout_reader_accepts_only_one_leading_bom(tmp):
    """One stream marker is accepted; every other BOM is refused."""
    del tmp
    workflow = (
        'jobs:\n  build:\n    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n          ref: safe\n')
    try:
        actual = _wfcheckout.checkout_refs('\ufeff' + workflow)
    except _wfcheckout.YAMLReadError as error:
        raise AssertionError(str(error)) from error
    assert actual == [('build', 'safe')]
    for invalid in ('\ufeff\ufeff' + workflow,
                    workflow.replace('safe', 'safe\ufeff', 1)):
        _assert_yaml_refusal(invalid, 'unsupported structural character')


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
