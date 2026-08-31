#!/usr/bin/env python3
"""Sender-alias boundaries for the JavaScript tab-routing scanner."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402


_RUNTIME_PREFIX = """const calls = [];
const extCmd = (...args) => calls.push(args);
const ordinary = () => undefined;
const chromeTab = 41;
"""


def _runtime_and_guard(source, path):
    script = (_RUNTIME_PREFIX + source
              + "\nconst routed = calls.some(args => args.some(\n"
              + "  value => value && value.tab === chromeTab));\n"
              + "process.stdout.write(routed ? '1' : '0');\n")
    path.write_text(script, encoding='utf-8')
    node = shutil.which('node')
    assert node, 'node is required to execute JavaScript routing controls'
    ran = subprocess.run([node, str(path)], capture_output=True, text=True,
                         timeout=30)
    assert ran.returncode == 0, (ran.returncode, ran.stdout, ran.stderr)
    guard = bool(js_tab_routing_violations(path, path.name))
    return ran.stdout == '1', guard


def test_sender_aliases_follow_source_order(tmp):
    cases = [
        ('direct', "async function go(chromeTab) {\n"
         "  return await extCmd('focus-tab', { tab: chromeTab });\n}\n", 1),
        ('alias', "async function go(chromeTab) {\n"
         "  const send = extCmd;\n"
         "  return await send('focus-tab', { tab: chromeTab });\n}\n", 1),
        ('transitive', "const s1 = extCmd;\nconst s2 = s1;\n"
         "s2('focus-tab', { tab: chromeTab });\n", 1),
        ('rebound', "let send = extCmd;\nsend = ordinary;\n"
         "send('focus-tab', { tab: chromeTab });\n", 0),
        ('unknown', "const send = maybeSend;\n"
         "send('focus-tab', { tab: chromeTab });\n", 0),
        ('nested-block', "const send = extCmd;\nif (chromeTab) {\n"
         "  function nested() {\n"
         "    return send('focus-tab', { tab: chromeTab });\n  }\n"
         "  nested();\n}\n", 1),
        ('unprovable', "const send = choose ? extCmd : ordinary;\n"
         "send('focus-tab', { tab: chromeTab });\n", 1),
        ('run-command-alias', "const send = runCommand;\n"
         "send({ type: 'focus-tab', tab: chromeTab });\n", 1),
        ('run-command-unprovable',
         "const send = choose ? runCommand : ordinary;\n"
         "send({ type: 'focus-tab', tab: chromeTab });\n", 1),
    ]
    source = Path(tmp) / 'sender.js'
    observed = []
    for label, text, _ in cases:
        source.write_text(text, encoding='utf-8')
        observed.append((label, len(js_tab_routing_violations(
            source, source.name))))
    expected = [(label, count) for label, _, count in cases]
    assert observed == expected, observed


def test_sender_alias_scopes_match_runtime(tmp):
    cases = [
        ('parameter-shadow', "const send = extCmd;\n"
         "function inner(send) {\n"
         "  send('focus-tab', { tab: chromeTab });\n}\n"
         "inner(ordinary);\n", False),
        ('arrow-shadow', "const send = extCmd;\n"
         "const inner = send => {\n"
         "  send('focus-tab', { tab: chromeTab });\n};\n"
         "inner(ordinary);\n", False),
        ('inner-local', "const send = extCmd;\n"
         "function inner() {\n  const send = ordinary;\n}\n"
         "inner();\nsend('focus-tab', { tab: chromeTab });\n", True),
        ('invoked-promotion', "let send = ordinary;\n"
         "function promote() { send = extCmd; }\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('invoked-demotion', "let send = extCmd;\n"
         "function demote() { send = ordinary; }\ndemote();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('uninvoked-promotion', "let send = ordinary;\n"
         "function promote() { send = extCmd; }\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('uninvoked-demotion', "let send = extCmd;\n"
         "function demote() { send = ordinary; }\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('same-name-callable-shadow', "let promote = ordinary;\n"
         "let send = ordinary;\n{ const promote = () => {\n"
         "  send = extCmd;\n}; }\npromote();\n"
         "send('focus-tab', { tab: chromeTab });\n", False),
        ('same-name-helper-shadow',
         "const fields = () => ({ tab: chromeTab });\n"
         "{ function fields() { return {}; } }\n"
         "extCmd('focus-tab', fields());\n", True),
        ('conditional', "let send = extCmd;\nconst flag = false;\n"
         "if (flag) send = ordinary;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('loop', "let send = extCmd;\nconst items = [];\n"
         "for (const item of items) send = ordinary;\n"
         "send('focus-tab', { tab: chromeTab });\n", True),
        ('member-call', "const send = extCmd;\n"
         "const box = { send: ordinary };\n"
         "box.send('focus-tab', { tab: chromeTab });\n", False),
        ('method-declaration', "const send = extCmd;\n"
         "const box = { send(type, {tab}) {} };\nvoid box;\n", False),
        ('sibling-shadow', "function first() { const send = extCmd; }\n"
         "function second(send) {\n"
         "  send('focus-tab', { tab: chromeTab });\n}\n"
         "first();\nsecond(ordinary);\n", False),
        ('inherited-alias', "const send = extCmd;\nfunction inner() {\n"
         "  send('focus-tab', { tab: chromeTab });\n}\ninner();\n", True),
        ('block-shadow', "const send = extCmd;\n{\n"
         "  const send = ordinary;\n"
         "  send('focus-tab', { tab: chromeTab });\n}\n", False),
    ]
    path = Path(tmp) / 'scope.js'
    observed = [(label, *_runtime_and_guard(source, path))
                for label, source, _ in cases]
    expected = [(label, value, value) for label, _, value in cases]
    assert observed == expected, observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
