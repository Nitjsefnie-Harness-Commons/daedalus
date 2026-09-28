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
# What keeps that true is the exemption below, which spares a key this
# module declares at its top level and nothing else in the file.
_AUTHORITY_HALF = 'whose `attr` is in `_LAUNCH_READS`'
_AUTHORITY_REST = 'attribute outside that set is a'
# The one phrase the control asserts no scanned file carries. It is a
# universal the module does not state; the authority the scan protects is
# `_carried_parts`, and every site refers there rather than restating it.
# The assignment is this phrase's own definition, so it is exempt too.
_STALE_UNIVERSAL = 'every other attribute is a constant read'
_AUTHORITY_FILE = '_coverage_bindings.py'
# The only module whose top-level declaration of a key is a definition
# rather than a statement. The file itself is read whole, like any other.
_KEY_HOLDER = Path(__file__).name


def _defines(text, squeezed, key):
    """True when this module's top level declares the phrase as a key.

    One shape, and only that one: a statement in the module body whose
    single target is a `Name` and whose value is a string `Constant`.
    An explicit `+` is a `BinOp` and an f-string a `JoinedStr`, so
    neither is a definition. Adjacent literals are a case the parser
    settles before this sees them — it folds them into one `Constant` —
    so they are a definition, and the suite pins that rather than
    leaving it to the reader.

    The module body and not the whole tree, because a nested assignment
    sits inside some other scope and is a statement wherever it is
    written, and an `if`-guarded one is guarded on something.
    """
    for node in ast.parse(text).body:
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
    second statement anywhere else. The one exemption is this module's
    own declaration of a key at its top level, and it is reached only
    there: the same assignment in any other file, under any name, at any
    depth, is a statement like any other. One is ever exempted, so a
    second declaration beside the first is still a second statement.
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
        if count and path.name == _KEY_HOLDER:
            if _defines(text, squeezed, key):
                count -= 1
        if count:
            stated.append(path.name)
        total += count
    return stated, total
