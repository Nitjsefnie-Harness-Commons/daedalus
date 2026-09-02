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
const target = { if: (v) => 1, do: (v) => 2 };
const a = 1;
let y = 1;
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
    ('division-after-nested-object', True,
     "let send = ordinary;\n"
     "const half = {a: {b:1} / \"a/b\" / 2};\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('division-after-ternary-object', True,
     "let send = ordinary;\n"
     "const half = 1 ? {a:1} : {b:2} / \"a/b\" / 2;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('ternary-decimal-consequent-write', True,
     "let send = ordinary;\n"
     "const half = 0?.5: {b:1} / [send = extCmd] / 2;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('ternary-decimal-consequent-arrow-write', True,
     "let send = ordinary;\n"
     "const f = () => 0?.5: {b:1} / [send = extCmd] / 2;\n"
     "f();\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('ternary-decimal-consequent-string-write', True,
     "let send = ordinary;\n"
     "const half = 0?.5: {b:1} / \"a/b\" / 2;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('member-case-before-ternary-colon-write', True,
     "let send = ordinary;\n"
     "const half = 0 ? x.case : {b:2} / [send = extCmd] / 2;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('case-keyed-nested-write', True,
     "let send = ordinary;\n"
     "const half = {case: {b:1} / \"a/b\" / 2};\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('case-named-member-write', True,
     "let send = ordinary;\n"
     "const half = target.case ? {a:1} : {b:2} / \"a/b\" / 2;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('labelled-block-write', True,
     "let send = ordinary;\n"
     "lbl: {}\n"
     "/['\"]/.test('q');\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('same-line-label-write', True,
     "let send = ordinary;\n"
     "lbl: {} /\"/.test(1); send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('case-clause-write', True,
     "let send = ordinary;\n"
     "switch (1) { case 1: {} /\"/.test(1); }\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('default-clause-write', True,
     "let send = ordinary;\n"
     "switch (1) { default: {} /\"/.test(1); }\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('case-ternary-clause-write', True,
     "let send = ordinary;\n"
     "switch (1) { case 1 ? 2 : 3: {} /\"/.test(1); }\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('division-after-regex', True,
     "let send = ordinary;\n"
     "const half = /x/ / \"a/b\" / 2;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('division-then-regex', True,
     "let send = ordinary;\n"
     "const half = 4 / /[\"]/.source;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('case-ternary-alternate-write', True,
     "let send = ordinary;\n"
     "switch (1) { case 1: const half = 0 ? {b:1} : {c:2}"
     " / [send = extCmd] / 2; }\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('case-key-second-write', True,
     "let send = ordinary;\n"
     "const half = {a:1, case: {b:1} / [send = extCmd] / 2};\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('default-key-second-write', True,
     "let send = ordinary;\n"
     "const half = {a:1, default: {b:1} / [send = extCmd] / 2};\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('label-after-ternary-write', True,
     "let send = ordinary;\n"
     "let c = 0; let x = c ? 1 : 2;\n"
     "lbl: {}\n"
     "/[\"']/.test('q');\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('label-after-optional-chain-write', True,
     "let send = ordinary;\n"
     "a?.b;\n"
     "lbl: {}\n"
     "/[\"']/.test('q');\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('label-after-nullish-write', True,
     "let send = ordinary;\n"
     "let z = y ?? 0;\n"
     "lbl: {}\n"
     "/[\"']/.test('q');\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('regex-division-regex-write', True,
     "let send = ordinary;\n"
     "const half = /x/ / /[\"]/.source;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('case-key-first-write', True,
     "let send = ordinary;\n"
     "const half = {case: {b:1} / [send = extCmd] / 2};\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('label-after-asi-optional-write', True,
     "let send = ordinary;\n"
     "a?.b\n"
     "lbl: {}\n"
     "/[\"']/.test('q');\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('label-after-asi-nullish-write', True,
     "let send = ordinary;\n"
     "let z2 = y ?? 0\n"
     "lbl: {}\n"
     "/[\"']/.test('q');\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('label-after-literal-statement-write', True,
     "let send = ordinary;\n"
     "let o = {a:1};\n"
     "lbl: {}\n"
     "/[\"']/.test('q');\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('spaced-method-division', True,
     "let send = ordinary;\n"
     "const half = target . if (1) / \"a/b\" / 2;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('optional-method-division', True,
     "let send = ordinary;\n"
     "const half = target?.if(1) / \"a/b\" / 2;\n"
     "send = extCmd;\n"
     "send('focus-tab', { tab: chromeTab });\n"),
    ('newline-method-division', True,
     "let send = ordinary;\n"
     "const half = target\n  .if(1) / \"a/b\" / 2;\n"
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


def test_division_after_a_case_keyed_literal_stays_division(tmp):
    """`case` naming an object-literal key is not a clause head: the
    literal it keys stays an operand, so the division behind it passes
    through and the line after stays visible."""
    del tmp
    source = 'const half = {case: {b:1} / "a/b" / 2};\nlet after;\n'
    mask = js_mask(source)
    assert mask == 'const half = {case: {b:1} /       / 2};\nlet after;\n'
    assert 'let after;' in mask, mask


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


def test_regex_after_a_labelled_block_stays_a_regex(tmp):
    """A `:` in front of a `{` also spells a statement label and a
    case/default clause, whose block Node follows with a regex literal:
    the body is blanked and the line behind it stays visible."""
    del tmp
    shapes = (
        'lbl: {} /["]/.test(s);',
        'a: b: {} /["]/.test(s);',
        'switch (1) { case 1: {} /["]/.test(1); }',
        'switch (1) { default: {} /["]/.test(1); }',
        'switch (1) { case 1 ? 2 : 3: {} /["]/.test(1); }',
        'switch (1) { case 0: 1; default: {} /["]/.test(1); }',
    )
    for shape in shapes:
        source = shape + '\nlet after;\n'
        mask = js_mask(source)
        assert len(mask) == len(source), mask
        assert mask == js_mask(shape.replace('/["]/', '/   /')
                               + '\nlet after;\n'), mask
        assert 'let after;' in mask, mask


def test_division_behind_a_ternary_alternate_keeps_a_write_visible(tmp):
    """A `?` that belongs to the colon being classified makes the `{` behind
    it a literal; the division behind that literal's `}` keeps a bracketed
    assignment in view, however the ternary is reached. Reading the `{` as a
    block instead blanks the assignment and the line after stays silent."""
    del tmp
    sources = (
        'switch (1) { case 1: const half = 0 ? {b:1} : {c:2}'
        ' / [send = extCmd] / 2; }\nlet after;\n',
        'const half = {a:1, case: {b:1} / [send = extCmd] / 2};\n'
        'let after;\n',
        'const half = {a:1, default: {b:1} / [send = extCmd] / 2};\n'
        'let after;\n',
    )
    for source in sources:
        mask = js_mask(source)
        assert len(mask) == len(source), mask
        assert 'send = extCmd' in mask, mask
        assert 'let after;' in mask, mask


def test_ternary_colon_before_a_decimal_consequent_stays_ternary(tmp):
    """`cond?.5:` is a conditional: `?.` is an optional chain only where no
    decimal digit follows, so the skip must read one character further.
    Reading the `?` of `?.5` as a chain answers the alternate's `{` a block,
    its `}` a regex position, and the division behind that `}` is blanked
    over the write — or swallows a quote and blanks the rest of the file."""
    del tmp
    sources = (
        'const half = 0?.5: {b:1} / [send = extCmd] / 2;\nlet after;\n',
        'const f = () => 0?.5: {b:1} / [send = extCmd] / 2;\n'
        'f();\nlet after;\n',
        'const half = 0?.5: {b:1} / "a/b" / 2;\nsend = extCmd;\n'
        'let after;\n',
    )
    for source in sources:
        mask = js_mask(source)
        assert len(mask) == len(source), mask
        assert 'send = extCmd' in mask, mask
        assert 'let after;' in mask, mask
    spaced = ('const half = 0 ? .5 : {b:1} / [send = extCmd] / 2;\n'
              'let after;\n')
    mask = js_mask(spaced)
    assert len(mask) == len(spaced), mask
    assert 'send = extCmd' in mask, mask
    assert 'let after;' in mask, mask


def test_member_case_before_a_ternary_colon_is_not_a_clause_head(tmp):
    """`x.case` between a ternary's `?` and `:` is member access, not a
    `case` clause: the `.` in front of the word refuses the clause-head
    reading. Without that refusal the colon is answered a label, the
    alternate's `{` is read a block and the write behind its `}` is
    blanked."""
    del tmp
    sources = (
        'const half = 0 ? x.case : {b:2} / [send = extCmd] / 2;\n'
        'let after;\n',
        'const half = 0 ? x.default : {b:2} / [send = extCmd] / 2;\n'
        'let after;\n',
    )
    for source in sources:
        mask = js_mask(source)
        assert len(mask) == len(source), mask
        assert 'send = extCmd' in mask, mask
        assert 'let after;' in mask, mask


def test_regex_after_a_label_survives_an_earlier_question_mark(tmp):
    """A `?` in an earlier statement — a ternary, an optional chain, a
    nullish coalescer — is not the colon's own ternary: the labelled block
    stays a block, the regex behind it is blanked and the line after stays
    visible, where a poisoned colon blanks the file from the quote on. The
    statement boundary behind the label is spelled both ways, with a `;` and
    with the ASI newline that carries none."""
    del tmp
    heads = (
        'let c = 0; let x = c ? 1 : 2;\n',
        'a?.b;\n',
        'let z = y ?? 0;\n',
        'let x2 = c ? 1 : 2\n',
        'a?.b\n',
        'let z2 = y ?? 0\n',
    )
    for head in heads:
        source = head + 'lbl: {}\n/["\']/.test(\'q\');\nlet after;\n'
        mask = js_mask(source)
        assert len(mask) == len(source), mask
        assert mask == js_mask(source.replace('/["\']/', '/    /')), mask
        assert 'let after;' in mask, mask


def test_regex_after_a_label_survives_an_earlier_literal(tmp):
    """The statement behind a label may end in the very thing the colon walk
    reads forward from — an object literal, a call argument or an array —
    and its `}`/`)`/`]` belongs to that statement, not to the label: the
    label block stays a block and the regex behind it stays a regex."""
    del tmp
    heads = (
        'let o = {a:1};\n',
        'f({a:1});\n',
        'let o = [1];\n',
        'const r = /x/;\n',
    )
    for head in heads:
        source = head + 'lbl: {}\n/["\']/.test(\'q\');\nlet after;\n'
        mask = js_mask(source)
        assert len(mask) == len(source), mask
        assert mask == js_mask(source.replace('/["\']/', '/    /')), mask
        assert 'let after;' in mask, mask


def test_division_after_a_regex_literal_reads_as_division(tmp):
    """The `/` after a closed regex literal is division however the
    literal is spelled. A regex body is blanked whole, so the closing
    slash leaves no previous token to read and js_mask has to remember
    it; reading the slash as a regex opener instead swallows the string
    behind it and the line after."""
    del tmp
    plain = 'const half = /x/ / "a/b" / y;\nlet after;\n'
    assert js_mask(plain) == 'const half = / / /       / y;\nlet after;\n'
    flagged = 'const half = /x/gi / "a/b" / y;\nlet after;\n'
    assert js_mask(flagged) == ('const half = / /   /       / y;\n'
                                'let after;\n')
    commented = 'const half = /x/ /*c*/ / "a/b" / y;\nlet after;\n'
    assert js_mask(commented) == ('const half = / /       /       / y;\n'
                                  'let after;\n')
    wrapped = 'const half = /x/\n/ "a/b" / y;\nlet after;\n'
    assert js_mask(wrapped) == 'const half = / /\n/       / y;\nlet after;\n'
    for source in (plain, flagged, commented, wrapped):
        assert 'let after;' in js_mask(source), source


def test_regex_after_a_division_slash_still_opens(tmp):
    """A division operator still reopens regex position, so the literal
    behind one is blanked rather than read as code."""
    del tmp
    source = 'const half = 4 / /["]/.test(2);\nlet after;\n'
    mask = js_mask(source)
    assert mask == js_mask('const half = 4 / /   /.test(2);\nlet after;\n')
    assert 'let after;' in mask, mask


def test_a_spaced_head_named_method_call_stays_division(tmp):
    """Whitespace around the member access does not make the keyword a
    head: the `.` is read past the space, so the division behind the call
    passes through unchanged, as it already does when the access is
    tight, optional-chained or continued on the next line."""
    del tmp
    sources = (
        'const v = obj . if (x) / "p/q" / 2;\nlet after;\n',
        'const v = obj?.if(x) / "p/q" / 2;\nlet after;\n',
        'const v = obj\n  .if(x) / "p/q" / 2;\nlet after;\n',
        'const v = obj.if(x) / "p/q" / 2;\nlet after;\n',
    )
    expected = 'const v = obj . if (x) /       / 2;\nlet after;\n'
    assert js_mask(sources[0]) == expected, js_mask(sources[0])
    for source in sources[1:]:
        mask = js_mask(source)
        assert mask == js_mask(source.replace('"p/q"', ' ' * 5)), mask
        assert 'let after;' in mask, mask


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsmask_')


if __name__ == '__main__':
    raise SystemExit(main())
