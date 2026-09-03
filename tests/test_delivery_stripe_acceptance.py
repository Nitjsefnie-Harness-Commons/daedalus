#!/usr/bin/env python3
"""The delivery-write stripe acceptance is recorded where the stripes live.

A caller can read stripe membership off the timing of its own delivery
writes, and the tree accepts that rather than implying a privacy guarantee
nothing enforces. The acceptance is therefore stated twice -- beside the
stripe table, and on the delivery-write path in the architecture map -- and
both statements are pinned here, because an acceptance nothing pins is a
comment. The bounded lock table the acceptance trades on is pinned to the
target names a caller produces.
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402

ROOT = _util.ROOT
RESULT_STORE = ROOT / 'daedalus_bridge' / 'result_store.py'
ARCHITECTURE_MAP = ROOT / 'AGENTS.md'

# What turns a paragraph into this acceptance rather than another paragraph
# about locks: the decision itself, the wait that reveals it, the subject it
# is about, and the alternative the acceptance declines. Patterns, so an
# inflected rewrite stays the same statement while the prose already on the
# bullet -- "acceptance stamp", "delivery id" -- does not carry it for free.
ACCEPTANCE_TERMS = (
    r'\bstripe',
    r'\bwaits?\b',
    r'\baccepted\b',
    r'\bunbounded\b',
)

# How far a statement may sit from what it is a statement about. A move to
# another module or another section is a withdrawal of the record; a move
# within the stripe definitions is not. The bullet is one line of prose and
# the record lives on that line, so the adjacent bullets -- one and two lines
# away -- are not the record either.
REGION_MARGIN = 2
BULLET_ADJACENCY = 0

STRIPE_TABLE = re.compile(r'^DELIVERY_LOCK_STRIPES\b')
LOCK_SELECTOR = 'delivery_lock_for'
# The endpoint bullet describing the write itself, not the directory entry
# that names where accepted results land.
DELIVERY_WRITE_BULLET = re.compile(r'^- `POST /result`')


def _term_lines(lines, pattern):
    return [number for number, line in enumerate(lines, start=1)
            if pattern.search(line)]


def _stripe_region(lines):
    """The span the stripe table and its selector occupy, as (first, last).

    Anchored on the definitions rather than on a line distance from them, so
    unrelated growth elsewhere in the module does not read as a move.
    """
    table = _term_lines(lines, STRIPE_TABLE)
    parsed = ast.parse('\n'.join(lines))
    functions = [node for node in parsed.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == LOCK_SELECTOR]
    assert table and functions, 'the stripe definitions are not findable'
    return min(table[0], functions[0].lineno), functions[0].end_lineno


def _states_acceptance(text):
    """Whether every acceptance pattern matches `text`."""
    missing = [term for term in ACCEPTANCE_TERMS
               if not re.search(term, text, re.IGNORECASE)]
    return not missing


def _lines(path):
    return path.read_text(encoding='utf-8').splitlines()


def _comment_blocks(lines):
    """Maximal runs of consecutive comment lines, as inclusive line spans."""
    blocks, start, previous = [], None, 0
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith('#'):
            if start is None:
                start = number
            previous = number
        else:
            if start is not None:
                blocks.append((start, previous))
            start = None
    if start is not None:
        blocks.append((start, previous))
    return blocks


def _block_text(lines, span):
    return '\n'.join(lines[span[0] - 1:span[1]])


def _acceptance_span(lines, spans, region):
    """The first in-region span stating the acceptance, or None."""
    first, last = region
    for span in spans:
        low = span[0] >= first - REGION_MARGIN
        high = span[1] <= last + REGION_MARGIN
        if low and high and _states_acceptance(_block_text(lines, span)):
            return span
    return None


def test_acceptance_stated_beside_the_stripe_definitions(tmp):
    """The acceptance sits in a comment beside the stripe definitions."""
    del tmp
    lines = _lines(RESULT_STORE)
    region = _stripe_region(lines)
    found = _acceptance_span(lines, _comment_blocks(lines), region)
    assert found, (
        'no comment block beside the stripe definitions states the '
        'delivery-write timing acceptance; a block needs every pattern of '
        f'{ACCEPTANCE_TERMS}, and the blocks inside {region} were '
        + repr([
            _block_text(lines, span)
            for span in [
                candidate for candidate in _comment_blocks(lines)
                if region[0] - REGION_MARGIN <= candidate[0]
                and candidate[1] <= region[1] + REGION_MARGIN]]))


def test_acceptance_recorded_on_the_delivery_write_path(tmp):
    """The architecture map records the acceptance beside the write."""
    del tmp
    lines = _lines(ARCHITECTURE_MAP)
    bullets = _term_lines(lines, DELIVERY_WRITE_BULLET)
    assert bullets, 'the delivery-write path is not described in AGENTS.md'
    nearby = [number for number in range(1, len(lines) + 1)
              if any(abs(number - bullet) <= BULLET_ADJACENCY
                     for bullet in bullets)]
    text = '\n'.join(lines[number - 1] for number in nearby)
    assert _states_acceptance(text), (
        'the delivery-write path in AGENTS.md no longer records that '
        'discovering a shared stripe by timing a write is accepted; '
        f'bullet {bullets} is missing a match for a pattern of '
        f'{ACCEPTANCE_TERMS}')


def test_lock_table_stays_bounded_as_targets_are_named(tmp):
    """Naming targets does not grow the delivery lock table."""
    del tmp
    store = _util.load(RESULT_STORE, 'stripe_acceptance_store')
    for number in range(200):
        store.delivery_lock_for(f'bounded-token_tab-{number:04d}')
    assert len(store.delivery_locks) == store.DELIVERY_LOCK_STRIPES, (
        'the delivery lock table grew with the target names mapped into '
        'it; the bounded stripe count is what the timing acceptance '
        'trades on')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='deliverystripeacceptance_')


if __name__ == '__main__':
    raise SystemExit(main())
