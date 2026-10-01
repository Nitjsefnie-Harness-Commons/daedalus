#!/usr/bin/env python3
"""Contracts for WHICH THREADS a journey's count covers, and for the
refusals that happen when a profile is not the shape the gate reads.
A mis-sorted profile summed as a whole tree is the one failure here
that produces a plausible number."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    _util,
    journeys,
)


def test_a_thread_the_profile_does_not_have_is_a_refusal(tmp):
    """A missing background thread is a failure, never a silent whole-tree sum.

    The failure this guards is the one the whole change exists to end: a
    profile the classifier could not read, summed as though every thread in
    it counted, and reported as a number nobody can read back.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py'}]
    kept, excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is not None and 'excludes' in failure, failure
    assert kept is None, kept
    assert excluded == ('front-end-import', 'uvicorn-serve'), excluded
    rows.append({'pid': 1, 'thread': 3, 'ir': 90_000_000,
                 'cmd': 'python3 server.py'})
    kept, excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is None, failure
    # Only the main thread is left: the serve thread is one of the two this
    # journey excludes, and the import is the other.
    assert kept == 430_000_000, kept


def test_a_thread_below_every_band_is_a_refusal_naming_it(tmp):
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 3, 'ir': 90_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 4, 'ir': 400,
             'cmd': 'python3 server.py'}]
    kept, _excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is not None, 'a profile this shape must not be summed'
    assert '400' in failure and 'thread 4' in failure, failure
    assert kept is None


def test_two_threads_in_one_band_cannot_be_told_apart(tmp):
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 3, 'ir': 90_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 4, 'ir': 95_000_000,
             'cmd': 'python3 server.py'}]
    kept, _excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is not None, 'two threads in the serve band are ambiguous'
    assert 'uvicorn-serve' in failure, failure
    assert kept is None


def test_a_profiles_threads_are_read_from_files_callgrind_writes(tmp):
    """The reader is driven by files in callgrind's own format.

    A slot is not a thread — callgrind reuses one when a thread exits — so
    what comes back is a slot and a cost, and nothing here may read a slot
    as one thread. The empty file is the one a process that cost nothing
    writes, and it carries no summary to read.
    """
    thread_classifier = _journey_contract.threads()
    directory = Path(tmp)
    (directory / 'cg.1').write_text('', encoding='utf-8')
    (directory / 'cg.1-01').write_text(
        'version: 1\npid: 1\ncmd:  python3 server.py\nthread: 1\n'
        'events: Ir\nsummary: 430000000\n', encoding='utf-8')
    (directory / 'cg.1-02').write_text(
        'version: 1\npid: 1\ncmd:  python3 server.py\nthread: 2\n'
        'events: Ir\nsummary: 3800000000\n', encoding='utf-8')
    rows, unread = thread_classifier.read(directory, 'cg')
    assert unread is None, unread
    assert [row['ir'] for row in rows] == [430000000, 3800000000], rows
    assert all(row['cmd'] == 'python3 server.py' for row in rows), rows
    # mcp-exec excludes only the import, so a profile carrying no serve
    # thread is a complete one for it — and the sum is the rest.
    kept, excluded, failure = thread_classifier.total_for(rows, 'mcp-exec')
    assert failure is None, failure
    assert kept == 430000000, kept
    assert excluded == ('front-end-import',), excluded
    # The other two need the serve thread, and a profile without one is a
    # refusal naming the role rather than a whole-tree sum.
    kept, excluded, failure = thread_classifier.total_for(
        rows, 'dashboard-fanout')
    assert kept is None and 'uvicorn-serve' in failure, failure
    assert excluded == ('front-end-import', 'uvicorn-serve'), excluded
    # A file carrying a summary and no `pid:` is NAMED, not dereferenced:
    # the reader that crashed on it would end the measurement with a
    # traceback saying nothing about which file was wrong.
    (directory / 'cg.1-03').write_text(
        'version: 1\nevents: Ir\nsummary: 90000000\n', encoding='utf-8')
    _read, unread = thread_classifier.read(directory, 'cg')
    assert unread is not None and 'cg.1-03' in unread, unread
    assert 'pid' in unread, unread
    kept, _excluded, failure = thread_classifier.total_for(
        [], 'mcp-exec', unread)
    assert kept is None and failure is unread, (kept, failure)


def test_every_journey_the_profiler_keeps_excludes_something(tmp):
    """No journey counts a front-end import, and only mcp-exec counts the
    serve thread — which is the whole of the per-journey rule."""
    del tmp
    threads = _journey_contract.threads()
    for name in journeys().NAMES:
        assert 'front-end-import' in threads.excluded_for(name), name
    assert threads.excluded_for('mcp-exec') == ('front-end-import',)
    assert threads.excluded_for('no-such-journey') == ()
    assert set(threads.ROLES) == {
        'front-end-import', 'uvicorn-serve', 'request', 'main'}


# ─── the step summary's own words ──────────────────────────────────────────


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())
