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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsmask_')


if __name__ == '__main__':
    raise SystemExit(main())
