#!/usr/bin/env python3
"""Contracts for WHICH THREADS a journey's count covers, and for the
refusals that happen when a profile is not the shape the gate reads.
A mis-sorted profile summed as a whole tree is the one failure here
that produces a plausible number."""
import contextlib
import contextvars
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    _util,
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


def test_a_worker_is_read_by_its_total_down_to_the_last_rung(tmp):
    """The ladder `role_of` reads a background thread by, rung by rung.

    Each rung is asserted at its own boundary rather than by one value from
    the middle of a band, because a `>=` written as `>` moves a boundary by
    one instruction and a value in the middle cannot see it. The last rung
    is the one with no role at all: a thread under `REQUEST_FROM` never
    entered the interpreter, so it has no rung to sit in and `classify`
    refuses the profile instead of including it quietly.
    """
    del tmp
    threads = _journey_contract.threads()
    role_of = threads.role_of
    assert role_of(threads.REQUEST_FROM - 1, 7) is None
    assert role_of(threads.REQUEST_FROM, 7) == threads.REQUEST
    assert role_of(threads.SERVE_FROM, 7) == threads.SERVE
    assert role_of(threads.IMPORT_FROM, 7) == threads.IMPORT


def test_the_main_thread_is_main_at_any_size(tmp):
    """The property the whole exclusion rests on, with the contrast it needs.

    `role_of` reads thread 1 first and whatever its total, so a journey's own
    work counts by construction and a worker's total never decides whether
    the work counts. The totals ABOVE the import band are the ones that
    matter: `mcp-exec`'s round trip measured 1,311,350,558, which a
    size-ordered classifier reads as the front end's import — and the same
    call on a non-main thread still reads it that way, which is the whole of
    issue 1461.
    """
    del tmp
    threads = _journey_contract.threads()
    for total in (999, threads.REQUEST_FROM, threads.SERVE_FROM,
                  threads.IMPORT_FROM - 1, threads.IMPORT_FROM,
                  1_311_350_558, 3_800_000_000):
        assert threads.role_of(total, 1) == threads.MAIN, total
    assert threads.role_of(1_311_350_558, 2) == threads.IMPORT


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


def test_two_threads_in_a_band_the_journey_does_not_exclude_are_counted(
        tmp):
    """The half of the refusal that has no case, and the half that ships.

    `classify` refuses a profile carrying two threads in one band, and that
    is right only where the journey EXCLUDES the band: there the gate has
    to pick which of the two is the background it drops, and nothing in the
    profile says which. In a band it does not exclude, every thread counts
    and two of them is two threads of work.

    This is the shape `net-capture` produces: its request thread runs to
    billions and lands in the import band beside the bridge's own bootstrap
    import. Deleting the `if role not in excluded: continue` guard restores
    the original defect, refuses this profile, and `journey_counters` then
    marks the whole counter unavailable — so every journey in the run reads
    unmeasured, and it does so behind a suite that is otherwise green. The
    journey under callgrind is the expensive path nobody runs locally, which
    is the whole reason this is pinned here.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 959_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 3, 'ir': 2_100_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 4, 'ir': 87_000_000,
             'cmd': 'python3 server.py'}]
    kept, excluded, failure = threads.total_for(rows, 'net-capture')
    assert failure is None, failure
    assert kept is not None, 'two threads in a counted band is not a refusal'
    # Derived from the table rather than written out, so this pins the
    # BEHAVIOUR and not the value of any exclusion list: the table is data,
    # and a test that asserted its contents is a second place for them to
    # drift.
    expected = sum(row['ir'] for row in rows
                   if threads.role_of(row['ir'], row['thread'])
                   not in excluded)
    assert kept == expected, (kept, expected)


def test_two_threads_in_an_excluded_band_are_still_a_refusal_naming_it(tmp):
    """The other half, which the widened rule must not have cost.

    The guard now has two halves and a reader cannot tell which one is
    load-bearing, so both are pinned. An EXCLUDED band with two threads in
    it is still the ambiguity the refusal exists for: the gate has to pick
    which of them it is dropping, and guessing would make the number mean
    something other than the journey.

    The journey is planted rather than named, so this pins no value of
    `EXCLUDED` either.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 3, 'ir': 2_100_000_000,
             'cmd': 'python3 server.py'}]
    planted = {**threads.EXCLUDED, 'planted-journey': (threads.IMPORT,)}
    with _journey_contract.planting(threads, EXCLUDED=planted):
        kept, excluded, failure = threads.total_for(rows, 'planted-journey')
    assert failure is not None, 'two threads in an excluded band must refuse'
    assert threads.IMPORT in failure, failure
    assert kept is None, kept
    # `total_for` hands back an EMPTY exclusion list beside a classify
    # failure, not the journey's own: the count never started, so what it
    # would have dropped is not a fact about this run.
    assert excluded == (), excluded


def test_one_thread_in_a_band_the_journey_does_not_exclude_just_counts(tmp):
    """The plain case the widened rule has to leave exactly as it was.

    One thread in a band nobody excludes is not an ambiguity and never was;
    this is the contrast the refusal above needs, so that a reader can see
    the guard is about COUNT and not about the number of threads.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py'},
            {'pid': 1, 'thread': 3, 'ir': 90_000_000,
             'cmd': 'python3 server.py'}]
    planted = {**threads.EXCLUDED, 'planted-journey': (threads.IMPORT,)}
    with _journey_contract.planting(threads, EXCLUDED=planted):
        kept, _excluded, failure = threads.total_for(rows, 'planted-journey')
    assert failure is None, failure
    assert kept == 430_000_000 + 90_000_000, kept


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


def test_a_profile_missing_any_of_its_header_lines_is_named(tmp):
    """Three fields, one refusal, and the same for each.

    `pid:` and `cmd:` are obvious. `thread:` is the one that must not
    default: 1 is MAIN, and MAIN is never excluded, so a defaulted thread is
    a thread the gate would KEEP without ever having said so — the one
    asymmetry a reader cannot see in a number.
    """
    thread_classifier = _journey_contract.threads()
    directory = Path(tmp)
    header = 'pid: 1\ncmd:  python3 server.py\nthread: 2\n'
    for omitted, fragment in (('pid: 1\n', 'no pid: line'),
                              ('cmd:  python3 server.py\n', 'no cmd: line'),
                              ('thread: 2\n', 'no thread: line')):
        for stale in directory.glob('cg.*'):
            stale.unlink()
        (directory / 'cg.1-01').write_text(
            'version: 1\n' + header.replace(omitted, '')
            + 'events: Ir\nsummary: 90000000\n', encoding='utf-8')
        rows, unread = thread_classifier.read(directory, 'cg')
        assert unread is not None, (omitted, rows)
        assert fragment in unread, (omitted, unread)
        assert 'cg.1-01' in unread, (omitted, unread)
        kept, _excluded, failure = thread_classifier.total_for(
            [], 'mcp-exec', unread)
        assert kept is None and failure is unread, (kept, failure)


def test_every_journey_says_which_roles_it_stops_counting(tmp):
    """`EXCLUDED` covers every name, and no entry excludes nothing.

    Nothing else asserts the coverage: the artefact refuses an empty list and
    `journey_rebaseline` refuses a measurement missing one, so a journey
    added to `_journeys.NAMES` and forgotten here is discovered by a CI run
    rather than by this suite. Every role is also checked to be one
    `role_of` can return, because a name that is not a role excludes nothing
    while looking like it excludes something.
    """
    del tmp
    threads = _journey_contract.threads()
    for name in _journey_contract.journeys().NAMES:
        assert name in threads.EXCLUDED, name
        roles = threads.excluded_for(name)
        assert roles, name
        assert set(roles) <= set(threads.ROLES), (name, roles)


def test_rendering_of_runs_a_journey_on_the_main_thread(tmp):
    """The line that decides the thread, driven rather than read.

    Every journey is measured through `rendering_of`, so a journey that put
    its own work on the main thread would still lose it to a `rendering_of`
    that called it on a worker — the shape issue 1461 describes, and one a
    test of `mcp_exec` alone cannot see. The journey function and the bridge
    are both planted: the recorder below stands where `mcp_exec` stands and
    reports the thread it was called from, and the planted bridge is a
    context manager rather than a process, so nothing is spawned and nothing
    is dialled to reach it.
    """
    del tmp
    journeys = _journey_contract.journeys()
    called = []

    def run(base, docroot):
        del docroot
        called.append((base, threading.current_thread()))
        return {'journey': 'mcp-exec'}

    @contextlib.contextmanager
    def bridge(_directory, env=None, await_mcp=False):
        # The double fails on what it does not model. `await_mcp=True` is
        # what makes a count comparable (see `rendering_of`), and a
        # `rendering_of` that stopped passing it would leave this suite
        # green; `env` carries the planted journey's own token, so a bridge
        # spawned under another credential is refused here too.
        assert await_mcp is True, await_mcp
        assert env == {'DAEDALUS_TOKEN': 'planted', 'TOKEN': ''}, env
        yield 'http://127.0.0.1:1', None

    with _journey_contract.planting(
            journeys, JOURNEYS={'mcp-exec': ('planted', run)}), \
            _journey_contract.planting(journeys._util, bridge=bridge):
        rendering = journeys.rendering_of('mcp-exec')
    assert rendering == {'journey': 'mcp-exec'}, rendering
    assert called and called[0][1] is threading.main_thread(), called


def test_the_mcp_round_trip_runs_on_the_journeys_main_thread(tmp):
    """The placement issue 1461 turns on, recorded rather than read.

    `rendering_of` calls a journey on the main thread (the test above
    drives that), and `role_of` reads thread 1 as `MAIN` whatever its total,
    so the round trip counts wherever the journey puts it — provided the
    journey puts it there, which is what this one pins. The stand-in front
    end records the thread each tool call was made from and refuses any
    payload that is not the one the journey is supposed to send, because a
    stub that swallows its arguments cannot tell a journey that changed
    what it does from one that only changed where it does it.
    """
    del tmp
    journeys = _journey_contract.journeys()
    seen = []

    class FrontEnd:
        """The two tools `mcp_exec` calls, and the thread each ran on."""

        _token = contextvars.ContextVar('journey_thread_test', default='')

        async def exec(self, **sent):
            seen.append(('exec', threading.current_thread()))
            assert sent == {'tab_id': journeys.MCP_TAB,
                            'cmd_id': journeys.MCP_COMMAND_ID,
                            'code': journeys.MCP_CODE, 'wait': False}, sent
            return {'command': {'id': journeys.MCP_COMMAND_ID,
                                '_did': 'journey-did'}}

        async def result(self, **read):
            seen.append(('result', threading.current_thread()))
            assert read == {'tab_id': journeys.MCP_TAB}, read
            return {'id': journeys.MCP_COMMAND_ID,
                    'tabId': journeys.MCP_TAB,
                    'value': journeys.MCP_RESULT, 'error': None}

    def load(_base):
        return FrontEnd()

    def post(_url, body):
        assert body == {'token': journeys._mcp_load.TOK,
                        'tabId': journeys.MCP_TAB,
                        'id': journeys.MCP_COMMAND_ID,
                        'result': journeys.MCP_RESULT, 'error': None,
                        'ts': 1, '_did': 'journey-did'}, body
        return 200, b'{}'

    with _journey_contract.planting(journeys, _load_front_end=load), \
            _journey_contract.planting(journeys._util, post_json=post):
        rendering = journeys.mcp_exec('http://127.0.0.1:1', None)
    assert rendering['journey'] == 'mcp-exec', rendering
    assert {'exec', 'result'} <= {name for name, _thread in seen}, seen
    assert all(thread is threading.main_thread()
               for _name, thread in seen), seen


def test_the_mcp_front_end_is_loaded_off_the_journeys_main_thread(tmp):
    """The one thing this journey must NOT count, kept off the main thread.

    A main thread is read as `MAIN` whatever its total, so an import on one
    is the journey's own work by every rule the bands apply. Loading it on a
    worker of its own and waiting for it is the whole of the asymmetry, and
    it costs the count nothing: the module the tool call reaches is the same
    one either way.
    """
    del tmp
    journeys = _journey_contract.journeys()
    loaded = []

    def load(_base):
        loaded.append(threading.current_thread())
        return 'front end'

    with _journey_contract.planting(journeys._mcp_load, _load_mcp=load):
        front = journeys._load_front_end('http://127.0.0.1:1')
    assert front == 'front end', front
    assert loaded and loaded[0] is not threading.main_thread(), loaded


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())
