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

    The expected id is recomputed from the file's own stat rather than
    written out, so every component of it is load-bearing — and the last one
    is the generation, which advances only when the drain vacates a name and
    so stands still for the redelivery this is about.
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
    stamp = os.stat(legacy)
    assert dids[0] == (
        f'legacy-{stamp.st_dev}-{stamp.st_ino}-{stamp.st_ctime_ns}-0'), dids
    # The consumer posts the `_did` back as a delivery id, which the bridge
    # refuses for a component it will not accept as a file name.
    assert not service.path_safety.unsafe_component(dids[0]), dids
    assert len(set(dids)) == 1, dids


def test_a_failed_removal_holds_the_generation_a_name_reached(tmp):
    """A redelivery at an advanced name keeps its generation, not a fresh one.

    At generation 0, "held" and "reset to 0" are one observation, so the
    control above cannot see a failed removal that resets. Here the name has
    been vacated once already, and 0 is the value a consumer is most likely
    still to hold there, because it is the first id the name ever carried.

    The identity is deliberately not re-derived from the file: the drain
    reads it from its own descriptor and this is a path lookup, and the two
    do not report the same incarnation on every interpreter — re-deriving it
    here is what `test_a_legacy_command_the_drain_cannot_remove_
    redelivers_one_id` does, and that one is green on every leg. What this
    control adds is the generation, and it reads that off the id itself.
    """
    service = _load_service('stream_service_legacy_generation_held')
    legacy = Path(tmp) / 'tok.json'
    legacy.write_text('{"id":"first","code":"1"}', encoding='utf-8')

    assert service.drain_legacy_file(
        legacy, None, command_ttl=100,
        frame_writer=lambda frame: None) == 1

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
    # One object, one id; and not the one a name this drain never vacated
    # would carry. Both are properties of the id the drain wrote, so neither
    # reads the filesystem a second way.
    assert len(set(dids)) == 1, dids
    assert dids[0].rsplit('-', 1)[1] == '1', dids


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
