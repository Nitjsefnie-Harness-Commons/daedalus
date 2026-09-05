#!/usr/bin/env python3
"""Regression contracts for block-scalar boundaries and anchor positions."""
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


def _document(prefix):
    return (f'{prefix}jobs:\n sample:\n  steps:\n'
            '   - name: *a\n     if: z\n')


def _names(source):
    items = workflow_step_items(source, 'sample')
    assert items is not None, source
    return [item.name for item in items]


def _base_name(source):
    parsed = yaml.load(source, Loader=yaml.BaseLoader)
    return parsed['jobs']['sample']['steps'][0]['name']


def _refused(source, detail):
    try:
        workflow_step_items(source, 'sample')
    except YAMLReadError as error:
        message = str(error)
        assert detail in message, message
        assert 'unknown YAML alias' not in message, message
        return
    raise AssertionError(f'expected a {detail} refusal')


def test_nested_sequence_block_headers_hide_scalar_content_anchors(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  - - |\n'
        '      key: &a hidden\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  -\n'
        '    |\n'
        '      key: &a hidden\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  -\n'
        '  |\n'
        '    key: &a hidden\n',
    )
    for prefix in prefixes:
        source = _document(prefix)
        parsed = yaml.load(source, Loader=yaml.BaseLoader)
        expected = parsed['jobs']['sample']['steps'][0]['name']
        assert _names(source) == [expected] == ['target'], source


def test_following_line_block_headers_hide_mapping_scalar_content(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoy:\n'
        '  job:\n'
        '   |\n'
        '     key: &a hidden\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoy:\n'
        '  env:\n'
        '   KEY:\n'
        '     |\n'
        '       key: &a hidden\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  ? |\n'
        '    key: &a hidden\n'
        '  : value\n',
    )
    for prefix in prefixes:
        source = _document(prefix)
        expected = _base_name(source)
        assert _names(source) == [expected] == ['target'], source


def test_nested_quoted_scalar_content_hides_anchors(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoy:\n'
        '  - - "open\n'
        '      key: &a hidden"\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoy:\n'
        "  - - 'open\n"
        "      key: &a hidden'\n",
        'anchors:\n'
        ' first: &a target\n'
        ' decoy:\n'
        '  -\n'
        '   "open\n'
        '     key: &a hidden"\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoy:\n'
        "  -\n"
        "   'open\n"
        "     key: &a hidden'\n",
    )
    for prefix in prefixes:
        source = _document(prefix)
        expected = _base_name(source)
        assert _names(source) == [expected] == ['target'], source


def test_nested_scalar_boundaries_preserve_same_level_siblings(tmp):
    del tmp
    headers = ('|2', '|2-', '>2', '|', '>-')
    for header in headers:
        prefixes = (
            'anchors:\n'
            ' decoy:\n'
            f'  - - {header}\n'
            '      hidden\n'
            '    - first: &a target\n',
            'anchors:\n'
            ' decoy:\n'
            f'  - - {header}\n'
            '    - first: &a target\n',
        )
        for prefix in prefixes:
            source = _document(prefix)
            expected = _base_name(source)
            assert _names(source) == [expected] == ['target'], source


def test_bare_sequence_anchor_position_skips_blank_and_comment_lines(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' decoy:\n'
        '  -\n'
        '\n'
        '   &a target\n',
        'anchors:\n'
        ' decoy:\n'
        '  -\n'
        '  # comment\n'
        '   &a target\n',
    )
    for prefix in prefixes:
        _refused(_document(prefix), 'sequence item')


def test_separate_line_explicit_key_anchor_names_mapping_key(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' decoy:\n'
        '  ?\n'
        '   &a key\n'
        '  : value\n',
        'anchors:\n'
        ' decoy:\n'
        '  ?\n'
        '\n'
        '  # comment\n'
        '   &a key\n'
        '  : value\n',
    )
    for prefix in prefixes:
        _refused(_document(prefix), 'mapping key')


def test_nested_explicit_key_scalar_content_hides_anchors(tmp):
    del tmp
    headers = ('|', '|-', '|2', '>-', '>2')
    for header in headers:
        for properties in ('', '&b '):
            prefix = (
                'anchors:\n'
                ' first: &a target\n'
                ' decoy:\n'
                f'  - - ? {properties}{header}\n'
                '        key: &a hidden\n'
                '      : value\n')
            source = _document(prefix)
            expected = _base_name(source)
            assert _names(source) == [expected] == ['target'], source


def test_nested_explicit_quoted_key_scalar_hides_anchors(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  - - ? "part: open\n'
        '        key: &a hidden\n'
        '        end"\n'
        '      : value\n',
        "anchors:\n"
        " first: &a target\n"
        " decoys:\n"
        "  - - ? 'part: open\n"
        "        key: &a hidden\n"
        "        end'\n"
        "      : value\n",
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  - - - - ? "part: open\n'
        '            key: &a hidden\n'
        '            end"\n'
        '          : value\n',
        "anchors:\n"
        " first: &a target\n"
        " decoys:\n"
        "  - - - - ? 'part: open\n"
        "            key: &a hidden\n"
        "            end'\n"
        "          : value\n",
    )
    for prefix in prefixes:
        source = _document(prefix)
        expected = _base_name(source)
        assert _names(source) == [expected] == ['target'], source


def test_inline_anchored_keys_name_their_yaml_position(tmp):
    del tmp
    cases = (
        ('anchors:\n ? &a "key: value" : value\n', 'mapping key'),
        ('anchors:\n ? &a key : value\n', 'mapping key'),
        (
            'anchors:\n'
            ' ?\n'
            '\n'
            ' # comment\n'
            '  &a "key: value"\n'
            ' : value\n',
            'mapping key',
        ),
        (
            'anchors:\n'
            ' ?\n'
            '\n'
            ' # comment\n'
            '  &a key\n'
            ' : value\n',
            'mapping key',
        ),
        ('anchors:\n - - &a "key: value" : value\n', 'nested sequence'),
        ('anchors:\n - - &a key: value\n', 'nested sequence'),
        ('anchors:\n - - - - &a "key: value" : value\n', 'nested sequence'),
        ('anchors:\n - - - - &a key: value\n', 'nested sequence'),
        (
            'anchors:\n'
            ' - -\n'
            '\n'
            '  # comment\n'
            '     &a "key: value"\n',
            'nested sequence',
        ),
        (
            'anchors:\n'
            ' - -\n'
            '\n'
            '  # comment\n'
            '     &a key: value\n',
            'nested sequence',
        ),
    )
    for prefix, detail in cases:
        _refused(_document(prefix), detail)


def test_nested_mapping_scalar_header_keeps_mapping_sibling_visible(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' decoys:\n'
        '  - - key: |2\n'
        '        scalar\n'
        '      first: &a target\n',
        'anchors:\n'
        ' decoys:\n'
        '  - - key: >2-\n'
        '        scalar\n'
        '      first: &a target\n',
    )
    for prefix in prefixes:
        source = _document(prefix)
        expected = _base_name(source)
        assert _names(source) == [expected] == ['target'], source


def test_empty_nested_literal_stops_before_a_sibling_anchor(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' decoy:\n'
        '  - - |\n'
        '    - first: &a target\n',
        'anchors:\n'
        ' decoy:\n'
        '  - -\n'
        '    |\n'
        '    - first: &a target\n',
        'anchors:\n'
        ' decoy:\n'
        '  - -\n'
        '\n'
        '    # comment\n'
        '    |\n'
        '\n'
        '    - first: &a target\n',
    )
    for prefix in prefixes:
        source = _document(prefix)
        expected = _base_name(source)
        assert _names(source) == [expected] == ['target'], source


def test_nested_explicit_key_scalar_keeps_mapping_sibling_visible(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' decoys:\n'
        '  - - ? |2\n'
        '        scalar\n'
        '      : value\n'
        '      first: &a target\n',
        'anchors:\n'
        ' decoys:\n'
        '  - - ? >2-\n'
        '        scalar\n'
        '      : value\n'
        '      first: &a target\n',
    )
    for prefix in prefixes:
        source = _document(prefix)
        expected = _base_name(source)
        assert _names(source) == [expected] == ['target'], source


def test_nested_flow_scalar_quotes_are_opaque(tmp):
    del tmp
    prefixes = (
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  - - ["open\n'
        '      key: &a hidden"]\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  -\n'
        '   [\n'
        '     "open\n'
        '       key: &a hidden"\n'
        '   ]\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoys:\n'
        '  - -\n'
        '     [\n'
        '       "open\n'
        '         key: &a hidden"\n'
        '     ]\n',
        'anchors:\n'
        ' first: &a target\n'
        ' decoy:\n'
        '  key:\n'
        '   [\n'
        '     "open\n'
        '       key: &a hidden"\n'
        '   ]\n',
    )
    for prefix in prefixes:
        source = _document(prefix)
        expected = _base_name(source)
        assert expected == 'target', source
        _refused(source, 'flow collection')


def test_nested_anchor_position_diagnostics_normalize_prefixes(tmp):
    del tmp
    cases = (
        (
            'anchors:\n'
            ' decoys:\n'
            '  - -\n'
            '     &a target\n',
            'nested sequence',
        ),
        (
            'anchors:\n'
            ' decoys:\n'
            '  - -\n'
            '  # comment\n'
            '     &a target\n',
            'nested sequence',
        ),
        (
            'anchors:\n'
            ' decoys:\n'
            '  - - ? &a key\n'
            '      : value\n',
            'mapping key',
        ),
        (
            'anchors:\n'
            ' decoys:\n'
            '  - - ? &a |2\n'
            '        scalar\n'
            '      : value\n',
            'mapping key',
        ),
    )
    for prefix, detail in cases:
        _refused(_document(prefix), detail)


def test_step_reader_rejects_nested_sequence_items(tmp):
    del tmp
    source = (
        'jobs:\n'
        ' sample:\n'
        '  steps:\n'
        '   - - name: hidden\n'
        '       if: z\n')
    parsed = yaml.load(source, Loader=yaml.BaseLoader)
    assert isinstance(parsed['jobs']['sample']['steps'][0], list), parsed
    try:
        workflow_step_items(source, 'sample')
    except YAMLReadError as error:
        assert 'step key has an unsupported plain scalar' in str(error), error
        return
    raise AssertionError('nested sequence item was returned as a step')


def test_present_unsupported_anchor_positions_name_their_yaml_position(tmp):
    del tmp
    cases = (
        ('anchors:\n ? &a key\n : value\n', 'mapping key'),
        ('anchors:\n -\n  &a target\n', 'sequence item'),
        ('anchors:\n - - &a target\n', 'nested sequence'),
    )
    for prefix, detail in cases:
        _refused(_document(prefix), detail)


def test_unsupported_tag_diagnostic_does_not_claim_a_false_resolution(tmp):
    del tmp
    try:
        yamlanchor.validate_node_properties(['!'], 'step name')
    except YAMLReadError as error:
        message = str(error)
        assert 'unsupported YAML tag !' in message, message
        assert 'only !!str resolves to a string' not in message, message
        return
    raise AssertionError('unsupported YAML tag was accepted')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
