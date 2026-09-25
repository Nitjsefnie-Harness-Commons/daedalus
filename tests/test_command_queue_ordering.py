#!/usr/bin/env python3
"""`next_seq` names are ordered by publish, not by the wall clock (issue 1098).

A queue entry's stem is `<ms:013d>_<counter:020d>`. If the wall clock steps
backwards between two mints, the later stem sorts below the earlier one and
the per-target FIFO inverts — the command enqueued second is delivered first.
The fix re-establishes ordering from disk: a one-shot seed under the shared
filesystem lock raises the millisecond mark and counter above everything
surviving on disk, and every mint clamps the millisecond to that mark. These
controls pin the three ways the clock can betray a name — a backwards step in
one process, a fresh process standing up over survivors, and foreign-format
neighbours the seed must ignore — and that the seed is taken once, not per
mint. Delivery order is asserted through a real drain, not a name compare.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _command_candidates import _load_queue  # noqa: E402
from _service_loader import _load_service  # noqa: E402


TOKEN = 'tok'
TAB = 'tab'


class _Clock:
    """A settable wall clock so a test can step it between two mints."""

    def __init__(self, value):
        self.value = value

    def time(self):
        return self.value


def _on(clock, module):
    """Point the module's `time` at the settable clock; return a restore.

    setattr, not `module.time = clock`: pyright refuses a direct attribute
    assignment on a ModuleType.
    """
    saved = module.time
    setattr(module, 'time', clock)
    return saved


def _qdir_name(module):
    return module.command_target_names(TOKEN, TAB)[0]


def _fresh_queue(name):
    """A by-path (command_queue, queue_order) pair, fresh for this test.

    The seed is one-shot and process-global inside `queue_order`, so a plain
    by-path load of `command_queue` would share one seed across every test in
    this process and only the first would seed. Load `queue_order` by path
    (fresh mark/counter/guard) and point this copy of `command_queue`'s
    `next_seq` at it — `enqueue` resolves the module global at call time. The
    mint's clock lives in `queue_order`, so tests step *its* `time`.
    """
    order = _util.load(
        _util.ROOT / 'daedalus_bridge' / 'queue_order.py',
        name=f'{name}_order')
    queue = _load_queue(name)
    setattr(queue, 'next_seq', order.next_seq)
    return queue, order


def _write_entry(qdir, stem, **fields):
    document = {'kind': 'event', '_did': stem}
    document.update(fields)
    path = Path(qdir) / f'{stem}.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path


def _drain_order(qdir, name):
    """Deliver every entry through a real `drain_queue`; return id order."""
    service = _load_service(name)
    frames = []
    service.drain_queue(Path(qdir), None, None, command_ttl=90,
                        frame_writer=frames.append)
    return [frame.get('id') for frame in frames]


def test_a_backwards_clock_step_keeps_per_target_fifo(tmp):
    """Two mints, the clock stepping backwards between them: the second stem
    still sorts above the first, and a real drain delivers them in publish
    order. Removing the in-process clamp mints the second at the lower
    millisecond, the stems invert, and the drain delivers 'second' first."""
    cq, order = _fresh_queue('ordering_backwards_step')
    cmd_dir = Path(tmp) / 'commands'
    clock = _Clock(5.0)
    saved = _on(clock, order)
    try:
        first, _ = cq.enqueue(cmd_dir, TOKEN, TAB, {'id': 'first'},
                              command_ttl=90)
        clock.value = 4.0  # the wall clock steps backwards
        second, _ = cq.enqueue(cmd_dir, TOKEN, TAB, {'id': 'second'},
                               command_ttl=90)
    finally:
        setattr(cq, 'time', saved)
    assert first < second, (first, second)
    qdir = cmd_dir / _qdir_name(cq)
    assert _drain_order(qdir, 'ordering_backwards_step_drain') == [
        'first', 'second']


def test_a_fresh_process_over_survivors_keeps_fifo(tmp):
    """The restart case: entries minted by a previous process survive on
    disk, the clock is stepped back, and a fresh generator stands up over
    that directory. The seed must raise the mark and counter above every
    survivor, so the new stem sorts above them all and a real drain of the
    mixed directory delivers the old commands before the new one. Without the
    seed the fresh process mints at the stepped-back clock and its entry
    sorts below the survivors, inverting FIFO."""
    cmd_dir = Path(tmp) / 'commands'
    qdir = cmd_dir / f'{TOKEN}_{TAB}'
    qdir.mkdir(parents=True)
    survivors = ['0000000005000_00000000000000000007',
                 '0000000006000_00000000000000000009']
    for index, stem in enumerate(survivors):
        _write_entry(qdir, stem, id=f'old{index}')

    cq, order = _fresh_queue('ordering_restart')
    clock = _Clock(4.0)  # stepped back below both survivors
    saved = _on(clock, order)
    try:
        minted, _ = cq.enqueue(cmd_dir, TOKEN, TAB, {'id': 'new'},
                               command_ttl=90)
    finally:
        setattr(cq, 'time', saved)
    for stem in survivors:
        assert minted > stem, (minted, stem)
    assert _drain_order(qdir, 'ordering_restart_drain') == [
        'old0', 'old1', 'new']


def test_the_seed_ignores_unparseable_neighbours(tmp):
    """A legacy drop and foreign-format entries beside the parseable
    survivors must not raise, must be left in place, and must not be counted
    as ours: a foreign name the seed tried to read as a stem would either
    crash or skew the mark. The new stem still sorts above the one parseable
    survivor."""
    cmd_dir = Path(tmp) / 'commands'
    qdir = cmd_dir / f'{TOKEN}_{TAB}'
    qdir.mkdir(parents=True)
    survivor = '0000000006000_00000000000000000009'
    _write_entry(qdir, survivor, id='old')
    # Foreign neighbours: a legacy single-file drop name, a name that is not
    # a stem at all, a command file minted by an older six-digit bridge, a
    # 13-character non-numeric millisecond field (reaching the isdigit guard),
    # and a file at the commands root (reaching the per-queue is_dir filter).
    (qdir / f'{TOKEN}_{TAB}.json').write_text('{}', encoding='utf-8')
    (qdir / 'not-a-stem.json').write_text('{}', encoding='utf-8')
    (qdir / '0000000007000_000001.json').write_text('{}', encoding='utf-8')
    (qdir / 'aaaaaaaaaaaaa_000001.json').write_text('{}', encoding='utf-8')
    (cmd_dir / f'{TOKEN}.json').write_text('{}', encoding='utf-8')

    cq, order = _fresh_queue('ordering_unparseable')
    clock = _Clock(4.0)
    saved = _on(clock, order)
    try:
        minted, _ = cq.enqueue(cmd_dir, TOKEN, TAB, {'id': 'new'},
                               command_ttl=90)
    finally:
        setattr(cq, 'time', saved)
    assert minted > survivor, (minted, survivor)
    # The foreign names are ignored, not consumed or removed by the seed.
    assert (qdir / 'not-a-stem.json').exists()
    assert (qdir / '0000000007000_000001.json').exists()


def test_a_narrow_counter_survivor_raises_the_mark(tmp):
    """A survivor whose counter is the released bridge's six digits still
    holds the mark above it.

    The seed exists to protect the upgrade path this branch itself opens
    (6→20 counter), and at an *equal* millisecond a zero-padded 20-digit
    counter sorts below any 6-digit counter with a nonzero leading digit,
    whatever its value — so rebasing the counter alone cannot rescue it. The
    seed must honour every parseable survivor's millisecond and take the
    mark strictly above. The survivor here is the ONLY parseable entry and
    its millisecond is above the stepped-back clock, so the control dies the
    moment the narrow field is ignored (the mark then rests on the raw
    clock, below the survivor) and passes only when the parse reads the six
    digits and the mark lands above.
    """
    cmd_dir = Path(tmp) / 'commands'
    qdir = cmd_dir / f'{TOKEN}_{TAB}'
    qdir.mkdir(parents=True)
    survivor = '1757389120000_000042'  # released-bridge stem: 6-digit counter
    _write_entry(qdir, survivor, id='old')

    cq, order = _fresh_queue('ordering_narrow_survivor')
    clock = _Clock(1_757_389_119.999)  # one second behind the survivor
    saved = _on(clock, order)
    try:
        minted, _ = cq.enqueue(cmd_dir, TOKEN, TAB, {'id': 'new'},
                               command_ttl=90)
    finally:
        setattr(cq, 'time', saved)
    # Assert the branch, not the outcome: the minted millisecond must equal
    # survivor_ms + 1, a value the real wall clock cannot produce in a test.
    # Asserting only `minted > survivor` would pass on the real clock (2026 is
    # above the hard-coded 2025 survivor) if the seed were ever bypassed, so
    # the control would go vacuous with the defect present.
    assert int(minted.split('_', 1)[0]) == 1_757_389_120_001, (
        'the mark did not land on survivor_ms + 1; the seed was bypassed or '
        'the narrow field was ignored', minted)
    assert minted > survivor, (minted, survivor)


def test_a_survivor_at_the_millisecond_ceiling_does_not_overflow_the_field(
        tmp):
    """A survivor whose millisecond is the largest the 13-digit field holds
    must not push the mark to 14 digits.

    `{ms:013d}` is a minimum width, not a fixed one: at a survivor of
    9999999999999 the unclamped `highest + 1` printed as
    10000000000000_... and sorted below every 13-digit entry — the exact
    inversion this module exists to prevent, and a silent dashboard loss. The
    seed clamps the mark to the field's width, so a mint stays 13 digits and
    is ordered by the counter against a same-millisecond 20-digit survivor.
    """
    cmd_dir = Path(tmp) / 'commands'
    qdir = cmd_dir / f'{TOKEN}_{TAB}'
    qdir.mkdir(parents=True)
    survivor = '9999999999999_00000000000000000001'  # largest 13-digit ms
    _write_entry(qdir, survivor, id='old')

    cq, order = _fresh_queue('ordering_ms_ceiling')
    clock = _Clock(1.0)  # far below the survivor
    saved = _on(clock, order)
    try:
        minted, _ = cq.enqueue(cmd_dir, TOKEN, TAB, {'id': 'new'},
                               command_ttl=90)
    finally:
        setattr(cq, 'time', saved)
    assert len(minted.split('_', 1)[0]) == 13, (
        'the mark overflowed the 13-digit field', minted)
    assert minted > survivor, (
        'a mint at the millisecond ceiling sorted below its survivor',
        minted, survivor)


def test_the_seed_is_taken_once_not_per_mint(tmp):
    """The disk scan happens once, before the first mint. If a later mint
    rescanned, planting a high-counter file after the first mint would raise
    the counter above it. One-shot: the second mint stays below the planted
    file, so the hot path never re-walks the commands root."""
    cq, order = _fresh_queue('ordering_one_shot')
    cmd_dir = Path(tmp) / 'commands'
    clock = _Clock(5.0)
    saved = _on(clock, order)
    try:
        cq.enqueue(cmd_dir, TOKEN, TAB, {'id': 'a'}, command_ttl=90)
        qdir = cmd_dir / _qdir_name(cq)
        planted = '0000000005000_00000000000000099999'
        _write_entry(qdir, planted, id='planted')
        second, _ = cq.enqueue(cmd_dir, TOKEN, TAB, {'id': 'b'},
                               command_ttl=90)
    finally:
        setattr(cq, 'time', saved)
    assert int(second.split('_', 1)[1]) < 99999, (
        'the second mint rescanned the commands root', second)


if __name__ == '__main__':
    raise SystemExit(_util.runner(
        _util.collect(globals()), tmp_prefix='cmdorder_'))
