#!/usr/bin/env python3
"""Equivalent YAML spellings must read identically, malformed ones must not.

The workflow-shape readers exist for STRUCTURE; expression validity is
already gated upstream by the pinned `actionlint` job. So a rewrite that
preserves the parsed value — quoting a scalar, writing a sequence or a
mapping in flow style, parenthesising an expression — must not change what
these tests read, while every shape the readers refuse today must still be
refused. Each guard is planted in a copy of a real workflow written to a
temp directory, because a synthetic fixture only shows what the reader
thinks it reads.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlread import YAMLReadError, job_scalar  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from _wfgraph import (  # noqa: E402
    _job_if_expression, _job_needs, _job_output_mapping,
    _job_output_step_ids, _tests_yml)


PLAIN_IF = '    if: ${{ !cancelled() && !failure() }}\n'
BLOCK_NEEDS = (
    '    needs:\n'
    '      - changes\n'
    '      - pycodestyle\n'
    '      - pylint\n'
    '      - pyright\n'
    '      - eslint\n'
    '      - actionlint\n')
BLOCK_OUTPUTS = (
    '    outputs:\n'
    '      matrix: ${{ steps.classify.outputs.matrix }}\n'
    '      docs_only: ${{ steps.classify.outputs.docs_only }}\n'
    '      workflows: ${{ steps.classify.outputs.workflows }}\n')


def _real(tmp, source, name='tests.yml'):
    """Write one mutated real workflow out and read it back as a target."""
    path = os.path.join(tmp, name)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(source)
    with open(path, encoding='utf-8', newline='') as handle:
        return handle.read()


def _replaced(old, new):
    """Return tests.yml with one real block swapped for a rewrite of it."""
    workflow = _tests_yml()
    assert old in workflow, old
    mutated = workflow.replace(old, new, 1)
    assert mutated != workflow
    return mutated


def _refuses(call, *args):
    """Return the message from the refusal `call` must raise."""
    try:
        call(*args)
    except (AssertionError, ValueError, YAMLReadError) as error:
        return f'{type(error).__name__}: {error}'
    raise AssertionError(f'{call.__name__} accepted the planted defect')


def test_quoted_job_if_reads_like_the_plain_spelling(tmp):
    """Quoting an if scalar cannot change the expression it carries."""
    plain = _job_if_expression(_tests_yml(), 'suites')
    assert plain == '${{ !cancelled() && !failure() }}', plain
    quoted = (
        '    if: "${{ !cancelled() && !failure() }}"\n',
        "    if: '${{ !cancelled() && !failure() }}'\n",
        '    if: "${{ !cancelled() && !failure() }}"  # quoted\n',
    )
    for spelling in quoted:
        workflow = _real(tmp, _replaced(PLAIN_IF, spelling))
        assert _job_if_expression(workflow, 'suites') == plain, spelling


def test_double_quoted_escapes_decode_to_the_plain_if(tmp):
    """A double-quoted escape resolves to the same expression value."""
    plain = _job_if_expression(_tests_yml(), 'suites')
    spelling = '    if: "${{ \\x21cancelled() && \\x21failure() }}"\n'
    workflow = _real(tmp, _replaced(PLAIN_IF, spelling))
    assert _job_if_expression(workflow, 'suites') == plain


def test_flow_needs_sequence_reads_like_the_block_sequence(tmp):
    """A flow sequence names the same jobs as the block sequence."""
    block = _job_needs(_tests_yml(), 'suites')
    assert block == ['changes', 'pycodestyle', 'pylint', 'pyright',
                     'eslint', 'actionlint'], block
    spellings = (
        '    needs: [changes, pycodestyle, pylint, pyright, eslint,'
        ' actionlint]\n',
        "    needs: ['changes', \"pycodestyle\", pylint, pyright, eslint,"
        ' actionlint]\n',
    )
    for spelling in spellings:
        workflow = _real(tmp, _replaced(BLOCK_NEEDS, spelling))
        assert _job_needs(workflow, 'suites') == block, spelling


def test_one_dependency_reads_the_same_in_all_three_shapes(tmp):
    """A bare scalar, a block sequence and a flow sequence name one job."""
    spellings = (
        '    needs: changes\n',
        '    needs:\n      - changes\n',
        '    needs: [changes]\n',
        "    needs: ['changes']\n",
    )
    for spelling in spellings:
        workflow = _real(tmp, _replaced(BLOCK_NEEDS, spelling))
        assert _job_needs(workflow, 'suites') == ['changes'], spelling


def test_an_apostrophe_inside_a_flow_item_is_content(tmp):
    """A quote opens a node only at node start; elsewhere it is content."""
    spellings = {
        "    needs: [don't, x]\n": ["don't", 'x'],
        "    needs: [it's]\n": ["it's"],
        "    needs: [a, 5 o'clock]\n": ['a', "5 o'clock"],
        "    needs: [dont, ca'nt]\n": ['dont', "ca'nt"],
    }
    for spelling, expected in spellings.items():
        workflow = _real(tmp, _replaced(BLOCK_NEEDS, spelling))
        assert _job_needs(workflow, 'suites') == expected, spelling


def test_a_quoted_flow_item_keeps_its_own_punctuation(tmp):
    """Quoting is what makes a comma, a quote or a backslash content."""
    spellings = {
        "    needs: ['a,b', pylint]\n": ['a,b', 'pylint'],
        "    needs: ['a''b,c', pylint]\n": ["a'b,c", 'pylint'],
        '    needs: ["a\\"b,c", pylint]\n': ['a"b,c', 'pylint'],
    }
    for spelling, expected in spellings.items():
        workflow = _real(tmp, _replaced(BLOCK_NEEDS, spelling))
        assert _job_needs(workflow, 'suites') == expected, spelling


def test_a_flow_collection_may_span_lines(tmp):
    """Wrapping a flow collection over lines cannot change what it names."""
    block = _job_needs(_tests_yml(), 'suites')
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS,
        '    needs: [changes, pycodestyle, pylint,  # the fast gates\n'
        '            pyright, eslint, actionlint]\n'))
    assert _job_needs(workflow, 'suites') == block
    outputs = _job_output_mapping(_tests_yml(), 'changes')
    workflow = _real(tmp, _replaced(
        BLOCK_OUTPUTS,
        "    outputs: {matrix: '${{ steps.classify.outputs.matrix }}',\n"
        "              docs_only: '${{ steps.classify.outputs.docs_only }}',\n"
        "              workflows:"
        " '${{ steps.classify.outputs.workflows }}'}\n"), 'outputs.yml')
    assert _job_output_mapping(workflow, 'changes') == outputs
    assert _job_output_step_ids(workflow, 'changes') == {'classify'}


def test_a_flow_collection_spans_lines_at_any_indentation(tmp):
    """A collection's extent is its own brackets, not the block's indent.

    Its continuation may sit at the key's own indentation and its closing
    bracket may stand alone on a line; both are the same one value, so the
    field enumerator must not claim a line the collection still holds open.
    """
    block = _job_needs(_tests_yml(), 'suites')
    spellings = (
        '    needs: [\n'
        '      changes, pycodestyle, pylint,\n'
        '      pyright, eslint, actionlint\n'
        '    ]\n',
        '    needs: [changes, pycodestyle, pylint,\n'
        '    pyright, eslint, actionlint]\n',
    )
    for spelling in spellings:
        workflow = _real(tmp, _replaced(BLOCK_NEEDS, spelling))
        assert _job_needs(workflow, 'suites') == block, spelling
    outputs = _job_output_mapping(_tests_yml(), 'changes')
    workflow = _real(tmp, _replaced(
        BLOCK_OUTPUTS,
        "    outputs: {matrix: '${{ steps.classify.outputs.matrix }}',\n"
        "    docs_only: '${{ steps.classify.outputs.docs_only }}',\n"
        "    workflows: '${{ steps.classify.outputs.workflows }}'}\n"),
        'outputs.yml')
    assert _job_output_mapping(workflow, 'changes') == outputs


def test_a_closed_flow_collection_claims_no_further_line(tmp):
    """Closing on its first line ends the collection's extent there."""
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS, '    needs: [changes, pylint]\n      pyright\n'))
    assert 'unsupported nested content' in _refuses(
        _job_needs, workflow, 'suites')


def test_a_block_sequence_item_may_span_lines_too(tmp):
    """A sequence item is a value like any other, so it may span lines."""
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS,
        '    needs:\n'
        '      - [changes, pycodestyle,\n'
        '         pylint]\n'
        '      - pyright\n'))
    assert complete_job_mapping(workflow, 'suites')['needs'] == [
        ['changes', 'pycodestyle', 'pylint'], 'pyright']


def test_a_multiline_plain_scalar_folds_into_one_value(tmp):
    """A plain scalar the workflow wraps is one value, not half of one.

    Folding joins the lines with the single space YAML would and strips each
    line's own comment, so a comment on the first line cannot truncate the
    condition at it.
    """
    folded = '${{ !cancelled() && !failure() }}'
    workflow = _real(tmp, _replaced(
        PLAIN_IF,
        '    if: ${{ !cancelled()\n            && !failure() }}\n'))
    assert job_scalar(workflow, 'suites', 'if') == folded
    assert complete_job_mapping(workflow, 'suites')['if'] == folded
    assert _job_if_expression(workflow, 'suites') == folded
    workflow = _real(tmp, _replaced(
        PLAIN_IF,
        '    if: ${{ !cancelled()  # never when cancelled\n'
        '            && !failure() }}\n'), 'comment.yml')
    assert job_scalar(workflow, 'suites', 'if') == folded
    assert complete_job_mapping(workflow, 'suites')['if'] == folded
    assert _job_if_expression(workflow, 'suites') == folded


def test_the_shipped_wrapped_conditions_read_the_same_both_ways(tmp):
    """The workflow the readers exist for wraps three job conditions."""
    del tmp
    workflow = _tests_yml()
    for job in ('actionlint', 'coverage', 'diff-coverage'):
        folded = _job_if_expression(workflow, job)
        assert folded is not None and '\n' not in folded, job
        assert complete_job_mapping(workflow, job)['if'] == folded, job


def test_a_flow_collection_spanning_lines_must_still_close(tmp):
    """Spanning lines buys no relief from the flow grammar."""
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS, '    needs: [changes,\n            pylint\n'))
    assert 'unbalanced flow collection' in _refuses(
        _job_needs, workflow, 'suites')
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS, '    needs: [changes,\n            ${{ x }}]\n'),
        'indicator.yml')
    assert 'flow indicator' in _refuses(_job_needs, workflow, 'suites')


def test_flow_outputs_mapping_reads_like_the_block_mapping(tmp):
    """A flow mapping carries the same outputs as the block mapping."""
    block = _job_output_mapping(_tests_yml(), 'changes')
    assert block is not None and set(block) == {
        'matrix', 'docs_only', 'workflows'}, block
    spelling = (
        "    outputs: {matrix: '${{ steps.classify.outputs.matrix }}',"
        " docs_only: '${{ steps.classify.outputs.docs_only }}',"
        " workflows: '${{ steps.classify.outputs.workflows }}'}\n")
    workflow = _real(tmp, _replaced(BLOCK_OUTPUTS, spelling))
    assert _job_output_mapping(workflow, 'changes') == block
    assert _job_output_step_ids(workflow, 'changes') == {'classify'}


def test_parenthesised_output_reference_names_the_same_step(tmp):
    """Parentheses and spacing cannot hide the step an output reads."""
    plain = _job_output_step_ids(_tests_yml(), 'changes')
    assert plain == {'classify'}, plain
    spellings = (
        '      matrix: ${{ (steps.classify.outputs.matrix) }}\n',
        '      matrix: ${{((steps.classify.outputs.matrix))}}\n',
        "      matrix: '${{   steps.classify.outputs.matrix   }}'\n",
    )
    original = '      matrix: ${{ steps.classify.outputs.matrix }}\n'
    for spelling in spellings:
        workflow = _real(tmp, _replaced(original, spelling))
        assert _job_output_step_ids(workflow, 'changes') == plain, spelling


def test_an_output_reference_must_be_exactly_one_step_lookup(tmp):
    """Each limb of the `steps.<id>.outputs.<name>` shape is pinned alone.

    Every spelling differs from the accepted reference in exactly one field,
    so dropping any single limb leaves one of them accepted.
    """
    original = '      matrix: ${{ steps.classify.outputs.matrix }}\n'
    spellings = (
        '      matrix: ${{ steps.classify.env.matrix }}\n',
        '      matrix: ${{ steps.classify.outputs }}\n',
        '      matrix: ${{ steps.classify.outputs.matrix.value }}\n',
        '      matrix: ${{ needs.changes.outputs.matrix }}\n',
    )
    for spelling in spellings:
        workflow = _real(tmp, _replaced(original, spelling))
        assert 'unsupported expression' in _refuses(
            _job_output_step_ids, workflow, 'changes'), spelling


def test_both_readers_decode_one_scalar_spelling_set(tmp):
    """_yamlread and _yamlsteps must agree on every equivalent spelling.

    A refusal is as much part of the set as a value: a spelling one reader
    reads and the other refuses is a disagreement, and an anchor or a tag
    read raw is a WRONG value rather than a refusal, so the shapes the
    readers once disagreed on are pinned here beside the ones they share.
    """
    spellings = (
        '${{ !cancelled() && !failure() }}',
        "'${{ !cancelled() && !failure() }}'",
        '"${{ !cancelled() && !failure() }}"',
        '"${{ \\x21cancelled() && \\x21failure() }}"',
        '${{ !cancelled() && !failure() }}  # why',
        '"${{ !cancelled() && !failure() }}"  # why',
    )
    for spelling in spellings:
        source = _real(tmp, 'jobs:\n  sample:\n    if: ' + spelling + '\n')
        scalar = job_scalar(source, 'sample', 'if')
        complete = complete_job_mapping(source, 'sample')
        assert complete is not None, spelling
        complete = complete['if']
        assert scalar == '${{ !cancelled() && !failure() }}', spelling
        assert complete == scalar, (spelling, complete, scalar)
    for spelling in ('a: b', '&anchor x', '!!str x', '*anchor', '@x',
                     '`x`', 'a\x01b'):
        source = _real(tmp, 'jobs:\n  sample:\n    if: ' + spelling + '\n')
        assert 'unsupported' in _refuses(
            job_scalar, source, 'sample', 'if'), spelling
        assert 'unsupported' in _refuses(
            complete_job_mapping, source, 'sample'), spelling


def test_tab_indentation_is_still_refused(tmp):
    """A tab where the reader expects spaces stays a refusal."""
    workflow = _real(tmp, _replaced(
        '      matrix: ${{ steps.classify.outputs.matrix }}\n',
        '      \tmatrix: ${{ steps.classify.outputs.matrix }}\n'))
    assert 'tab' in _refuses(_job_output_mapping, workflow, 'changes')
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS, '    needs:\n      \t- changes\n'), 'needs.yml')
    assert 'tab' in _refuses(_job_needs, workflow, 'suites')


def test_a_sequence_where_outputs_belong_is_still_refused(tmp):
    """Outputs written as a sequence is a shape, not a spelling."""
    workflow = _real(tmp, _replaced(
        BLOCK_OUTPUTS, '    outputs:\n      - matrix\n'))
    assert 'outputs is not a mapping' in _refuses(
        _job_output_mapping, workflow, 'changes')


def test_a_duplicate_job_field_is_still_refused(tmp):
    """Two if fields on one job stay a refusal, quoted or not."""
    workflow = _real(tmp, _replaced(
        PLAIN_IF, PLAIN_IF + '    if: "${{ always() }}"\n'))
    assert 'duplicate job field' in _refuses(
        _job_if_expression, workflow, 'suites')


def test_an_unbalanced_flow_collection_is_refused(tmp):
    """A flow collection that never closes is not an equivalent spelling."""
    for spelling in ('    needs: [changes, pylint\n',
                     '    needs: [changes]]\n',
                     '    needs: [changes, [pylint]\n'):
        workflow = _real(tmp, _replaced(BLOCK_NEEDS, spelling))
        assert 'unbalanced flow collection' in _refuses(
            _job_needs, workflow, 'suites'), spelling


def test_a_trailing_comma_ends_a_flow_collection(tmp):
    """A trailing separator closes a collection; an interior gap does not."""
    workflow = _real(tmp, _replaced(BLOCK_NEEDS, '    needs: [changes, ]\n'))
    assert _job_needs(workflow, 'suites') == ['changes']
    workflow = _real(tmp, _replaced(
        BLOCK_OUTPUTS, "    outputs: {matrix: 'built',}\n"), 'outputs.yml')
    assert _job_output_mapping(workflow, 'changes') == {'matrix': 'built'}
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS, '    needs: [changes, , pylint]\n'), 'interior.yml')
    assert 'empty flow item' in _refuses(_job_needs, workflow, 'suites')


def test_a_flow_indicator_inside_a_plain_scalar_is_refused(tmp):
    """An unquoted flow scalar may not carry any of `,[]{}`."""
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS, '    needs: [${{ matrix.suite }}]\n'))
    assert 'flow indicator' in _refuses(_job_needs, workflow, 'suites')
    workflow = _real(tmp, _replaced(
        BLOCK_NEEDS, '    needs: [a{b}c]\n'), 'brace.yml')
    assert 'flow indicator' in _refuses(_job_needs, workflow, 'suites')
    workflow = _real(tmp, _replaced(
        BLOCK_OUTPUTS,
        '    outputs: {matrix: ${{ steps.classify.outputs.matrix }}}\n'),
        'mapping.yml')
    assert 'flow indicator' in _refuses(
        _job_output_mapping, workflow, 'changes')


def test_an_incomplete_quoted_scalar_is_refused(tmp):
    """An unterminated quote cannot decode to the value it looks like."""
    for spelling in ('    if: "${{ always() }}\n',
                     "    if: '${{ always() }}\n",
                     '    if: "${{ always() }}" trailing\n'):
        workflow = _real(tmp, _replaced(PLAIN_IF, spelling))
        assert 'incomplete quoted scalar' in _refuses(
            _job_if_expression, workflow, 'suites'), spelling


def test_an_unrecognized_output_expression_is_still_refused(tmp):
    """A reference the reader cannot resolve to one step stays refused."""
    original = '      matrix: ${{ steps.classify.outputs.matrix }}\n'
    for spelling in (
            "      matrix: ${{ steps[format('a{0}', 'b')].outputs.v }}\n",
            '      matrix: ${{ steps.classify.outputs.matrix == \'x\' }}\n',
            '      matrix: steps.classify.outputs.matrix\n',
            '      matrix: ${{ (steps.classify.outputs.matrix }}\n'):
        workflow = _real(tmp, _replaced(original, spelling))
        assert 'unsupported expression' in _refuses(
            _job_output_step_ids, workflow, 'changes'), spelling


def test_the_planted_targets_come_from_the_real_workflow(tmp):
    """Every mutation above is anchored in the shipped tests.yml."""
    del tmp
    source = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    for block in (PLAIN_IF, BLOCK_NEEDS, BLOCK_OUTPUTS):
        assert block in source, block


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
