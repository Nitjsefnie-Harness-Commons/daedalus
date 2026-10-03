#!/usr/bin/env python3
"""Contracts for WHICH THREAD a journey's count reads, and for the
refusals that happen when a profile is not the shape the gate reads.
A mis-sorted profile summed as a whole tree is the one failure here
that produces a plausible number. The other half — that a journey puts
its own work on the thread this file reads as the main one — is
`test_journey_placement.py`.

The controls here build their profiles; `test_journey_real_profile.py`
holds the ones that are real, and the split is deliberate: a profile whose
`fn=` lines are the constants under test passes by construction, so this
file pins the rules and that one pins the reading of a profile a real run
produced.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    _util,
)

# The front end's signature has no literal HERE, deliberately: its owner is
# the installed extension, and `front_end_symbol()` derives the init symbol
# from the file that extension actually is. A literal in the tree is a
# second copy of a contract with an owner, and a rename moves both copies
# in one commit and leaves the suite agreeing with itself. The measurements
# that decided WHICH symbols the table holds are in the module docstring and
# in `tests/_journey_profile_fixture.py`.
FRONT_END_EXTENSION = 'pydantic_core._pydantic_core'
# What a thread that imports ANY module carries, and what the front end's
# import has to be told apart from. A signature of these would call every
# request thread that imported anything the front end's import, and the
# journeys that exclude it would drop the journey's own work.
GENERIC_IMPORT_SYMBOLS = ('_PyImport_RunModInitFunc', 'import_find_and_load')

# The two command lines a counter measures: the harness child it launched,
# and the bridge that child started. Every row a control here builds is one
# or the other, because the PROCESS is what the classifier reads first.
HARNESS = 'python3 tests/_journeys.py --journey mcp-exec --root .'
BRIDGE = 'python3 server.py'


def row(thread, ir, cmd=BRIDGE, names=(), pid=1):
    """One thread as `journey_threads.read` hands one back."""
    return {'pid': pid, 'thread': thread, 'ir': ir, 'cmd': cmd,
            'names': frozenset(names)}


def front_end_symbol():
    """The CPython init symbol of the INSTALLED front-end extension.

    CPython's init macro prefixes `PyInit_` to the module's leaf name, and
    the leaf name here is `_pydantic_core` — which is why the symbol has
    two underscores and not one. Deriving it from the file the module
    resolves to is what makes this an oracle rather than a second copy of
    the constant: a renamed or rebuilt extension moves the expectation with
    it, and a constant that names a symbol nothing exports fails here
    rather than in a CI run.

    An extension that is not installed is a REFUSAL naming it. There is no
    fallback to a literal, because a literal is exactly the thing this
    exists not to be.
    """
    import importlib.util
    try:
        spec = importlib.util.find_spec(FRONT_END_EXTENSION)
    except (ImportError, AttributeError, ValueError) as error:
        raise AssertionError(
            f'{FRONT_END_EXTENSION} is not installed, so the front end\'s '
            f'signature cannot be checked against anything: '
            f'{error}') from None
    assert spec is not None and spec.origin, (
        f'{FRONT_END_EXTENSION} resolves to no file, so the front end\'s '
        f'signature cannot be checked against anything')
    leaf = spec.origin.replace('\\', '/').rsplit('/', 1)[-1]
    return 'PyInit_' + leaf.split('.')[0]


def test_the_reader_collects_the_names_a_thread_declared(tmp):
    """`fn=(id) name` names a symbol; `fn=(id)` only repeats one.

    A thread's identity is read from what it entered, so the reader has to
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
        '3 16\n'
        'cfn=(3)\n'
        '4 18\n', encoding='utf-8')
    rows, unread = classifier.read(directory, 'cg')
    assert unread is None, unread
    assert rows[0]['names'] == frozenset({'first_symbol', 'second_symbol'}), \
        rows[0]['names']


def test_the_reader_collects_a_symbol_callgrind_declared_as_a_callee(tmp):
    """`cfn=(id) name` names the same symbol, and the reader takes it.

    Callgrind declares a symbol's cost inline as `fn=` when it runs there
    and writes it as `cfn=` when it runs as a callee. Which one it picks is
    a cost-attribution detail and not a property of the thread: on the real
    profiles in `tests/fixtures/journey_profiles/`, the front end's init
    symbol is declared `fn=` on the bridge's own front-end thread in the
    `bridge-only` run and `cfn=` on that same thread in
    `command-round-trip`. A reader that took only `fn=` finds the front end
    in one run and misses it in the next, and a journey excluding it is
    refused on one and counted on the other.
    """
    classifier = _journey_contract.threads()
    directory = Path(tmp)
    expected = front_end_symbol()
    (directory / 'cg.1-01').write_text(
        'version: 1\npid: 1\ncmd:  python3 server.py\nthread: 2\n'
        'events: Ir\nsummary: 90000000\n'
        f'cfn=(7) {expected}\n'
        'calls=1 0\n'
        '* 12\n', encoding='utf-8')
    rows, unread = classifier.read(directory, 'cg')
    assert unread is None, unread
    assert expected in rows[0]['names'], rows[0]['names']
    assert classifier.role_of(rows[0]) == classifier.IMPORT


def test_a_clone_of_the_front_ends_init_symbol_still_matches(tmp):
    """Callgrind suffixes a CLONE of a symbol, and a clone is the symbol.

    Two threads in one profile run the same C function and callgrind writes
    one of them as `PyInit__pydantic_core'2`. A reader that compares names
    literally never sees that thread's module execution, and the bootstrap
    import thread stops being the import thread.

    The clone is written out here rather than substituted into a signature
    read from the module: the earlier version of this control replaced the
    text `import_find_and_load`, which no longer appears in the signature,
    so it wrote a profile with no clone in it and passed with the folding
    deleted.
    """
    classifier = _journey_contract.threads()
    directory = Path(tmp)
    expected = front_end_symbol()
    (directory / 'cg.1-01').write_text(
        'version: 1\npid: 1\ncmd:  python3 server.py\nthread: 2\n'
        'events: Ir\nsummary: 90000000\n'
        f"fn=(1) {expected}'2\n"
        '1 12\n', encoding='utf-8')
    rows, unread = classifier.read(directory, 'cg')
    assert unread is None, unread
    assert rows[0]['names'] == frozenset({f"{expected}'2"}), \
        rows[0]['names']
    assert classifier.role_of(rows[0]) == classifier.IMPORT, \
        'the clone is the symbol it is a clone of'


def test_a_thread_in_the_journeys_own_process_is_main_or_request_at_any_size(
        tmp):
    """Issue 1466's regression: a role that moves when the work grows.

    A per-connection request thread measured 3,548,079 instructions, which
    is 2.82x under the floor a band read held the serve thread to. Grow that
    thread past it and a band-ordered classifier calls it background — a
    role `command-round-trip` and `dashboard-fanout` exclude — so the
    journey's own work silently leaves the count.

    The role here comes from the PROCESS the thread ran in, so no size moves
    it: thread 1 is the main one whatever it executed, and every other
    thread of the harness process is the journey's own work at any size.
    The main thread's totals are the ones that matter, because a large one
    on it is still the main thread's work.
    """
    del tmp
    classifier = _journey_contract.threads()
    for total in (999, 3_548_079, 1_311_350_558, 3_800_000_000):
        for names in (frozenset(),
                      frozenset(classifier.SIGNATURES[classifier.IMPORT]),
                      frozenset(GENERIC_IMPORT_SYMBOLS)):
            assert classifier.role_of(row(1, total, HARNESS, names)) \
                == classifier.MAIN, (total, names)
        for names in (frozenset(),
                      frozenset(classifier.SIGNATURES[classifier.IMPORT]),
                      frozenset(GENERIC_IMPORT_SYMBOLS)):
            assert classifier.role_of(row(7, total, HARNESS, names)) \
                == classifier.REQUEST, (total, names)


def test_a_thread_outside_the_journeys_process_is_background(tmp):
    """The bridge's own threads, and the one signature that splits them.

    Every thread of another process is background by construction, so the
    exclusion list can reach background work and can never reach the
    journey's. The one signature left decides which background thread is the
    front end's one-off bootstrap; a thread no signature claims is the
    bridge serving, which is what the rest of them are.
    """
    del tmp
    classifier = _journey_contract.threads()
    expected = front_end_symbol()
    for total in (classifier.REQUEST_FROM, 3_548_079, 10_000_000,
                  3_548_079_000, 1_000_000_000, 90_000_000_000):
        assert classifier.role_of(row(1, total)) == classifier.SERVE, total
        assert classifier.role_of(row(1, total)) != classifier.MAIN, total
    assert classifier.role_of(
        row(4, 3_800_000_000, BRIDGE, (expected,))) == classifier.IMPORT
    assert classifier.role_of(
        row(4, 3_800_000_000, BRIDGE, GENERIC_IMPORT_SYMBOLS)) \
        == classifier.SERVE, 'import machinery is not the front end'


def test_the_signature_must_be_complete_before_a_thread_is_the_import(tmp):
    """Carrying a symbol is not the role; carrying the signature is.

    `_carries` requires every member, so dropping one WIDENS the signature
    toward calling a background thread the role it names, and a control that
    reads its expectation from the subject cannot see that.
    """
    del tmp
    classifier = _journey_contract.threads()
    members = classifier.SIGNATURES[classifier.IMPORT]
    for member in members:
        without = set(members) - {member}
        assert classifier.role_of(row(4, 3_800_000_000, BRIDGE, without)) \
            == classifier.SERVE, (member, without)


def test_a_request_thread_that_imports_one_module_is_still_a_request_thread(
        tmp):
    """The finding that made the generic signature unshippable.

    A request thread that imports anything at all is the thread the
    journeys that exclude the import silently drop, and the number it
    produces is plausible: the reviewer drove the gate and `mcp-exec`
    answered `kept=400000000, failure=None` with 5,000 instructions of
    journey work gone and nothing said. That is issue 1466's own shape
    through the new door — a journey loses its own work with no refusal —
    so the signature has to name the front end rather than imports, and the
    PROCESS has to be read before the signature at all: a thread of the
    journey's own process that imported the front end is still the
    journey's.
    """
    del tmp
    classifier = _journey_contract.threads()
    rows = [row(1, 400_000_000, HARNESS),
            row(3, 3_800_000_000, HARNESS, (front_end_symbol(),)),
            row(2, 5_000, HARNESS, GENERIC_IMPORT_SYMBOLS),
            row(4, 3_800_000_000, BRIDGE, (front_end_symbol(),))]
    kept, excluded, failure = classifier.total_for(rows, 'mcp-exec')
    assert failure is None, failure
    assert excluded == (classifier.IMPORT,), excluded
    # The journey's own MCP client — 3.8 billion instructions of it — is
    # inside the count, and the bridge's front end is the one thing out.
    assert kept == 400_000_000 + 3_800_000_000 + 5_000, kept


def test_the_module_init_signature_is_the_installed_extensions_init_symbol(
        tmp):
    """The front end's symbol, checked against the installed extension.

    `daedalus_mcp/*.py` is pure Python and has no `PyInit_<name>` of its
    own, but `mcp==2.2.0` pulls pydantic v2, whose compiled core is a C
    extension and is the one thing on the front end's import thread that a
    bridge thread never initialises. The expected symbol comes from the file
    that extension resolves to, so this fails on a constant that names
    something nothing exports, and survives a rename the way a literal
    cannot: the review shipped `PyInit_pydantic_core` where the symbol is
    `PyInit__pydantic_core`, and a literal on both sides of the assertion
    agreed with itself all the way to green.
    """
    del tmp
    classifier = _journey_contract.threads()
    expected = front_end_symbol()
    assert classifier.SIGNATURES[classifier.IMPORT] == [expected], (
        f'the front end\'s import is told by '
        f'{classifier.SIGNATURES[classifier.IMPORT]} and the installed '
        f'{FRONT_END_EXTENSION} exports {expected}')
    assert classifier.role_of(row(4, 3_800_000_000, BRIDGE, (expected,))) \
        == classifier.IMPORT


def test_an_extension_that_is_not_installed_is_a_refusal_naming_it(tmp):
    """The oracle's failure path, driven — a claim until something drives it.

    `front_end_symbol` says an absent extension is a refusal and there is no
    fallback to a literal. That is the whole guarantee the round exists to
    make, and a guard branch nothing reaches is a finding: replacing the
    `raise` with `return 'PyInit_pydantic_core'` leaves the rest of the
    suite green, because every other control drives the success path.

    Both ways it can fail are driven. An extension that cannot be imported
    and one that resolves to no file are both answers the oracle has no
    right to accept, and neither may produce a symbol.
    """
    del tmp
    import importlib.util

    def unimportable(_name):
        raise ImportError(f'No module named {_name!r}')

    def fileless(_name):
        # A namespace portion, which is what `find_spec` really answers for
        # one. A stand-in carrying only a falsy `.origin` failed on the
        # other conjunct, which left the `is not None` half of the guard
        # undriven and the limb that has to refuse a bare None with it.
        return None

    for planted, why in ((unimportable, 'cannot be imported'),
                         (fileless, 'resolves to no file')):
        with _journey_contract.planting(
                importlib.util, find_spec=planted):
            try:
                found = front_end_symbol()
            except AssertionError as refusal:
                assert FRONT_END_EXTENSION in str(refusal), (why, refusal)
                continue
        raise AssertionError(
            f'an extension that {why} produced {found!r} instead of a '
            f'refusal naming {FRONT_END_EXTENSION}')


def test_the_installed_extensions_init_symbol_is_the_one_that_was_measured(
        tmp):
    """The oracle AND the measurement, which are two different claims.

    The oracle above says the constant is spelled the way the installed
    file says. This says the constant is spelled the way a callgrind
    profile says, which is the claim the oracle cannot make: a
    `PyInit_<leaf>` that no module exports and one that no thread executes
    fail the same way from the reader's side and differently from the
    build's.
    """
    del tmp
    classifier = _journey_contract.threads()
    measured = ('PyInit__pydantic_core',)
    assert classifier.SIGNATURES[classifier.IMPORT] == list(measured), (
        classifier.SIGNATURES[classifier.IMPORT])
    assert front_end_symbol() in measured, (
        'the symbol measured on the front end\'s import thread is not the '
        f'one {FRONT_END_EXTENSION} exports: {front_end_symbol()}')


def test_a_refusal_names_a_role_that_has_no_symbol_of_its_own(tmp):
    """`main` is read from a thread's position and never from a symbol.

    The refusal names the symbol it was looking for, and for two of the four
    roles there is no symbol: `main` is the main thread wherever it is, and
    `request` is what the journey's own process leaves over. The artefact's
    validator refuses both in a recorded exclusion list, so the sentence that
    would describe a missing one is reached here by planting the table entry
    directly — which is what keeps the non-indexing case honest.
    """
    del tmp
    threads = _journey_contract.threads()
    planted = {**threads.EXCLUDED, 'planted-journey': (threads.MAIN,)}
    rows = [row(2, 3_000_000)]
    with _journey_contract.planting(threads, EXCLUDED=planted):
        kept, _excluded, failure = threads.total_for(rows, 'planted-journey')
    assert kept is None, kept
    assert threads.MAIN in failure, failure
    assert 'never from a symbol' in failure, failure


def test_a_thread_below_the_floor_is_a_refusal_naming_it(tmp):
    """The floor is a shape check now, and it stays.

    `REQUEST_FROM` no longer decides a role; it says a thread this small
    never entered the interpreter, so a profile carrying one is not the
    shape this gate reads. A refusal is still the answer, and it still
    names the thread and its count.
    """
    del tmp
    classifier = _journey_contract.threads()
    rows = [row(1, 430_000_000, HARNESS), row(4, 400)]
    kept, _excluded, failure = classifier.total_for(rows, 'mcp-exec')
    assert failure is not None and kept is None, (kept, failure)
    assert '400' in failure and 'thread 4' in failure, failure


def test_a_thread_the_profile_does_not_have_is_a_refusal(tmp):
    """A missing background thread is a failure, never a silent whole-tree sum.

    The failure this guards is the one the whole change exists to end: a
    profile the classifier could not read, summed as though every thread in
    it counted, and reported as a number nobody can read back. A journey
    that excludes a role no thread carries is refused here — the check is
    more load-bearing now that a missing role is a missing process rather
    than a missing symbol, and a gate that silently widened its count to
    cover one would be back to summing a whole tree.
    """
    del tmp
    threads = _journey_contract.threads()
    # A profile that is the harness alone: no bridge, so neither background
    # role is in it.
    rows = [row(1, 430_000_000, HARNESS)]
    kept, excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is not None and 'excludes' in failure, failure
    assert kept is None, kept
    assert excluded == (threads.IMPORT, threads.SERVE), excluded
    assert front_end_symbol() in failure, failure
    assert threads.SERVE in failure, failure
    # The bridge's front end alone satisfies neither, because the rest of
    # the bridge is what `uvicorn-serve` is.
    rows = [row(1, 430_000_000, HARNESS),
            row(4, 3_800_000_000, BRIDGE,
                (front_end_symbol(),))]
    kept, excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert kept is None and threads.SERVE in failure, failure
    # One background thread of each and the journey counts.
    rows.append(row(5, 90_000_000))
    kept, excluded, failure = threads.total_for(rows, 'dashboard-fanout')
    assert failure is None, failure
    assert kept == 430_000_000, kept


def test_several_threads_of_one_excluded_role_are_all_dropped(tmp):
    """The refusal this replaced was for an ambiguity the gate never had.

    The classifier dropped EVERY thread of an excluded role, so two threads
    of one role was two threads of background and not a choice between them
    — while the refusal said the gate had to pick. On a real profile the
    refusal fired on every run, because the bridge has several threads no
    signature claims: `command-round-trip`'s own bridge carries six. The
    control is the corrected rule, and it is pinned both ways: several
    threads of an excluded role are all dropped, and several of a role
    nobody excludes are all counted.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [row(1, 430_000_000, HARNESS),
            row(4, 3_800_000_000, BRIDGE, (front_end_symbol(),)),
            row(5, 87_000_000), row(6, 3_500_000),
            row(7, 2_100_000_000, HARNESS)]
    kept, excluded, failure = threads.total_for(rows, 'command-round-trip')
    assert failure is None, failure
    assert kept == 430_000_000 + 2_100_000_000, kept
    # `mcp-exec` excludes the import and not the serve loop, so the two
    # serve threads are BOTH counted beside it — several of a role nobody
    # excludes are all kept, not one of them.
    kept, excluded, failure = threads.total_for(rows, 'mcp-exec')
    assert failure is None, failure
    assert kept == 430_000_000 + 87_000_000 + 3_500_000 + 2_100_000_000, kept
    assert excluded == (threads.IMPORT,), excluded


def test_one_thread_in_a_role_nobody_excludes_just_counts(tmp):
    """The plain case the widened rule has to leave exactly as it was.

    One thread of a role nobody excludes is not an ambiguity and never was.
    """
    del tmp
    threads = _journey_contract.threads()
    rows = [row(1, 430_000_000, HARNESS),
            row(4, 3_800_000_000, BRIDGE, (front_end_symbol(),)),
            row(5, 90_000_000)]
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
        f'version: 1\npid: 1\ncmd:  {HARNESS}\nthread: 1\n'
        'events: Ir\nsummary: 430000000\n'
        'fn=(1) a_symbol\n1 12\n', encoding='utf-8')
    (directory / 'cg.2-04').write_text(
        f'version: 1\npid: 2\ncmd:  {BRIDGE}\nthread: 4\n'
        'events: Ir\nsummary: 3800000000\n'
        + ''.join(
            f'fn=({index}) {symbol}\n1 12\n'
            for index, symbol in enumerate(
                thread_classifier.SIGNATURES[thread_classifier.IMPORT],
                start=1)),
        encoding='utf-8')
    rows, unread = thread_classifier.read(directory, 'cg')
    assert unread is None, unread
    assert [row['ir'] for row in rows] == [430000000, 3800000000], rows
    assert [row['cmd'] for row in rows] == [HARNESS, BRIDGE], rows
    assert rows[0]['names'] == frozenset({'a_symbol'}), rows[0]['names']
    # `mcp-exec` excludes only the import, so a profile carrying no
    # `uvicorn-serve` thread is a complete one for it — and the sum is the
    # rest.
    kept, excluded, failure = thread_classifier.total_for(rows, 'mcp-exec')
    assert failure is None, failure
    assert kept == 430000000, kept
    assert excluded == ('front-end-import',), excluded
    # `dashboard-fanout` needs a `uvicorn-serve` thread, and a profile
    # without one is a refusal naming the role rather than a whole-tree sum.
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
    rather than by this suite. Every role is also checked to be one `role_of`
    can return, because a name that is not a role excludes nothing while
    looking like it excludes something. And no journey may exclude `main` or
    `request`: both are the journey's own process, and an exclusion that
    could reach either is issue 1466.
    """
    del tmp
    threads = _journey_contract.threads()
    for name in _journey_contract.journeys().NAMES:
        assert name in threads.EXCLUDED, name
        roles = threads.excluded_for(name)
        assert roles, name
        assert set(roles) <= set(threads.ROLES), (name, roles)
        assert threads.REQUEST not in roles, name
        assert threads.MAIN not in roles, name
    # The other half of the module docstring's rule, stated over the TABLE
    # rather than over a journey: the two roles a journey's own process
    # produces are reachable by no list at all, and `journey_artifact`
    # refuses either by name. The import half — every journey drops the
    # bridge's bootstrap — is its own control, derived from `NAMES`. A
    # docstring naming a journey where a shape statement will do would be a
    # second copy of the table, and a table entry moved changes both.
    own_process = {threads.MAIN, threads.REQUEST}
    assert own_process.isdisjoint(
        {role for roles in threads.EXCLUDED.values() for role in roles}), \
        threads.EXCLUDED
    # The serve role is the OTHER constant, and the table splits on it: two
    # journeys exercise the bridge's HTTP surface and none of the front end's
    # event loop, some call that loop, and `net-capture` counts the bridge's
    # own per-connection handling of the capture, which carries the loop's
    # tick in as a residual. The invariant this replaced pinned the split as
    # a side effect of naming one journey.
    keeps_serve = [roles for roles in threads.EXCLUDED.values()
                   if threads.SERVE not in roles]
    assert keeps_serve, threads.EXCLUDED
    assert len(keeps_serve) < len(threads.EXCLUDED), threads.EXCLUDED


def test_every_journey_drops_the_front_ends_bootstrap_import(tmp):
    """The import is one thread of the BRIDGE, so no journey's own work is
    behind it and every journey stops counting it.

    The bootstrap is the one background thread whose cost does not repeat:
    five rounds of identical code on one runner measured 4,015,865,696 to
    4,020,617,049 instructions, a range of 0.118% of the smaller figure.
    The 1.14% issue 1495 records for a journey keeping it is its own
    six-draw whole-journey `max/min - 1`, a second measurement rather than
    a consequence of that range. `net-capture` is the case that made it
    safe: under size bands a thread that journey put to work measured 2.10
    billion instructions and landed in the import band beside the bootstrap,
    so dropping the import there would have dropped the work the journey
    exists to measure. Roles are decided by the process a thread ran in now,
    so no importing request thread can claim the role whatever its size.

    Derived from `NAMES` through `excluded_for`, so a journey added to that
    list and given an entry that keeps the import fails here. A hand-written
    list of the seven would agree with itself for ever.

    An absence assertion reads as green over an empty set, so the read is
    driven both ways: a table dropping one journey's import must show up in
    the same comprehension that must come back empty here.
    """
    del tmp
    threads = _journey_contract.threads()
    names = _journey_contract.journeys().NAMES
    assert names, 'no journey names, so this control reads nothing'
    keeps = [name for name in names
             if threads.IMPORT not in threads.excluded_for(name)]
    assert not keeps, keeps
    planted = {**threads.EXCLUDED, names[0]: (threads.SERVE,)}
    with _journey_contract.planting(threads, EXCLUDED=planted):
        seen = [name for name in names
                if threads.IMPORT not in threads.excluded_for(name)]
    assert seen == [names[0]], seen


def test_the_artefact_records_the_signatures_that_decide_a_role(tmp):
    """The gate compares the table the measurement classified by.

    `excluded_threads` records the ROLES a journey leaves out while the
    table that PUTS a thread in one of them is guarded nowhere, so a run
    that read a profile by a different set of symbols would compare a count
    taken under one classifier against counts taken under another.
    """
    del tmp
    threads = _journey_contract.threads()
    assert set(threads.SIGNATURES) == {threads.IMPORT}, threads.SIGNATURES
    assert threads.SIGNATURES == {
        threads.IMPORT: [front_end_symbol()]}, threads.SIGNATURES


def test_the_artefact_is_the_table_it_was_recorded_under(tmp):
    """The committed `excluded_threads` IS the live table, by NAME.

    Both arms of every recorded-vs-measured comparison are built from
    `excluded_for`, so the committed map and the table were never compared
    against each other by name: putting `uvicorn-serve` back into
    `net-capture` changed what the gate drops and only a baseline literal
    that happens to move noticed.
    """
    del tmp
    threads = _journey_contract.threads()
    artifact = _journey_contract.artifact()
    document = artifact.load()
    names = _journey_contract.journeys().NAMES
    assert names, 'no journey names, so this control reads nothing'
    recorded = document['excluded_threads']
    for name in names:
        applied = list(threads.excluded_for(name))
        assert recorded.get(name) == applied, (
            f'{name} is recorded as {recorded.get(name)} and the table '
            f'excludes {applied}, so this artefact counts a different '
            'quantity from the table a run classified the profile under')
    # No tolerance of its own, so `net-capture` rides `tolerance_pct`: the
    # three CI draws of the count this branch re-recorded span 2,672,681,420
    # to 2,672,726,671 instructions — 0.0017% (run 37096636089, artefact
    # `journey-counts` 11265650340) — and a per-journey tolerance is
    # recorded only for a journey whose own draws exceed the default.
    assert 'net-capture' not in document['tolerances'], (
        f'net-capture is held to {document["tolerances"]["net-capture"]}% '
        'while its own draws span 0.0017%, so the bound is read from a '
        f'measurement this artefact does not carry: {document["tolerances"]}')
    assert artifact.tolerance_of(document, 'net-capture') == \
        document['tolerance_pct'], document['tolerances']


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())
