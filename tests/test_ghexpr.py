#!/usr/bin/env python3
"""Executable contracts for the strict GitHub Actions expression reader."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ghexpr import ExpressionError, evaluate  # noqa: E402


def _raises(expression, context):
    """Require an expression to refuse instead of returning a guess."""
    try:
        evaluate(expression, context)
    except ExpressionError:
        return
    raise AssertionError(f'expression was accepted: {expression!r}')


def test_boolean_logic_and_parentheses(tmp):
    """The supported operators obey Actions' precedence and negation."""
    del tmp
    assert evaluate('true && !false', {}) is True
    assert evaluate('false || (true && false)', {}) is False
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
    """Admitted scalar values follow Actions' truthiness and loose equality."""
    del tmp
    assert evaluate('empty', {'empty': ''}) is False
    assert evaluate('zero', {'zero': 0}) is False
    assert evaluate('text_zero', {'text_zero': '0'}) is True
    assert evaluate('items', {'items': []}) is True
    assert evaluate("value == 'FOO'", {'value': 'foo'}) is True
    assert evaluate('value == number', {
        'value': '1', 'number': 1,
    }) is True
    assert evaluate('value == number', {
        'value': '', 'number': 0,
    }) is True
    assert evaluate("value != 'bar'", {'value': 'foo'}) is True


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
