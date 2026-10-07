#!/usr/bin/env python3
"""Which job names the segment namespace spends, and which it refuses.

A job named K owns four names under the segments root: its directory `K`,
its record `K.json`, the marker `.{K}.json.dirty` and the temp
`.{K}.json.tmp`. The last two are the only ones another job name can land
on, and a caller that mints one parks a directory exactly where the
owner's write path has to put a file. The mint refuses those two shapes,
on shape rather than on a lookup, and answers the namespace collision's
own 409 so it is not an oracle.

The controls drive the real HTTP bridge, so a refusal is pinned at the
endpoint, and assert the status and body. None of them uses a clock. The
lock-side controls live in `test_segment_lock_stripes.py`, which needs the
injected seams these do not.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _segment_job_record import _job_record  # noqa: E402
from _segments import (BRIDGE_ENV, TOK, mint_job,  # noqa: E402
                       post_segment, seg_job)


def _store_one_segment(base, job, sig):
    """POST one admitted segment, answering (status, body)."""
    return post_segment(base, job, sig, '0', payload=b'abc')


def _load_store():
    """Import segment_store the way the bridge does, with no config set."""
    return _util.load(_util.ROOT / 'daedalus_bridge' / 'segment_store.py',
                      name='segname_store')


def test_a_bookkeeping_name_beside_its_job_is_refused(tmp):
    """The store's own names are refused: the marker and the record temp.

    A job named K spends four names under the segments root: its directory,
    its record `K.json`, the marker `.{K}.json.dirty` that `mark_dirty`
    writes and the temp `.{K}.json.tmp` that `write_usage` replaces from.
    Minted as a job of its own, either parks a directory exactly where the
    victim's write path has to put a file: the marker means every later
    write for the victim answers 500, and the temp means the record never
    lands, so the job's stored totals stay at zero and every later write
    rescans instead of trusting them. The refusal is the collision
    refusal's own answer, so a caller cannot read the reservation as an
    oracle for which names are in use.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        job = seg_job()
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        for reserved in (f'.{job}.json.dirty', f'.{job}.json.tmp'):
            status, body = mint_job(base, TOK, reserved)
            assert (status, body) == (
                409, {'error': 'job name unavailable'}), (status, body)
            assert not (Path(docroot) / 'segments' / reserved).exists()
        # A refused mint writes nothing, so it cannot leave the directory
        # that would have broken the victim; the victim itself still works.
        status, body = _store_one_segment(base, job, sig)
        assert status == 200, (status, body)
        record = _job_record(docroot, job)
        assert (record['stored_count'], record['stored_bytes']) == (1, 3), (
            record)
        mark = Path(docroot) / 'segments' / f'.{job}.json.dirty'
        assert not mark.exists(), 'the dirty mark was not cleared'


def test_a_bookkeeping_name_is_refused_with_no_owner_on_disk(tmp):
    """The reservation is structural: it holds with no victim minted.

    This is the order a conditional refusal gets wrong. With no `relay` on
    disk there is nothing to collide with, so a lookup admits the name —
    and the victim's own mint then succeeds into a namespace the squatter
    already holds, leaving every write for it answering 500. The shape is
    the same question whichever job is minted first, and it is the only
    form of the answer that cannot be raced by a concurrent mint.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        job = seg_job()
        reserved = f'.{job}.json.dirty'
        assert not (Path(docroot) / 'segments' / job).exists()
        status, body = mint_job(base, TOK, reserved)
        assert (status, body) == (409, {'error': 'job name unavailable'}), (
            status, body)
        assert not (Path(docroot) / 'segments' / reserved).exists()
        # And the victim that order was aimed at is unaffected.
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        status, body = _store_one_segment(base, job, body['sig'])
        assert status == 200, (status, body)


def test_the_reservation_refuses_only_the_two_bookkeeping_shapes(tmp):
    """A name that merely contains a dot, or has no job in it, still mints.

    The rule is the two bookkeeping shapes and nothing wider: a dotted job
    name, the two names with no job between the dot and the affix, a
    leading dot with a different affix, and a trailing temp affix without
    a leading one. Each of those is a name the layout never spends, so
    refusing any of them would be the bridge inventing a restriction.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        for job in (f'{seg_job()}.1', '.json.dirty', '.json.tmp',
                    f'.{seg_job()}.dirty', f'{seg_job()}.json.tmp'):
            status, body = mint_job(base, TOK, job)
            assert status == 200, (job, status, body)
            status, body = _store_one_segment(base, job, body['sig'])
            assert status == 200, (job, status, body)


def test_the_reservation_covers_exactly_the_names_the_layout_writes(tmp):
    """The refused names are the names the write path spends, and no others.

    One authority: the affix the refusal folds on is the affix the marker and
    the temp are built from, so a change to the layout cannot leave the mint
    writing a name the reservation no longer reserves. The two spellings are
    written out on purpose — that is the drift this pins, and a control that
    derived them from the constants would follow them anywhere.
    """
    store = _load_store()
    root = Path(tmp) / 'segments'
    root.mkdir()
    for job in ('relay', 'a.b', 'K-1'):
        assert store._dirty_path(root, job).name == f'.{job}.json.dirty', job
        assert (store.record_temp_path(root, job).name
                == f'.{job}.json.tmp'), job
        for name in (f'.{job}.json.dirty', f'.{job}.json.tmp'):
            assert store.reserved_bookkeeping_name(name), (
                f'the layout writes {name!r} and the mint would accept it')
        # And the shapes the layout never writes stay acceptable.
        assert not store.reserved_bookkeeping_name(f'{job}.json'), job
        assert not store.reserved_bookkeeping_name(job), job


def test_a_case_variant_suffixed_bookkeeping_name_is_refused(tmp):
    """`.{job}.json.DIRTY` and `.{job}.json.TMP` are the reserved names.

    The stripe key folds case, so on a case-insensitive filesystem this
    name and `.{job}.json.dirty` are one directory: the refused spelling
    and the accepted one would be the same entry, and the accepted one
    parks a directory exactly where `mark_dirty` has to write. #1167.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        job = seg_job()
        # The fullwidth stop is the third limb: NFKD folds U+FF0E to `.`, so
        # on a normalising filesystem this name is the same directory as
        # `.{job}.json.dirty`. A casefold WITHOUT NFKD would leave it
        # unmatched and admit it, which is what mutant D1 is.
        for reserved in (f'.{job}.json.DIRTY', f'.{job}.json.TMP',
                         f'.{job}\uff0ejson.DIRTY'):
            status, body = mint_job(base, TOK, reserved)
            assert (status, body) == (
                409, {'error': 'job name unavailable'}), (
                    reserved, status, body)
            assert not (Path(docroot) / 'segments' / reserved).exists(), (
                f'{reserved!r} was refused but its directory was written')


def test_a_case_variant_owner_alone_is_refused(tmp):
    """`.{JOB}.json.dirty` — the owner limb on its own, suffix spelled right.

    Pinned separately because fixing one limb and missing its twin is the
    mistake the review rounds caught on the fold controls: here the owner is
    the only thing case-varied, so a suffix-only fix would not touch it and a
    control written only for the suffix would not notice.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        job = seg_job()
        reserved = f'.{job.upper()}.json.dirty'
        status, body = mint_job(base, TOK, reserved)
        assert (status, body) == (409, {'error': 'job name unavailable'}), (
            reserved, status, body)


def test_the_fold_does_not_over_refuse_harmless_names(tmp):
    """The reservation refuses only the two shapes, folded or spelled wide.

    Names the layout never spends keep minting whatever the fold does: the
    two bookkeeping shapes with no owner between the dot and the affix,
    case-varied too — a fold applied to the length check rather than to
    the comparison is what would over-refuse — and a legitimate dotted
    name that is not one of the two shapes is a job, folded or not.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        for job in ('.json.dirty', '.JSON.DIRTY', '.json.tmp', '.JSON.TMP',
                    f'{seg_job()}.1', f'{seg_job().upper()}.Ts'):
            status, body = mint_job(base, TOK, job)
            assert status == 200, (job, status, body)
            status, body = _store_one_segment(base, job, body['sig'])
            assert status == 200, (job, status, body)


def test_a_job_whose_derived_names_exceed_the_ceiling_is_refused(tmp):
    """The marker is the longest derived form: job + 12 bytes."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, body = mint_job(base, TOK, 'x' * 229)
        assert (status, body) == (400, {'error': 'bad job'}), (status, body)
        assert not (Path(docroot) / 'segments' / ('x' * 229)).exists()


def test_the_longest_job_with_safe_derived_names_is_accepted(tmp):
    """228 bytes is the last job whose derived names all stay in bounds."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        status, body = mint_job(base, TOK, 'x' * 228)
        assert status == 200, (status, body)
        status, body = _store_one_segment(base, 'x' * 228, body['sig'])
        assert status == 200, (status, body)


def test_a_segment_write_for_an_over_derived_job_is_refused(tmp):
    """POST /segment admits through the same derived-name check."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        status, body = post_segment(base, 'x' * 229, 'sig', '0')
        assert status == 400, (status, body)
        assert json.loads(body)['error'] == 'invalid param', body


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='segjobnames_')


if __name__ == '__main__':
    raise SystemExit(main())
