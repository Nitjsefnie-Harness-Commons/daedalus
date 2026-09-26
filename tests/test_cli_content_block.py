#!/usr/bin/env python3
"""Every branch of do_block_requests, _positive_rule_id,
do_unblock_requests and do_list_block_rules in
daedalus_cli/commands_content.py.

The three handlers build their own `/command` body and pair `api` with
`wait_for_result`, so each test asserts the body that reached the wire
and the delivery the handler then waited on, beside the line an operator
would read back. `_positive_rule_id` runs at parse time and is reached
through the real parser's refusal rather than through a handler.

`--rule-id 0` and `--chrome-tab 0` are the two values that separate a
presence test from a truthiness test in this module, and each is pinned
here: both guards read `is not None`, and a handler that had written `if
x:` would drop the field silently while every other value still passed.

No marker glyphs are folded here: every line these handlers print is
plain ASCII, so the expected output below is the whole of it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cli_dispatch  # noqa: E402
import _cli_parse  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

run_cli = _cli_dispatch.run_cli
run_cli_exit = _cli_dispatch.run_cli_exit
refused = _cli_parse.refused

TOK = 'clitok'
PATTERN = '*.cdn.example.com/vod/*/seg-*'


def _put(body):
    return {'via': 'api', 'method': 'PUT', 'path': '/command', 'body': body}


def _wait(cmd_id, delivery):
    return {'via': 'wait_for_result', 'id': cmd_id, 'tab': 'extension',
            'delivery': delivery, 'timeout': 10, 'interval': 0.5}


def _envelope(**over):
    base = {'id': '_x', 'result': {}, 'error': None, 'ts': 1}
    return dict(base, **over)


# ── _positive_rule_id ────────────────────────────────────────────────

def test_a_rule_id_that_is_not_an_integer_is_refused_at_parse_time(tmp):
    """The ValueError arm names the value and says what was expected.

    The refusal happens before anything is enqueued, which is the whole
    point of the check: a rule id the extension cannot read would
    otherwise travel as a string and be dropped on arrival.
    """
    del tmp
    code, message = refused(['unblock-requests', '--rule-id', 'seven'])

    assert code == 2, (code, message)
    assert "'seven' is not an integer" in message, message


def test_a_rule_id_of_zero_is_refused_because_rule_ids_are_positive(tmp):
    """Zero is the boundary, and it is refused rather than forwarded.

    The extension reads a present-but-zero id as an absent one, which
    widens the removal into every rule; the CLI is where that is caught.
    """
    del tmp
    code, message = refused(['unblock-requests', '--rule-id', '0'])

    assert code == 2, (code, message)
    assert 'rule ids are positive; got 0' in message, message


def test_a_positive_rule_id_reaches_the_wire_as_the_integer_it_was(tmp):
    """The accepted arm: `'7'` is decoded to `7`, and travels as `ruleId`.

    Pinned on the request rather than on the output, because the two
    arms a caller can distinguish -- one rule removed and every rule
    removed -- print the same shape of line.
    """
    del tmp
    body = {'id': '_unblock', 'type': 'unblock-requests', 'token': TOK,
            'tab': 'extension', 'ruleId': 7}
    recorded, out = run_cli(
        ['unblock-requests', '--rule-id', '7'],
        [{'did': 'd9'}, _envelope(result={'removed': [7]})],
        plan=[_put(body), _wait('_unblock', 'd9')], token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == 'Removed 1 rule(s): [7]\n', repr(out)


# ── do_block_requests ────────────────────────────────────────────────

def test_do_block_requests_sends_the_pattern_it_was_given(tmp):
    """The bare arm: the pattern, no `tabId`, and the rule the extension
    reported back.

    `tabs` is the extension's own list of the tabs it resolved the
    pattern against, so the line is what an operator reads to learn
    whether the block reached what they meant.
    """
    del tmp
    body = {'id': '_block', 'type': 'block-requests', 'token': TOK,
            'tab': 'extension', 'pattern': PATTERN}
    recorded, out = run_cli(
        ['block-requests', PATTERN],
        [{'did': 'd1'},
         _envelope(result={'pattern': PATTERN, 'ruleId': 3,
                           'tabIds': [11, 12]})],
        plan=[_put(body), _wait('_block', 'd1')], token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == f'Blocked: {PATTERN}  ruleId=3  tabs=[11, 12]\n', repr(out)


def test_do_block_requests_names_the_tab_chrome_numbered_zero(tmp):
    """`--chrome-tab 0` adds `tabId: 0`; the guard is a presence test."""
    del tmp
    body = {'id': '_block', 'type': 'block-requests', 'token': TOK,
            'tab': 'extension', 'pattern': PATTERN, 'tabId': 0}
    recorded, out = run_cli(
        ['block-requests', PATTERN, '--chrome-tab', '0'],
        [{'did': 'd1'}, _envelope(result={'pattern': PATTERN, 'ruleId': 1})],
        plan=[_put(body), _wait('_block', 'd1')], token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == f'Blocked: {PATTERN}  ruleId=1  tabs=[]\n', repr(out)


def test_do_block_requests_renders_a_result_echoing_nothing(tmp):
    """A result with none of the three keys prints three empty cells.

    An empty pattern cell is what a rule the extension declined to echo
    looks like, and printing `None` there would read as a real pattern
    spelled badly.
    """
    del tmp
    body = {'id': '_block', 'type': 'block-requests', 'token': TOK,
            'tab': 'extension', 'pattern': PATTERN}
    _recorded, out = run_cli(
        ['block-requests', PATTERN], [{'did': 'd1'}, _envelope(result={})],
        plan=[_put(body), _wait('_block', 'd1')], token=TOK)

    assert out == 'Blocked:   ruleId=  tabs=[]\n', repr(out)


def test_do_block_requests_exits_when_no_result_arrives(tmp):
    """The timeout exit names the fixed ten this handler waits for."""
    del tmp
    body = {'id': '_block', 'type': 'block-requests', 'token': TOK,
            'tab': 'extension', 'pattern': PATTERN}
    code, out = run_cli_exit(
        ['block-requests', PATTERN], [{'did': 'd1'}, None],
        plan=[_put(body), _wait('_block', 'd1')], token=TOK)

    assert code == 'Timeout (10s)', code
    assert out == '', repr(out)


def test_do_block_requests_exits_with_the_error_the_extension_reported(tmp):
    """A refused whole command is an exit, not an empty result line."""
    del tmp
    body = {'id': '_block', 'type': 'block-requests', 'token': TOK,
            'tab': 'extension', 'pattern': PATTERN}
    code, out = run_cli_exit(
        ['block-requests', PATTERN],
        [{'did': 'd1'},
         {'id': '_block', 'error': 'Extension asleep', 'ts': 1}],
        plan=[_put(body), _wait('_block', 'd1')], token=TOK)

    assert code == 'Error: Extension asleep', code
    assert out == '', repr(out)


# ── do_unblock_requests ──────────────────────────────────────────────

def test_do_unblock_requests_omits_the_rule_id_when_none_was_given(tmp):
    """The bare arm asks for every rule, and says how many went.

    Two ids rather than one: a single-element list would satisfy a
    handler that rendered the same lone id, so the count and the list
    are only pinned when there is more than one thing to count.
    """
    del tmp
    body = {'id': '_unblock', 'type': 'unblock-requests', 'token': TOK,
            'tab': 'extension'}
    recorded, out = run_cli(
        ['unblock-requests'],
        [{'did': 'd2'}, _envelope(result={'removed': [4, 9]})],
        plan=[_put(body), _wait('_unblock', 'd2')], token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert out == 'Removed 2 rule(s): [4, 9]\n', repr(out)


def test_do_unblock_requests_renders_an_empty_removal_as_zero(tmp):
    """No rules went, so the line is a zero and an empty list."""
    del tmp
    body = {'id': '_unblock', 'type': 'unblock-requests', 'token': TOK,
            'tab': 'extension'}
    _recorded, out = run_cli(
        ['unblock-requests'], [{'did': 'd2'}, _envelope(result={})],
        plan=[_put(body), _wait('_unblock', 'd2')], token=TOK)

    assert out == 'Removed 0 rule(s): []\n', repr(out)


def test_do_unblock_requests_exits_when_no_result_arrives(tmp):
    """The timeout exit, with nothing rendered before it."""
    del tmp
    body = {'id': '_unblock', 'type': 'unblock-requests', 'token': TOK,
            'tab': 'extension'}
    code, out = run_cli_exit(
        ['unblock-requests'], [{'did': 'd2'}, None],
        plan=[_put(body), _wait('_unblock', 'd2')], token=TOK)

    assert code == 'Timeout (10s)', code
    assert out == '', repr(out)


def test_do_unblock_requests_exits_with_the_error_the_extension_reported(tmp):
    """A refused whole command is an exit, not a zero."""
    del tmp
    body = {'id': '_unblock', 'type': 'unblock-requests', 'token': TOK,
            'tab': 'extension'}
    code, out = run_cli_exit(
        ['unblock-requests'],
        [{'did': 'd2'},
         {'id': '_unblock', 'error': 'no such rule', 'ts': 1}],
        plan=[_put(body), _wait('_unblock', 'd2')], token=TOK)

    assert code == 'Error: no such rule', code
    assert out == '', repr(out)


# ── do_list_block_rules ──────────────────────────────────────────────

def test_do_list_block_rules_prints_one_row_per_rule_in_the_order_given(tmp):
    """Two rules, and the count that says there were two.

    The listing does not sort, so this pins the order the extension
    returned: `tabIds` and `urlFilter` differ per row, and a handler
    that printed one row per rule from a one-rule fixture would satisfy
    a weaker version of this test without ever rendering the second.
    """
    del tmp
    body = {'id': '_list_rules', 'type': 'list-block-rules', 'token': TOK,
            'tab': 'extension'}
    rules = [
        {'id': 4, 'condition': {'urlFilter': '*.a.example.com/*',
                                'tabIds': [1, 2]}},
        {'id': 2, 'condition': {'urlFilter': '*.b.example.com/*'}},
    ]
    _recorded, out = run_cli(
        ['list-block-rules'], [{'did': 'd3'}, _envelope(result=rules)],
        plan=[_put(body), _wait('_list_rules', 'd3')], token=TOK)

    assert out == (
        '  id=4  pattern=*.a.example.com/*  tabs=[1, 2]\n'
        '  id=2  pattern=*.b.example.com/*  tabs=all\n'
        '2 rule(s)\n'), repr(out)


def test_do_list_block_rules_names_every_rule_the_bridge_reported(tmp):
    """The body carries no selector at all, and the wait is fixed."""
    del tmp
    body = {'id': '_list_rules', 'type': 'list-block-rules', 'token': TOK,
            'tab': 'extension'}
    recorded, out = run_cli(
        ['list-block-rules'],
        [{'did': 'd3'},
         _envelope(result=[{'id': 1, 'condition': {'urlFilter': '*'}}])],
        plan=[_put(body), _wait('_list_rules', 'd3')], token=TOK)

    assert recorded.api_calls == [('PUT', '/command', body)], \
        recorded.api_calls
    assert recorded.timeouts == [10], recorded.timeouts
    assert out == '  id=1  pattern=*  tabs=all\n1 rule(s)\n', repr(out)


def test_do_list_block_rules_renders_a_rule_carrying_no_condition(tmp):
    """A rule with no condition reads an empty pattern and `all` tabs.

    Both defaults at once: the pattern cell must be visibly empty rather
    than `None`, and the tab cell says the rule is not narrowed to any
    tab, which is the opposite of "unknown".
    """
    del tmp
    body = {'id': '_list_rules', 'type': 'list-block-rules', 'token': TOK,
            'tab': 'extension'}
    _recorded, out = run_cli(
        ['list-block-rules'],
        [{'did': 'd3'}, _envelope(result=[{'id': 8}])],
        plan=[_put(body), _wait('_list_rules', 'd3')], token=TOK)

    assert out == '  id=8  pattern=  tabs=all\n1 rule(s)\n', repr(out)


def test_do_list_block_rules_says_so_when_no_rule_is_active(tmp):
    """An empty list is a sentence, and the count line is not printed."""
    del tmp
    body = {'id': '_list_rules', 'type': 'list-block-rules', 'token': TOK,
            'tab': 'extension'}
    _recorded, out = run_cli(
        ['list-block-rules'], [{'did': 'd3'}, _envelope(result=[])],
        plan=[_put(body), _wait('_list_rules', 'd3')], token=TOK)

    assert out == 'No active block rules\n', repr(out)


def test_do_list_block_rules_exits_when_no_result_arrives(tmp):
    """The timeout exit, with nothing rendered before it."""
    del tmp
    body = {'id': '_list_rules', 'type': 'list-block-rules', 'token': TOK,
            'tab': 'extension'}
    code, out = run_cli_exit(
        ['list-block-rules'], [{'did': 'd3'}, None],
        plan=[_put(body), _wait('_list_rules', 'd3')], token=TOK)

    assert code == 'Timeout (10s)', code
    assert out == '', repr(out)


def test_do_list_block_rules_exits_with_the_error_the_extension_reported(tmp):
    """A refused whole command is an exit, not an empty listing."""
    del tmp
    body = {'id': '_list_rules', 'type': 'list-block-rules', 'token': TOK,
            'tab': 'extension'}
    code, out = run_cli_exit(
        ['list-block-rules'],
        [{'did': 'd3'},
         {'id': '_list_rules', 'error': 'Extension asleep', 'ts': 1}],
        plan=[_put(body), _wait('_list_rules', 'd3')], token=TOK)

    assert code == 'Error: Extension asleep', code
    assert out == '', repr(out)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='clicontblk_')


if __name__ == '__main__':
    raise SystemExit(main())
