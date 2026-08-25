#!/usr/bin/env python3
"""How the speed gate decides, and what it refuses to decide from.

The comparison sums the tests present and passing on both sides, pairs whole
rounds rather than per-test minima, and takes the median of the paired ratios.
Each of those is a decision about what a number is allowed to describe, so
these tests drive the comparison with rounds that disagree.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
import _wfcheckout  # noqa: E402


_FORBIDDEN_CHECKOUT_NAMES = ('head', 'branch', 'ref', 'sha', 'commit')


def _durations_tree(tmp, side, rounds):
    """Write one summary directory per round, as run_tests.py would."""
    dirs = []
    for index, tests in enumerate(rounds, start=1):
        d = Path(tmp) / f'{side}-{index}'
        d.mkdir(parents=True)
        (d / 'test_suite.json').write_text(json.dumps({
            'total': len(tests), 'passed': len(tests),
            'skipped': 0, 'failed': 0, 'tests': tests,
        }), encoding='utf-8')
        dirs.append(str(d))
    return dirs


def _compare_durations():
    return _util.load(ROOT / 'scripts' / 'ci' / 'compare_durations.py')


def _time_tests():
    return _util.load(ROOT / 'scripts' / 'ci' / 'time_tests.py')


def test_speed_comparison_pairs_whole_rounds_rather_than_per_test_minima(tmp):
    """Every total reported has to be one a complete round actually achieved.

    Summing each test's independent minimum builds a side total no run ever
    produced, and it takes different amounts of noise off each side: on a
    recorded artifact it removed 2.4s from the baseline and 22.4s from the
    head, reporting 0.999 where every complete pair of rounds was at least
    1.07.

    Here each side is noisy on a different test in each round, so per-test
    minima would report 2.00s for a side whose every round took 11.00s.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'slow': 10.0, 'fast': 1.0},
                                         {'slow': 1.0, 'fast': 10.0}])
    head = _durations_tree(tmp, 'head', [{'slow': 11.0, 'fast': 1.0},
                                         {'slow': 1.0, 'fast': 11.0}])
    shared, pairs, _moves = compare.compare(compare.side_rounds(base),
                                            compare.side_rounds(head))
    assert shared == ['fast', 'slow'], shared
    assert [(base_total, head_total) for base_total, head_total, _r in pairs] \
        == [(11.0, 12.0), (11.0, 12.0)], pairs
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_gates_on_the_median_of_the_pairs(tmp):
    """One spoiled pair must not decide the verdict, and a majority must.

    A paired ratio can be ruined outright by a noisy neighbour landing on one
    round, so gating on any single pair would be a coin toss. The median only
    moves once most of the pairs agree.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'a': 1.0}, {'a': 1.0}, {'a': 1.0}])
    spoiled = _durations_tree(tmp, 'spoiled',
                              [{'a': 1.0}, {'a': 5.0}, {'a': 1.0}])
    assert compare.main(['--base', *base, '--head', *spoiled,
                         '--max-regression', '0.30']) == 0
    real = _durations_tree(tmp, 'real', [{'a': 1.4}, {'a': 5.0}, {'a': 1.4}])
    assert compare.main(['--base', *base, '--head', *real,
                         '--max-regression', '0.30']) == 1


def test_speed_comparison_refuses_to_pair_unequal_round_counts(tmp):
    """A side that lost a round cannot be paired against one that did not.

    Pairing the first N and dropping the rest would answer with a comparison
    narrower than the one that was asked for, and say nothing about it.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'a': 1.0}, {'a': 1.0}])
    head = _durations_tree(tmp, 'head', [{'a': 1.0}])
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', *base, '--head', *head, '--summary-file', str(summary)]
    assert compare.main(argv) == 0
    assert compare.main(argv + ['--require-measurements']) == 1
    assert 'cannot be paired' in summary.read_text(encoding='utf-8')


def test_speed_comparison_ignores_a_test_only_one_side_ran(tmp):
    """Adding or removing a test must not move the number.

    This is the whole reason the comparison is per-test rather than a suite
    total: a release that grew three tests would otherwise read as a
    regression, and one that deleted three as an improvement.
    """
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'shared': 1.0, 'gone': 50.0}])
    head = _durations_tree(tmp, 'head', [{'shared': 1.0, 'added': 50.0}])
    shared, pairs, _moves = compare.compare(compare.side_rounds(base),
                                            compare.side_rounds(head))
    assert shared == ['shared'], shared
    assert pairs == [(1.0, 1.0, 1.0)], pairs
    assert compare.main(['--base', *base, '--head', *head]) == 0


def test_speed_comparison_fails_only_past_its_budget(tmp):
    """A slowdown inside the budget passes; one past it fails."""
    compare = _compare_durations()
    base = _durations_tree(tmp, 'base', [{'a': 1.0}])
    within = _durations_tree(tmp, 'within', [{'a': 1.2}])
    past = _durations_tree(tmp, 'past', [{'a': 1.4}])
    assert compare.main(['--base', *base, '--head', *within,
                         '--max-regression', '0.30']) == 0
    assert compare.main(['--base', *base, '--head', *past,
                         '--max-regression', '0.30']) == 1


def test_speed_comparison_can_refuse_an_unmeasurable_run(tmp):
    """CI must not read "no data" as "no regression".

    Both no-data paths returned 0, so a speed job whose timing step produced
    nothing rendered a Skipped note and went green — the shape that reports
    clean forever. The lenient exit is still available for a hand run against
    a baseline that predates the durations format; --require-measurements is
    what the workflow passes.
    """
    empty_base = Path(tmp) / 'base'
    empty_head = Path(tmp) / 'head'
    for side in (empty_base, empty_head):
        side.mkdir(parents=True)
    summary = Path(tmp) / 'summary.md'
    argv = ['--base', str(empty_base), '--head', str(empty_head),
            '--base-label', 'baseline', '--summary-file', str(summary)]
    assert _compare_durations().main(argv) == 0
    assert _compare_durations().main(argv + ['--require-measurements']) == 1

    # A baseline that measured something, against a head that shares nothing
    # with it, is the second no-data path and answers the same way.
    (empty_base / 'suite.json').write_text(
        json.dumps({'tests': {'only_on_base': 1.0}}), encoding='utf-8')
    (empty_head / 'suite.json').write_text(
        json.dumps({'tests': {'only_on_head': 1.0}}), encoding='utf-8')
    assert _compare_durations().main(argv) == 0
    assert _compare_durations().main(argv + ['--require-measurements']) == 1


def test_speed_comparison_passes_when_a_side_was_never_measured(tmp):
    """An unmeasured side is not a fast side, and must not divide the total.

    The timing instrument belongs to the comparison rather than to either
    checkout, so an empty side means its timing step failed. A comparator that
    treated that as zero seconds would report an infinite speedup.
    """
    compare = _compare_durations()
    old = Path(tmp) / 'base-1'
    old.mkdir(parents=True)
    (old / 'test_suite.json').write_text(json.dumps({
        'total': 1, 'passed': 1, 'skipped': 0, 'failed': 0,
    }), encoding='utf-8')
    head = _durations_tree(tmp, 'head', [{'a': 1.0}])
    summary = Path(tmp) / 'summary.md'
    assert compare.main(['--base', str(old), '--head', *head,
                         '--summary-file', str(summary)]) == 0
    assert 'produced no per-test durations' in summary.read_text(encoding='utf-8')


def test_timing_a_tree_that_yields_nothing_is_a_failure(tmp):
    """A tree whose every suite fails measured nothing; it is not fast."""
    tree = Path(tmp) / 'tree'
    (tree / 'tests').mkdir(parents=True)
    (tree / 'tests' / 'test_nothing.py').write_text(
        'import sys\n'
        'print("  FAIL  test_a: deliberate")\n'
        'print("0/1 passed")\n'
        'sys.exit(1)\n', encoding='utf-8')
    out = Path(tmp) / 'out'
    code = _time_tests().main(
        ['--tree', str(tree), '--python', sys.executable, '--out', str(out)])
    assert code == 1, code


def test_checkout_reader_returns_structured_checkout_refs(tmp):
    """The reader exposes checkout refs from the workflow structure."""
    del tmp
    workflow = (
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: ${{ github.sha }}\n'
        '  test:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n')
    assert _wfcheckout.checkout_refs(workflow) == [
        ('build', '${{ github.sha }}')]


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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedmeasurement_')


if __name__ == '__main__':
    raise SystemExit(main())
