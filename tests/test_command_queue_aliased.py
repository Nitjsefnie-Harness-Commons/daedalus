#!/usr/bin/env python3
"""Aliased command names deliver nothing, and a refusal logs once per object.

The queue drain, the legacy drain and poll share the refusal registry: a
retained, refused candidate logs its ``[STREAM] REFUSED`` line once per object,
not once per drain pass. The key is the candidate's logical name paired with
the object's incarnation, and the expiry sweep retires a name it vacates so a
different object taking that name logs again. How a candidate is read is in
``test_command_queue_candidates``.
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import (BRIDGE_ENV, TOK, framer, put_command,  # noqa: E402
                     stream_response)
from _command_candidates import (  # noqa: E402
    _hard_link, _load_queue, _symlink, _write_command)
from _service_loader import _load_service  # noqa: E402


def _refusals(captured):
    return [line for line in captured.getvalue().splitlines()
            if '[STREAM] REFUSED' in line]


def _drain_refusals(service, qdir, frames):
    """Run one queue drain, returning only its REFUSED lines."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        service.drain_queue(
            qdir, None, None, command_ttl=100, frame_writer=frames.append)
    return _refusals(captured)


def test_queue_drain_leaves_a_hard_linked_pair_undelivered(tmp):
    service = _load_service('aliased_queue_drain_hard_link')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    first = qdir / '0000000000001_000001.json'
    second = qdir / '0000000000002_000002.json'
    _write_command(first, 'aliased')
    _hard_link(first, second)
    frames = []

    delivered = service.drain_queue(
        qdir, None, None, command_ttl=100, frame_writer=frames.append)

    assert delivered == 0, delivered
    assert frames == [], frames
    assert first.exists() and second.exists(), 'a refusal unlinked a name'


def test_legacy_drain_leaves_a_hard_linked_pair_undelivered(tmp):
    service = _load_service('aliased_legacy_drain_hard_link')
    first = Path(tmp) / 'tok_dup.json'
    second = Path(tmp) / 'tok_other.json'
    _write_command(first, 'aliased')
    _hard_link(first, second)
    frames = []

    delivered = service.drain_legacy_file(
        first, 'dup', command_ttl=100, frame_writer=frames.append)

    assert delivered == 0, delivered
    assert frames == [], frames
    assert first.exists() and second.exists(), 'a refusal unlinked a name'


def test_legacy_drain_does_not_read_a_name_that_left_the_root(tmp):
    service = _load_service('aliased_legacy_drain_symlink')
    outside = Path(tmp) / 'outside.json'
    _write_command(outside, 'outside-payload')
    link = Path(tmp) / 'tok_dup.json'
    _symlink(link, outside)
    frames = []

    delivered = service.drain_legacy_file(
        link, 'dup', command_ttl=100, frame_writer=frames.append)

    assert delivered == 0, delivered
    assert frames == [], frames
    assert link.is_symlink(), 'a refusal unlinked the name'
    assert json.loads(outside.read_text(encoding='utf-8')) == {
        'id': 'outside-payload', 'code': '1'}


def test_poll_answers_empty_for_a_hard_linked_legacy_name(tmp):
    service = _load_service('aliased_poll_hard_link')
    first = Path(tmp) / 'tok.json'
    second = Path(tmp) / 'tok_second.json'
    _write_command(first, 'aliased')
    _hard_link(first, second)

    answer = service.poll_legacy(Path(tmp), 'tok')

    assert answer == (200, {}), answer
    assert first.exists() and second.exists(), 'a refusal unlinked a name'


def test_queue_drain_leaves_a_directory_entry_alone(tmp):
    service = _load_service('aliased_queue_drain_directory')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    entry = qdir / '0000000000001_000001.json'
    entry.mkdir()
    frames = []

    delivered = service.drain_queue(
        qdir, None, None, command_ttl=100, frame_writer=frames.append)

    assert delivered == 0, delivered
    assert frames == [], frames
    assert entry.is_dir(), 'the drain removed a directory entry'


def test_queue_drain_keeps_an_entry_it_cannot_unlink(tmp):
    """Symmetric with poll: the drop is swallowed and the entry stays."""
    service = _load_service('aliased_queue_drain_unlink_failure')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    entry = qdir / '0000000000001_000001.json'
    entry.write_text('["not a command"]', encoding='utf-8')
    real_unlink = pathlib.Path.unlink
    attempted = []

    def refusing_unlink(self, *args, **kwargs):
        if self.name == entry.name:
            attempted.append(self.name)
            raise PermissionError('the removal failed')
        return real_unlink(self, *args, **kwargs)

    def fail(frame):
        raise AssertionError(f'a non-object entry was delivered: {frame}')

    pathlib.Path.unlink = refusing_unlink
    try:
        delivered = service.drain_queue(
            qdir, None, None, command_ttl=100, frame_writer=fail)
    finally:
        pathlib.Path.unlink = real_unlink

    assert attempted == [entry.name], attempted
    assert delivered == 0, delivered
    assert entry.exists(), 'a failed removal must not lose the entry'


def test_poll_answers_the_command_when_the_file_will_not_unlink(tmp):
    """A failed removal is the drains' outcome too: answered, left in place."""
    service = _load_service('aliased_poll_unlink_failure')
    cq = service.command_queue
    _, legacy = cq.command_target_names('tok')
    path = Path(tmp) / legacy
    path.write_text('{"id": "kept", "code": "1"}', encoding='utf-8')
    real_unlink = pathlib.Path.unlink
    attempted = []

    def refusing_unlink(self, *args, **kwargs):
        if self.name == legacy:
            attempted.append(self.name)
            raise PermissionError('the removal failed')
        return real_unlink(self, *args, **kwargs)

    pathlib.Path.unlink = refusing_unlink
    try:
        answer = service.poll_legacy(Path(tmp), 'tok')
    finally:
        pathlib.Path.unlink = real_unlink

    assert attempted == [legacy], attempted
    assert answer == (200, {'id': 'kept', 'code': '1'}), answer
    assert path.exists(), 'a failed removal must not lose the command'


def test_poll_refuses_a_legacy_name_that_leaves_the_root(tmp):
    service = _load_service('aliased_poll_escape')
    outside = Path(tmp).parent / 'poll-escape-payload.json'
    _write_command(outside, 'outside-payload')
    link = Path(tmp) / 'tok.json'
    _symlink(link, outside)

    answer = service.poll_legacy(Path(tmp), 'tok')

    assert answer == (400, {'error': 'invalid path component'}), answer
    assert link.is_symlink(), 'the refused alias was unlinked'
    assert json.loads(outside.read_text(encoding='utf-8')) == {
        'id': 'outside-payload', 'code': '1'}


def test_a_retained_candidate_logs_its_refusal_once(tmp):
    """One line per refused candidate, not one per drain pass."""
    service = _load_service('aliased_refusal_logged_once')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    first = qdir / '0000000000001_000001.json'
    second = qdir / '0000000000002_000002.json'
    _write_command(first, 'aliased')
    _hard_link(first, second)
    frames = []
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        for _pass in range(2):
            service.drain_queue(
                qdir, None, None, command_ttl=100,
                frame_writer=frames.append)

    refusals = [line for line in captured.getvalue().splitlines()
                if '[STREAM] REFUSED' in line]
    assert frames == [], frames
    assert refusals, 'no refusal was logged at all'
    assert len(refusals) == 2, refusals


def test_a_retained_legacy_candidate_logs_its_refusal_once(tmp):
    """The legacy drain shares the once-per-candidate registry."""
    service = _load_service('aliased_legacy_refusal_logged_once')
    first = Path(tmp) / 'tok_dup.json'
    second = Path(tmp) / 'tok_other.json'
    _write_command(first, 'aliased')
    _hard_link(first, second)
    frames = []
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        for _pass in range(2):
            service.drain_legacy_file(
                first, 'dup', command_ttl=100, frame_writer=frames.append)

    refusals = [line for line in captured.getvalue().splitlines()
                if '[STREAM] REFUSED' in line]
    assert frames == [], frames
    assert len(refusals) == 1, refusals


def test_a_swept_queue_name_refused_again_for_a_different_object(tmp):
    """G1: the once-registry keys the refused object, not only its name.

    The queue sweep vacates a name by unlinking it without an alias check, so
    a different refused object can appear at a name the registry already
    recorded. That object must log its own refusal.
    """
    service = _load_service('aliased_swept_name_refusal')
    queue = service.command_queue
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    victim = qdir / '0001_000001.json'
    # A hard-linked object is refused under both of its names; the sweep
    # frees the name by unlinking it, and a filesystem may hand the freed
    # inode straight back to whatever takes the name next.
    twin = qdir / '0002_000002.json'
    _write_command(twin, 'aliased')
    _hard_link(twin, victim)

    frames = []
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        service.drain_queue(
            qdir, None, None, command_ttl=100, frame_writer=frames.append)
    first = _refusals(captured)
    assert any(victim.name in line for line in first), first

    aged = time.time() - 160
    os.utime(victim, (aged, aged))
    queue.collect_expired(Path(tmp) / 'commands', 90)
    assert not victim.exists(), 'the sweep did not vacate the name'
    qdir.mkdir(exist_ok=True)

    # A DIFFERENT object — a directory named like an entry — takes the name.
    victim.mkdir()

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        service.drain_queue(
            qdir, None, None, command_ttl=100, frame_writer=frames.append)
    second = _refusals(captured)

    assert frames == [], frames
    assert any(victim.name in line for line in second), (
        f'a different object at a swept name logged nothing: {second!r}')


def test_the_vacating_retire_lets_a_swept_name_log_again(tmp):
    """The vacating retire, pinned without reproducing inode reuse.

    `_identity` returns one constant for the whole row, so every object at a
    name carries the identity the registry already recorded for the object the
    sweep removed. That degeneracy stands in for the one case identity cannot
    decide alone — a filesystem reusing a freed inode in one clock tick. The
    retire is then the sole thing that lets the second refusal log, so this row
    fails when the retire is removed.
    """
    service = _load_service('aliased_vacating_retire_pinned')
    queue = service.command_queue
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    victim = qdir / '0001_000001.json'
    twin = qdir / '0002_000002.json'
    _write_command(twin, 'aliased')
    _hard_link(twin, victim)

    real_identity = queue._identity
    queue._identity = lambda stat_result: (0, 0, 0)
    try:
        frames = []
        first = _drain_refusals(service, qdir, frames)
        assert any(victim.name in line for line in first), first

        aged = time.time() - 160
        os.utime(victim, (aged, aged))
        queue.collect_expired(Path(tmp) / 'commands', 90)
        assert not victim.exists(), 'the sweep did not vacate the name'
        qdir.mkdir(exist_ok=True)

        # A different refused object takes the swept name; with a constant
        # identity only the retire distinguishes it from the one recorded.
        victim.mkdir()
        second = _drain_refusals(service, qdir, frames)
    finally:
        queue._identity = real_identity

    assert frames == [], frames
    assert any(victim.name in line for line in second), (
        f'a swept name did not log again once the retire ran: {second!r}')


def test_the_vacating_retire_does_not_fire_when_the_unlink_fails(tmp):
    """A name the sweep did not free keeps its record; the retire must not
    fire.

    An aged directory named like an entry is refused, but the sweep cannot
    unlink a directory, so the name is still occupied when the sweep is done.
    With `_identity` pinned to one constant, the second drain computes the
    same key whatever the clock did, so the silence below can only come from
    the record surviving the sweep. The retire firing despite the failed
    unlink would clear that record and the same object would re-log.
    """
    service = _load_service('aliased_vacating_retire_unlink_boundary')
    queue = service.command_queue
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    entry = qdir / '0001_000001.json'
    entry.mkdir()

    real_identity = queue._identity
    queue._identity = lambda stat_result: (0, 0, 0)
    try:
        frames = []
        first = _drain_refusals(service, qdir, frames)
        assert first, 'the directory entry logged no refusal'

        aged = time.time() - 160
        os.utime(entry, (aged, aged))
        queue.collect_expired(Path(tmp) / 'commands', 90)
        assert entry.is_dir(), 'the sweep removed the occupied name'

        second = _drain_refusals(service, qdir, frames)
    finally:
        queue._identity = real_identity

    assert frames == [], frames
    assert second == [], (
        f'a name the sweep did not free lost its record (the retire fired): '
        f'{second!r}')


def test_the_registry_evicts_its_oldest_entry_past_the_bound(tmp):
    """G5: the bound holds, and the OLDEST recorded entry is the one evicted.

    A removed eviction loop grows the registry without bound; a reversed one
    forgets the newest rather than the oldest. Driving three refusals past a
    bound of two, the registry must hold exactly the two newest and have
    forgotten the oldest, so the row pins the bound and the order together.
    """
    service = _load_service('aliased_refusal_bound_eviction')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    victims = [qdir / f'000{i}_00000{i}.json' for i in (1, 2, 3)]
    for i, victim in enumerate(victims, start=1):
        twin = Path(tmp) / f'twin{i}.json'
        _write_command(twin, f'aliased{i}')
        _hard_link(twin, victim)

    real_limit = service._REFUSED_CANDIDATE_LIMIT
    setattr(service, '_REFUSED_CANDIDATE_LIMIT', 2)
    try:
        frames = []
        first = _drain_refusals(service, qdir, frames)
        registry = dict(service._refused_candidates)
    finally:
        setattr(service, '_REFUSED_CANDIDATE_LIMIT', real_limit)

    assert frames == [], frames
    assert len(first) == 3, first
    assert len(registry) == 2, registry
    names = {key[0] for key in registry}
    assert f'queue:tok/{victims[0].name}' not in names, (
        f'the oldest entry was not evicted: {names}')
    assert f'queue:tok/{victims[2].name}' in names, (
        f'the newest entry was evicted: {names}')


def test_a_replaced_queue_symlink_refused_again_for_its_name(tmp):
    """G6: a refusal with no open descriptor still carries an identity.

    A symlinked name is refused by the O_NOFOLLOW open itself on POSIX, so
    there is no descriptor to stat; elsewhere the open follows the link and
    the identity check refuses what it named. Either way a DIFFERENT symlink
    taking the same name must log again.
    """
    service = _load_service('aliased_replaced_symlink_refusal')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    entry = qdir / '0001_000001.json'
    target_a = Path(tmp) / 'a.json'
    target_b = Path(tmp) / 'b.json'
    _write_command(target_a, 'a')
    _write_command(target_b, 'b')
    _symlink(entry, target_a)

    frames = []
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        service.drain_queue(
            qdir, None, None, command_ttl=100, frame_writer=frames.append)
    first = _refusals(captured)
    assert first, 'the first symlinked name logged no refusal'

    # Stage the replacement while the first link still holds its inode, then
    # move it into place, so the two inodes cannot coincide.
    staged = qdir / 'staged'
    _symlink(staged, target_b)
    entry.unlink()
    os.replace(staged, entry)

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        service.drain_queue(
            qdir, None, None, command_ttl=100, frame_writer=frames.append)
    second = _refusals(captured)

    assert frames == [], frames
    assert second, 'a replaced symlink at the same name logged nothing'


def test_a_replaced_legacy_symlink_refused_again_for_its_name(tmp):
    """G6 on the legacy drain: the same no-descriptor identity."""
    service = _load_service('aliased_replaced_legacy_symlink')
    entry = Path(tmp) / 'tok_dup.json'
    target_a = Path(tmp) / 'a.json'
    target_b = Path(tmp) / 'b.json'
    _write_command(target_a, 'a')
    _write_command(target_b, 'b')
    _symlink(entry, target_a)

    frames = []
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        service.drain_legacy_file(
            entry, 'dup', command_ttl=100, frame_writer=frames.append)
    first = _refusals(captured)
    assert first, 'the first legacy symlink logged no refusal'

    staged = Path(tmp) / 'staged'
    _symlink(staged, target_b)
    entry.unlink()
    os.replace(staged, entry)

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        service.drain_legacy_file(
            entry, 'dup', command_ttl=100, frame_writer=frames.append)
    second = _refusals(captured)

    assert frames == [], frames
    assert second, 'a replaced legacy symlink logged nothing'


def test_poll_refuses_again_a_replaced_candidate_under_its_name(tmp):
    """G6 on poll: its once-registry entry is keyed like the drains'."""
    service = _load_service('aliased_poll_replaced_candidate')
    cq = service.command_queue
    cmd_dir = Path(tmp)
    _, legacy = cq.command_target_names('tok')
    entry = cmd_dir / legacy
    twin = Path(tmp) / 'twin.json'
    _write_command(twin, 'aliased')
    _hard_link(twin, entry)

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        answer = service.poll_legacy(cmd_dir, 'tok')
    first = _refusals(captured)
    assert answer == (200, {}), answer
    assert first, 'the first aliased poll logged no refusal'
    # Pin the poll producer's own spelling, so a drift here is caught here.
    recorded = {key[0] for key in service._refused_candidates}
    assert recorded == {f'legacy:{legacy}'}, recorded

    # The name is vacated; the twin keeps the original object allocated, so
    # the replacement below cannot reuse its inode.
    entry.unlink()
    entry.mkdir()

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        answer = service.poll_legacy(cmd_dir, 'tok')
    second = _refusals(captured)

    assert answer == (200, {}), answer
    assert second, 'a replaced candidate at the same name logged nothing'


def test_sweep_leaves_an_aliased_legacy_pair_for_a_later_pass(tmp):
    queue = _load_queue('aliased_sweep_hard_link')
    cmd_dir = Path(tmp) / 'commands'
    cmd_dir.mkdir(parents=True)
    first = cmd_dir / 'tok_dup.json'
    second = cmd_dir / 'tok_other.json'
    _write_command(first, 'aliased')
    _hard_link(first, second)
    now = time.time()
    os.utime(first, (now - 91, now - 91))

    queue.collect_expired(cmd_dir, 90)

    assert first.exists() and second.exists(), 'the sweep unlinked an alias'


def test_a_hard_linked_legacy_pair_is_delivered_zero_times(tmp):
    """One extension stream, two names for one object, zero commands out."""
    served = []
    with _util.bridge(tmp, output=served,
                      env=BRIDGE_ENV) as (base, docroot):
        commands = Path(docroot) / 'commands'
        first = commands / f'{TOK}_dup.json'
        second = commands / f'{TOK}_other.json'
        _write_command(first, 'aliased')
        _hard_link(first, second)
        conn, response = stream_response(base, TOK, tab='extension')
        try:
            assert response.status == 200, response.status
            time.sleep(1.25)
            assert first.exists() and second.exists(), (
                'an aliased legacy name was consumed')
            status, _ = put_command(
                base, {'token': TOK, 'id': 'after', 'code': '1'})
            assert status == 200, status
            frame = framer(response, served)('the first frame')
            assert frame.get('id') == 'after', frame
            assert first.exists() and second.exists(), (
                'an aliased legacy name was consumed')
        finally:
            response.close()
            conn.close()


def test_a_stream_admitted_before_the_name_reads_nothing_outside(tmp):
    """The containment an admission checked cannot vouch for a later alias."""
    served = []
    with _util.bridge(tmp, output=served,
                      env=BRIDGE_ENV) as (base, docroot):
        outside = Path(docroot).parent / 'outside-payload.json'
        _write_command(outside, 'outside-payload')
        conn, response = stream_response(base, TOK, tab='dup')
        try:
            assert response.status == 200, response.status
            link = Path(docroot) / 'commands' / f'{TOK}_dup.json'
            _symlink(link, outside)
            time.sleep(1.25)
            assert link.is_symlink(), 'the refused alias was unlinked'
            assert json.loads(outside.read_text(encoding='utf-8')) == {
                'id': 'outside-payload', 'code': '1'}
            status, _ = put_command(
                base, {'token': TOK, 'tab': 'dup', 'id': 'after', 'code': '1'})
            assert status == 200, status
            frame = framer(response, served)('the first frame')
            assert frame.get('id') == 'after', frame
            assert link.is_symlink(), 'the refused alias was unlinked'
        finally:
            response.close()
            conn.close()


def test_a_swept_legacy_name_refused_again_for_a_different_object(tmp):
    """The legacy half of the retire, on the constant-identity stand-in.

    A refused legacy object is retained by the sweep, but a parseable one at
    the same name is unlinked, which frees the name. With `_identity`
    constant, only the retire lets a later refused object there log, so this
    row fails while the `not legacy` carve-out stands.
    """
    service = _load_service('aliased_legacy_retire_pinned')
    queue = service.command_queue
    entry = Path(tmp) / 'tok_dup.json'
    real_identity = queue._identity
    queue._identity = lambda stat_result: (0, 0, 0)
    try:
        frames = []

        def drain():
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                service.drain_legacy_file(
                    entry, 'dup', command_ttl=100,
                    frame_writer=frames.append)
            return _refusals(captured)

        _write_command(entry, 'aliased')
        _hard_link(entry, Path(tmp) / 'twin.json')
        assert drain(), 'the aliased legacy file logged no refusal'

        # A parseable object at the name is unlinked by the sweep, freeing it.
        entry.unlink()
        _write_command(entry, 'later')
        os.utime(entry, (0, 0))
        queue.collect_expired(Path(tmp), 90)
        assert not entry.exists(), 'the sweep did not free the name'

        _write_command(entry, 'aliased')
        _hard_link(entry, Path(tmp) / 'twin2.json')
        second = drain()
    finally:
        queue._identity = real_identity

    assert frames == [], frames
    assert second, f'a swept legacy name did not log again: {second!r}'


def test_a_raising_retire_callback_does_not_kill_the_sweeper(tmp):
    """A callback that breaks its no-raise contract cannot stop the sweep.

    The guard is `except Exception`, so it covers every class a callback can
    raise, not just one. This drives a TypeError and a RuntimeError — the
    latter sits outside the sweep's own narrow `except` tuple, so a guard
    narrowed to a single class would let it escape and kill the daemon.
    """
    service = _load_service('aliased_retire_raising')
    queue = service.command_queue
    cmd_dir = Path(tmp) / 'commands'
    qdir = cmd_dir / 'tok'
    entries = [qdir / f'000{i}_00000{i}.json' for i in (1, 2)]
    real = queue._name_vacated
    for exc in (TypeError, RuntimeError):
        qdir.mkdir(parents=True, exist_ok=True)  # a sweep may rmdir the dir
        for entry in entries:
            _write_command(entry, entry.name)
            os.utime(entry, (0, 0))

        def boom(name, exc=exc):
            raise exc('the retire callback must not raise')
        queue._name_vacated = boom
        try:
            queue.collect_expired(cmd_dir, 90)  # must not propagate
        finally:
            queue._name_vacated = real
    assert all(not entry.exists() for entry in entries)


def test_the_refusal_key_separates_a_changed_change_time(tmp):
    """ctime is a key component: identities differing only in it are 2 keys."""
    service = _load_service('aliased_ctime_component')
    qdir = Path(tmp) / 'commands' / 'tok'
    qdir.mkdir(parents=True)
    victim = qdir / '0001_000001.json'
    _write_command(victim, 'x')
    base = service.command_queue._identity(os.lstat(victim))
    assert len(base) == 3, f'identity must carry st_ctime_ns: {base!r}'
    assert base[2] == os.lstat(victim).st_ctime_ns, f'ctime: {base!r}'
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        for bumped in (base, base[:2] + (base[2] + 1,)):
            service._refusal_once((f'queue:tok/{victim.name}', bumped),
                                  'q=x', 'synthetic')
    assert len(_refusals(captured)) == 2, _refusals(captured)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
