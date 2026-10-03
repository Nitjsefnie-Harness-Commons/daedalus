#!/usr/bin/env python3
"""The two test-tree helpers left with no suite of their own.

`tests/_sweep_launch_scan.py` stays after this cut — its own deletion kept
surfacing refusals nothing else pinned — so nothing here covers the sweep
scan. What is left is `_coverage_memo`'s materialised node list, the key its
`analysed` memo is held under, and the shared source reader. Every kept
suite that imports any of them exercises a rule ABOUT the guard, never
about the helper, so these are pinned here once.

It is a separate file rather than a fold into a neighbour because there is
nowhere to fold them: their kept importers, the
`tests/test_coverage_environment.py` and `tests/test_unresolved_routes.py`
suites, are at or within a few lines of the 700-line tests ceiling, and
the new suite has room under it.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _coverage_memo  # noqa: E402
import _util  # noqa: E402
from _coverage_source_fixtures import _normalized_source  # noqa: E402


def test_the_analysed_tree_is_its_own_first_node(tmp):
    """`nodes(tree)` includes the root, so a module-level binding is read.

    The materialised list is `[tree] + below`, and every guard that walks
    through it judges module-level bindings on the strength of the root
    being there. A list built from `ast.walk(tree)[1:]` matches a fresh
    walk everywhere below the root and silently stops judging the whole
    module, which is a guard that goes quiet rather than one that goes red.
    """
    del tmp
    tree = ast.parse('import os\n\nSUBJECT = "a value"\n')
    assert _coverage_memo.nodes(tree) == list(ast.walk(tree))


def test_a_crlf_module_reads_back_normalised(tmp):
    """The reader's CRLF arm, over bytes on disk rather than in memory.

    A checkout can carry CRLF, and a guard that reads a copied test module
    through this reader counts lines to place a diagnostic. Reading the
    bytes and decoding without the replacement leaves the carriage return
    in the text, so every line number derived from it after the first is
    off by one against the offsets the file on disk reports. It has to be
    real bytes: `read_text` applies universal newlines, so an in-memory
    string or a text-mode read cannot tell this reader from a correct one.
    """
    target = Path(tmp) / 'crlf.py'
    target.write_bytes(b'import os\r\nSUBJECT = "a value"\r\n')
    assert b'\r\n' in target.read_bytes()
    assert _normalized_source(target) == 'import os\nSUBJECT = "a value"\n'


def test_the_memo_key_carries_the_analyser_as_well_as_the_file(tmp):
    """`analysed` serves a caller that swaps the guard that guard's verdict.

    The key is the analyser, the path and the content together, so a second
    guard over the same file is analysed in its own right rather than
    handed the first one's answer. The order below is the one that matters:
    the clean guard runs first and the refusing one second, so dropping the
    analyser from the key hands the second an empty list and it PASSES
    where the base refuses it — a bound site nobody reports, rather than a
    false red a reader would have noticed.

    Nothing in the kept tree calls two analysers over one source, so the
    second guard is the only way this is observable at all.
    """
    del tmp
    relative, source = 'tests/synthetic.py', 'import os\n'
    ran = []

    def clean(_relative, _source, keeps):
        del keeps
        ran.append('clean')
        return []

    def refusing(_relative, _source, keeps):
        del keeps
        ran.append('refusing')
        return [f'{relative}:1 is refused']

    assert _coverage_memo.analysed(clean, relative, source, []) == []
    assert _coverage_memo.analysed(refusing, relative, source, []) == [
        f'{relative}:1 is refused']
    assert ran == ['clean', 'refusing'], ran


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
