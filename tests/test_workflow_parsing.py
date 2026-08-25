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
    _event_option_keys,
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
    """A trigger is found however its plain YAML key is spaced or commented.

    The test below refuses one trigger by name, which is only a refusal if the
    name is found however it was spaced. Each of these is valid YAML that
    declares `workflow_dispatch`.
    """
    del tmp
    head = ('name: x\n\non:\n  push:\n    branches: [main]\n'
            '  pull_request:\n')
    tail = '\npermissions:\n  contents: read\n'
    declared = (
        '  workflow_dispatch:\n',
        '  workflow_dispatch :\n',
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


def test_quoted_trigger_name_is_refused_instead_of_dropped(tmp):
    """A quoted `push` key cannot disappear from the comparison set."""
    del tmp
    workflow = ('name: x\n\non:\n'
                '  "push":\n'
                '    paths-ignore: [foo]\n'
                '  pull_request:\n'
                '    paths-ignore: [bar]\n')
    try:
        _workflow_triggers(workflow, 'quoted-trigger.yml')
    except AssertionError as failure:
        assert 'quoted-trigger.yml' in str(failure), failure
        assert '"push"' in str(failure), failure
    else:
        raise AssertionError('a quoted trigger key was accepted')


def test_quoted_path_key_pair_is_refused_and_is_yaml_unequal(tmp):
    """Escaped path keys cannot make two different filters look empty."""
    del tmp
    yaml_push = {'paths-ignore': ['foo']}
    yaml_pull_request = {'paths-ignore': ['bar']}
    assert yaml_push != yaml_pull_request
    workflow = ('name: x\n\non:\n'
                '  push:\n'
                '    "paths\\u002dignore": [foo]\n'
                '  pull_request:\n'
                '    "paths\\u002dignore": [bar]\n')
    triggers = _workflow_triggers(workflow, 'quoted-path-key.yml')
    for event in ('push', 'pull_request'):
        try:
            _workflow_path_filters(
                triggers[event], 'quoted-path-key.yml')
        except AssertionError as failure:
            assert 'quoted-path-key.yml' in str(failure), failure
            assert repr('"paths\\u002dignore"') in str(failure), failure
        else:
            raise AssertionError(f'{event}: quoted path key was accepted')


def test_typed_plain_scalar_pair_is_refused_and_is_yaml_unequal(tmp):
    """A YAML boolean cannot be flattened into the quoted string spelling."""
    del tmp
    yaml_push = {'paths-ignore': [True]}
    yaml_pull_request = {'paths-ignore': ['true']}
    assert yaml_push != yaml_pull_request
    try:
        _workflow_path_filters(
            ['    paths-ignore: [true]'], 'typed-push.yml')
    except AssertionError as failure:
        assert 'typed-push.yml' in str(failure), failure
        assert '[true]' in str(failure), failure
    else:
        raise AssertionError('a typed plain scalar was accepted')
    assert _workflow_path_filters(
        ["    paths-ignore: ['true']"], 'typed-pull-request.yml') == (
            yaml_pull_request)


def test_yaml_core_non_string_scalar_spellings_are_refused(tmp):
    """Every listed implicit YAML scalar family is outside the allow-list."""
    del tmp
    values = (
        'false', 'YES', 'n', 'NULL', '~',
        '0', '+12', '-07', '0x2a', '0o52', '0b1010', '1_000',
        '.5', '1.', '1e3', '1.0e+3', '.inf', '-.NaN', '1:20',
        '2024-01-02', '2024-01-02T03:04:05Z',
    )
    for value in values:
        try:
            _workflow_path_filters(
                [f'    paths-ignore: [{value}]'], 'typed.yml')
        except AssertionError as failure:
            assert 'typed.yml' in str(failure), (value, failure)
            assert value in str(failure), (value, failure)
        else:
            raise AssertionError(
                f'implicit non-string scalar accepted: {value}')


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
    for item in ("'**/*.md'", '"**/*.md"'):
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


def test_an_inline_trigger_value_is_refused(tmp):
    """A trigger cannot declare its options on the key's own line.

    Only the lines indented under a trigger become its options, so
    `push: {branches: [main], paths-ignore: ['**/*.md']}` read as a key
    plus nothing records a push trigger with NO filters — issue #144's
    defect passing the test written to catch it, on YAML actionlint and
    zizmor both accept. The `on:` level already refuses an inline value.
    """
    del tmp
    inline = ('name: x\n\non:\n'
              "  push: {branches: [main], paths-ignore: ['**/*.md']}\n"
              '  pull_request:\n\npermissions:\n  contents: read\n')
    try:
        _workflow_triggers(inline, 'inline.yml')
    except AssertionError as failure:
        assert "inline value for 'push' is not understood" in str(failure), (
            failure)
    else:
        raise AssertionError('an inline trigger mapping was accepted')


def test_a_block_sequence_at_the_trigger_indent_belongs_to_its_key(tmp):
    """`schedule:` with its crons at indent 2 is one trigger, not two.

    A `- cron: ...` line is a mapping line, so a reader taking any key at
    the trigger indent registers a trigger literally named `- cron` and
    hands the rest of the event to it; a second cron entry then raises
    `duplicate trigger` and every policy test errors on a valid workflow.
    """
    del tmp
    workflow = ('name: x\n\non:\n  push:\n    branches: [main]\n'
                '  schedule:\n'
                "  - cron: '12 4 * * *'\n"
                "  - cron: '47 3 * * 3'\n"
                '  pull_request:\n\npermissions:\n  contents: read\n')
    triggers = _workflow_triggers(workflow, 'crons.yml')
    assert sorted(triggers) == ['pull_request', 'push', 'schedule'], (
        sorted(triggers))
    assert len(triggers['schedule']) == 2, triggers['schedule']
    assert _event_option_keys(triggers['push']) == ['branches'], (
        triggers['push'])


def test_path_filters_drop_a_trailing_comment_from_a_block_item(tmp):
    """A comment after a path is not part of the path.

    YAML calls `- LICENSE  # not code` and `- LICENSE` the same filter, so
    keeping the comment reports an asymmetry the workflow does not have. A
    `#` inside quotes, or one with no whitespace before it, is a value.
    """
    del tmp
    assert _workflow_path_filters([
        '    paths-ignore:',
        '      - LICENSE  # not code',
        "      - '**/*.md'   # docs only",
        "      - 'has # hash'",
        '      - keep#me',
    ]) == {'paths-ignore': ['LICENSE', '**/*.md', 'has # hash', 'keep#me']}


def test_path_filters_accept_a_comment_after_the_key(tmp):
    """An explanation on the key's line is not the key's value.

    `_mapping_key` reads a trigger key written this way, and this
    repository's style puts such explanations exactly there; the option
    level used to raise `unsupported value` on the comment instead.
    """
    del tmp
    assert _workflow_path_filters([
        '    paths-ignore:  # deny-list, not an allow-list',
        '      - LICENSE',
    ]) == {'paths-ignore': ['LICENSE']}


def test_tab_indentation_is_refused(tmp):
    """A tab has no indentation width, so it is refused, not measured.

    Counting only spaces made a tab-indented line look unindented: the
    event's option indent collapsed to 0 and every space-indented filter
    was skipped, leaving `{}` — which both policy tests read as a trigger
    declaring no filters at all.
    """
    del tmp
    try:
        _workflow_path_filters(['\tbranches: [main]',
                                "    paths-ignore: ['**/*.md']"])
    except AssertionError as failure:
        assert 'tab in the indentation' in str(failure), failure
    else:
        raise AssertionError('tab indentation was measured as zero')
    tabbed = ('name: x\n\non:\n  push:\n\tbranches: [main]\n'
              '  pull_request:\n\npermissions:\n  contents: read\n')
    try:
        _workflow_triggers(tabbed, 'tabbed.yml')
    except AssertionError as failure:
        assert 'tab in the indentation' in str(failure), failure
    else:
        raise AssertionError('a tab-indented option line was accepted')


def test_flow_items_that_were_not_parsed_are_refused(tmp):
    """A flow mapping or a quote escape is refused, not handed back.

    `[{a: b}, c]` and `[a: b]` are mappings to YAML; `["a\\", b]` is the
    one path `a\\` and `['a''b']` is `a'b`. Returning any of them verbatim
    is a value this reader never parsed, which is what it exists to avoid.
    """
    del tmp
    for value in ('[{a: b}, c]', '[a: b]', '["a\\\\", b]', "['a''b']"):
        try:
            _workflow_path_filters([f'    paths-ignore: {value}'])
        except AssertionError as failure:
            assert 'unsupported value' in str(failure), (value, failure)
        else:
            raise AssertionError(f'unparsed flow item accepted: {value}')


def test_path_filters_refuse_tags_and_pin_their_yaml_values(tmp):
    """A tag is refused rather than returned as part of the scalar."""
    del tmp
    yaml_push = {'paths-ignore': ['foo']}
    yaml_pull_request = {'paths-ignore': ['!!str foo']}
    assert yaml_push != yaml_pull_request
    value = '[!!str foo]'
    try:
        _workflow_path_filters(
            [f'    paths-ignore: {value}'], 'tagged-push.yml')
    except AssertionError as failure:
        assert 'tagged-push.yml' in str(failure), failure
        assert value in str(failure), failure
    else:
        raise AssertionError('tagged-push.yml: YAML tag was accepted')
    assert _workflow_path_filters(
        ["    paths-ignore: ['!!str foo']"], 'tagged-pull-request.yml') == {
            'paths-ignore': yaml_pull_request['paths-ignore']}


def test_path_filters_refuse_anchors_and_aliases_and_pin_yaml_values(tmp):
    """Anchor and alias syntax is refused in block path sequences."""
    del tmp
    yaml_anchor = {'paths-ignore': ['foo', 'foo']}
    yaml_literal = {'paths-ignore': ['&named foo', '*named']}
    assert yaml_anchor != yaml_literal
    for filename, offending in (
            ('anchored.yml', '&named foo'),
            ('aliased.yml', '*named')):
        try:
            _workflow_path_filters([
                '    paths-ignore:', f'      - {offending}'], filename)
        except AssertionError as failure:
            assert filename in str(failure), failure
            assert offending in str(failure), failure
        else:
            raise AssertionError(f'{filename}: YAML property was accepted')
    assert _workflow_path_filters([
        '    paths-ignore:',
        "      - '&named foo'",
        "      - '*named'"], 'literal.yml') == {
            'paths-ignore': yaml_literal['paths-ignore']}


def test_path_filters_refuse_undecoded_block_quote_syntax(tmp):
    """Unsupported quote escapes in block items are refused."""
    del tmp
    for value in ("'a''b'", '"a\\\\b"'):
        try:
            _workflow_path_filters([
                '    paths-ignore:', f'      - {value}'], 'quoted.yml')
        except AssertionError as failure:
            assert 'quoted.yml' in str(failure), failure
            assert repr(value) in str(failure), failure
        else:
            raise AssertionError(f'undecoded quote syntax accepted: {value}')


def test_path_filters_refuse_block_scalars_and_pin_yaml_values(tmp):
    """A block-scalar header is not a path value this reader decodes."""
    del tmp
    yaml_push = {'paths-ignore': ['foo']}
    yaml_pull_request = {'paths-ignore': ['|-']}
    assert yaml_push != yaml_pull_request
    push_lines = [
        '    paths-ignore:',
        '      - |-',
        '        foo',
    ]
    pull_request_lines = [
        '    paths-ignore:',
        "      - '|-'",
    ]
    assert _workflow_path_filters(
        pull_request_lines, 'block-pull-request.yml') == yaml_pull_request
    empty_block = [
        '    paths-ignore:',
        '      - |-',
    ]
    yaml_empty_block = {'paths-ignore': ['']}
    assert yaml_empty_block != yaml_pull_request
    try:
        actual = _workflow_path_filters(empty_block, 'empty-block.yml')
    except AssertionError as failure:
        assert 'empty-block.yml' in str(failure), failure
        assert "'|-'" in str(failure), failure
    else:
        assert actual == yaml_empty_block, (actual, yaml_empty_block)
        raise AssertionError('empty block scalar was accepted')
    try:
        actual = _workflow_path_filters(push_lines, 'block-push.yml')
    except AssertionError as failure:
        assert 'block-push.yml' in str(failure), failure
        assert "'|-'" in str(failure), failure
    else:
        assert actual == yaml_push, (actual, yaml_push)
        raise AssertionError('block scalar was accepted')


def test_path_filters_refuse_undecoded_continuations_and_pin_yaml_values(tmp):
    """An indented plain-scalar continuation is not silently discarded."""
    del tmp
    yaml_push = {'paths-ignore': ['foo bar']}
    yaml_pull_request = {'paths-ignore': ['foo']}
    assert yaml_push != yaml_pull_request
    push_lines = [
        '    paths-ignore:',
        '      - foo',
        '        bar',
    ]
    pull_request_lines = [
        '    paths-ignore:',
        '      - foo',
    ]
    assert _workflow_path_filters(
        pull_request_lines, 'continuation-pull-request.yml') == (
            yaml_pull_request)
    try:
        actual = _workflow_path_filters(push_lines, 'continuation-push.yml')
    except AssertionError as failure:
        assert 'continuation-push.yml' in str(failure), failure
        assert 'bar' in str(failure), failure
    else:
        assert actual == yaml_push, (actual, yaml_push)
        raise AssertionError('undecoded continuation was accepted')


def test_a_duplicate_option_key_in_one_event_is_refused(tmp):
    """Last-wins describes a document the workflow does not contain."""
    del tmp
    try:
        _workflow_path_filters(['    paths-ignore: [a]',
                                '    paths-ignore: [b]'])
    except AssertionError as failure:
        assert "duplicate event option 'paths-ignore'" in str(failure), failure
    else:
        raise AssertionError('a duplicate option key was accepted')


def test_event_option_keys_are_the_events_own_keys(tmp):
    """A key nested under an option is not an option of the event.

    `test_no_workflow_gates_one_commit_twice` asks whether push carries a
    `branches:` filter. Any line starting `branches:` would answer yes for
    the block below, where the event itself declares no such filter.
    """
    del tmp
    lines = [
        '    paths:',
        "      - '**'",
        '    types:',
        '      branches: [main]',
    ]
    assert _event_option_keys(lines) == ['paths', 'types']


def test_path_filters_split_multi_item_flow_sequences(tmp):
    """The comma scan is what replaced ast.literal_eval, so pin it here.

    Every other flow case in this suite holds one item, which leaves the
    splitting loop exercised only through the repository's own workflows.
    """
    del tmp
    assert _workflow_path_filters(
        ['    paths-ignore: [README.md, LICENSE, .gitignore]']) == {
            'paths-ignore': ['README.md', 'LICENSE', '.gitignore']}
    assert _workflow_path_filters(
        ['''    paths: [src/**, 'docs/*.md', "a b", .c]''']) == {
            'paths': ['src/**', 'docs/*.md', 'a b', '.c']}


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
