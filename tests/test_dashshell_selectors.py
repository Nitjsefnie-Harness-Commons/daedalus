#!/usr/bin/env python3
"""What the dashboard shell's selector parser refuses, and what it answers.

The parser is the shell's one fail-closed surface: a selector it cannot
read must be refused by name, because a selector that silently matches
nothing is a scaffold that answers a question it did not understand.
Each refusal below is reached by one selector and pinned by the phrase
the parser refuses with, so a branch with no selector here is a branch
no control can delete.

The controls live in their own file because the suite they came from
passed the 700-line ceiling with them in it, and this repository's
remedy for a file over the ceiling is relocation rather than an entry
in the size baseline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _dashshell import run_scenario  # noqa: E402


# One selector per refusal the parser can name, and the phrase it names
# it by. A branch with no entry here is a branch no control can delete.
# `blankName` and `valuelessName` share a phrase -- the parser refuses
# both a blank attribute body and an empty name before `=` the same way
# -- so two selectors are what tells the two branches apart. The blank
# one needs a tab: `compile` splits on spaces first, so `'[ ]'` is two
# parts and never reaches the branch that refuses a blank body.
_SELECTOR_REFUSALS = {
    'blankName': 'empty attribute name',
    'valuelessName': 'empty attribute name',
    'unclosed': 'unclosed attribute',
    'emptyPart': 'empty selector part',
    'emptyName': 'empty name after',
}


_SELECTORS = r"""
(async () => {
const empty = document.querySelectorAll('[data-meta="nothing-here"]');
const token = document.createElement('span');
token.setAttribute('data-meta', 'token');
document.body.appendChild(token);
const doubleQuoted = document.querySelectorAll('[data-meta="token"]').length;
const singleQuoted = document.querySelectorAll("[data-meta='token']").length;
let combinator = null;
try {
  document.querySelectorAll('.rail-list > li');
} catch (error) { combinator = error.message; }
let unquoted = null;
try {
  document.querySelectorAll('[data-meta="token]');
} catch (error) { unquoted = error.message; }
let notAString = null;
try {
  document.querySelectorAll(null);
} catch (error) { notAString = error.message; }
const refused = {};
for (const [name, selector] of Object.entries({
  blankName: '[\t]',
  valuelessName: '[=x]',
  unclosed: '[data-meta',
  emptyPart: 'div  a',
  emptyName: '#',
})) {
  let why = null;
  try { document.querySelectorAll(selector); }
  catch (error) { why = error.message; }
  refused[name] = why;
}
report({ empty: empty.length, isArray: Array.isArray(empty),
  doubleQuoted, singleQuoted, combinator, unquoted, notAString, refused });
})().catch(leave);
"""


def test_an_unimplemented_selector_fails_by_name(_tmp):
    """A selector the shell does not implement raises, naming it; one
    that matches nothing returns an empty list. A value it cannot read
    is refused too: a single-quoted value read as part of the value
    matches nothing, which is the failure this is about. Permissive
    parsing leaves `combinator` and `unquoted` null."""
    report = run_scenario(_SELECTORS)
    assert report['empty'] == 0, report
    assert report['isArray'] is False, report
    assert report['doubleQuoted'] == 1, report
    assert report['singleQuoted'] == 1, report
    assert report['combinator'] is not None, report
    assert 'does not implement selector' in report['combinator'], report
    assert '.rail-list > li' in report['combinator'], report
    assert report['unquoted'] is not None, report
    assert 'unreadable attribute value' in report['unquoted'], report
    assert report['notAString'] is not None, report
    assert 'is not a selector' in report['notAString'], report
    # The combinator is the one selector that reaches `unknown syntax`,
    # so its refusal is pinned by the phrase as well as the selector.
    assert 'unknown syntax' in report['combinator'], report
    for name, why in sorted(_SELECTOR_REFUSALS.items()):
        message = report['refused'][name]
        assert message is not None, (name, report)
        assert why in message, (name, message)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashselect_')


if __name__ == '__main__':
    raise SystemExit(main())
