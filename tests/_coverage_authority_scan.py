"""Count where one phrase is stated across `tests/*.py`.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/test_coverage_bindings.py, whose opened-set control
carried the search keys, the squeeze and the holder count in a file that
was six lines under the tests ceiling, so a concurrent branch's unrelated
row would have been what decided the size gate. The control stays in that
suite under the test name the rule is recorded by; the mechanism it calls
came here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import ROOT  # noqa: E402

# Holding the keys here is what lets the scan run, and it is not a
# second statement of the rule: this file counts, it does not claim.
_AUTHORITY_HALF = 'whose `attr` is in `_LAUNCH_READS`'
_AUTHORITY_REST = 'attribute outside that set is a'
# The one phrase the control asserts no scanned file carries. It is a
# universal the module does not state; the authority the scan protects is
# `_carried_parts`, and every site refers there rather than restating it.
_STALE_UNIVERSAL = 'every other attribute is a constant read'
_AUTHORITY_FILE = '_coverage_bindings.py'

_OWN_FILE = Path(__file__).name


def phrase_holders(phrase, excluded=()):
    """(the files stating `phrase`, how many times between them).

    Occurrences and not files, because a second copy in the same module
    is the likeliest one of all and a file count cannot see it.

    This module and every name `excluded` carries are skipped: this one
    holds the search keys, and neither it nor a checker is a second
    authority. The skip is by name, so a file renamed out from under a
    caller's exclusion is scanned again rather than silently dropped.
    """
    def squeezed(text):
        """The text with a rewrap's marks gone, so wrapping cannot hide it."""
        return text.replace('\n', '').replace(' ', '').replace(
            '\t', '').replace('#', '')

    key = squeezed(phrase)
    skip = {_OWN_FILE, *excluded}
    stated, total = [], 0
    for path in sorted((ROOT / 'tests').glob('*.py')):
        if path.name in skip:
            continue
        count = squeezed(path.read_text(encoding='utf-8')).count(key)
        if count:
            stated.append(path.name)
        total += count
    return stated, total
