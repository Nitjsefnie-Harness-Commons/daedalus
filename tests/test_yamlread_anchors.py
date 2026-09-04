#!/usr/bin/env python3
"""Executable contracts for node properties and aliases on a step scalar."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))

from workflow_yaml import (  # noqa: E402
    YAMLReadError, workflow_step_items,
)
import yamlanchor  # noqa: E402


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


def _reader_refused(source, detail):
    """Require the sample job's steps to be refused, naming `detail`."""
    try:
        workflow_step_items(source, 'sample')
    except YAMLReadError as error:
        assert detail in str(error), str(error)
        return
    raise AssertionError(f'a spelling {detail!r} must refuse was accepted')


def _reader_refused_exact(source, detail):
    """Require one exact production-reader refusal."""
    try:
        workflow_step_items(source, 'sample')
    except YAMLReadError as error:
        assert str(error) == detail, str(error)
        return
    raise AssertionError(f'expected exact refusal: {detail}')


def test_parse_alias_accepts_only_a_complete_alias_spelling(tmp):
    del tmp
    parse_alias = getattr(yamlanchor, 'parse_alias', None)
    assert callable(parse_alias), 'parse_alias interface is missing'
    assert parse_alias('*name', 'step name') == 'name'
    assert parse_alias('name', 'step name') is None


def test_parse_alias_refuses_a_malformed_alias_spelling(tmp):
    del tmp
    parse_alias = getattr(yamlanchor, 'parse_alias', None)
    assert callable(parse_alias), 'parse_alias interface is missing'
    for value in ('*', '*[bad]', '*name]'):
        try:
            parse_alias(value, 'step name')
        except YAMLReadError as error:
            assert str(error) == 'step name has a malformed YAML alias', error
            continue
        raise AssertionError(f'malformed alias {value!r} was accepted')


def test_anchored_step_name_decodes_like_the_plain_spelling(tmp):
    del tmp
    assert _names(_document('name: &a target')) == ['target']


def test_string_tagged_step_name_decodes_like_the_plain_spelling(tmp):
    del tmp
    assert _names(_document('name: !!str target')) == ['target']


def test_the_non_specific_tag_is_refused_in_every_scalar_form(tmp):
    """Bare `!` needs schema/tag resolution outside this bounded reader."""
    del tmp
    for field in (
            'name: ! 5', 'name: ! true', 'name: ! null',
            'name: ! "5"'):
        _reader_refused(_document(field), 'unsupported YAML tag !')


def test_the_non_specific_tag_is_refused_on_a_block_scalar(tmp):
    del tmp
    _reader_refused(
        'jobs:\n sample:\n  steps:\n'
        '   - name: ! |\n       5\n     if: z\n',
        'unsupported YAML tag !')


def test_a_string_tagged_number_and_bool_decode_to_strings(tmp):
    """`!!str` is the one tag whose resolution is guaranteed text."""
    del tmp
    for field, decoded in (('name: !!str 5', '5'),
                           ('name: !!str true', 'true')):
        assert _names(_document(field)) == [decoded], field


def test_an_anchor_and_a_tag_decode_in_either_order(tmp):
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
    _reader_refused(
        _document('name: &a "one\n          two"'),
        'unsupported multiline scalar')


def test_aliased_step_name_decodes_to_the_anchored_value(tmp):
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a target\n')
    assert _names(source) == ['target']


def test_step_id_accepts_every_property_spelling(tmp):
    del tmp
    prefix = 'anchors:\n gate: &a target\n'
    for field in ('id: &b target', 'id: !!str target',
                  'id: &b !!str target', 'id: *a'):
        assert _identities(_document(field, prefix)) == ['target'], field
    _reader_refused(
        _document('id: ! target', prefix), 'unsupported YAML tag !')


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
    del tmp
    prefix = 'anchors:\n first: &a wrong\n second: &a target\n'
    assert _names(_document('name: *a', prefix)) == ['target']


def test_mapping_value_aliases_match_base_loader_strings(tmp):
    del tmp
    cases = (
        ('plain', 'target'),
        ('number', '5'),
        ('boolean', 'true'),
        ('single', "'single quoted'"),
        ('double', '"double quoted"'),
        ('literal', '|\n  literal block\n'),
        ('folded', '>\n  folded block\n'),
    )
    for key, value in cases:
        source = _document(
            'name: *a', f'anchors:\n {key}: &a {value}'
            + ('' if value.endswith('\n') else '\n'))
        parsed = yaml.load(source, Loader=yaml.BaseLoader)
        expected = parsed['jobs']['sample']['steps'][0]['name']
        assert _names(source) == [expected], key


def test_an_alias_reads_an_anchor_defined_on_an_earlier_step_field(tmp):
    del tmp
    source = ('jobs:\n sample:\n  steps:\n'
              '   - id: &a target\n     if: z\n'
              '   - name: *a\n     if: z\n')
    assert _names(source) == [None, 'target']


def test_an_alias_with_no_earlier_anchor_is_refused(tmp):
    del tmp
    _reader_refused(
        _document('name: *missing'), 'unknown YAML alias: &missing')


def test_a_decoy_anchor_in_content_defines_no_alias_target(tmp):
    del tmp
    for prefix in ('anchors:\n gate: |\n  &a hidden\n',
                   'anchors:\n gate: "&a hidden"\n',
                   'anchors:\n# gate: &a hidden\n gate: real\n'):
        _reader_refused(
            _document('name: *a', prefix), 'unknown YAML alias: &a')


def test_bare_sequence_block_scalars_do_not_define_alias_targets(tmp):
    del tmp
    prefix = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  - |\n'
        '    key: &a hidden\n'
        '  - >\n'
        '    key: &a hidden\n')
    assert _names(_document('name: *a', prefix)) == ['target']


def test_bare_sequence_open_quotes_do_not_define_alias_targets(tmp):
    del tmp
    prefix = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  - "open\n'
        '    key: &a hidden"\n'
        "  - 'open\n"
        "    key: &a hidden'\n")
    assert _names(_document('name: *a', prefix)) == ['target']


def test_multiline_flow_alias_candidates_are_refused_as_opaque(tmp):
    del tmp
    for value, closing in (
            ('[\n  &a hidden,\n', '  ]\n'),
            ('{\n  key: &a hidden,\n', '}\n')):
        prefix = f'anchors:\n first: &a target\n decoy: {value}{closing}'
        _reader_refused_exact(
            _document('name: *a', prefix),
            'step name has an unsupported YAML alias target in a flow '
            'collection: &a')


def test_unclosed_flow_keeps_apparent_jobs_opaque(tmp):
    del tmp
    source = ('decoy: [one, two\n'
              'jobs:\n sample:\n  steps:\n'
              '   - name: hidden\n     if: z\n')
    assert workflow_step_items(source, 'sample') is None


def _assert_flow_property_decoy(decoy):
    prefix = 'anchors:\n first: &a target\n ' + decoy
    _reader_refused_exact(
        _document('name: *a', prefix),
        'step name has an unsupported YAML alias target in a flow '
        'collection: &a')


def test_anchor_before_double_quote_keeps_multiline_flow_opaque(tmp):
    del tmp
    _assert_flow_property_decoy(
        'decoy: [\n'
        '  &x "quoted ] and escaped \\\" delimiter", # member\n'
        '  &a hidden\n'
        ' ]\n')


def test_anchor_before_single_quote_keeps_multiline_flow_opaque(tmp):
    del tmp
    _assert_flow_property_decoy(
        'decoy: {\n'
        "  key: &x 'quoted } and doubled '' quote', # member\n"
        '  other: &a hidden\n'
        ' }\n')


def test_tag_before_quote_keeps_multiline_flow_opaque(tmp):
    del tmp
    _assert_flow_property_decoy(
        'decoy: [\n'
        '  !!str "tagged ] and escaped \\\" delimiter", # member\n'
        '  &a hidden\n'
        ' ]\n')


def test_flow_properties_keep_same_line_quoted_members_opaque(tmp):
    del tmp
    values = (
        '[&x "quoted ] and escaped \\\" delimiter", &a hidden]',
        "{key: &x 'quoted } and doubled '' quote', other: &a hidden}",
        '[!!str "tagged ] and escaped \\\" delimiter", &a hidden]',
    )
    for value in values:
        prefix = f'anchors:\n first: &a target\n decoy: {value}\n'
        _reader_refused_exact(
            _document('name: *a', prefix),
            'step name has an unsupported YAML alias target in a flow '
            'collection: &a')


def test_no_space_flow_mapping_keeps_quoted_closing_brace_opaque(tmp):
    del tmp
    values = (
        ('"key":"quoted } and escaped \\\" delimiter"',
         'quoted } and escaped " delimiter'),
        ("'key':'quoted } and doubled '' delimiter'",
         "quoted } and doubled ' delimiter"),
    )
    for member, expected in values:
        prefix = (
            'anchors:\n'
            ' decoy: {\n'
            f'  {member},\n'
            '  "other": &a hidden\n'
            ' }\n')
        source = _document('name: *a', prefix)
        parsed = yaml.load(source, Loader=yaml.BaseLoader)
        assert parsed['anchors']['decoy']['key'] == expected
        assert parsed['jobs']['sample']['steps'][0]['name'] == 'hidden'
        _reader_refused_exact(
            source,
            'step name has an unsupported YAML alias target in a flow '
            'collection: &a')


def test_verbatim_tags_keep_same_line_flow_members_opaque(tmp):
    del tmp
    values = (
        '[!<tag:yaml.org,2002:str> "quoted ] delimiter", &a hidden]',
        '{key: !<tag:yaml.org,2002:str> "quoted } delimiter", '
        'other: &a hidden}',
    )
    for value in values:
        _assert_flow_property_decoy(f'decoy: {value}\n')


def test_verbatim_tags_keep_multiline_flow_members_opaque(tmp):
    del tmp
    values = (
        ('decoy: [\n'
         '  !<tag:yaml.org,2002:str> "quoted ] delimiter", # member\n'
         '  &a hidden\n'
         ' ]\n'),
        ('decoy: {\n'
         '  key: !<tag:yaml.org,2002:str> "quoted } delimiter", # member\n'
         '  other: &a hidden\n'
         ' }\n'),
    )
    for decoy in values:
        _assert_flow_property_decoy(decoy)


def test_verbatim_tag_flow_candidates_refuse_without_an_older_target(tmp):
    del tmp
    prefix = (
        'anchors:\n'
        ' decoy: [\n'
        '  !<tag:yaml.org,2002:str> "quoted ] delimiter",\n'
        '  &a hidden\n'
        ' ]\n')
    _reader_refused_exact(
        _document('name: *a', prefix),
        'step name has an unsupported YAML alias target in a flow '
        'collection: &a')


def test_unclosed_verbatim_tag_keeps_the_remaining_flow_opaque(tmp):
    del tmp
    prefix = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoy: [\n'
        '  !<tag:yaml.org,2002:str "unterminated property",\n'
        '  &a hidden\n'
        ' ]\n')
    _reader_refused_exact(
        _document('name: *a', prefix),
        'step name has an unsupported YAML alias target in a flow '
        'collection: &a')


def test_same_line_flow_alias_candidates_are_refused_as_opaque(tmp):
    del tmp
    for value in ('[&a hidden]', '{key: &a hidden}'):
        prefix = f'anchors:\n first: &a target\n decoy: {value}\n'
        _reader_refused_exact(
            _document('name: *a', prefix),
            'step name has an unsupported YAML alias target in a flow '
            'collection: &a')


def test_flow_extent_keeps_nested_quotes_and_comments_opaque(tmp):
    del tmp
    prefix = (
        'anchors:\n first: &a target\n decoy: [\n'
        '  "quoted ] and escaped \\\" ]",\n'
        "  'single ] and doubled '' quote',\n"
        '  {nested: ["still ]"]}, # closing brackets are comment data\n'
        '  &a hidden\n'
        ']\n')
    _reader_refused_exact(
        _document('name: *a', prefix),
        'step name has an unsupported YAML alias target in a flow '
        'collection: &a')


def test_an_anchor_on_a_sequence_item_is_an_unsupported_alias_target(tmp):
    del tmp
    prefix = 'anchors:\n - &a target\n'
    _reader_refused_exact(
        _document('name: *a', prefix),
        'step name has an unsupported YAML alias target on a sequence '
        'item: &a')


def test_an_anchor_on_a_mapping_key_is_an_unsupported_alias_target(tmp):
    del tmp
    prefix = 'anchors:\n &a key: target\n'
    _reader_refused_exact(
        _document('name: *a', prefix),
        'step name has an unsupported YAML alias target on a mapping key: &a')


def test_an_anchor_on_a_step_mapping_node_is_an_unsupported_alias_target(tmp):
    del tmp
    source = (
        'jobs:\n sample:\n  steps:\n'
        '   - &a name: hidden\n     if: z\n'
        '   - name: *a\n     if: z\n')
    _reader_refused_exact(
        source,
        'step name has an unsupported YAML alias target on a sequence '
        'item: &a')


def test_anchor_name_boundaries_do_not_match_a_longer_name(tmp):
    del tmp
    prefix = 'anchors:\n first: &ab wrong\n decoy: [&ab hidden]\n'
    _reader_refused_exact(
        _document('name: *a', prefix),
        'step name has an unknown YAML alias: &a')


def test_a_later_unsupported_candidate_does_not_fall_back_to_an_older_one(tmp):
    del tmp
    prefix = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  - &a hidden\n')
    _reader_refused_exact(
        _document('name: *a', prefix),
        'step name has an unsupported YAML alias target on a sequence '
        'item: &a')


def test_a_colon_bearing_decoy_in_a_block_body_defines_no_target(tmp):
    del tmp
    _reader_refused(
        _document('name: *a', 'anchors:\n gate: |\n  k: &a hidden\n'),
        'unknown YAML alias: &a')


def test_a_colon_bearing_decoy_in_a_quoted_scalar_defines_no_target(tmp):
    del tmp
    _reader_refused(_document(
        'name: *a',
        'anchors:\n gate: "k: &a hidden\n  j: &a deeper"\n'),
        'unknown YAML alias: &a')


def test_an_alias_refuses_an_anchor_defined_after_it(tmp):
    """PyYAML composes aliases against earlier definitions only."""
    del tmp
    source = ('jobs:\n sample:\n  steps:\n'
              '   - name: *a\n     if: z\n'
              'anchors:\n gate: &a target\n')
    _reader_refused(source, 'unknown YAML alias: &a')


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
    _reader_refused(source, 'unsupported multiline scalar')


def test_an_alias_to_a_nested_mapping_is_refused(tmp):
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a\n  key: value\n')
    _reader_refused(source, 'unsupported alias to a nested mapping')


def test_an_alias_to_a_nested_scalar_names_the_scalar(tmp):
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a\n  hidden\n')
    _reader_refused(source, 'unsupported alias to a nested scalar')


def test_an_alias_to_an_empty_mapping_value_names_an_empty_node(tmp):
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a\n')
    _reader_refused_exact(
        source, 'step name has an unsupported alias to an empty node: &a')


def test_an_alias_to_a_sequence_is_refused(tmp):
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a\n  - one\n')
    _reader_refused(source, 'unsupported alias to a nested sequence')


def test_a_dash_without_a_space_names_a_scalar(tmp):
    """`-5` is the plain scalar `-5`, so the dash-width limb names it."""
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a\n  -5\n')
    _reader_refused(source, 'unsupported alias to a nested scalar')


def test_a_tab_widened_dash_still_names_a_sequence(tmp):
    del tmp
    source = _document('name: *a', 'anchors:\n gate: &a\n  -\t5\n')
    _reader_refused(source, 'unsupported alias to a nested sequence')


def test_an_alias_to_a_flow_collection_names_the_collection(tmp):
    """A flow child is neither a block mapping nor a plain scalar."""
    del tmp
    for child in ('[x]', '{k: v}', '{a}'):
        source = _document('name: *a', f'anchors:\n gate: &a\n  {child}\n')
        _reader_refused(
            source, 'unsupported alias to a nested flow collection')


def test_a_non_string_tag_is_refused(tmp):
    del tmp
    _reader_refused(_document('name: !!int 5'), 'unsupported YAML tag !!int')


def test_a_malformed_anchor_is_refused(tmp):
    del tmp
    _reader_refused(_document('name: &[bad] target'), 'malformed YAML anchor')


def test_a_malformed_alias_is_refused(tmp):
    del tmp
    for field in ('name: *', 'name: *[bad]'):
        _reader_refused(_document(field), 'malformed YAML alias')


def test_an_alias_carrying_an_anchor_is_refused(tmp):
    """An alias is a complete node; PyYAML refuses properties on one."""
    del tmp
    _reader_refused(
        _document('name: &x *b'),
        'alias carrying node properties: &x *b')


def test_an_alias_carrying_a_tag_is_refused(tmp):
    del tmp
    _reader_refused(
        _document('id: !!str *t'),
        'alias carrying node properties: !!str *t')


def test_a_tag_with_no_value_is_refused(tmp):
    """A contentless node is no scalar this reader decodes as a name.

    PyYAML reads `!!str` alone as the empty string, but the reader takes
    name and id fields as scalar values to match policy against, and the
    untagged empty field refuses the same way.
    """
    del tmp
    _reader_refused(_document('name: !!str'), 'step name has no scalar value')


def test_two_anchors_on_one_node_are_refused(tmp):
    del tmp
    _reader_refused(_document('name: &a &b target'), 'two YAML anchors')


def test_two_tags_on_one_node_are_refused(tmp):
    del tmp
    _reader_refused(_document('name: !!str !!str target'), 'two YAML tags')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
