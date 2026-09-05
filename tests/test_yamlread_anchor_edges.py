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
        parsed = yaml.load(source, Loader=yaml.BaseLoader)
        expected = parsed['jobs']['sample']['steps'][0]['name']
        assert _names(source) == [expected] == ['target'], source


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
