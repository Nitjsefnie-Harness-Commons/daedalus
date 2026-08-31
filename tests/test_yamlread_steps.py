#!/usr/bin/env python3
"""Executable contracts for locating named workflow steps."""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _yamlsteps  # noqa: E402
from _yamlread import (  # noqa: E402
    YAMLReadError, _comment, job_mapping, job_scalar, step_scalar,
    step_scalars,
)
from _yamlscalar import decode_inline_scalar  # noqa: E402
from workflow_yaml import workflow_step_items  # noqa: E402


def _raises(source, detail):
    """Require unsupported step-name syntax to be refused."""
    try:
        step_scalar(source, 'sample', 'target', 'if')
    except YAMLReadError as error:
        assert detail in str(error), str(error)
        return
    raise AssertionError(f'{detail} step-name syntax was accepted')


def _job_raises(source, detail):
    """Require unsupported job-scalar syntax to be refused."""
    try:
        job_scalar(source, 'sample', 'if')
    except YAMLReadError as error:
        assert detail in str(error), str(error)
        return
    raise AssertionError(f'{detail} job syntax was accepted')


def test_step_name_can_follow_another_mapping_field(tmp):
    """A later name field still identifies its sequence item."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - x: y\n     name: target\n     if: z")
    assert step_scalar(source, 'sample', 'target', 'if') == 'z'


def test_step_scalar_reads_sequence_item_mapping_field(tmp):
    """The field carried after the dash belongs to the step mapping."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - if: >-\n"
        "       first\n"
        "       && second\n"
        "     name: target\n")
    try:
        value = step_scalar(source, 'sample', 'target', 'if')
    except YAMLReadError as error:
        raise AssertionError(
            f'sequence-item scalar was not decoded: {error}') from error
    assert value == 'first && second', value


def test_equivalent_quoted_step_keys_are_duplicates(tmp):
    """Quoted and plain spellings cannot define one field twice."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: target\n"
        "     if: z\n"
        "     'if': false\n")
    try:
        step_scalar(source, 'sample', 'target', 'if')
    except YAMLReadError as error:
        assert 'duplicate mapping key: if' in str(error), str(error)
        return
    raise AssertionError('equivalent duplicate if keys were accepted')


def test_step_name_inline_comment_is_refused(tmp):
    """A name comment cannot silently turn a present step into absence."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: target #\n     if: z")
    _raises(source, 'inline comment')


def test_step_name_anchor_is_refused(tmp):
    """A YAML anchor on a name is outside the admitted step subset."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: &a target\n     if: z")
    _raises(source, 'anchor')


def test_aliased_step_name_is_refused(tmp):
    """A YAML alias on a name is outside the admitted step subset."""
    del tmp
    source = (
        "target-name: &target-name target\n"
        "jobs:\n sample:\n  steps:\n"
        "   - name: *target-name\n     if: z")
    _raises(source, 'alias')


def test_step_name_tag_is_refused(tmp):
    """A YAML tag on a name is outside the admitted step subset."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: ! target\n     if: z")
    _raises(source, 'tag')


def test_quoted_step_name_key_is_recognized(tmp):
    """An exactly quoted name key still names the step mapping field."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - 'name': target\n     if: z")
    assert step_scalar(source, 'sample', 'target', 'if') == 'z'


def test_double_quoted_step_name_key_is_recognized(tmp):
    """A double-quoted name key still names the step mapping field."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - \"name\": target\n     if: z")
    assert step_scalar(source, 'sample', 'target', 'if') == 'z'


def test_step_name_without_mapping_separator_is_refused(tmp):
    """A colon without following whitespace is not a mapping field."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name:target\n     if: z")
    _raises(source, 'unsupported YAML mapping line')


def test_non_ascii_mapping_separator_is_refused(tmp):
    """A no-break space cannot separate a mapping key and value."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name:\u00a0target\n     if: z")
    _raises(source, 'unsupported YAML mapping line')


def test_non_ascii_scalar_whitespace_is_not_normalized(tmp):
    """No-break spaces in scalar values remain data, not trim markers."""
    del tmp
    job_source = 'jobs:\n sample:\n  if: \u00a0z\u00a0\n'
    assert job_scalar(job_source, 'sample', 'if') == '\u00a0z\u00a0'
    step_source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: \u00a0target\u00a0\n     if: z")
    assert step_scalar(step_source, 'sample', 'target', 'if') is None
    block_source = 'jobs:\n sample:\n  if: |\n   \u00a0\n   z\n'
    assert job_scalar(block_source, 'sample', 'if') == '\u00a0\nz\n'
    _job_raises(
        'jobs:\n sample:\n  if: >-\u00a0\n   z\n',
        'unsupported block scalar header')


def test_non_ascii_structural_whitespace_is_refused(tmp):
    """No-break spaces cannot hide malformed structure or comments."""
    del tmp
    assert not _comment(('\u00a0# comment', False))
    _raises(
        'jobs: \u00a0\n sample:\n  steps:\n'
        '   - name: target\n     if: z',
        'jobs is not a mapping')
    _raises(
        'jobs:\n sample:\n  steps:\n'
        '   - name: target\n     \u00a0\n     if: z',
        'unsupported YAML mapping line')
    _raises(
        'jobs:\n sample:\n  steps:\n'
        '   - name: target\n     \u00a0# comment\n     if: z',
        'unsupported YAML mapping line')


def _coverage_workflow():
    """Read the privileged workflow whose steps must remain trusted."""
    path = _util.ROOT / '.github/workflows/coverage-comment.yml'
    return path.read_text(encoding='utf-8')


def _assert_no_privileged_checkout(workflow):
    """Refuse checkout by the decoded identity of every step action."""
    uses = step_scalars(workflow, 'comment', 'uses')
    assert uses is not None, 'privileged workflow steps were not decoded'
    for value in uses:
        identity = value.split('@', 1)[0].casefold()
        assert identity != 'actions/checkout', (
            f'privileged workflow decodes checkout action: {value}')


def _assert_checkout_mutation_refused(workflow):
    """Require one real-workflow checkout mutation to fail the contract."""
    try:
        _assert_no_privileged_checkout(workflow)
    except AssertionError as error:
        assert 'decodes checkout action' in str(error), str(error)
        return
    raise AssertionError('decoded checkout mutation was accepted')


def test_step_scalar_list_decodes_each_uses_spelling(tmp):
    """Plain, quoted, and escaped fields return decoded action values."""
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - uses: owner/one@abc\n'
        "      - 'uses': 'owner/two@def'\n"
        '      - "u\\x73es": "actions\\x2fcheckout@123"\n'
        '      - run: echo harmless\n')
    assert step_scalars(source, 'sample', 'uses') == [
        'owner/one@abc', 'owner/two@def', 'actions/checkout@123']


def test_step_scalar_list_decodes_folded_values(tmp):
    """A wrapped action pin remains visible to decoded step policy."""
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - uses: >-\n'
        '          owner/action@0123456789abcdef\n')
    try:
        values = step_scalars(source, 'sample', 'uses')
    except YAMLReadError as error:
        raise AssertionError(
            f'folded step scalar was not decoded: {error}') from error
    assert values == ['owner/action@0123456789abcdef'], values


def test_step_mappings_decode_every_field_and_nested_mapping(tmp):
    """The whole decoded step mapping is returned without selected keys."""
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - name: target\n'
        '        id: chosen\n'
        '        if: >-\n'
        '          success()\n'
        '          && true\n'
        '        env:\n'
        '          HEAD_SHA: ${{ github.sha }}\n'
        '          "QUOT\\x45D": \'decoded\'\n'
        '        with:\n'
        '          run-id: ${{ github.run_id }}\n'
        '        run: |\n'
        '          echo "$HEAD_SHA"\n'
        '        continue-on-error: false\n')
    assert _yamlsteps.step_mappings(source, 'sample') == [{
        'name': 'target',
        'id': 'chosen',
        'if': 'success() && true',
        'env': {
            'HEAD_SHA': '${{ github.sha }}',
            'QUOTED': 'decoded',
        },
        'with': {'run-id': '${{ github.run_id }}'},
        'run': 'echo "$HEAD_SHA"\n',
        'continue-on-error': 'false',
    }]


def test_comment_header_cannot_hide_a_following_step_field(tmp):
    """A comment that resembles a block header owns no scalar content."""
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - name: target\n'
        '      # note: |\n'
        '        id: chosen\n')
    items = workflow_step_items(source, 'sample')
    assert items is not None
    assert len(items) == 1, items
    assert items[0].identity == 'chosen', items[0]


def test_quoted_scalar_escapes_cannot_expose_forged_steps(tmp):
    """Escaped quotes in multiline data do not end the scalar early."""
    del tmp
    source = (
        "jobs:\n  sample:\n    steps:\n"
        "      - run: 'first'' piece\n"
        "        - name: forged-single\n"
        "        final'' piece'\n"
        "        name: single\n"
        '      - run: "first\\"\n'
        "        - name: forged-double\n"
        '        final\\" piece"\n'
        "        name: double\n")
    items = workflow_step_items(source, 'sample')
    assert items is not None
    assert [item.name for item in items] == ['single', 'double'], items


def test_incomplete_multiline_quote_is_refused(tmp):
    """An unterminated quoted scalar cannot hide the rest of a workflow."""
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - run: "never closes\n')
    try:
        workflow_step_items(source, 'sample')
    except YAMLReadError as error:
        assert str(error) == (
            'workflow has an incomplete quoted scalar'), error
        return
    raise AssertionError('an incomplete multiline quote was accepted')


def test_block_scalar_boundaries_preserve_sibling_steps(tmp):
    """Blank and empty block bodies cannot absorb sibling sequence items."""
    del tmp
    cases = (
        (
            'jobs:\n  sample:\n    steps:\n'
            '      - name: first\n'
            '        run: |\n'
            '\n'
            '          true\n',
            ['first'],
        ),
        (
            'jobs:\n  sample:\n    steps:\n'
            '      - name: first\n'
            '        run: |\n'
            '      - name: second\n',
            ['first', 'second'],
        ),
        (
            'jobs:\n  sample:\n    steps:\n'
            '      - name: first\n'
            '        run: |\n'
            '\n',
            ['first'],
        ),
    )
    for source, expected in cases:
        items = workflow_step_items(source, 'sample')
        assert items is not None
        assert [item.name for item in items] == expected, items


def test_block_scalar_with_two_indent_indicators_is_refused(tmp):
    """Two explicit indentation indicators are not a scalar header."""
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - name: target\n'
        '        run: >1+2\n'
        '          true\n')
    try:
        workflow_step_items(source, 'sample')
    except YAMLReadError as error:
        assert str(error) == (
            'workflow block has two indentation indicators'), error
        return
    raise AssertionError('two block indentation indicators were accepted')


def test_complete_workflow_and_job_mappings_decode_every_container(tmp):
    """Complete maps retain every nested workflow and job value."""
    del tmp
    source = (
        'name: sample\n'
        'on:\n'
        '  workflow_run:\n'
        '    workflows: [tests]\n'
        '    types: [completed]\n'
        'env:\n'
        '  WORKFLOW_ONLY: workflow\n'
        'defaults:\n'
        '  run:\n'
        "    shell: bash -c 'bash \"$1\"' -- {0}\n"
        'concurrency:\n'
        '  group: sample\n'
        '  cancel-in-progress: true\n'
        'jobs:\n'
        '  sample:\n'
        '    runs-on: ubuntu-latest\n'
        '    timeout-minutes: 10\n'
        '    services: {}\n'
        '    env:\n'
        '      JOB_ONLY: job\n'
        '    steps:\n'
        '      - name: target\n'
        '        run: |\n'
        '          true\n')
    workflow_mapping = getattr(_yamlsteps, 'workflow_mapping', None)
    complete_job_mapping = getattr(
        _yamlsteps, 'complete_job_mapping', None)
    assert callable(workflow_mapping), (
        'complete workflow mapping decoder is missing')
    assert callable(complete_job_mapping), (
        'complete job mapping decoder is missing')
    expected_job = {
        'runs-on': 'ubuntu-latest',
        'timeout-minutes': '10',
        'services': {},
        'env': {'JOB_ONLY': 'job'},
        'steps': [{'name': 'target', 'run': 'true\n'}],
    }
    expected = {
        'name': 'sample',
        'on': {
            'workflow_run': {
                'workflows': ['tests'],
                'types': ['completed'],
            },
        },
        'env': {'WORKFLOW_ONLY': 'workflow'},
        'defaults': {
            'run': {'shell': 'bash -c \'bash "$1"\' -- {0}'},
        },
        'concurrency': {
            'group': 'sample',
            'cancel-in-progress': 'true',
        },
        'jobs': {'sample': expected_job},
    }
    assert complete_job_mapping(source, 'sample') == expected_job
    assert workflow_mapping(source) == expected


def test_plain_scalar_oracle_corpus_refuses_unsafe_spellings(tmp):
    """The bounded grammar refuses every unsafe plain scalar spelling."""
    del tmp
    sha = '3d3c42e5aac5ba805825da76410c181273ba90b1'
    checkout = f'actions/checkout@{sha}'
    spellings = {
        checkout, f"'{checkout}'", f'"{checkout}"',
        f'"actions\\/checkout@{sha}"',
        f'"actions\\x2fcheckout@{sha}"',
        f'"actions\\u002fcheckout@{sha}"',
        f'"actions\\U0000002fcheckout@{sha}"',
        "'a''b'", '"a\\tb"', '"a\\Nb"', '"a\\_b"',
        '"a\\Lb"', '"a\\Pb"', 'plain', 'a:b', 'a::b', '-', ':',
        '@bad', 'foo # comment', "'foo # data' # comment",
        '"foo # data" # comment',
    }
    alphabet = 'aA09-_.@/+:$()'
    for length in range(1, 4):
        spellings.update(
            ''.join(chars)
            for chars in itertools.product(alphabet, repeat=length))
    assert len(spellings) == 2974, len(spellings)
    accepted = []
    for spelling in sorted(spellings):
        try:
            decode_inline_scalar(spelling, 'oracle corpus')
        except YAMLReadError:
            continue
        if (':' in spelling or spelling == '-'
                or spelling.startswith('@')):
            accepted.append(spelling)
    assert not accepted, (
        f'accepted {len(accepted)} unsafe spellings: {accepted[:20]!r}')


def test_real_workflow_trailing_colon_action_is_refused(tmp):
    """A runtime-invalid action scalar cannot pass trusted step policy."""
    del tmp
    workflow = _coverage_workflow()
    action = (
        'actions/download-artifact@'
        '37930b1c2abaa49bbe596cd826c3c89aef350131'
    )
    folded = f'        uses: >-\n          {action}\n'
    mutated = workflow.replace(folded, f'        uses: {action}:\n', 1)
    assert mutated != workflow, 'real download action was not mutated'
    try:
        step_scalars(mutated, 'comment', 'uses')
    except YAMLReadError as error:
        assert 'unsupported plain scalar' in str(error), str(error)
        return
    raise AssertionError('runtime-invalid trailing-colon action was accepted')


def test_privileged_workflow_has_no_decoded_checkout(tmp):
    """Every action in the trusted workflow is decoded before policy."""
    del tmp
    _assert_no_privileged_checkout(_coverage_workflow())


def test_escaped_checkout_value_mutation_is_refused(tmp):
    """The review's escaped checkout value cannot evade decoded policy."""
    del tmp
    workflow = _coverage_workflow()
    step = (
        '      - name: Check out pull-request code\n'
        '        uses: "actions\\x2fcheckout@'
        '3d3c42e5aac5ba805825da76410c181273ba90b1"\n'
        '        with:\n'
        '          ref: ${{ github.event.workflow_run.head_sha }}\n'
        '          persist-credentials: false\n\n')
    mutated = workflow.replace('    steps:\n', '    steps:\n' + step, 1)
    assert mutated != workflow, 'real privileged steps were not mutated'
    _assert_checkout_mutation_refused(mutated)


def test_quoted_and_escaped_uses_keys_are_refused(tmp):
    """Equivalent decoded uses keys cannot hide a checkout action."""
    del tmp
    workflow = _coverage_workflow()
    fields = ("        'uses': ", '        "u\\x73es": ')
    action = 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1'
    for field in fields:
        step = '      - name: Checkout spelling\n' + field + action + '\n'
        mutated = workflow.replace('    steps:\n', '    steps:\n' + step, 1)
        assert mutated != workflow, field
        _assert_checkout_mutation_refused(mutated)


def test_a_step_scalar_stops_where_its_own_field_stops(tmp):
    """A later sibling's nested block is not this scalar's continuation.

    The shipped values are the proof: reading `id` as the id plus the whole
    `run:` script is a value no parser produces, on source the suites read.
    """
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - name: s\n'
        '        shell: bash\n'
        '        run: |\n'
        '          echo hi\n')
    assert step_scalar(source, 'sample', 's', 'shell') == 'bash'
    tests_yml = (_util.ROOT / '.github/workflows/tests.yml').read_text(
        encoding='utf-8')
    speed_yml = (_util.ROOT / '.github/workflows/speed.yml').read_text(
        encoding='utf-8')
    measured = "${{ !cancelled() && steps.measure.conclusion == 'success' }}"
    shipped = (
        (tests_yml, 'actionlint', 'Install zizmor', 'id', 'install_zizmor'),
        (tests_yml, 'actionlint', 'actionlint', 'id', 'actionlint'),
        (tests_yml, 'coverage', 'Work out the raise this run justifies',
         'id', 'ratchet'),
        (tests_yml, 'coverage', 'Python coverage summary', 'if', measured),
        (tests_yml, 'coverage', 'JavaScript coverage summary', 'if',
         measured),
        (tests_yml, 'coverage', 'JavaScript coverage gate', 'if', measured),
        (tests_yml, 'coverage', 'Work out the raise this run justifies', 'if',
         "${{ !cancelled() && steps.measure.conclusion == 'success'"
         " && github.event_name == 'push'"
         " && github.ref == 'refs/heads/main' }}"),
        (speed_yml, 'timed', 'Build one virtualenv per side', 'if',
         "steps.baseline.outputs.point != ''"),
    )
    for workflow, job, step, key, expected in shipped:
        assert step_scalar(workflow, job, step, key) == expected, (
            job, step, key)


def test_every_reader_folds_a_wrapped_plain_scalar(tmp):
    """The subset folds plain scalars, so no entry point may refuse one.

    A reader that refuses what its siblings fold makes one workflow two
    documents depending on which reader opened it.
    """
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - name: s\n'
        '        run: python3 x.py --a\n'
        '          --b\n')
    assert _yamlsteps.step_mappings(source, 'sample') == [
        {'name': 's', 'run': 'python3 x.py --a --b'}]
    assert step_scalar(
        source, 'sample', 's', 'run') == 'python3 x.py --a --b'
    source = (
        'jobs:\n  sample:\n    env:\n'
        '      A: one\n'
        '        two\n'
        '      B: three\n')
    assert job_mapping(source, 'sample', 'env') == {
        'A': 'one two', 'B': 'three'}


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
