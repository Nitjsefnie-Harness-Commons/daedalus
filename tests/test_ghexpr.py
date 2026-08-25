#!/usr/bin/env python3
"""Executable contracts for the strict GitHub Actions expression reader."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ghexpr import (  # noqa: E402
    MAX_NESTING_DEPTH,
    MAX_SOURCE_LENGTH,
    MAX_TOKEN_COUNT,
    ExpressionError,
    evaluate,
)


def _raises(expression, context, detail=None):
    """Require an expression to refuse instead of returning a guess."""
    try:
        evaluate(expression, context)
    except ExpressionError as error:
        if detail is not None:
            assert detail in str(error), str(error)
        return
    raise AssertionError(f'expression was accepted: {expression!r}')


def test_boolean_logic_and_parentheses(tmp):
    """The supported operators obey Actions' precedence and negation."""
    del tmp
    assert evaluate('true && !false', {}) is True
    assert evaluate('false || (true && false)', {}) is False
    assert evaluate('false && true || true', {}) is True
    assert evaluate('!(false || false)', {}) is True


def test_context_paths_and_expression_wrapper(tmp):
    """Dotted values and the optional Actions wrapper are evaluated."""
    del tmp
    context = {'github': {'event': {'name': 'Pull_Request'}}}
    assert evaluate("${{ github.event.name == 'pull_request' }}", context)
    assert evaluate("github.event.name != 'push'", context)


def test_status_functions_come_from_the_context(tmp):
    """Status functions read explicit values rather than hidden defaults."""
    del tmp
    context = {'status': {
        'always': True,
        'success': False,
        'failure': True,
        'cancelled': False,
    }}
    assert evaluate('always()', context) is True
    assert evaluate('failure() && !cancelled()', context) is True
    assert evaluate('success()', context) is False


def test_actions_truthiness_and_comparison(tmp):
    """Admitted values keep their supported truthiness and equality."""
    del tmp
    assert evaluate('empty', {'empty': ''}) is False
    assert evaluate('nothing', {'nothing': None}) is False
    assert evaluate('text_zero', {'text_zero': '0'}) is True
    assert evaluate('items', {'items': []}) is True
    assert evaluate("value == 'FOO'", {'value': 'foo'}) is True
    assert evaluate('left != right', {
        'left': True, 'right': False,
    }) is True
    assert evaluate('value == true', {'value': 'TRUE'}) is True
    assert evaluate("value == 'It''s open source!'", {
        'value': "It's open source!",
    }) is True
    assert evaluate('value != empty', {
        'value': None, 'empty': '',
    }) is True
    assert evaluate("value != 'bar'", {'value': 'foo'}) is True
    _raises('zero', {'zero': 0}, 'numeric context value')
    _raises('value == zero', {
        'value': None, 'zero': 0,
    }, 'numeric context value')


def test_non_ascii_identifiers_and_comparisons_refuse(tmp):
    """The admitted identifier and string comparison alphabets are ASCII."""
    del tmp
    _raises('é', {'é': True}, 'ASCII identifier')
    _raises('github.é', {'github': {'é': True}}, 'ASCII identifier')
    _raises('left == right', {
        'left': 'ß', 'right': 'ss',
    }, 'non-ASCII string comparison')
    _raises('left == right', {
        'left': 'İ', 'right': 'i',
    }, 'non-ASCII string comparison')


def test_numeric_comparisons_refuse_instead_of_coercing(tmp):
    """Numbers never enter this bounded reader's comparison domain."""
    del tmp
    _raises('value == number', {
        'value': ' 1 ', 'number': 1,
    }, 'numeric context value')
    _raises('value == number', {
        'value': 'Infinity', 'number': float('inf'),
    }, 'numeric context value')
    _raises('left == right', {
        'left': 2 ** 53, 'right': 2 ** 53 + 1,
    }, 'numeric context value')


def test_reader_limits_refuse_at_the_first_unsafe_value(tmp):
    """Length, token, and nesting boundaries are stable ExpressionErrors."""
    del tmp
    source_at_limit = 'true' + ' ' * (MAX_SOURCE_LENGTH - len('true'))
    assert evaluate(source_at_limit, {}) is True
    _raises('true' + ' ' * (MAX_SOURCE_LENGTH - len('true') + 1), {},
            'source length')

    term_count = MAX_TOKEN_COUNT // 2
    at_token_limit = ' || '.join(['false'] * term_count)
    assert evaluate(at_token_limit, {}) is False
    past_token_limit = ' || '.join(['false'] * (term_count + 1))
    _raises(past_token_limit, {}, 'token count')

    at_depth_limit = '!' * MAX_NESTING_DEPTH + 'true'
    assert evaluate(at_depth_limit, {}) is True
    _raises('!' * (MAX_NESTING_DEPTH + 1) + 'true', {},
            'nesting depth')
    at_group_limit = '(' * MAX_NESTING_DEPTH + 'true' + ')' * MAX_NESTING_DEPTH
    assert evaluate(at_group_limit, {}) is True
    too_deep_groups = (
        '(' * (MAX_NESTING_DEPTH + 1) + 'true'
        + ')' * (MAX_NESTING_DEPTH + 1))
    _raises(too_deep_groups, {}, 'nesting depth')


def test_unknown_paths_and_functions_refuse(tmp):
    """Missing context and unsupported functions never become false."""
    del tmp
    _raises('github.event.missing == true', {
        'github': {'event': {}},
    })
    _raises('false && github.event.missing', {})
    _raises('contains(value, other)', {})
    _raises('missing()', {})


def test_unsupported_syntax_and_values_refuse(tmp):
    """Unsupported operators, quoting, calls and object comparisons fail."""
    del tmp
    _raises('123', {}, 'numeric literal')
    _raises('value >= 1', {'value': 1})
    _raises('value == "one"', {'value': 'one'})
    _raises('always(value)', {'status': {'always': True}})
    _raises("left == right == 'x'", {
        'left': 'a', 'right': 'b',
    })
    _raises('value == other', {'value': [], 'other': []})


def test_invalid_context_and_literals_refuse(tmp):
    """The reader requires mappings and rejects malformed literal syntax."""
    del tmp
    _raises("'unterminated", {})
    _raises('${{ true', {})
    _raises('true trailing', {})
    _raises('value', {'value': object()})
    _raises('always()', {})


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
