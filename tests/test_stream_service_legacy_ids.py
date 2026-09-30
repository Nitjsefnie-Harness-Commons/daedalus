#!/usr/bin/env python3
"""The delivery id a delivered legacy command file carries.

The drain removes a legacy file after writing its frame, and a removal that
fails is swallowed: the file stays and the next scan delivers it again. That
redelivery is the documented at-least-once outcome, and a queued command
absorbs it because the queue stamped a `_did` the consumer deduplicates on. A
legacy file is written by an external publisher and carries no such id, so
the id is derived from the object the drain read: the same on the
redelivery of one file, different for a second drop of the same command.
"""
import contextlib
import pathlib
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _service_loader import _load_service  # noqa: E402


@contextlib.contextmanager
def _refusing_unlink(name):
    """Make the removal of `name` fail, as a held-open file does on Windows.

    Nothing on POSIX refuses, so the refusal the drain has to survive is
    supplied here rather than waited for. The unlink of every other name —
    the suite's own temp files included — is left working.
    """
    real_unlink = pathlib.Path.unlink
    attempted = []

    def refusing(self, *args, **kwargs):
        if self.name == name:
            attempted.append(self.name)
            raise PermissionError('the removal failed')
        return real_unlink(self, *args, **kwargs)

    pathlib.Path.unlink = refusing
    try:
        yield attempted
    finally:
        pathlib.Path.unlink = real_unlink


def test_a_legacy_command_the_drain_cannot_remove_redelivers_one_id(tmp):
    """A failed removal redelivers, and the repeat carries the same id."""
    service = _load_service('stream_service_legacy_redelivery_id')
    legacy = Path(tmp) / 'tok_42.json'
    legacy.write_text('{"id":"held-open","code":"1"}', encoding='utf-8')
    frames = []

    with _refusing_unlink(legacy.name) as attempted:
        first = service.drain_legacy_file(
            legacy, '42', command_ttl=100, frame_writer=frames.append)
        second = service.drain_legacy_file(
            legacy, '42', command_ttl=100, frame_writer=frames.append)

    assert (first, second) == (1, 1), (first, second)
    assert attempted == [legacy.name, legacy.name], attempted
    assert legacy.exists(), 'a refused removal lost the command'
    dids = [frame.get('_did') for frame in frames]
    assert all(isinstance(did, str) and did for did in dids), frames
    # The consumer posts the `_did` back as a delivery id, where the bridge
    # refuses a component it will not accept as a file name.
    assert not service.path_safety.unsafe_component(dids[0]), dids
    assert len(set(dids)) == 1, dids


def test_two_identical_legacy_drops_carry_two_delivery_ids(tmp):
    """The id names the object, not the command body.

    Two drops of one command are two commands; an id taken from the payload
    would collapse them into one, and the second would never be delivered.
    """
    service = _load_service('stream_service_legacy_distinct_ids')
    first = Path(tmp) / 'tok_42.json'
    second = Path(tmp) / 'tok_43.json'
    body = '{"id":"same","code":"1"}'
    first.write_text(body, encoding='utf-8')
    second.write_text(body, encoding='utf-8')
    frames = []

    assert service.drain_legacy_file(
        first, '42', command_ttl=100, frame_writer=frames.append) == 1
    assert service.drain_legacy_file(
        second, '43', command_ttl=100, frame_writer=frames.append) == 1

    assert [frame.get('id') for frame in frames] == ['same', 'same'], frames
    dids = [frame.get('_did') for frame in frames]
    assert len(set(dids)) == 2, dids


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='streamlegacyids_')


if __name__ == '__main__':
    raise SystemExit(main())
