"""Count where one phrase is stated across `tests/*.py`.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/test_coverage_bindings.py, whose opened-set control
carried the search keys, the squeeze and the holder count in a file that
was six lines under the tests ceiling, so a concurrent branch's unrelated
row would have been what decided the size gate. The control stays in that
suite under the test name the rule is recorded by; the mechanism it calls
came here. The exemption that lets this file hold the keys without
claiming them is stated with the function below, not here.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import ROOT  # noqa: E402

# Holding the keys here is what lets the scan run, and it is not a
# second statement of the rule: this file counts, it does not claim.
# What keeps that true is the exemption below, which spares the
# definition of a key and nothing else in the file.
_AUTHORITY_HALF = 'whose `attr` is in `_LAUNCH_READS`'
_AUTHORITY_REST = 'attribute outside that set is a'
# The one phrase the control asserts no scanned file carries. It is a
# universal the module does not state; the authority the scan protects is
# `_carried_parts`, and every site refers there rather than restating it.
# The assignment is this phrase's own definition, so it is exempt too.
_STALE_UNIVERSAL = 'every other attribute is a constant read'
_AUTHORITY_FILE = '_coverage_bindings.py'


def _defines(text, squeezed, key):
    """True when a plain assignment binds the phrase to a name.

    One definition, and only that shape: a single target that is a
    `Name`, and a value that is a string constant. A concatenation or an
    f-string is not a definition, so it stays counted — the exemption is
    a key naming itself, not a way of writing a key.
    """
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = node.value
        if (len(targets) == 1 and isinstance(targets[0], ast.Name)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and squeezed(value.value) == key):
            return True
    return False


def phrase_holders(phrase, directory=None):
    """(the files stating `phrase`, how many times between them).

    Occurrences and not files, because a second copy in the same module
    is the likeliest one of all and a file count cannot see it.

    Every file in the directory is read and none is excluded by name,
    this module among them, so a second statement here is counted like a
    second statement anywhere else. The one exemption is the key's own
    definition, where it is defined rather than which file holds it, and
    only one is ever exempted: a second definition site stays counted,
    so the exemption cannot become the blind spot it replaced.
    """
    def squeezed(text):
        """The text with a rewrap's marks gone, so wrapping cannot hide it."""
        return text.replace('\n', '').replace(' ', '').replace(
            '\t', '').replace('#', '')

    key = squeezed(phrase)
    stated, total = [], 0
    for path in sorted((directory or ROOT / 'tests').glob('*.py')):
        text = path.read_text(encoding='utf-8')
        count = squeezed(text).count(key)
        if count and _defines(text, squeezed, key):
            count -= 1
        if count:
            stated.append(path.name)
        total += count
    return stated, total
