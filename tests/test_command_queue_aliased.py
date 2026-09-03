#!/usr/bin/env python3
"""Aliased command names deliver nothing and are read through one descriptor.

A command candidate is opened through a descriptor checked against the name it
was found under, so two names for one object are not two delivery targets and
a name that stopped naming the object behind it is refused. The queue drain,
the legacy drain, poll and the expiry sweep share that helper, and a refused
candidate is left in place rather than unlinked.
"""
import json
import os
import pathlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import TOK, framer, put_command, stream_response  # noqa: E402


def _load_queue(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'command_queue.py', name=name)


def _load_service(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'stream_service.py', name=name)


def _write_command(path, identifier):
    path.write_text(
        json.dumps({'id': identifier, 'code': '1'}), encoding='utf-8')


def _hard_link(source, destination):
    try:
        os.link(source, destination)
    except (OSError, NotImplementedError):
        _util.skip('this filesystem will not hold a hard link')


def _symlink(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        _util.skip('this filesystem will not hold a symlink')


def test_a_plain_candidate_opens_and_reads(tmp):
    queue = _load_queue('aliased_plain_candidate')
    path = Path(tmp) / 'tok.json'
    _write_command(path, 'plain')

    stream, reason = queue.open_command_candidate(path)
    assert stream is not None, reason
    try:
        assert reason is None, reason
        assert json.loads(stream.read().decode('utf-8')) == {
            'id': 'plain', 'code': '1'}
    finally:
        stream.close()

    assert path.exists(), 'opening consumed the candidate'


def test_a_hard_linked_object_is_refused_and_left_in_place(tmp):
    queue = _load_queue('aliased_hard_linked_candidate')
    first = Path(tmp) / 'tok_dup.json'
    second = Path(tmp) / 'tok_other.json'
    _write_command(first, 'aliased')
    _hard_link(first, second)

    stream, reason = queue.open_command_candidate(first)
    if stream is not None:
        stream.close()

    assert stream is None, 'an object named twice was opened for delivery'
    assert reason, 'the refusal carries its reason'
    assert first.exists() and second.exists(), 'a refusal unlinked a name'


def test_a_symlinked_name_is_refused_without_following_it(tmp):
    queue = _load_queue('aliased_symlink_candidate')
    outside = Path(tmp) / 'outside.json'
    _write_command(outside, 'outside')
    link = Path(tmp) / 'tok_dup.json'
    _symlink(link, outside)

    stream, reason = queue.open_command_candidate(link)
    if stream is not None:
        stream.close()

    assert stream is None, 'a symlinked name was followed'
    assert reason, 'the refusal carries its reason'
    assert link.is_symlink(), 'a refusal unlinked the name'
    assert json.loads(outside.read_text(encoding='utf-8')) == {
        'id': 'outside', 'code': '1'}


def test_a_missing_name_is_absent(tmp):
    queue = _load_queue('aliased_missing_candidate')

    stream, reason = queue.open_command_candidate(Path(tmp) / 'absent.json')

    assert stream is None, 'a missing name produced a stream'
    assert reason is None, 'absence is not a refusal'


def test_a_broken_symlink_names_nothing(tmp):
    queue = _load_queue('aliased_broken_symlink_candidate')
    link = Path(tmp) / 'tok.json'
    _symlink(link, Path(tmp) / 'never-written')

    stream, reason = queue.open_command_candidate(link)
    if stream is not None:
        stream.close()

    assert stream is None, (stream, reason)
    assert link.is_symlink(), 'a refusal unlinked the name'


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
    with _util.bridge(tmp, output=served) as (base, docroot):
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
    with _util.bridge(tmp, output=served) as (base, docroot):
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


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
