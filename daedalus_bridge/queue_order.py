"""The queue-queue name ordering source (issue 1098).

Owns the one place a queue-entry stem is minted, so the ordering guarantee
lives in a single module: the millisecond mark, the monotonic counter, the
one-shot disk seed that re-establishes both across a restart, and the
`<ms:013d>_<counter:020d>` format. `command_queue` calls `next_seq` and
re-exports it; nothing else here is mutable or public. Reads only the
commands root passed in — binds no configuration of its own, and relies on
callers holding `command_queue.command_fs_lock` so the seed and the clamp
cannot interleave with a concurrent publish.
"""
import itertools
import os
import time


# The field widths a stem can carry. This bridge writes a 20-digit counter; a
# released one wrote 6. The seed reads both so it protects the upgrade path.
_COUNTER_WIDTH = 20

# One-shot ordering state, re-established from disk before the first mint.
# `_ms_mark` is the highest millisecond seen (on disk, then raised by the
# clamp); `_seq_counter` is rebased above the highest same-width counter.
_ms_mark = 0
_seq_counter = itertools.count(1)
_seeded = False


def _parse_stem(name):
    """`(millisecond, counter, width)` for a stem, else None.

    A stem is `<13 digits>_<N digits>.json` for any width N — the current
    bridge writes 20, a released one 6, and the seed must read both because
    it protects the upgrade path between them. The mark honours the
    millisecond; the counter is rebased only from same-width entries. A
    legacy drop, a non-stem, a temp, or a non-numeric field is None, ignored
    without raising.
    """
    if name.startswith('.') or not name.endswith('.json'):
        return None
    millisecond, separator, counter = name[:-len('.json')].partition('_')
    if separator != '_' or len(millisecond) != 13 or not counter:
        return None
    if not (millisecond.isascii() and millisecond.isdigit()
            and counter.isascii() and counter.isdigit()):
        return None
    return int(millisecond), int(counter), len(counter)


def _seed_from_disk(cmd_dir):
    """Raise the mark strictly above every survivor on disk, the counter
    above every same-width one.

    One-shot, before the first mint, under the caller's `command_fs_lock`: a
    restart resets the in-process state, so ordering is re-established from
    disk, not the clock. The mark honours every parseable survivor's
    millisecond and lands one above, so a fresh entry wins on millisecond
    alone — needed because at an equal millisecond a zero-padded 20-digit
    counter sorts below a narrower one with a nonzero leading digit, whatever
    its value. The counter is rebased only from same-width entries. The scan
    is bounded in practice by the TTL sweep, which empties aged entries and
    their empty queues; a non-directory or unreadable one is ignored, not
    raised on.
    """
    global _ms_mark, _seq_counter, _seeded
    highest_millisecond = 0
    highest_counter = 0
    try:
        queues = [entry for entry in os.scandir(cmd_dir) if entry.is_dir()]
    except OSError:
        queues = []
    for queue in queues:
        try:
            children = [entry for entry in os.scandir(queue.path)
                        if entry.is_file()]
        except OSError:
            continue
        for child in children:
            parsed = _parse_stem(child.name)
            if parsed is not None:
                millisecond, counter, width = parsed
                highest_millisecond = max(highest_millisecond, millisecond)
                if width == _COUNTER_WIDTH:
                    highest_counter = max(highest_counter, counter)
    _ms_mark = highest_millisecond + 1
    _seq_counter = itertools.count(highest_counter + 1)
    _seeded = True


def next_seq(cmd_dir):
    """Monotonic, lexically-sortable queue filename stem: <ms>_<counter>.

    Both fields are fixed width, so byte order is the order the two
    components were issued. The millisecond is not raw wall-clock time: each
    mint takes `max(now, _ms_mark)` and raises the mark, and once, before the
    first mint, the seed honours every parseable survivor's millisecond and
    takes the mark strictly above the highest (`_seed_from_disk`). So a
    backwards clock step — within a process or across a restart that left
    entries queued — never mints a stem below one already issued or
    surviving, and the queue's FIFO holds. That the guarantee spans *every*
    survivor width, not just the current one, is exactly why the seed honours
    each millisecond and lands one above rather than rebasing the counter
    alone. The width is the bound: order holds while the counter is below
    10**20, which `itertools.count` does not itself cap. Callers hold
    `command_queue.command_fs_lock`; the seed and clamp rely on it.
    """
    global _ms_mark
    if not _seeded:
        _seed_from_disk(cmd_dir)
    _ms_mark = max(_ms_mark, int(time.time() * 1000))
    return f'{_ms_mark:013d}_{next(_seq_counter):020d}'
