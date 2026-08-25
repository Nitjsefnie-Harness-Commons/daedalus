#!/usr/bin/env python3
"""The workflow reader itself, pinned apart from the policies it serves.

Every other workflow test asks whether a specific workflow is shaped
correctly, and each one is only as good as the reader underneath it. A
reader that missed a trigger would report the very thing a test was
refusing as absent, so the reader gets its own suite.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _workflows import (  # noqa: E402
    _trigger_names,
    _workflow_path_filters,
    _workflow_triggers,
)


def test_a_second_top_level_on_block_is_refused(tmp):
    """Two `on:` keys is invalid YAML, and silently reading the first lies.

    The reader used to stop at the first match, which made the refusal below
    unreachable — a workflow whose second block carried the real triggers
    would have been described by the first.
    """
    del tmp
    doubled = ('name: x\n\non:\n  push:\n    branches: [main]\n'
               '\non:\n  pull_request:\n\npermissions:\n  contents: read\n')
    try:
        _workflow_triggers(doubled, 'doubled.yml')
    except AssertionError as failure:
        assert 'duplicate on: blocks' in str(failure), failure
    else:
        raise AssertionError('a second on: block was accepted')


def test_trigger_names_survive_every_spelling_of_a_key(tmp):
    """A trigger is found however its YAML key happens to be written.

    The test below refuses one trigger by name, which is only a refusal if the
    name is found however it was spelled. Each of these is valid YAML that
    declares `workflow_dispatch`, and a reader keyed on a line ENDING in a
    colon sees none of the last three.
    """
    del tmp
    head = ('name: x\n\non:\n  push:\n    branches: [main]\n'
            '  pull_request:\n')
    tail = '\npermissions:\n  contents: read\n'
    declared = (
        '  workflow_dispatch:\n',
        '  workflow_dispatch :\n',
        "  'workflow_dispatch':\n",
        '  "workflow_dispatch":\n',
        '  workflow_dispatch: # manual benchmark\n',
        '  workflow_dispatch: {}\n',
        '  workflow_dispatch:\n    inputs:\n      x:\n        type: string\n',
    )
    for block in declared:
        names = _trigger_names(head + block + tail)
        assert 'workflow_dispatch' in names, (block, sorted(names))
        assert {'push', 'pull_request'} <= names, (block, sorted(names))
    # A mention inside a comment declares nothing.
    absent = _trigger_names(
        head + '  # workflow_dispatch: not a trigger\n' + tail)
    assert 'workflow_dispatch' not in absent, sorted(absent)


def test_path_filters_ignore_deeper_nested_mappings(tmp):
    """A nested filter is not an event-level path filter."""
    del tmp
    lines = [
        '    branches:',
        '      - main',
        '    nested:',
        '      paths-ignore:',
        "        - '**/*.md'",
    ]
    assert _workflow_path_filters(lines) == {}


def test_path_filters_accept_empty_events(tmp):
    """An event with no options has no path filters."""
    del tmp
    assert _workflow_path_filters([]) == {}


def test_path_filters_normalize_block_and_flow_sequences(tmp):
    """Block and flow filters normalize quoted and bare scalars alike."""
    del tmp
    expected = ['**/*.md']
    block = [
        '    paths-ignore:',
        "      - '**/*.md'",
    ]
    assert _workflow_path_filters(block) == {'paths-ignore': expected}
    for item in ("'**/*.md'", '"**/*.md"', '**/*.md'):
        flow = [f'    paths-ignore: [{item}]']
        assert _workflow_path_filters(flow) == {'paths-ignore': expected}


def test_path_filters_keep_paths_keys_separate(tmp):
    """Both filter keys are returned independently for one event."""
    del tmp
    lines = [
        '    paths: [src/**]',
        "    paths-ignore: ['**/*.md']",
    ]
    assert _workflow_path_filters(lines) == {
        'paths': ['src/**'],
        'paths-ignore': ['**/*.md'],
    }


def test_path_filters_accept_empty_flow_sequences(tmp):
    """An empty flow sequence produces an empty filter list."""
    del tmp
    assert _workflow_path_filters(['    paths-ignore: []']) == {
        'paths-ignore': [],
    }


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
