#!/usr/bin/env python3
"""The control over the sites `test_repo_layout.py` drops.

Its docstring calls the dropped set out of scope "by its own stated
boundary"; this is the control that makes that sentence true.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _launch_audit import launch_refusals  # noqa: E402
from _launch_keep import control_keeps, in_launch_population  # noqa: E402
from _repo import iter_tree_files  # noqa: E402

ROOT = _util.ROOT

# A refusal names the file and line it is about; the whole-file refusals
# carry no line and are irrelevant here, so the matcher does not match
# them. The line is parsed out of the text rather than searched for as a
# substring: ':241' also occurs inside ':2410', so a substring lets one
# site's line satisfy another site's check.
_NAMES_A_LINE = re.compile(r'^(?P<path>.*?):(?P<line>\d+)\b')


def _control_population():
    """The tracked Python files the launch control reads, as (name, text)."""
    for path in sorted(iter_tree_files(ROOT)):
        name = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding='utf-8', errors='surrogateescape')
        if in_launch_population(name, source):
            yield name, source


def _refused_lines(here, refusals):
    """How many of this file's OWN refusals name each of its lines.

    A count rather than a set, because the reach this control does NOT
    have is a statement about sites named by SEVERAL refusals, and a set
    has already thrown the multiplicity away.
    """
    lines = {}
    for refusal in refusals:
        match = _NAMES_A_LINE.match(refusal)
        if match and match.group('path') == here:
            line = int(match.group('line'))
            lines[line] = lines.get(line, 0) + 1
    return lines


def _dropped_sites():
    """`(reported, dropped, multiply-refused)` for the whole tree.

    `dropped` rows carry whether any refusal names them; the third value
    counts how many of them TWO OR MORE do, which is the reach this
    control does not have. Both are returned so a reader re-derives them
    by calling this rather than trusting a number in a docstring.

    The population and the rule are the control's own, both read from
    `tests/_launch_keep.py`, so narrowing either moves this control's
    population with it instead of quietly beside it.

    One `launch_refusals` walk yields both halves: `bound_sites` is that
    same walk with a sink, so asking for the refusals and the sites
    separately parsed every tracked module twice.
    """
    dropped = []
    reported = 0
    multiply = 0
    for here, source in _control_population():
        sink = []
        refused = _refused_lines(here, launch_refusals(source, here, sink))
        for line, head, kind in sink:
            reported += 1
            if control_keeps(head, kind):
                continue
            naming = refused.get(line, 0)
            multiply += naming > 1
            dropped.append((here, line, head, kind, bool(naming)))
    return reported, dropped, multiply


def test_every_bounded_site_the_launch_control_drops_is_a_refusal(tmp):
    """A site the keep rule drops is one the analyser refused.

    The two cannot be true of one site, and every orphan is in the one
    message, because a fix that repaired only the first would meet the
    rest by rerunning.

    What this does NOT reach: on the tree this was last measured, 150 of
    the 155 dropped sites were named by two or more refusals, so moving
    one refusal class leaves it green. `_dropped_sites` returns both, so
    re-measure by calling it rather than by editing these. The sink's own
    contents are pinned by
    `test_the_sink_pins_the_unplaced_and_ambiguous_branches` in
    `test_repo_layout.py`; this control reaches the refusal text, not the
    sink.
    """
    del tmp
    reported, dropped, _ = _dropped_sites()
    assert reported, (
        'the launch analyser reported no bounded site in the tracked tree, '
        'so the boundary below was not checked at all')
    assert dropped, (
        "no bounded site fell outside the launch control's keep rule, so the "
        'boundary its docstring states is not exercised by this tree')
    orphans = [site for site in dropped if not site[4]]
    assert not orphans, (
        f'{len(orphans)} of the {len(dropped)} bounded sites the launch '
        'control drops are named by no refusal:\n'
        + '\n'.join(f'  {here}:{line} a {kind} bound site at a {head} head'
                    for here, line, head, kind, _ in orphans))


def test_a_non_python_tracked_path_is_outside_the_population(tmp):
    """The `.py` half of the population, on a name that is not one.

    `iter_tree_files` hands back every tracked path, 87 of which are not
    Python, and `bound_sites` parses whatever it is given — so a Markdown
    file reaching it raises `SyntaxError` inside this control rather than
    failing a check. Every fixture in this suite and in the control's own
    is named `probe.py`, so nothing else in either file would notice the
    `.py` half going away.
    """
    del tmp
    assert any(path.suffix != '.py' for path in iter_tree_files(ROOT)), (
        'the tree holds no tracked non-Python path, so this fixture can no '
        "longer tell `in_launch_population` from `lambda: True`")
    assert not in_launch_population('notes.md', '# timeout\n'), (
        'a tracked Markdown file is inside the launch control\'s population, '
        'and the control parses what it is given: the `.py` half of '
        '`in_launch_population` is unpinned, and the first non-Python file '
        'this tree grows raises SyntaxError inside it')


def test_a_refusal_line_is_parsed_and_not_matched_as_a_substring(tmp):
    """The one matcher property the tree cannot tell the two forms apart on.

    A sweep of every dropped site in this tree found no site where a
    substring match on `':{line}'` differs from the parse, because every
    such refusal already names a real site. The tree is therefore blind to
    a regression to the forbidden form, and this is the fixture the tree
    cannot supply: one refusal naming a line, and a site three lines
    earlier.
    """
    del tmp
    refused = _refused_lines('probe.py', ['probe.py:2410 carries a timeout='])
    assert 2410 in refused, refused
    assert 241 not in refused, (
        f'a substring match on ":241" read {refused}, which is the form the '
        'comment above forbids and the tree cannot detect on its own')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
