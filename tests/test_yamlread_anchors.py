#!/usr/bin/env python3
"""Executable contracts for node properties and aliases on a step scalar."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))

from workflow_yaml import (  # noqa: E402
    YAMLReadError, workflow_step_items,
)

_GATE_STEP = '      - name: Python coverage gate\n'


def _document(field, prefix=''):
    """A one-step workflow whose step carries `field` before an `if`."""
    return (f'{prefix}jobs:\n sample:\n  steps:\n'
            f'   - {field}\n     if: z\n')


def _names(source):
    """The decoded name of every step in the sample job."""
    items = workflow_step_items(source, 'sample')
    assert items is not None, source
    return [item.name for item in items]


def _identities(source):
    """The decoded id of every step in the sample job."""
    items = workflow_step_items(source, 'sample')
    assert items is not None, source
    return [item.identity for item in items]


def _refused(source, detail):
    """Require the sample job's steps to be refused, naming `detail`."""
    try:
        workflow_step_items(source, 'sample')
    except YAMLReadError as error:
        assert detail in str(error), str(error)
        return
    raise AssertionError(f'a spelling {detail!r} must refuse was accepted')


def test_anchored_step_name_decodes_like_the_plain_spelling(tmp):
    """An anchor in front of a name is a property, not part of the value."""
    del tmp
    assert _names(_document('name: &a target')) == ['target']


def test_string_tagged_step_name_decodes_like_the_plain_spelling(tmp):
    """`!!str` resolves to the string the plain spelling already gives."""
    del tmp
    assert _names(_document('name: !!str target')) == ['target']


def test_the_non_specific_tag_is_refused_in_every_scalar_form(tmp):
    """`!` resolves by default resolution, not to a string, so no form.

    PyYAML reads `! 5` as the number 5, `! true` as true and `! null` as
    null, and a quoted or block body changes nothing, so accepting any
    spelling of it would return a value no parser returns.
    """
    del tmp
    for field in ('name: ! 5', 'name: ! true', 'name: ! null',
                  'name: ! "5"'):
        _refused(_document(field), 'unsupported YAML tag !')


def test_the_non_specific_tag_is_refused_on_a_block_scalar(tmp):
    del tmp
    _refused('jobs:\n sample:\n  steps:\n'
             '   - name: ! |\n       5\n     if: z\n',
             'unsupported YAML tag !')


def test_a_string_tagged_number_and_bool_decode_to_strings(tmp):
    """`!!str` is the one tag whose resolution is guaranteed text."""
    del tmp
    for field, decoded in (('name: !!str 5', '5'),
                           ('name: !!str true', 'true')):
        assert _names(_document(field)) == [decoded], field


def test_an_anchor_and_a_tag_decode_in_either_order(tmp):
    """Both properties may precede one value, in whichever order."""
    del tmp
    for field in ('name: &a !!str target', 'name: !!str &a target'):
        assert _names(_document(field)) == ['target'], field


def test_a_quoted_name_after_properties_keeps_hash_sign_text(tmp):
    del tmp
    for field in ('name: &a "one # two"', 'name: !!str "one # two"'):
        assert _names(_document(field)) == ['one # two'], field


def test_a_quoted_name_continued_past_a_property_is_a_boundary(tmp):
    """A multiline quoted scalar is an unsupported boundary, not invalid."""
    del tmp
    _refused(_document('name: &a "one\n          two"'),
             'unsupported multiline scalar')


def test_aliased_step_name_decodes_to_the_anchored_value(tmp):
    """An alias reads as the value its anchor was attached to."""
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a target\n')
    assert _names(source) == ['target']


def test_step_id_accepts_every_property_spelling(tmp):
    """The id field takes the properties and aliases a name takes."""
    del tmp
    prefix = 'anchors:\n gate: &a target\n'
    for field in ('id: &b target', 'id: !!str target',
                  'id: &b !!str target', 'id: *a'):
        assert _identities(_document(field, prefix)) == ['target'], field
    _refused(_document('id: ! target', prefix), 'unsupported YAML tag !')


def test_anchored_block_scalar_name_decodes_like_the_plain_block(tmp):
    """An anchored block header still opens a block, body lines and all.

    The body's unclosed quote is content inside a block and an unterminated
    scalar outside one, so a scan blind to the header refuses the file.
    """
    del tmp
    body = ('jobs:\n sample:\n  steps:\n'
            '   - name: {header}\n'
            '       key: "one\n'
            '       two\n'
            '     if: z\n')
    plain = _names(body.format(header='|'))
    assert plain == ['key: "one\ntwo\n'], plain
    assert _names(body.format(header='&a |')) == plain


def test_an_alias_resolves_to_the_last_anchor_defined_before_it(tmp):
    """A redefined anchor name resolves to its most recent definition."""
    del tmp
    prefix = 'anchors:\n first: &a wrong\n second: &a target\n'
    assert _names(_document('name: *a', prefix)) == ['target']


def test_an_alias_reads_an_anchor_defined_on_an_earlier_step_field(tmp):
    """A field written after a sequence dash still anchors its own value."""
    del tmp
    source = ('jobs:\n sample:\n  steps:\n'
              '   - id: &a target\n     if: z\n'
              '   - name: *a\n     if: z\n')
    assert _names(source) == [None, 'target']


def test_an_alias_with_no_earlier_anchor_is_refused(tmp):
    """An undefined alias is a refusal, never the empty string."""
    del tmp
    _refused(_document('name: *missing'), 'unknown YAML alias: &missing')


def test_a_decoy_anchor_in_content_defines_no_alias_target(tmp):
    """An `&a` inside a block body, a quoted scalar or a comment is text."""
    del tmp
    for prefix in ('anchors:\n gate: |\n  &a hidden\n',
                   'anchors:\n gate: "&a hidden"\n',
                   'anchors:\n# gate: &a hidden\n gate: real\n'):
        _refused(_document('name: *a', prefix), 'unknown YAML alias: &a')


def test_a_colon_bearing_decoy_in_a_block_body_defines_no_target(tmp):
    """A mapping field spelled inside block content anchors nothing."""
    del tmp
    _refused(_document('name: *a', 'anchors:\n gate: |\n  k: &a hidden\n'),
             'unknown YAML alias: &a')


def test_a_colon_bearing_decoy_in_a_quoted_scalar_defines_no_target(tmp):
    """A mapping field on a quoted scalar's continuation anchors nothing."""
    del tmp
    _refused(_document(
        'name: *a',
        'anchors:\n gate: "k: &a hidden\n  j: &a deeper"\n'),
        'unknown YAML alias: &a')


def test_an_alias_refuses_an_anchor_defined_after_it(tmp):
    """PyYAML composes aliases against earlier definitions only."""
    del tmp
    source = ('jobs:\n sample:\n  steps:\n'
              '   - name: *a\n     if: z\n'
              'anchors:\n gate: &a target\n')
    _refused(source, 'unknown YAML alias: &a')


def test_a_quote_opening_after_a_property_may_continue_at_column_zero(tmp):
    """A quote opening after a property still owns its continuation line,
    and a line inside the quote never becomes a duplicate mapping key."""
    del tmp
    source = ('anchors:\n gate: &a "one\njobs: two"\n'
              'jobs:\n sample:\n  steps:\n   - name: plain\n     if: z\n')
    assert _names(source) == ['plain']


def test_an_alias_to_a_multiline_quoted_scalar_is_a_boundary(tmp):
    """Resolving an alias re-reads the anchor's value, which refuses a
    quote this reader cannot take, exactly as the direct form does."""
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a "one\n  two"\n')
    _refused(source, 'unsupported multiline scalar')


def test_an_alias_to_a_nested_mapping_is_refused(tmp):
    """An anchor on a mapping resolves to no scalar this reader can give."""
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a\n  key: value\n')
    _refused(source, 'unsupported alias to a nested mapping')


def test_an_alias_to_a_sequence_is_refused(tmp):
    """An anchor on a sequence resolves to no scalar either."""
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a\n  - one\n')
    _refused(source, 'unsupported alias to a nested sequence')


def test_a_non_string_tag_is_refused(tmp):
    """Only a tag guaranteed to resolve to a string may be decoded."""
    del tmp
    _refused(_document('name: !!int 5'), 'unsupported YAML tag !!int')


def test_a_malformed_anchor_is_refused(tmp):
    """An anchor name carrying a flow indicator is refused, not guessed."""
    del tmp
    _refused(_document('name: &[bad] target'), 'malformed YAML anchor')


def test_a_malformed_alias_is_refused(tmp):
    del tmp
    for field in ('name: *', 'name: *[bad]'):
        _refused(_document(field), 'malformed YAML alias')


def test_a_tag_with_no_value_is_refused(tmp):
    """A contentless node is no scalar this reader decodes as a name.

    PyYAML reads `!!str` alone as the empty string, but the reader takes
    name and id fields as scalar values to match policy against, and the
    untagged empty field refuses the same way.
    """
    del tmp
    _refused(_document('name: !!str'), 'step name has no scalar value')


def test_two_anchors_on_one_node_are_refused(tmp):
    """One node carries at most one anchor."""
    del tmp
    _refused(_document('name: &a &b target'), 'two YAML anchors')


def test_two_tags_on_one_node_are_refused(tmp):
    """One node carries at most one tag."""
    del tmp
    _refused(_document('name: !!str !!str target'), 'two YAML tags')


def _run_ratchet(path, text):
    """Raise the Python floor in a copy of a workflow, returning stdout."""
    path.write_text(text, encoding='utf-8')
    done = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'ci' / 'ratchet.py'),
         '--language', 'python', '--measured', '99.0',
         '--workflow', str(path)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (done.stdout, done.stderr)
    return done.stdout


def _plant(text, step):
    """Replace the shipped gate step line with `step`."""
    assert text.count(_GATE_STEP) == 1, _GATE_STEP
    return text.replace(_GATE_STEP, step, 1)


def test_the_ratchet_raises_the_real_workflow_for_each_spelling(tmp):
    """The reader and the ratchet agree on a real target, not a fixture."""
    source = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    plain = _run_ratchet(Path(tmp) / 'plain.yml', source)
    assert 'raised the Python coverage floor' in plain, plain
    aliased = _plant(
        source.replace(
            'name: tests\n',
            'name: tests\n\nenv:\n'
            '  GATE_NAME: &gatename Python coverage gate\n', 1),
        '      - name: *gatename\n')
    spellings = {
        'anchor.yml': _plant(
            source, '      - name: &gatename Python coverage gate\n'),
        'tag.yml': _plant(
            source, '      - name: !!str Python coverage gate\n'),
        'alias.yml': aliased,
    }
    for filename, text in spellings.items():
        assert _run_ratchet(Path(tmp) / filename, text) == plain, filename


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
