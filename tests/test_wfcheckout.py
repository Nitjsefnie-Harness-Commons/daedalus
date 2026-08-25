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


def test_checkout_reader_decodes_supported_scalar_styles(tmp):
    """Checkout refs use runner values, not source-line spellings."""
    del tmp
    workflow = (
        'jobs:\n'
        '    plain:\n'
        '      steps:\n'
        '        - uses: "actions/checkout@v4"\n'
        '          with:\n'
        "            ref: 'one''two'\n"
        '    folded:\n'
        '      steps:\n'
        '        - uses: actions/checkout@v4\n'
        '          with:\n'
        '            ref: >-\n'
        '              first\n'
        '              second\n'
        '    literal:\n'
        '      steps:\n'
        '        - uses: actions/checkout@v4\n'
        '          with:\n'
        '            ref: |+\n'
        '              first\n'
        '              second\n'
        '    double:\n'
        '      steps:\n'
        '        - uses: actions/checkout@v4\n'
        '          with:\n'
        '            ref: "line\\nnext"\n')
    assert _wfcheckout.checkout_refs(workflow) == [
        ('plain', "one'two"),
        ('folded', 'first second'),
        ('literal', 'first\nsecond\n'),
        ('double', 'line\nnext'),
    ]


def test_checkout_reader_keeps_block_scalar_comment_content(tmp):
    """An indented hash line is scalar data, not a YAML comment."""
    del tmp
    workflow = (
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: |-\n'
        '            # ${{ steps.baseline.outputs.ref }}\n'
        '                                                    safe\n')
    assert _wfcheckout.checkout_refs(workflow) == [
        ('build', '# ${{ steps.baseline.outputs.ref }}\n'
                  '                                        safe')]


def test_checkout_reader_ends_block_scalar_at_outdented_comment(tmp):
    """An outdented YAML comment is not sliced into scalar content."""
    del tmp
    workflow = (
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: |-\n'
        '            safe\n'
        '# ${{ steps.baseline.outputs.ref }}\n')
    assert _wfcheckout.checkout_refs(workflow) == [('build', 'safe')]


def test_checkout_reader_consumes_doubled_quote_before_hash(tmp):
    """A doubled apostrophe keeps a later hash inside the quoted value."""
    del tmp
    workflow = (
        "name: 'Team''s #1 build'\n"
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - run: echo safe\n')
    try:
        refs = _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError as error:
        raise AssertionError(str(error)) from error
    assert refs == []


def test_checkout_reader_consumes_doubled_quote_before_key_colon(tmp):
    """A colon after an escaped apostrophe remains inside a quoted key."""
    del tmp
    workflow = (
        "'Team''s: label': ignored\n"
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: safe\n')
    try:
        refs = _wfcheckout.checkout_refs(workflow)
    except _wfcheckout.YAMLReadError as error:
        raise AssertionError(str(error)) from error
    assert refs == [('build', 'safe')]


def test_checkout_reader_skips_multiline_quoted_mapping_content(tmp):
    """Quoted continuation lines never become walked mapping entries."""
    del tmp
    failures = []
    expression = '${{ steps.baseline.outputs.ref }}'
    for quote in ('"', "'"):
        workflows = (
            (
                'jobs:\n'
                '  build:\n'
                f'    name: {quote}harmless\n'
                '    steps:\n'
                '      - uses: actions/checkout@v4\n'
                '        with:\n'
                f'          ref: {expression}{quote}\n'
            ),
            (
                'jobs:\n'
                '  build:\n'
                '    steps:\n'
                f'      - run: {quote}echo harmless\n'
                '        uses: actions/checkout@v4\n'
                '        with:\n'
                f'          ref: {expression}{quote}\n'
            ),
            (
                'jobs:\n'
                '  build:\n'
                '    steps:\n'
                '      - uses: actions/checkout@v4\n'
                '        with:\n'
                f'          path: {quote}harmless\n'
                f'          ref: {expression}{quote}\n'
            ),
        )
        for level, workflow in zip(('job', 'step', 'with'), workflows):
            refs = _wfcheckout.checkout_refs(workflow)
            if refs:
                failures.append((quote, level, refs))
    assert failures == [], failures


def test_checkout_reader_keeps_tabs_after_block_scalar_indent(tmp):
    """A tab after required scalar spaces is content, not indentation."""
    del tmp
    cases = (
        (
            'jobs:\n'
            '  build:\n'
            '    steps:\n'
            '      - run: |\n'
            '          echo before\n'
            '          \techo after\n',
            [],
        ),
        (
            'jobs:\n'
            '  build:\n'
            '    steps:\n'
            '      - uses: actions/checkout@v4\n'
            '        with:\n'
            '          ref: |-\n'
            '            first\n'
            '            \tsecond\n',
            [('build', 'first\n\tsecond')],
        ),
    )
    failures = []
    for workflow, expected in cases:
        try:
            actual = _wfcheckout.checkout_refs(workflow)
        except _wfcheckout.YAMLReadError as error:
            failures.append(str(error))
        else:
            if actual != expected:
                failures.append((actual, expected))
    assert failures == [], failures


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


def test_checkout_reader_refuses_a_multiline_quoted_root_value(tmp):
    """Physical lines in a quoted scalar cannot become root mapping keys."""
    del tmp
    suffix = (
        'jobs:\n'
        '  decoy:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - run: echo decoy"\n')
    cases = (
        ('name: "decoy\n', 'unterminated double-quoted scalar'),
        ('name:\n  "decoy\n', 'unterminated double-quoted scalar'),
        ('name: !!str "decoy\n', 'explicit tag'),
        ('name: &label "decoy\n', 'anchor'),
    )
    for prefix, wording in cases:
        _assert_yaml_refusal('on: push\n' + prefix + suffix, wording)


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


def test_checkout_reader_decodes_an_escaped_top_level_jobs_key(tmp):
    """A quoted escape is decoded before selecting the jobs mapping."""
    del tmp
    path = ROOT / '.github' / 'workflows' / 'speed.yml'
    workflow = path.read_text(encoding='utf-8')
    mutated = workflow.replace('jobs:\n', '"jo\\x62s":\n', 1)
    assert mutated != workflow
    assert _wfcheckout.checkout_refs(mutated) == [
        ('speed', '${{ github.event.pull_request.head.sha || github.sha }}'),
        ('speed', '${{ steps.baseline.outputs.point }}'),
    ]


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


def _assert_checkout_refs_safe(workflow, workflow_name='fixture.yml'):
    """Apply the pin's conservative expression contract to one workflow."""
    offenders = []
    expressions = re.compile(r'\$\{\{(.*?)\}\}', re.DOTALL)
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
            leading = re.match(r'[A-Za-z_][A-Za-z0-9_-]*', expression)
            if leading and leading.group(0) == 'github':
                continue
            if not re.fullmatch(
                    r'[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)*',
                    expression):
                raise AssertionError(
                    f'{workflow_name}, job {job}: cannot decompose '
                    f'expression {expression!r}')
            segments = expression.split('.')
            if len(segments) < 2:
                raise AssertionError(
                    f'{workflow_name}, job {job}: cannot decompose '
                    f'expression {expression!r}')
            for segment in segments[1:]:
                if segment == 'outputs':
                    continue
                if any(word in spelling
                       for spelling in (segment, segment.lower())
                       for word in _FORBIDDEN_CHECKOUT_NAMES):
                    offenders.append(
                        f'{workflow_name}, job {job}: {segment}')
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
