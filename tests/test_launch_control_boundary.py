#!/usr/bin/env python3
"""The boundary `test_repo_layout.py` states about the sites it drops.

Every other reported launch — a non-git or unreadable head carrying a
bound — is dropped by `_bound_sites`, and the control's own docstring
calls the dropped set out of scope "by its own stated boundary". This is
the control that makes that sentence true.
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
    """The line numbers this file's OWN refusals name, as a set."""
    lines = set()
    for refusal in refusals:
        match = _NAMES_A_LINE.match(refusal)
        if match and match.group('path') == here:
            lines.add(int(match.group('line')))
    return lines


def _dropped_sites():
    """Every site the control's keep rule drops, and whether it is refused.

    The population is the control's own, because the boundary checked here
    is that control's and not the analyser's, and the prefilter and the
    keep rule are both read from `tests/_launch_keep.py` rather than
    restated — the control reads the same two, so narrowing either moves
    this control's population with it instead of quietly beside it.

    One `launch_refusals` walk yields both halves: `bound_sites` is that
    same walk with a sink, so asking for the refusals and the sites
    separately parsed every tracked module twice.
    """
    dropped = []
    reported = 0
    for here, source in _control_population():
        sink = []
        refused = _refused_lines(here, launch_refusals(source, here, sink))
        for line, head, kind in sink:
            reported += 1
            if control_keeps(head, kind):
                continue
            dropped.append((here, line, head, kind, line in refused))
    return reported, dropped


def test_every_bounded_site_the_launch_control_drops_is_a_refusal(tmp):
    """A site the keep rule drops is one the analyser refused.

    The two cannot be true of one site: a launch read as a non-git or
    unreadable head is dropped because it cannot be a git launch, and the
    analyser said so in a refusal. Every orphan is in the one message,
    because a fix that repaired only the first would meet the rest by
    rerunning.

    What this does NOT reach: 135 of the 139 dropped sites are named by
    two or more refusals, so moving one refusal class leaves it green. The
    sink's own contents are pinned in `test_repo_layout.py`, and this
    control reaches the refusal text, not the sink.
    """
    del tmp
    reported, dropped = _dropped_sites()
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


def test_a_refusal_line_is_parsed_and_not_matched_as_a_substring(tmp):
    """The one matcher property the tree cannot tell the two forms apart on.

    A sweep of every dropped site in this tree found no site where a
    substring match on `':{line}'` differs from the parse, because every
    such refusal already names a real site. The tree is therefore blind to
    a regression to the forbidden form, and this is the fixture the tree
    cannot supply: one refusal naming a line, and a site three lines
    earlier, which the parse separates and the substring does not.
    """
    del tmp
    refused = _refused_lines('probe.py', ['probe.py:2410 carries a timeout='])
    assert 2410 in refused, refused
    assert 241 not in refused, (
        f'a substring match on ":241" read {refused}, which is the form the '
        'comment above forbids and the tree cannot detect on its own')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
