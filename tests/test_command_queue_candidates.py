#!/usr/bin/env python3
"""A command candidate is opened through a descriptor checked against its name.

This module is what a candidate IS: the reading of one name, opened and refused
without following it, so two names for one object are not two delivery targets.
What the bridge does about a refusal — recording it once per object, retiring a
name the sweep vacates — lives with the registry rows in
``test_command_queue_aliased``.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _command_candidates import (  # noqa: E402
    _hard_link, _load_queue, _symlink, _write_command)


def test_a_plain_candidate_opens_and_reads(tmp):
    queue = _load_queue('aliased_plain_candidate')
    path = Path(tmp) / 'tok.json'
    _write_command(path, 'plain')

    stream, reason, _ = queue.open_command_candidate(path)
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

    stream, reason, _ = queue.open_command_candidate(first)
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

    stream, reason, _ = queue.open_command_candidate(link)
    if stream is not None:
        stream.close()

    assert stream is None, 'a symlinked name was followed'
    assert reason, 'the refusal carries its reason'
    assert link.is_symlink(), 'a refusal unlinked the name'
    assert json.loads(outside.read_text(encoding='utf-8')) == {
        'id': 'outside', 'code': '1'}


def test_a_missing_name_is_absent(tmp):
    queue = _load_queue('aliased_missing_candidate')

    stream, reason, _ = queue.open_command_candidate(
        Path(tmp) / 'absent.json')

    assert stream is None, 'a missing name produced a stream'
    assert reason is None, 'absence is not a refusal'


def test_a_broken_symlink_names_nothing(tmp):
    queue = _load_queue('aliased_broken_symlink_candidate')
    link = Path(tmp) / 'tok.json'
    _symlink(link, Path(tmp) / 'never-written')

    stream, reason, _ = queue.open_command_candidate(link)
    if stream is not None:
        stream.close()

    assert stream is None, (stream, reason)
    assert link.is_symlink(), 'a refusal unlinked the name'


def test_a_directory_named_like_an_entry_is_refused(tmp):
    queue = _load_queue('aliased_directory_candidate')
    entry = Path(tmp) / 'tok' / '0000000000001_000001.json'
    entry.parent.mkdir(parents=True)
    entry.mkdir()

    stream, reason, _ = queue.open_command_candidate(entry)
    if stream is not None:
        stream.close()

    # Refusal and retention are the contract on every platform. Which arm
    # names the reason is not: a POSIX open returns a descriptor for a
    # directory and the regular-file check refuses it, while Windows refuses
    # the open itself.
    assert stream is None, 'a directory was opened for delivery'
    assert reason, 'the refusal carries no reason'
    if os.name == 'nt':
        assert reason.startswith('cannot open'), reason
    else:
        assert reason == 'candidate is not a regular file', reason
    assert entry.is_dir(), 'a refusal removed the candidate'


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
