#!/usr/bin/env python3
"""Which physical JavaScript lines contribute to coverage's denominator."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
from js_lines import _Scanner, code_lines  # noqa: E402


def test_blank_and_comment_only_lines_are_not_code(tmp):
    """Removing whitespace and comments can leave no denominator line."""
    del tmp
    source = 'const value = 1;\n\n   \n// note\n/* note */\n}\n'
    assert code_lines(source) == {1, 6}


def test_byte_order_mark_is_javascript_whitespace(tmp):
    """A BOM-only physical line does not enter the denominator."""
    del tmp
    source = '\ufeff\nconst value = 1;\n'
    assert code_lines(source) == {2}


def test_line_comments_end_at_each_javascript_terminator(tmp):
    """All four JavaScript line terminators end a line comment."""
    del tmp
    source = (
        'const a = 1; // after code\n'
        '// only\r'
        'const b = 2; // after code\u2028'
        '// only\u2029'
        'const c = 3;')
    assert code_lines(source) == {1, 3, 5}


def test_crlf_is_one_physical_line_terminator(tmp):
    """A CRLF pair advances the physical line number only once."""
    del tmp
    source = 'const one = 1;\r\nconst two = 2;\r\nconst three = 3;\r\n'
    assert code_lines(source) == {1, 2, 3}


def test_block_comments_can_span_lines_and_share_code_lines(tmp):
    """Only text outside a spanning block comment contributes code."""
    del tmp
    source = (
        'const before = 1; /* open\n'
        'inside\n'
        'close */ const after = 2;\n'
        '/* only */\n')
    assert code_lines(source) == {1, 3}


def test_comment_openers_inside_quoted_strings_are_text(tmp):
    """Single and double quoted strings retain comment-looking text."""
    del tmp
    source = (
        "const single = 'it\\'s // text';\n"
        'const double = "a \\"/* text";\n')
    assert code_lines(source) == {1, 2}


def test_comment_openers_inside_a_template_are_text(tmp):
    """Template raw text is code even when it resembles comments."""
    del tmp
    source = (
        'const value = `// text\n'
        'still /* text */`;\n')
    assert code_lines(source) == {1, 2}


def test_template_holes_nest_and_model_their_own_literals(tmp):
    """A hole can hold braces, comments, strings and nested templates."""
    del tmp
    source = (
        'const value = `outer ${\n'
        '  {nested: `inner ${"`" /* note */}`}\n'
        '} tail`;\n')
    assert code_lines(source) == {1, 2, 3}


def test_template_backslash_escapes_preserve_raw_text(tmp):
    """Backslash escapes, including an escaped backtick, stay raw."""
    del tmp
    source = 'const text = `slash \\\\ and tick \\``;\n'
    assert code_lines(source) == {1}


def test_regex_slashes_survive_classes_and_escapes(tmp):
    """Neither a class slash nor an escaped slash ends a regex early."""
    del tmp
    source = (
        'const inClass = /[/]/g;\n'
        'const escaped = /^data:image\\/\\w+;base64,/i;\n')
    assert code_lines(source) == {1, 2}


def test_regex_character_class_can_be_followed_by_a_quantifier(tmp):
    """A slash inside a class does not end the regex before its star."""
    del tmp
    assert code_lines('const slashRun = /[/]*/;\n') == {1}


def test_regex_literals_follow_start_keywords_and_punctuators(tmp):
    """Expression-leading positions classify a slash as a regex."""
    del tmp
    source = (
        '/^start/;\n'
        'return /after-return/;\n'
        'const grouped = (/after-paren/);\n'
        'const selected = ready ? /yes/ : /no/;\n')
    assert code_lines(source) == {1, 2, 3, 4}


def test_throw_keyword_is_followed_by_a_regex_literal(tmp):
    """The expression after throw can begin with a regex literal."""
    del tmp
    assert code_lines('throw /boom/;\n') == {1}


def test_keywords_used_as_property_names_end_expressions(tmp):
    """Keyword spellings after member access are property names."""
    del tmp
    source = (
        'obj.throw / 2;\n'
        'obj?.throw / 2;\n'
        'obj.return / 2;\n'
        'obj.delete / 2;\n'
        'throw /boom/;\n'
        'throwable / 2;\n')
    assert code_lines(source) == {1, 2, 3, 4, 5, 6}


def test_brace_ended_receivers_can_have_keyword_properties(tmp):
    """Object and class expressions retain member-access context."""
    del tmp
    source = (
        'const objectValue = {}.yield / 2;\n'
        'const classValue = class {}?.yield / 2;\n')
    assert code_lines(source) == {1, 2}


def test_division_follows_parentheses_identifiers_and_numbers(tmp):
    """Expression-ending tokens classify a slash as division."""
    del tmp
    source = (
        'const seconds = (Date.now() - ms) / 1000;\n'
        'const ratio = total / count;\n'
        'const half = 10 / 2;\n')
    assert code_lines(source) == {1, 2, 3}


def test_division_follows_postfix_increment_and_decrement(tmp):
    """Postfix update tokens leave a completed expression before slash."""
    del tmp
    source = 'const up = n++ / 2;\nconst down = n-- / 2;\n'
    assert code_lines(source) == {1, 2}


def test_numeric_exponent_can_end_an_expression(tmp):
    """A signed exponent remains one number before division."""
    del tmp
    source = 'const large = 1e+5;\nconst half = 1e+5 / 2;\n'
    assert code_lines(source) == {1, 2}


def _assert_refusal(source, shape, line):
    """Assert one unsupported source shape is named with its location."""
    path = 'fixtures/refusal.js'
    try:
        code_lines(source, path)
    except ValueError as failure:
        message = str(failure)
        assert path in message, message
        assert f'line {line}' in message, message
        assert shape in message, message
    else:
        raise AssertionError(f'accepted unsupported {shape}')


def test_slash_after_a_closing_brace_is_refused(tmp):
    """Block and object endings are deliberately not guessed apart."""
    del tmp
    _assert_refusal('function done() {}\n/pattern/;\n',
                    'slash after }', 2)


def test_slash_after_an_unclassified_token_is_refused(tmp):
    """A slash outside the stated token rules cannot affect a metric."""
    del tmp
    _assert_refusal('value. / pattern;\n', 'unclassified slash', 1)


def test_escaped_identifier_is_refused_by_its_own_shape(tmp):
    """An unsupported escaped identifier is not blamed on its slash."""
    del tmp
    _assert_refusal(r'\u{61} / 2;', 'escaped identifier', 1)


def test_advancing_at_end_of_source_is_a_noop(tmp):
    """An attempted cursor advance at EOF changes no cursor state."""
    del tmp
    scanner = _Scanner('', 'fixtures/empty.js')
    scanner._advance()
    assert (scanner.index, scanner.line) == (0, 1)


def test_unterminated_block_comment_is_refused(tmp):
    """A block comment reaching EOF names its opening line."""
    del tmp
    _assert_refusal('const value = 1;\n/* never closed',
                    'unterminated block comment', 2)


def test_unterminated_string_is_refused(tmp):
    """A quoted string reaching EOF names its opening line."""
    del tmp
    _assert_refusal("const value = 1;\nconst text = 'open",
                    'unterminated string', 2)


def test_string_reaching_a_line_ending_is_refused(tmp):
    """A quoted string cannot cross an unescaped physical line."""
    del tmp
    _assert_refusal("const text = 'open\nstill open",
                    'unterminated string', 1)


def test_unterminated_template_is_refused(tmp):
    """A template or one of its holes must reach the closing backtick."""
    del tmp
    _assert_refusal('const value = 1;\nconst text = `open ${value',
                    'unterminated template', 2)


def test_raw_unterminated_template_is_refused(tmp):
    """Raw template text reaching EOF names the opening line."""
    del tmp
    _assert_refusal('const text = `open', 'unterminated template', 1)


def test_unterminated_regex_is_refused(tmp):
    """A regex reaching EOF names its opening line."""
    del tmp
    _assert_refusal('const value = 1;\nconst match = /open[abc',
                    'unterminated regex', 2)


def test_regex_reaching_a_line_ending_is_refused(tmp):
    """An unescaped physical line ending cannot occur in a regex."""
    del tmp
    _assert_refusal('const match = /open\nstill open',
                    'unterminated regex', 1)


def test_regex_escaped_line_ending_is_refused(tmp):
    """A backslash cannot make a physical line ending part of a regex."""
    del tmp
    _assert_refusal('const match = /open\\\nstill open/;',
                    'unterminated regex', 1)


def test_every_shipped_javascript_file_is_modelled(tmp):
    """Every tracked shipped script has a bounded nonempty denominator."""
    del tmp
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z', '--',
         'extension', 'dashboard'],
        capture_output=True, check=True, timeout=30)
    paths = sorted(
        Path(raw.decode('utf-8', 'surrogateescape'))
        for raw in listed.stdout.split(b'\0')
        if raw and raw.endswith(b'.js'))
    assert paths, 'Git returned no tracked shipped JavaScript files'
    for relative in paths:
        source = (ROOT / relative).read_text(encoding='utf-8')
        found = code_lines(source, str(relative))
        nonblank = sum(bool(line.strip()) for line in source.splitlines())
        assert found, f'{relative} has an empty code-line denominator'
        assert len(found) <= nonblank, (
            f'{relative}: {len(found)} code lines exceed '
            f'{nonblank} non-blank lines')


def main():
    """Run this file as a standalone suite."""
    return _util.runner(_util.collect(globals()), tmp_prefix='jslines_')


if __name__ == '__main__':
    raise SystemExit(main())
