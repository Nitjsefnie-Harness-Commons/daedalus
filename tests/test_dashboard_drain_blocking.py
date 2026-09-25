#!/usr/bin/env python3
"""The dashboard drain stops at the first entry it cannot resolve.

Every dashboard window drains one shared directory, so the head entry is
routinely contended, refused, or unparseable. The drain must deliver a
*prefix* and never advance its cursor past an entry it did not consume:
advancing strands the unresolved entry below the cursor, where the next pass
treats it as already-consumed and the removal join unlinks it. That loses a
real contended event, removes a refused object against the contract (and
outside the refusal registry's retire path), and drops an unparseable file
before its TTL. Each control below fails against the `continue` form. The
bounded cost — a stuck head blocks later events until the TTL sweep vacates
it — is pinned here too, not just the block.
"""
import os
import threading
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _fanout import (  # noqa: E402
    DASHBOARD,
    captured_stdout as _captured,
    dashboard_queue as _queue,
    service_pair as _service,
    write_event as _write_event,
)


def test_a_contended_entry_is_retried_not_skipped(tmp):
    """Two windows drain one directory and claim the same queue key, so a
    real contention at the head is routine. The window that loses the claim
    must stop and retry, not skip the entry and deliver the ones behind it:
    advancing past the contended entry strands it below the cursor, where the
    next pass unlinks it and the event is lost. The claim is driven for real
    (a second thread holds it), not stubbed. With the contended entry at the
    head the second window delivers nothing this pass, then — once the
    holder releases — receives every event, the contended one included."""
    service, drain = _service('fanout_contended')
    cq = service.command_queue
    token = 'tok'
    cmd_dir = Path(tmp) / 'commands'
    qdir = _queue(service, tmp, token)
    _a_id, _a_killed = service.register(token, DASHBOARD)
    _b_id, b_killed = service.register(token, DASHBOARD)
    cq.notify_dashboard(cmd_dir, token, {'type': 'first'})
    cq.notify_dashboard(cmd_dir, token, {'type': 'second'})
    entries = sorted(qdir.iterdir())
    assert len(entries) == 2, entries
    head = entries[0].name
    hold_key = cq.queue_key(qdir.name, head)
    holding, release = threading.Event(), threading.Event()

    def hold():
        with cq.claimed(hold_key):  # the real claim the drain contends on
            holding.set()
            release.wait()

    holder = threading.Thread(target=hold)
    holder.start()
    holding.wait()
    b_frames = []
    try:
        contended = drain.drain_dashboard(
            qdir, token, b_killed, command_ttl=90,
            frame_writer=b_frames.append)
    finally:
        release.set()
        holder.join()
    assert contended == 0, (
        'the losing consumer skipped the contended entry and delivered past '
        f'it: {contended}')
    assert b_frames == [], b_frames
    assert service.subscription_cursor(b_killed) < head, (
        'the cursor advanced past an unconsumed entry', head)

    got = []
    assert drain.drain_dashboard(
        qdir, token, b_killed, command_ttl=90, frame_writer=got.append) == 2
    assert [f['type'] for f in got] == ['first', 'second'], got


def test_a_refused_entry_is_left_in_place_and_stops_the_scan(tmp):
    """A refused object (a symlinked name) is never delivered or unlinked.
    The scan must stop there rather than deliver the resolvable entries
    behind it and strand the refused one below the cursor, where the join
    would unlink it and bypass the refusal registry's retire path. The
    refused entry survives across drains and the same object logs only one
    REFUSED line."""
    service, drain = _service('fanout_refused_head')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    target = Path(tmp) / 'elsewhere.json'
    target.write_text('{"id":"elsewhere"}', encoding='utf-8')
    refused = qdir / '0000000000001_00000001.json'
    refused.symlink_to(target)
    _write_event(qdir, '0000000000002_00000002', type='behind')
    _sub_id, killed = service.register(token, DASHBOARD)

    with _captured() as out:
        first = drain.drain_dashboard(
            qdir, token, killed, command_ttl=90, frame_writer=lambda _d: None)
        second = drain.drain_dashboard(
            qdir, token, killed, command_ttl=90, frame_writer=lambda _d: None)

    assert first == 0, first  # stopped at the refused head
    assert second == 0, second
    assert refused.exists() or refused.is_symlink(), (
        'the refused entry was unlinked by the drain')
    assert out.getvalue().count('[STREAM] REFUSED') == 1, out.getvalue()


def test_an_unparseable_entry_survives_until_the_sweep_clears_it(tmp):
    """An unparseable entry is left in place (not unlinked before its TTL)
    and stops the scan; the collector's TTL sweep removes it, which clears
    the block and lets the entries behind it flow again. The block is
    bounded by the command TTL, not permanent."""
    service, drain = _service('fanout_unparseable')
    cq = service.command_queue
    token = 'tok'
    cmd_dir = Path(tmp) / 'commands'
    qdir = _queue(service, tmp, token)
    broken = qdir / '0000000000001_00000001.json'
    broken.write_text('{"id": "torn', encoding='utf-8')  # not valid JSON
    _write_event(qdir, '0000000000002_00000002', type='behind')
    _sub_id, killed = service.register(token, DASHBOARD)
    frames = []

    blocked = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                    frame_writer=frames.append)
    assert blocked == 0, blocked
    assert broken.exists(), 'the unparseable entry was removed before TTL'
    assert frames == [], frames

    # The sweep is the backstop: vacates on age, clearing the block.
    old = time.time() - 500
    os.utime(broken, (old, old))
    cq.collect_expired(cmd_dir, 90)
    assert not broken.exists(), 'the sweep did not vacate the broken entry'
    after = drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                                  frame_writer=frames.append)
    assert after == 1, after
    assert [f['type'] for f in frames] == ['behind'], frames


def test_the_cursor_never_advances_past_an_unconsumed_entry(tmp):
    """The property the three stop reasons are instances of: after a drain
    that could not resolve the head entry, this connection's cursor is at or
    before that entry, never past it. Advancing past an unconsumed entry is
    what strands and then unlinks it. Asserted directly on the cursor so the
    invariant, not just each symptom, is pinned."""
    service, drain = _service('fanout_cursor_invariant')
    token = 'tok'
    qdir = _queue(service, tmp, token)
    head = _write_event(qdir, '0000000000001_00000001', type='head')
    _write_event(qdir, '0000000000002_00000002', type='behind')
    _sub_id, killed = service.register(token, DASHBOARD)
    # Make the head unresolvable: an unparseable body stops the scan before
    # the entry behind it.
    head.write_text('not json at all', encoding='utf-8')
    frames = []

    drain.drain_dashboard(qdir, token, killed, command_ttl=90,
                          frame_writer=frames.append)
    cursor = service.subscription_cursor(killed)
    assert cursor <= head.name, (
        'the cursor advanced past an entry this connection never consumed',
        cursor, head.name)
    assert frames == [], frames


if __name__ == '__main__':
    raise SystemExit(_util.runner(
        _util.collect(globals()), tmp_prefix='dashdrainblocking_'))
