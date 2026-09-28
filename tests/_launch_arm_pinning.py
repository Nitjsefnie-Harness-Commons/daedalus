"""How an arm's recorded address is read out of the source it names.

`tests/test_launch_arms.py` holds the checks; this holds the three
readings of a source line they are written in, and the derivation of
the arms those readings cannot tell apart. It is a module of its own
because that suite is at its size ceiling.

    collapsed    one source line with its runs of whitespace folded
    anchor       the arm's own text, less the ellipsis a truncated
                 anchor ends on
    from_line    the collapsed source from a line on, for at least
                 `width` characters
    unpinnable   the arms `anchor` and `from_line` leave ambiguous, and
                 why each one is

An anchor is a PREFIX of the arm's block, so the table costs one line
per arm instead of three, and the prefix is what has to still be there
for the arm to be where the table says it is. Where that prefix reads
the same at two places, the text cannot say which arm is meant, and
`unpinnable` is the answer to that: such an arm is settled by cutting
it, not by reading it.
"""
from pathlib import Path

from _launch_arm_records import ANCHOR, FILE, ID, LINE
from _launch_arms import LAUNCH_ARMS

TESTS = Path(__file__).resolve().parent


def collapsed(text):
    return ' '.join(text.split())


def anchor(arm):
    return collapsed(arm[ANCHOR]).removesuffix('...').rstrip()


def from_line(rows, line, width):
    """The collapsed source from `line` on, for at least `width` chars.

    An anchor is not always inside the clause the sweep cuts: a
    `drop_stmt` arm's runs on into the next statement, a `boolop` arm's
    out to the end of the disjunction. So the span is the anchor's own
    length -- the check is that the line is where the text BEGINS.
    """
    taken, length = [], 0
    for row in rows[line - 1:]:
        taken.append(row)
        length += len(row) + 1
        if length >= width:
            break
    return ' '.join(taken)


def _collapsed_rows(name):
    return [collapsed(row)
            for row in (TESTS / name).read_text(encoding='utf-8').splitlines()]


def unpinnable():
    """`[(arm, why)]` for the arms the source text cannot address.

    Three ways a foreign clause can take an arm's address while every
    text check stays green, and all three are derived from the source
    rather than recorded, so an edit that makes one is visible here:
    an anchor another arm carries too, so the two can exchange `line`
    and `cut`; a line another arm sits on, so `cut` is all that is left
    to tell them apart; and an anchor `from_line` accepts at more than
    one line, so a re-point at the other one satisfies the line check.
    """
    rows = {name: _collapsed_rows(name)
            for name in {arm[FILE] for arm in LAUNCH_ARMS}}
    by_text, by_line = {}, {}
    for arm in LAUNCH_ARMS:
        by_text.setdefault((arm[FILE], anchor(arm)), []).append(arm[ID])
        by_line.setdefault((arm[FILE], arm[LINE]), []).append(arm[ID])
    out = []
    for arm in LAUNCH_ARMS:
        text, line = anchor(arm), arm[LINE]
        twins = [n for n in by_text[(arm[FILE], text)] if n != arm[ID]]
        mates = [n for n in by_line[(arm[FILE], line)] if n != arm[ID]]
        also = [n for n, _ in enumerate(rows[arm[FILE]], 1)
                if n != line and from_line(
                    rows[arm[FILE]], n, len(text)).startswith(text)]
        parts = ([f'anchor shared with {twins}'] if twins else []
                 + [f'line shared with {mates}'] if mates else []
                 + [f'anchor also accepted at {also}'] if also else [])
        if parts:
            out.append((arm, ', '.join(parts)))
    return out
