#!/usr/bin/env python3
"""The scalar layer of the workflow checkout reader."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
import _wfcheckout  # noqa: E402
from test_wfcheckout import _assert_yaml_refusal  # noqa: E402


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
        '           # ${{ steps.baseline.outputs.ref }}\n')
    assert _wfcheckout.checkout_refs(workflow) == [('build', 'safe')]
    _assert_yaml_refusal(workflow + '            more\n',
                         'content after block scalar')


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
            '            \t\n'
            '            \tsecond\n',
            [('build', 'first\n\t\n\tsecond')],
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


def test_checkout_reader_folds_block_scalars_exactly(tmp):
    """Folded breaks and EOF chomping match YAML scalar values."""
    del tmp
    prefix = (
        'name: folding oracle\n'
        'on: push\n'
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n')
    cases = (
        (
            'tab content',
            '          ref: >-\n'
            '            alpha\n'
            '            \tbeta\n',
            'alpha\n\tbeta',
        ),
        (
            'blank after more-indented content',
            '          ref: >-\n'
            '            alpha\n'
            '              code\n'
            '\n'
            '            beta\n',
            'alpha\n  code\n\nbeta',
        ),
        (
            'literal EOF without a break',
            '          ref: |\n'
            '            feature',
            'feature',
        ),
        (
            'folded EOF without a break',
            '          ref: >\n'
            '            feature',
            'feature',
        ),
    )
    failures = []
    for name, scalar, expected in cases:
        actual = _wfcheckout.checkout_refs(prefix + scalar)
        if actual != [('build', expected)]:
            failures.append((name, actual, expected))
    assert failures == [], failures


def test_checkout_reader_folds_plain_scalar_blank_line_to_newline(tmp):
    """A physical blank line in a plain scalar folds to a newline."""
    del tmp
    workflow = (
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: first\n'
        '\n'
        '            second\n')
    assert _wfcheckout.checkout_refs(workflow) == [('build', 'first\nsecond')]


def test_checkout_reader_refuses_invalid_plain_scalar_continuations(tmp):
    """A comment ends a plain scalar, and a tab cannot continue one."""
    del tmp
    prefix = (
        'jobs:\n'
        '  build:\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n'
        '          ref: safe')
    cases = (
        (
            'comment resume',
            prefix + '\n'
            '            # comment\n'
            '            forbidden-ref\n',
            'content after plain scalar comment',
        ),
        (
            'tab continuation',
            prefix + '\tforbidden-ref\n',
            'tab in plain scalar',
        ),
    )
    failures = []
    for name, workflow, wording in cases:
        try:
            actual = _wfcheckout.checkout_refs(workflow)
        except _wfcheckout.YAMLReadError as error:
            if wording not in str(error):
                failures.append((name, str(error)))
        else:
            failures.append((name, actual))
    assert failures == [], failures


def test_checkout_reader_decodes_multiline_quoted_scalars(tmp):
    """Root values and checkout refs use YAML quoted-line folding."""
    del tmp
    body = (
        'on: push\n'
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n')
    cases = (
        (
            'double root and single ref',
            'name: "Team\n  build"\n',
            "          ref: 'feature\n            branch'\n",
            'feature branch',
        ),
        (
            'single root and double ref',
            "name: 'Team\n  build'\n",
            '          ref: "feature\n            branch"\n',
            'feature branch',
        ),
        (
            'escaped double-quoted line',
            'name: Team build\n',
            '          ref: "feature\\\n            branch"\n',
            'featurebranch',
        ),
        (
            'quoted ref followed by comments',
            'name: Team build\n',
            "          ref: 'feature: #branch' # trailing\n"
            '          # after ref\n',
            'feature: #branch',
        ),
    )
    failures = []
    for name, root, ref, expected in cases:
        try:
            actual = _wfcheckout.checkout_refs(root + body + ref)
        except _wfcheckout.YAMLReadError as error:
            failures.append((name, str(error)))
        else:
            if actual != [('build', expected)]:
                failures.append((name, actual, expected))
    assert failures == [], failures


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
        ('name: "decoy\n', 'top-level jobs mapping not found'),
        ('name:\n  "decoy\n', 'top-level jobs mapping not found'),
        ('name: !!str "decoy\n', 'explicit tag'),
        ('name: &label "decoy\n', 'anchor'),
    )
    for prefix, wording in cases:
        _assert_yaml_refusal('on: push\n' + prefix + suffix, wording)


def test_checkout_reader_accepts_schema_string_plain_refs(tmp):
    """Plain refs outside PyYAML's typed boundary remain strings."""
    del tmp
    prefix = (
        'name: schema oracle\n'
        'on: push\n'
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n')
    spellings = (
        'y', 'Y', 'n', 'N', 'yEs', 'tRuE', 'nUlL',
        '0B10', '0o17', '0XFF', '08',
        '1e3', '1E+3', '1.0e3',
        '2026-8-6', '2026-08-6', '2026-08-26T12:30',
        'v1.2.3', 'main', 'release-2026',
        '-feature', '?query', ':value', 'foo:bar', 'foo#bar',
    )
    failures = []
    for spelling in spellings:
        workflow = prefix + f'          ref: {spelling}\n'
        try:
            actual = _wfcheckout.checkout_refs(workflow)
        except _wfcheckout.YAMLReadError as error:
            failures.append((spelling, str(error)))
        else:
            if actual != [('build', spelling)]:
                failures.append((spelling, actual))
    assert failures == [], failures


def test_checkout_reader_refuses_schema_typed_plain_refs(tmp):
    """A plain checkout ref must be provably a YAML string."""
    del tmp
    prefix = (
        'name: schema oracle\n'
        'on: push\n'
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n')
    spellings = (
        'yes', 'Yes', 'YES', 'no', 'No', 'NO',
        'true', 'True', 'TRUE', 'false', 'False', 'FALSE',
        'on', 'On', 'ON', 'off', 'Off', 'OFF',
        'null', 'Null', 'NULL', '~', '',
        '0', '-17', '+1_000', '0b10', '077', '0xFF', '1:20',
        '1.5', '.5', '1.', '1.0e+3', '1:20.5',
        '.inf', '.Inf', '.INF', '-.Inf', '+.INF',
        '.nan', '.NaN', '.NAN',
        '2026-08-26', '2026-8-6T1:02:03',
        '2026-08-26 12:30:00Z',
        '2026-08-26T12:30:00+02:00',
        '<<', '=',
    )
    failures = []
    for spelling in spellings:
        workflow = prefix + f'          ref: {spelling}\n'
        try:
            actual = _wfcheckout.checkout_refs(workflow)
        except _wfcheckout.YAMLReadError as error:
            if 'plain scalar is not provably a string' not in str(error):
                failures.append((spelling, str(error)))
        else:
            failures.append((spelling, actual))
    assert failures == [], failures


def test_checkout_reader_refuses_invalid_plain_scalar_syntax(tmp):
    """YAML-forbidden indicators and colon separators are not refs."""
    del tmp
    prefix = (
        'name: syntax oracle\n'
        'on: push\n'
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/checkout@v4\n'
        '        with:\n')
    spellings = (
        ',value', ']value', '}value', '%value', '@value', '`value',
        ': value', 'foo: bar', 'foo:',
    )
    failures = []
    for spelling in spellings:
        workflow = prefix + f'          ref: {spelling}\n'
        try:
            actual = _wfcheckout.checkout_refs(workflow)
        except _wfcheckout.YAMLReadError:
            continue
        failures.append((spelling, actual))
    assert failures == [], failures


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
