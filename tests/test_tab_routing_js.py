#!/usr/bin/env python3
"""Sender-alias boundaries for the JavaScript tab-routing scanner."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402


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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='jsrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
