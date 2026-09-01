#!/usr/bin/env python3
"""The mask reveals a template interpolation as code, and the scanner agrees
with Node about what ran. Every case runs under Node beside the scanner and
demands agreement, so a case whose write never ran cannot pass vacuously."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsread import js_mask  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402


_NODE_PREFIX = """const calls = [];
const extCmd = (...args) => calls.push(args);
const ordinary = () => undefined;
const chromeTab = 41;
"""


def _node_and_guard(source, path):
    """Run the send under Node and read the scanner's verdict on one file."""
    script = (_NODE_PREFIX + source
              + "\nconst routed = calls.some(args => args.some(\n"
              + "  value => value && value.tab === chromeTab));\n"
              + "process.stdout.write(routed ? '1' : '0');\n")
    path.write_text(script, encoding='utf-8')
    node = shutil.which('node')
    assert node, 'node is required to execute JavaScript routing controls'
    ran = subprocess.run([node, str(path)], capture_output=True, text=True,
                         timeout=30)
    assert ran.returncode == 0, (ran.returncode, ran.stdout, ran.stderr)
    return ran.stdout == '1', bool(js_tab_routing_violations(path, path.name))


_TEMPLATE_CASES = [
    ('repro', True,
     "let send = ordinary;\n"
     "const obj = { hook: `a${(send = extCmd, 1)}b` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('construction-demotion', False,
     "let send = extCmd;\n"
     "const obj = { hook: `t${(send = ordinary, 1)}t` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('object-before-write', True,
     "let send = ordinary;\n"
     "const o = { h: `${ {a: 1} && (send = extCmd, 1)}` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('object-before-demotion', False,
     "let send = extCmd;\n"
     "const o = { h: `${ {a: 1} && (send = ordinary, 1)}` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('promotion-then-demotion', False,
     "let send = ordinary;\n"
     "const obj = { hook: `t${(send = extCmd, 1)}t` };\n"
     "send = ordinary;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('escaped-interpolation', False,
     "let send = ordinary;\n"
     "const obj = { hook: `\\${(send = extCmd, 1)}` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('second-interpolation', True,
     "let send = ordinary;\n"
     "const obj = { hook: `a${0}b${(send = extCmd, 1)}c` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('nested-template', True,
     "let send = ordinary;\n"
     "const obj = { hook: `a${`n${(send = extCmd, 1)}c`}b` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('brace-in-string', True,
     "let send = ordinary;\n"
     "const bag = { '}': 1 };\n"
     "const obj = { hook: `a${bag['}'] + ((send = extCmd, 1))}b` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('backtick-in-string', True,
     "let send = ordinary;\n"
     "const tags = { '`': 1 };\n"
     "const obj = { hook: `a${tags['`']}b${(send = extCmd, 1)}c` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('escaped-backtick', True,
     "let send = ordinary;\n"
     "const obj = { hook: `a\\`b${(send = extCmd, 1)}c` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('plain-template', True,
     "let send = ordinary;\n"
     "const obj = { hook: `plain` };\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    # Pins Node-agreement only, not the fix: under a mask that never read
    # regex literals the phantom string swallowed the demotion and the send
    # alike, so the two verdicts agreed either way. The mask-level pin for
    # this direction is test_regex_body_quote_and_comment_opener_stay_literal.
    ('regex-quote-demotion', False,
     "let send = extCmd;\n"
     "const obj = { hook: /[\"]/ };\n"
     "send = ordinary;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('regex-quote-promotion', True,
     "let send = ordinary;\n"
     "const obj = { hook: /[\"]/ };\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('regex-brace-write', True,
     "let send = ordinary;\n"
     "const o = { h: `${/}/.test('}') && (send = extCmd, 1)}` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('regex-equals-brace-write', True,
     "let send = ordinary;\n"
     "const o = { h: `${/=}/.test('x=}') && (send = extCmd, 1)}` };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('division-after-template', True,
     "let send = ordinary;\n"
     "const half = `t` / \"a/b\" / 2;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('division-after-object', True,
     "let send = ordinary;\n"
     "const half = {a: 1} / \"a/b\" / 2;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('grouped-write', True,
     "let send = ordinary;\n"
     "const obj = { hook: ((send = extCmd)) };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('grouped-demotion', False,
     "let send = extCmd;\n"
     "const obj = { hook: ((send = ordinary)) };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('sequence-write', True,
     "let send = ordinary;\n"
     "const obj = { hook: (send = extCmd, 1) };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('computed-key-write', True,
     "let send = ordinary;\n"
     "const obj = { ['k']: (send = extCmd) };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('spread-write', True,
     "let send = ordinary;\n"
     "const obj = { ...((send = extCmd, {})) };\n"
     "send('focus-tab', { tab: chromeTab });\n"),
]


def test_template_interpolation_sends_match_runtime(tmp):
    """Node's verdict governs every arrangement; the scanner may only agree
    with it, reporting where the send routes and staying clean where an
    escaped form never runs."""
    path = Path(tmp) / 'template.js'
    observed = [(label, *_node_and_guard(source, path))
                for label, _, source in _TEMPLATE_CASES]
    expected = [(label, verdict, verdict)
                for label, verdict, _ in _TEMPLATE_CASES]
    assert observed == expected, observed


def test_template_mask_reveals_interpolations_only(tmp):
    """Positions and newlines survive; literal chunks stay blanked and the
    interpolation expression is the only thing revealed."""
    del tmp
    literal = "`a${(send = extCmd, 1)}b`"
    mask = js_mask(literal)
    assert len(mask) == len(literal), mask
    assert 'send = extCmd' in mask, mask
    assert '${' in mask, mask
    assert '`' not in mask, mask
    escaped = "`a\\${(send = extCmd, 1)}b`"
    assert js_mask(escaped) == ' ' * len(escaped), escaped
    nested = "`a${`n${(send = ordinary, 2)}c`}d`"
    assert 'send = ordinary' in js_mask(nested), js_mask(nested)
    inside = js_mask("`a${obj['}'] + (send = extCmd, 1)}b` tail")
    assert 'send = extCmd' in inside, inside
    assert ' tail' in inside, inside
    multiline = "`a${x\ny}b`\nlet after;\n"
    assert js_mask(multiline).count('\n') == multiline.count('\n'), (
        js_mask(multiline))
    raw_newline = "`chunk\nnext`"
    line_continuation = "`chunk\\\nnext`"
    newline_masks = (js_mask(raw_newline), js_mask(line_continuation))
    assert newline_masks == ("      \n     ", "       \n     "), newline_masks


def test_regex_body_quote_and_comment_opener_stay_literal(tmp):
    """A `\"` and a `/*` inside a regex body are body text, not state: the
    walk over the code after the regex is exactly the walk it would be
    without the regex, so a demotion there stays visible."""
    del tmp
    quote = 'const obj = { hook: /["]/ };\nlet after;\n'
    quote_mask = js_mask(quote)
    assert len(quote_mask) == len(quote), quote_mask
    assert quote_mask == js_mask('const obj = { hook: /   / };\n'
                                 'let after;\n'), quote_mask
    assert 'let after;' in quote_mask, quote_mask
    starred = 'const obj = { hook: /[/*]/ };\nlet after;\n'
    starred_mask = js_mask(starred)
    assert len(starred_mask) == len(starred), starred_mask
    assert starred_mask == js_mask('const obj = { hook: /    / };\n'
                                   'let after;\n'), starred_mask
    assert 'let after;' in starred_mask, starred_mask


def test_regex_body_brace_stays_out_of_interpolation_state(tmp):
    """A `}` inside a regex body does not close the interpolation holding
    it; the construction-time write after the regex stays visible."""
    del tmp
    source = ("const o = { h: `${/}/.test('}') && (send = extCmd, 1)}` };\n"
              'let after;\n')
    mask = js_mask(source)
    assert 'send = extCmd' in mask, mask
    assert 'let after;' in mask, mask


def test_regex_literal_body_is_masked_not_code(tmp):
    """The body and the flags are blanked in place; the delimiters stay so
    an expression continuation still sees a `/` at the line head."""
    del tmp
    source = 'const re = /ab+c/gi;\nlet after = 1;\n'
    assert js_mask(source) == 'const re = /    /  ;\nlet after = 1;\n'


def test_division_slashes_stay_code_in_the_mask(tmp):
    """A slash with a value on its left is division and passes through
    untouched, whatever follows it on the line."""
    del tmp
    source = 'let half = a / b / c;\nlet after;\n'
    assert js_mask(source) == source, js_mask(source)


def test_regex_opening_follows_the_previous_token(tmp):
    """A statement head reopens regex position after a keyword's `)` or a
    closing block brace, and an escaped slash does not close a body
    early; division after a plain value still passes through."""
    del tmp
    head = 'if (x) /["]/.test(s);\nlet after;\n'
    assert 'let after;' in js_mask(head), js_mask(head)
    block = 'function g() {}\n/["]/.test(x);\nlet after;\n'
    assert 'let after;' in js_mask(block), js_mask(block)
    escaped = 'const r = /a\\/b/;\nlet after;\n'
    assert js_mask(escaped) == 'const r = /    /;\nlet after;\n'


def test_regex_body_starting_with_slash_equals_stays_literal(tmp):
    """`/=` opens a regex body wherever a regex may open, and a quote it
    opens with stays body text: the previous token decides, not the two
    characters at the slash."""
    del tmp
    source = 'const obj = { hook: /="/ };\nlet after;\n'
    mask = js_mask(source)
    assert len(mask) == len(source), mask
    assert mask == js_mask('const obj = { hook: /  / };\nlet after;\n'), mask
    assert 'let after;' in mask, mask


def test_regex_body_brace_with_equals_stays_out_of_interpolation_state(tmp):
    """A body opening `=}` is body text too: the `}` does not close the
    interpolation holding the regex, so the write behind it stays
    visible."""
    del tmp
    source = ("const o = { h: `${/=}/.test('x=}')"
              ' && (send = extCmd, 1)}` };\nlet after;\n')
    mask = js_mask(source)
    assert len(mask) == len(source), mask
    assert mask.count('\n') == source.count('\n'), mask
    assert 'send = extCmd' in mask, mask
    assert 'let after;' in mask, mask


def test_division_after_a_blanked_literal_reads_as_division(tmp):
    """A string or template literal is blanked whole, so it leaves no token
    for the previous-token test to read: the `/` directly after one is
    division, the string behind it keeps both quotes, and the line after
    it stays visible."""
    del tmp
    source = 'x = `t` / "a/b" / y;\nif (v) send();\n'
    mask = js_mask(source)
    expected = ('x = ' + ' ' * 3 + ' / ' + ' ' * 5 + ' / y;\n'
                'if (v) send();\n')
    assert mask == expected, mask


def test_division_after_an_object_literal_reads_as_division(tmp):
    """A `}` that closed an object literal is an operand, so the `/` after
    it is division and the string behind it keeps both quotes."""
    del tmp
    source = 'x = {a:1} / "a/b" / y;\nif (v) send();\n'
    mask = js_mask(source)
    expected = 'x = {a:1} / ' + ' ' * 6 + '/ y;\nif (v) send();\n'
    assert mask == expected, mask


def test_division_after_an_object_literal_keeps_a_write_visible(tmp):
    """The object-literal reading also keeps a bracketed assignment behind
    the slash in view, where reading it as a regex body would blank the
    write."""
    del tmp
    source = 'const x = {}\n/ [send = extCmd] / 1;\nlet after;\n'
    mask = js_mask(source)
    assert len(mask) == len(source), mask
    assert mask.count('\n') == source.count('\n'), mask
    assert 'send = extCmd' in mask, mask
    assert 'let after;' in mask, mask


def test_division_after_a_head_named_method_call_stays_division(tmp):
    """A method named for a head keyword is not a head: the `)` of its call
    does not reopen regex position, so the division behind it passes
    through unchanged."""
    del tmp
    source = 'const v = obj.if(x) / 2 / y;\nlet after;\n'
    assert js_mask(source) == source, js_mask(source)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsmask_')


if __name__ == '__main__':
    raise SystemExit(main())
