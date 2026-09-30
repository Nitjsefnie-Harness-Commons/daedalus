#!/usr/bin/env python3
"""The delivery id a delivered legacy command file carries.

The drain removes a legacy file after writing its frame, and a removal that
fails is swallowed, so the next scan delivers it again — the documented
at-least-once outcome. A queued command absorbs that redelivery because the
queue stamped a `_did`; a legacy file is published externally and carries
none, so the id is derived from the object the drain read.
"""
import contextlib
import os
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
    supplied here rather than waited for.
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
    """A failed removal redelivers, and the repeat carries the same id.

    The id's shape and the values behind it are both pinned, because the
    first review round found a mutant that deleted a component and passed:
    every component has to be load-bearing, and the generation is the one
    that stands still here — it advances when the object at the name
    changes or the drain vacates it, and a failed removal is neither.
    """
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
    # Every component, and the values behind them, but never a comparison
    # between two ways of reading one object: the drain takes its identity
    # from a descriptor and a path lookup does not report the same one on
    # every interpreter — Windows 3.12 does not, and a control that needs a
    # platform to agree with itself is not pinning this. So the shape is
    # checked first and on its own, where deleting a component or keeping
    # only one of them is a change of arity and dies however the platform
    # reads the file.
    parts = dids[0].split('-')
    assert parts[0] == 'legacy' and len(parts) == 4, dids
    assert all(part.isdigit() for part in parts[1:]), dids
    assert parts[3] == '0', dids
    # The values, from the same mechanism the drain reads them by: its own
    # descriptor, not the name.
    handle = os.open(legacy, os.O_RDONLY)
    try:
        by_descriptor = os.fstat(handle)
    finally:
        os.close(handle)
    assert parts[1:3] == [
        str(by_descriptor.st_dev), str(by_descriptor.st_ino)], dids
    # The consumer posts the `_did` back as a delivery id, which the bridge
    # refuses for a component it will not accept as a file name.
    assert not service.path_safety.unsafe_component(dids[0]), dids
    assert len(set(dids)) == 1, dids


def test_a_failed_removal_holds_the_generation_a_name_reached(tmp):
    """A redelivery at an advanced name keeps its generation, not a fresh one.

    At the first generation, "held" and "reset" are one observation, so the
    control above cannot see a failed removal that resets. Here the name has
    already changed hands once, and its first generation is the value a
    consumer is most likely still to hold there, because it is the first id
    that object ever carried. A failure is the removal that did NOT happen,
    so neither signal advances: the object at the name is unchanged, and
    this drain never vacated it.
    """
    service = _load_service('stream_service_legacy_generation_held')
    legacy = Path(tmp) / 'tok.json'
    legacy.write_text('{"id":"first","code":"1"}', encoding='utf-8')
    first = []
    assert service.drain_legacy_file(
        legacy, None, command_ttl=100, frame_writer=first.append) == 1

    legacy.write_text('{"id":"second","code":"1"}', encoding='utf-8')
    frames = []
    with _refusing_unlink(legacy.name):
        assert service.drain_legacy_file(
            legacy, None, command_ttl=100,
            frame_writer=frames.append) == 1
        assert service.drain_legacy_file(
            legacy, None, command_ttl=100,
            frame_writer=frames.append) == 1

    dids = [frame.get('_did') for frame in frames]
    # One object, one id; and not the one this name started from. Both are
    # read off the id the drain wrote, so neither reads the filesystem a
    # second way.
    assert len(set(dids)) == 1, dids
    held = dids[0].rsplit('-', 1)[1]
    started = first[0].get('_did').rsplit('-', 1)[1]
    assert held != started, (held, started)


def test_a_publisher_replacing_an_unremovable_file_gets_another_id(tmp):
    """A different file at a name is a different command, vacate or not.

    This is the case a vacate cannot report: the removal failed, so this
    drain never emptied the name, and only the object standing at it says
    anything changed. It cannot be a recycled inode either — a file that is
    still there holds its own — so the generation is the only thing standing
    between the two commands, and a reader that saw one id for both would
    drop the second.
    """
    service = _load_service('stream_service_legacy_replaced')
    legacy = Path(tmp) / 'tok.json'
    in_progress = Path(tmp) / '.tok.json.tmp'
    legacy.write_text('{"id":"first","code":"1"}', encoding='utf-8')
    frames = []

    with _refusing_unlink(legacy.name):
        assert service.drain_legacy_file(
            legacy, None, command_ttl=100,
            frame_writer=frames.append) == 1
        in_progress.write_text(
            '{"id":"second","code":"1"}', encoding='utf-8')
        os.replace(in_progress, legacy)
        assert service.drain_legacy_file(
            legacy, None, command_ttl=100,
            frame_writer=frames.append) == 1

    assert [frame.get('id') for frame in frames] == ['first', 'second'], frames
    dids = [frame.get('_did') for frame in frames]
    assert all(isinstance(did, str) and did for did in dids), frames
    assert len(set(dids)) == 2, dids


def test_sequential_drops_at_one_name_carry_distinct_ids(tmp):
    """One name re-dropped is as many commands as drops.

    Published the way `AGENTS.md` documents — sibling `.tmp`, then
    `os.replace` — and drained between each, so every drop is delivered
    before the next takes the name. What a file carries is not a generation:
    a new file can be handed the inode the vacated one had, and its change
    time moves only when the clock does, so consecutive drops can be
    indistinguishable. The consumer's ledger is what decides, and an id it
    already holds is a command that never runs.
    """
    service = _load_service('stream_service_legacy_sequential')
    name = Path(tmp) / 'tok.json'
    in_progress = Path(tmp) / '.tok.json.tmp'
    frames = []

    for index in range(8):
        in_progress.write_text(
            '{"id":"drop-%d","code":"1"}' % index, encoding='utf-8')
        os.replace(in_progress, name)
        assert service.drain_legacy_file(
            name, None, command_ttl=100, frame_writer=frames.append) == 1

    dids = [frame.get('_did') for frame in frames]
    assert [frame.get('id') for frame in frames] == [
        f'drop-{index}' for index in range(8)], frames
    assert len(set(dids)) == 8, dids


def test_the_untagged_legacy_frame_carries_a_delivery_id(tmp):
    """The stamp is not under the `chromeTab` branch.

    `serve_stream`'s broadcast call sites pass no tab, so a stamp indented
    under `if chrome_tab is not None` would leave the one namespace every
    stream reads with nothing to deduplicate on — the defect this change
    exists to remove, restored for exactly those frames.
    """
    service = _load_service('stream_service_legacy_untagged')
    legacy = Path(tmp) / 'tok.json'
    legacy.write_text('{"id":"broadcast","code":"1"}', encoding='utf-8')
    frames = []

    assert service.drain_legacy_file(
        legacy, None, command_ttl=100, frame_writer=frames.append) == 1

    assert '_did' in frames[0], frames
    assert frames[0]['_did'].startswith('legacy-'), frames


def test_two_identical_legacy_drops_carry_two_delivery_ids(tmp):
    """The id names the object, not the command body.

    Two drops of one command are two commands; an id taken from the payload
    would collapse them, and the second would never be delivered.
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


def test_an_id_the_drain_did_not_mint_is_kept_only_when_it_is_one(tmp):
    """A publisher's own `_did` is its delivery identity, when it is one.

    Two values are not one, and both cost the command its identity. A value
    of another type is not: the consumer's dedup ledger is a set of strings
    and the result route drops anything else. Neither is the empty string:
    the extension's frame handler tests `_did` for truth before recording
    it, so an empty one deduplicates nothing and is never posted back.
    """
    service = _load_service('stream_service_legacy_own_id')
    kept = Path(tmp) / 'tok_42.json'
    kept.write_text('{"id":"mine","_did":"publisher-1"}', encoding='utf-8')
    replaced = Path(tmp) / 'tok_43.json'
    replaced.write_text('{"id":"mine","_did":7}', encoding='utf-8')
    blank = Path(tmp) / 'tok_44.json'
    blank.write_text('{"id":"mine","_did":""}', encoding='utf-8')
    frames = []

    assert service.drain_legacy_file(
        kept, '42', command_ttl=100, frame_writer=frames.append) == 1
    assert service.drain_legacy_file(
        replaced, '43', command_ttl=100, frame_writer=frames.append) == 1
    assert service.drain_legacy_file(
        blank, '44', command_ttl=100, frame_writer=frames.append) == 1

    assert frames[0].get('_did') == 'publisher-1', frames
    assert frames[1].get('_did', '').startswith('legacy-'), frames
    assert frames[2].get('_did', '').startswith('legacy-'), frames


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='streamlegacyids_')


if __name__ == '__main__':
    raise SystemExit(main())
