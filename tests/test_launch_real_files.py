#!/usr/bin/env python3
"""What the census emits from the three REAL files issue #1299 names.

`tests/test_launch_census_receivers.py` holds the four SHAPES, every one
of them planted. This holds the same census read over the three shipped
files those shapes live in, because a planted fixture cannot see a shape
the file happens to spell differently -- and one of them did:
`test_real_browser_harness.py:131` is a `subprocess.run` double that
neither `DISCHARGED` nor `CHILD_BOUNDS` named, so the set of rows was
open at both ends until the completeness control below closed it.

Nothing here runs the shipped tree for its verdict. Those three files are
not on the launch path on main, so the census reports nothing at all for
them and a control that read only that proves nothing; the real-file
controls force the path instead, which is how a surviving row goes live
when `_realbrowser.py` reaches `_noderun.py`.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent


def _every_function(tree):
    """The forced `in_path` every real-file control reads the tree through."""
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    return frozenset(node.name for node in ast.walk(tree)
                     if isinstance(node, functions))


def _real_rows(relative):
    """The census reading a REAL file with every function forced in path.

    The only way to reach these three files today: the path is forced, so
    a row here is a row the census will emit the day the real-browser
    harness joins the path.

    Forcing can only OVER-report, never under-report. It puts every
    function in the file into `in_path`, which widens the callee set the
    reachability arm matches a bare name against and adds an enclosing
    scope per function, so a row may appear that the real derivation
    would not produce — and no row the real derivation produces is
    missing. So a DISCHARGED row here is proof the rule is right about
    those lines, and a KEPT row is weaker evidence than it looks, being a
    bound under a reading strictly wider than the shipped one. Measured
    on all three files at `6b055a10`, the forced row set equals the union
    of the per-function solo derivations, so nothing is overstated today;
    when issue 1121's branch lands the forcing becomes redundant and this
    should read the real `census()` instead.
    """
    tree = ast.parse((TESTS / relative).read_text(encoding='utf-8'))
    return census._faults(relative, tree, _every_function(tree))


def _line_holding(relative, snippet, occurrence=1):
    """The line number of a snippet in a real file, or a loud failure.

    Addressed by TEXT rather than by a number, because the number moves
    with every edit above it and a control that reds on an unrelated
    addition teaches the reader to ignore it. A snippet that is GONE is
    still a failure: the site was deleted or reworded, and the claim it
    carried has to be re-decided rather than quietly dropped.
    """
    lines = (TESTS / relative).read_text(encoding='utf-8').splitlines()
    found = [number for number, text in enumerate(lines, 1)
             if snippet in text]
    if len(found) < occurrence:
        raise AssertionError((relative, snippet, occurrence, found))
    return found[occurrence - 1]


# The four shapes, in the files they live in. `urlopen` at 567 and 574
# are the same line of text, so the second carries its occurrence.
DISCHARGED = (
    ('test_bridge_startup.py', 'def join(self, timeout=None):', 1),
    ('test_bridge_startup.py', 'def refusing_await(', 1),
    ('test_parent_watch.py', 'def _wait_for_exit(proc, info=None,', 1),
    ('test_bridge_startup.py', 'urllib.request.urlopen(request, timeout=10)',
     1),
    ('test_real_browser_harness.py',
     'urllib.request.urlopen(page_url, timeout=2)', 1),
    ('test_real_browser_harness.py',
     'urllib.request.urlopen(page_url, timeout=2)', 2),
    ('test_real_browser_harness.py', 'def timed_out(', 1),
    ('test_real_browser_harness.py', 'def outer_timeout(', 1),
    ('test_real_browser_harness.py', 'def websocket_failed(', 1),
)

# The genuine child bounds, with the route and the reason each carries
# today. `tests/test_real_browser_harness.py` also has a `thread.join`
# site, which no rule may discharge: it is the shape the previous
# narrowing of this rule missed, in a real file.
WRITTEN = 'the bound is written at the call site, not named'
UNCHAINED = ('the constant WAIT_TIMEOUT is not computed from a named '
             'chain, so the figure behind it is written rather than '
             'composed')
CHILD_BOUNDS = (
    ('test_bridge_startup.py', 'proc.wait(timeout=10)', 1, WRITTEN),
    ('test_bridge_startup.py', 'await_listening_line(proc,', 1, WRITTEN),
    ('test_bridge_startup.py', 'proc, drained, timeout=1)', 1, WRITTEN),
    ('test_bridge_startup.py', 'proc.wait(timeout=10)', 2, WRITTEN),
    ('test_bridge_startup.py', 'timeout=_util.COLD_START_TIMEOUT', 1,
     WRITTEN),
    ('test_parent_watch.py', 'proc.communicate(timeout=WAIT_TIMEOUT)', 1,
     UNCHAINED),
    ('test_parent_watch.py', 'proc.communicate(timeout=WAIT_TIMEOUT)', 2,
     UNCHAINED),
    ('test_parent_watch.py', 'proc.wait(timeout=10)', 1, WRITTEN),
    ('test_parent_watch.py', '_wait_for_exit(process, info, timeout=0)', 1,
     WRITTEN),
    ('test_real_browser_harness.py', 'process.wait(timeout=10) == 0', 1,
     WRITTEN),
    ('test_real_browser_harness.py', 'process.wait(timeout=10)', 1, WRITTEN),
    ('test_real_browser_harness.py', 'text=True, timeout=10)', 1, WRITTEN),
    ('test_real_browser_harness.py', 'text=True, timeout=10)', 2, WRITTEN),
    ('test_real_browser_harness.py', 'thread.join(timeout=2 *', 1, WRITTEN),
)

# The one row these three files carry that no table above accounts for, and
# the only site the narrowing does not recover. `_successful_run_recorder`'s
# inner `run` hands the deadline to `recorded`, a PARAMETER, and no writing
# in the module proves what a caller passes it -- the arm discharges only on
# a proof, so the signature is refused. `origin/main` refuses this site too,
# so the census returns it to main's verdict rather than improving on it,
# which is what makes it an over-refusal and not a regression. #1337 carries
# the diagnosis.
NAMED_OVER_REFUSALS = (
    ('test_real_browser_harness.py', 'def run(args, *, cwd, ', 1),)

# How many rows each real file carries, measured with `_real_rows`. The two
# tables above say what every row MEANS and neither can say there is no row
# beyond them, which is the whole of the control below.
REAL_ROW_TOTALS = {'test_bridge_startup.py': 7, 'test_parent_watch.py': 7,
                   'test_real_browser_harness.py': 8}


def test_the_real_files_emit_exactly_the_rows_this_suite_names(tmp):
    """The set of rows is CLOSED, not merely the nine that carry none.

    Delete the narrowing and all nine of the `DISCHARGED` sites carry a
    row again -- 16 rows across the nine, measured with both arms stubbed
    off. But that table says nine lines carry no row and `CHILD_BOUNDS`
    says fourteen carry one, and a row on a FIFTEENTH line of these
    files is invisible to both -- which is what
    `test_real_browser_harness.py:131` was, until `NAMED_OVER_REFUSALS`
    named it. So the set is closed in both directions: no row sits at a
    line no table resolves, and each file carries the measured count, so
    an EXTRA row at a known line is red here as well as a row on an
    unknown one.
    """
    del tmp
    for relative, snippet, occurrence in DISCHARGED:
        line = _line_holding(relative, snippet, occurrence)
        rows = [row for row in _real_rows(relative) if row[1] == line]
        assert not rows, (relative, line, snippet, rows)
    for relative, total in REAL_ROW_TOTALS.items():
        rows = _real_rows(relative)
        known = {_line_holding(relative, *row[1:3])
                 for table in (CHILD_BOUNDS, NAMED_OVER_REFUSALS)
                 for row in table if row[0] == relative}
        stray = [(row[1], row[2]) for row in rows if row[1] not in known]
        assert not stray, (relative, 'a row no table accounts for', stray)
        assert len(rows) == total, (relative, total,
                                    [(row[1], row[2]) for row in rows])


def test_a_named_over_refusal_refuses(tmp):
    """The one site the narrowing does not recover: #1337.

    `origin/main` refuses this site too, so the census returns it to
    main's verdict rather than improving on it -- which is what makes it
    an over-refusal and not a regression. Note `ast.arg` uses
    `setdefault`, so a parameter does not veto a key a literal already
    proved; here nothing proved `recorded`, so there was nothing for the
    exemption to preserve.

    Pinned as a REFUSAL so an edit that starts discharging it is
    visible. #1337 carries the diagnosis; this is the site row.
    """
    del tmp
    for relative, snippet, occurrence in NAMED_OVER_REFUSALS:
        line = _line_holding(relative, snippet, occurrence)
        routes = [row[2] for row in _real_rows(relative) if row[1] == line]
        assert routes == ['timeout parameter'], (
            relative, line, 'the named over-refusal started discharging',
            routes)


def test_the_real_files_keep_every_genuine_child_bound(tmp):
    """The negative space holds: fourteen sites, byte for byte.

    Twelve of the table in the issue are here; its last two rows name a
    `test_real_browser_harness.py` that has no
    `communicate(timeout=WAIT_TIMEOUT)` at 487 or 490 — those lines are
    `test_parent_watch.py`'s, and are in the list twice over — and the
    `thread.join` site that table omits is here in their place, because
    it is the shape a "not a subprocess" narrowing would discharge.
    """
    del tmp
    for relative, snippet, occurrence, reason in CHILD_BOUNDS:
        line = _line_holding(relative, snippet, occurrence)
        rows = sorted(row for row in _real_rows(relative) if row[1] == line)
        assert rows, (relative, line, snippet, 'the bound is no longer read')
        assert all(row[3] == reason for row in rows), (relative, line, rows)
        assert {row[2] for row in rows} <= {
            'timeout= keyword',
            'positional timeout on a launched child'}, (relative, line, rows)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchrealfiles_')


if __name__ == '__main__':
    raise SystemExit(main())
