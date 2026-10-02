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

# What was MEASURED on this box, spelled out here rather than read back
# from the module that holds it. `_carries` requires every member, so
# deleting one WIDENS a signature toward calling a request thread the role
# it names — and a control that reads its expectation from the subject
# cannot see that. These two tuples are the evidence the signatures are
# allowed to be exactly.
#
# The first: exclusive to a thread running a CPython asyncio event loop and
# to no other thread in a profile of an event loop and a blocking socket
# worker. The second: exclusive to a thread that imported the MCP front end
# and to no other thread in a profile of that import and of one unrelated
# stdlib import.
MEASURED_LOOP_SYMBOLS = (
    'FutureIter_iternext',
    'FutureObj_dealloc',
    'FutureObj_finalize',
    'PyGen_am_send',
    'TaskObj_dealloc',
    'TaskObj_finalize',
    'TaskStepMethWrapper_dealloc',
)
MEASURED_FRONT_END_SYMBOLS = ('PyInit_pydantic_core',)
# What a thread that imports ANY module carries, and what the front end's
# import has to be told apart from. A signature of these would call every
# request thread that imported anything the front end's import, and the
# journeys that exclude it would drop the journey's own work.
GENERIC_IMPORT_SYMBOLS = ('_PyImport_RunModInitFunc', 'import_find_and_load')


def test_the_reader_collects_the_names_a_thread_declared(tmp):
    """`fn=(id) name` names a symbol; `fn=(id)` only repeats one.

    A thread's identity is read from what it executed, so the reader has to
    keep the names and nothing else. An unnamed repeat carries the id of a
    name declared earlier in the same file, and inventing a name for it
    would put a symbol in the set that no call ever named.
    """
    classifier = _journey_contract.threads()
    directory = Path(tmp)
    (directory / 'cg.1-01').write_text(
        'version: 1\npid: 1\ncmd:  python3 server.py\nthread: 2\n'
        'events: Ir\nsummary: 90000000\n'
        'fn=(1) first_symbol\n'
        '1 12\n'
        'fn=(1)\n'
        '2 14\n'
        'fn=(2) second_symbol\n'
        '3 16\n', encoding='utf-8')
    rows, unread = classifier.read(directory, 'cg')
    assert unread is None, unread
    assert rows[0]['names'] == frozenset({'first_symbol', 'second_symbol'}), \
        rows[0]['names']


def test_a_repeated_symbol_name_still_matches_a_signature(tmp):
    """Callgrind suffixes a CLONE of a symbol, and a clone is the symbol.

    Two threads in one profile run the same C function and callgrind
    writes one of them as `import_find_and_load'2`. A reader that compares
    names literally never sees that thread's module execution, and the
    bootstrap import thread stops being the import thread.
    """
    classifier = _journey_contract.threads()
    directory = Path(tmp)
    body = ''.join(f"fn=({index}) {name}\n1 12\n"
                   for index, name in enumerate(
                       classifier.MODULE_INIT_SIGNATURE, start=1))
    (directory / 'cg.1-01').write_text(
        'version: 1\npid: 1\ncmd:  python3 server.py\nthread: 2\n'
        'events: Ir\nsummary: 90000000\n'
        + body.replace('import_find_and_load\n',
                       "import_find_and_load'2\n"), encoding='utf-8')
    rows, unread = classifier.read(directory, 'cg')
    assert unread is None, unread
    assert classifier.role_of(2, rows[0]['names']) == classifier.IMPORT


def test_the_event_loop_signature_names_the_serve_thread(tmp):
    """The loop is told from what CPython runs to advance it.

    A profile holds no Python-level name at all, so this is the C symbol
    set an asyncio event loop is made of, and the set is pinned to the
    measured one member for member: a member removed from the module is a
    member the module no longer requires, which widens the role rather
    than narrowing it.
    """
    del tmp
    classifier = _journey_contract.threads()
    assert (sorted(MEASURED_LOOP_SYMBOLS)
            == classifier.SIGNATURES[classifier.SERVE]), \
        classifier.SIGNATURES[classifier.SERVE]
    assert (classifier.role_of(2, frozenset(MEASURED_LOOP_SYMBOLS))
            == classifier.SERVE)


def test_the_module_init_signature_names_the_import_thread(tmp):
    """The FRONT END's import, told by a module only the front end pulls.

    `daedalus_mcp/*.py` is pure Python and has no `PyInit_<name>` of its
    own, but `mcp==2.2.0` pulls pydantic v2, whose compiled core is a C
    extension. Measured on this box: a worker that imported
    `daedalus_mcp.server` carries `PyInit_pydantic_core` and a worker that
    imported one unrelated stdlib module does not, and neither does the
    main thread.
    """
    del tmp
    classifier = _journey_contract.threads()
    assert (sorted(MEASURED_FRONT_END_SYMBOLS)
            == classifier.SIGNATURES[classifier.IMPORT]), \
        classifier.SIGNATURES[classifier.IMPORT]
    assert (classifier.role_of(2, frozenset(MEASURED_FRONT_END_SYMBOLS))
            == classifier.IMPORT)


def test_a_request_thread_that_imports_one_module_is_still_a_request_thread(
        tmp):
    """The finding that made the generic signature unshippable.

    A request thread that imports anything at all is the thread the
    journeys that exclude the import silently drop, and the number it
    produces is plausible: the reviewer drove the gate and `mcp-exec`
    answered `kept=400000000, failure=None` with 5,000 instructions of
    journey work gone and nothing said. That is issue 1466's own shape
    through the new door — a journey loses its own work with no refusal —
    so the signature has to name the front end rather than imports.
    """
    del tmp
    classifier = _journey_contract.threads()
    # A COMPLETE mcp-exec profile: the main thread, the front end's own
    # bootstrap import, and a request thread that imported something of its
    # own. The front end's thread is there so the journey's exclusion is
    # satisfied and the count starts, which is what makes the request
    # thread's fate the question.
    rows = [{'pid': 1, 'thread': 1, 'ir': 400_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(MEASURED_FRONT_END_SYMBOLS)},
            {'pid': 1, 'thread': 3, 'ir': 5_000,
             'cmd': 'python3 server.py',
             'names': frozenset(GENERIC_IMPORT_SYMBOLS)}]
    kept, excluded, failure = classifier.total_for(rows, 'mcp-exec')
    assert failure is None, failure
    assert excluded == ('front-end-import',), excluded
    assert kept == 400_000_000 + 5_000, kept


def test_presence_is_not_exclusivity(tmp):
    """Carrying a symbol is not the role; carrying the signature is.

    Driven member by member from the measured sets rather than from the
    module's: a signature missing one member of these is a signature whose
    role is claimed by one symbol fewer, and the role is an excluded one.
    """
    del tmp
    classifier = _journey_contract.threads()
    for measured in (MEASURED_LOOP_SYMBOLS, MEASURED_FRONT_END_SYMBOLS):
        for member in measured:
            without = set(measured) - {member}
            assert classifier.role_of(2, without) == classifier.REQUEST, (
                member, without)
    assert (classifier.role_of(
        2, frozenset(MEASURED_LOOP_SYMBOLS) | frozenset(
            MEASURED_FRONT_END_SYMBOLS)) == classifier.SERVE), \
        'a thread carrying both is the loop, which is the more specific'


def test_the_main_thread_is_main_whatever_it_executed(tmp):
    """The property the whole exclusion rests on, with the contrast it needs.

    `role_of` reads thread 1 first and whatever its total and its names, so
    a journey's own work counts by construction. The totals ABOVE what a
    size-ordered classifier would have called the front end's import are
    the ones that matter: `mcp-exec`'s round trip measured 1,311,350,558
    and `command-round-trip`'s own request thread measured 3,548,079,
    which a band read moves out of the count (issue 1466).
    """
    del tmp
    classifier = _journey_contract.threads()
    for names in (frozenset(),
                  frozenset(classifier.EVENT_LOOP_SIGNATURE),
                  frozenset(classifier.MODULE_INIT_SIGNATURE)):
        for total in (999, 3_548_079, 1_311_350_558, 3_800_000_000):
            assert classifier.role_of(1, names) == classifier.MAIN, \
                (total, names)


def test_a_request_thread_is_a_request_thread_at_any_size(tmp):
    """Issue 1466's regression: a role that moves when the work grows.

    A per-connection request thread measured 3,548,079 instructions, which
    is 2.82x under the floor a band read held the serve thread to. Grow
    that thread past it and a band-ordered classifier calls it
    `uvicorn-serve` — a role `command-round-trip` and `dashboard-fanout`
    exclude — so the journey's own work silently leaves the count. The
    role here comes from what the thread executed and nothing else, so no
    size moves it, and the whole ladder the old ladder test walked is gone
    with the ladder.
    """
    del tmp
    classifier = _journey_contract.threads()
    empty = frozenset()
    for total in (classifier.REQUEST_FROM, 3_548_079, 10_000_000,
                  3_548_079_000, 1_000_000_000, 90_000_000_000):
        assert classifier.role_of(7, empty) == classifier.REQUEST, total
    assert classifier.role_of(7, empty) != classifier.SERVE
    assert classifier.role_of(7, empty) != classifier.IMPORT
    # The whole path as well, because a band put back anywhere — in the
    # role, in the reader, in the sum — is the defect and not a spelling of
    # it. The import thread is beside this one so the profile is one a
    # journey could have excluded something from.
    for total in (3_548_079, 90_000_000_000):
        rows = [{'pid': 1, 'thread': 1, 'ir': 4_000_000,
                 'cmd': 'python3 server.py', 'names': empty},
                {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
                 'cmd': 'python3 server.py',
                 'names': frozenset(
                     classifier.SIGNATURES[classifier.IMPORT])},
                {'pid': 1, 'thread': 3, 'ir': total,
                 'cmd': 'python3 server.py', 'names': empty}]
        roles, failure = classifier.classify(rows, (classifier.IMPORT,))
        assert failure is None, (total, failure)
        assert roles[(1, 3)] == classifier.REQUEST, (total, roles)


def test_a_thread_with_no_signature_is_a_request_thread(tmp):
    """The fall-through is stated, and it is the counted side.

    A thread no signature claims is not an unreadable profile: it is a
    worker doing the journey's own work, and no journey excludes `request`.
    So it counts. Refusing here instead would make every count depend on
    a signature being complete, and would drop real work the moment one
    CPython renamed a symbol.
    """
    del tmp
    classifier = _journey_contract.threads()
    assert classifier.role_of(9, frozenset()) == classifier.REQUEST
    for name in _journey_contract.journeys().NAMES:
        assert classifier.REQUEST not in classifier.excluded_for(name), name


def test_a_thread_below_the_floor_is_a_refusal_naming_it(tmp):
    """The floor is a shape check now, and it stays.

    `REQUEST_FROM` no longer decides a role; it says a thread this small
    never entered the interpreter, so a profile carrying one is not the
    shape this gate reads. A refusal is still the answer, and it still
    names the thread and its count.
    """
    del tmp
    classifier = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()},
            {'pid': 1, 'thread': 4, 'ir': 400,
             'cmd': 'python3 server.py', 'names': frozenset()}]
    kept, _excluded, failure = classifier.total_for(rows, 'mcp-exec')
    assert failure is not None and kept is None, (kept, failure)
    assert '400' in failure and 'thread 4' in failure, failure


def test_a_thread_the_profile_does_not_have_is_a_refusal(tmp):
    """A missing background thread is a failure, never a silent whole-tree sum.

    The failure this guards is the one the whole change exists to end: a
    profile the classifier could not read, summed as though every thread in
    it counted, and reported as a number nobody can read back. Roles now
    come from what each thread executed, so a journey that excludes a role
    whose signature is in no thread is refused here — the check is more
    load-bearing than it was, not less.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.IMPORT])}]
    kept, excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is not None and 'excludes' in failure, failure
    assert kept is None, kept
    assert excluded == ('front-end-import', 'uvicorn-serve'), excluded
    rows.append({'pid': 1, 'thread': 3, 'ir': 90_000_000,
                 'cmd': 'python3 server.py',
                 'names': frozenset(threads.SIGNATURES[threads.SERVE])})
    kept, excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is None, failure
    assert kept == 430_000_000, kept


def test_two_threads_in_one_excluded_role_cannot_be_told_apart(tmp):
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.IMPORT])},
            {'pid': 1, 'thread': 3, 'ir': 90_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.SERVE])},
            {'pid': 1, 'thread': 4, 'ir': 95_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.SERVE])}]
    kept, _excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is not None, 'two serve threads are ambiguous'
    assert 'uvicorn-serve' in failure, failure
    assert kept is None


def test_two_threads_in_a_role_the_journey_does_not_exclude_are_counted(
        tmp):
    """The half of the refusal that has no case, and the half that ships.

    `classify` refuses a profile carrying two threads of one role, and
    that is right only where the journey EXCLUDES the role: there the gate
    has to pick which of the two is the background it drops, and nothing
    in the profile says which. In a role it does not exclude, every thread
    counts and two of them is two threads of work.

    This is the shape `net-capture` produces: its request thread runs to
    billions and, under the old bands, landed in the import band beside
    the bridge's own bootstrap import. Deleting the `if role not in
    excluded: continue` guard restores the original defect, refuses this
    profile, and `journey_counters` then marks the whole counter
    unavailable — so every journey in the run reads unmeasured, and it
    does so behind a suite that is otherwise green. The journey under
    callgrind is the expensive path nobody runs locally, which is the
    whole reason this is pinned here.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 959_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.IMPORT])},
            {'pid': 1, 'thread': 3, 'ir': 2_100_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()},
            {'pid': 1, 'thread': 4, 'ir': 87_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.SERVE])}]
    kept, excluded, failure = threads.total_for(rows, 'net-capture')
    assert failure is None, failure
    assert kept is not None, 'two counted threads is not a refusal'
    # Derived from the table rather than written out, so this pins the
    # BEHAVIOUR and not the value of any exclusion list: the table is data,
    # and a test that asserted its contents is a second place for them to
    # drift.
    expected = sum(row['ir'] for row in rows
                   if threads.role_of(row['thread'], row['names'])
                   not in excluded)
    assert kept == expected, (kept, expected)


def test_two_threads_in_an_excluded_role_are_still_a_refusal_naming_it(tmp):
    """The other half, which the widened rule must not have cost.

    The guard has two halves and a reader cannot tell which one is
    load-bearing, so both are pinned. An EXCLUDED role with two threads in
    it is still the ambiguity the refusal exists for: the gate has to pick
    which of them it is dropping, and guessing would make the number mean
    something other than the journey.

    The journey is planted rather than named, so this pins no value of
    `EXCLUDED` either.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.IMPORT])},
            {'pid': 1, 'thread': 3, 'ir': 2_100_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.IMPORT])}]
    planted = {**threads.EXCLUDED, 'planted-journey': (threads.IMPORT,)}
    with _journey_contract.planting(threads, EXCLUDED=planted):
        kept, excluded, failure = threads.total_for(rows, 'planted-journey')
    assert failure is not None, 'two threads of one role must refuse'
    assert threads.IMPORT in failure, failure
    assert kept is None, kept
    # `total_for` hands back an EMPTY exclusion list beside a classify
    # failure, not the journey's own: the count never started, so what it
    # would have dropped is not a fact about this run.
    assert excluded == (), excluded


def test_one_thread_in_a_role_nobody_excludes_just_counts(tmp):
    """The plain case the widened rule has to leave exactly as it was.

    One thread of a role nobody excludes is not an ambiguity and never was;
    this is the contrast the refusal above needs, so that a reader can see
    the guard is about COUNT and not about the number of threads.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [{'pid': 1, 'thread': 1, 'ir': 430_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()},
            {'pid': 1, 'thread': 2, 'ir': 3_800_000_000,
             'cmd': 'python3 server.py',
             'names': frozenset(threads.SIGNATURES[threads.IMPORT])},
            {'pid': 1, 'thread': 3, 'ir': 90_000_000,
             'cmd': 'python3 server.py', 'names': frozenset()}]
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
        'events: Ir\nsummary: 430000000\n'
        'fn=(1) a_symbol\n1 12\n', encoding='utf-8')
    (directory / 'cg.1-02').write_text(
        'version: 1\npid: 1\ncmd:  python3 server.py\nthread: 2\n'
        'events: Ir\nsummary: 3800000000\n'
        + ''.join(
            f'fn=({index}) {name}\n1 12\n'
            for index, name in enumerate(
                thread_classifier.SIGNATURES[thread_classifier.IMPORT],
                start=1)),
        encoding='utf-8')
    rows, unread = thread_classifier.read(directory, 'cg')
    assert unread is None, unread
    assert [row['ir'] for row in rows] == [430000000, 3800000000], rows
    assert all(row['cmd'] == 'python3 server.py' for row in rows), rows
    assert rows[0]['names'] == frozenset({'a_symbol'}), rows[0]['names']
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
    while looking like it excludes something. And no journey may exclude
    `request`, which is the whole of issue 1466: a request thread is
    whatever a request thread grew to be.
    """
    del tmp
    threads = _journey_contract.threads()
    for name in _journey_contract.journeys().NAMES:
        assert name in threads.EXCLUDED, name
        roles = threads.excluded_for(name)
        assert roles, name
        assert set(roles) <= set(threads.ROLES), (name, roles)
        assert threads.REQUEST not in roles, name


def test_the_artefact_records_the_signatures_that_decide_a_role(tmp):
    """The gate compares the table the measurement classified by.

    `excluded_threads` records the ROLES a journey leaves out while the
    table that PUTS a thread in one of them is guarded nowhere, so a run
    that read a profile by a different set of symbols would compare a
    count taken under one classifier against counts taken under another.
    That is the bands gate's argument with its subject replaced, and the
    subject has to be replaced: the bands no longer decide a role.
    """
    del tmp
    threads = _journey_contract.threads()
    assert set(threads.SIGNATURES) == {threads.IMPORT, threads.SERVE}, \
        threads.SIGNATURES
    assert threads.SIGNATURES == {
        threads.IMPORT: sorted(MEASURED_FRONT_END_SYMBOLS),
        threads.SERVE: sorted(MEASURED_LOOP_SYMBOLS)}, threads.SIGNATURES


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
    drives that), and `role_of` reads thread 1 as `MAIN` whatever its total
    and whatever it executed, so the round trip counts wherever the journey
    puts it — provided the journey puts it there, which is what this one
    pins. The stand-in front end records the thread each tool call was made
    from and refuses any payload that is not the one the journey is supposed
    to send, because a stub that swallows its arguments cannot tell a
    journey that changed what it does from one that only changed where it
    does it.
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

    A main thread is read as `MAIN` whatever its total and whatever it
    executed, so an import on one is the journey's own work by every rule
    this classifier applies. Loading it on a worker of its own and waiting
    for it is the whole of the asymmetry, and it costs the count nothing:
    the module the tool call reaches is the same one either way.
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
