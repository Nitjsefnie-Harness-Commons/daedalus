#!/usr/bin/env python3
"""The properties that need a parent which folds case, on a real one.

Every fixture here asks the same question of a filesystem -- does this
parent resolve a name that differs only in case to the entry it spells? --
and the answer is the parent's, not the interpreter's. A case-sensitive host
says no, and on such a host the whole family of shapes this file covers is
**unreachable**: a name that differs only in case is simply a different name,
and the only way two names reach one entry is a symlink, which the delivery
guard refuses by name. So these fixtures run against a real case-folding
parent, named by `DAEDALUS_CASE_FOLD_ROOT`, and skip with that reason in the
runner's own output where there is none.

The half that does not need one is pinned host-independently and lives with
the code it pins: the case-sensitive twin of each fixture here, the symlinked
alias the guard refuses (`test_result_store`), two names for one entry taking
one stripe (`test_delivery_stripes`), the reserved-name checks under their
exact spelling, and the whole absent-entry contract
(`test_result_stripe`). A skip in this file is therefore never the only
evidence for a property, except where the report says so by name.

There was once a double here that patched `os.stat` to fold, and it was
verified on one platform and assumed on three. It could not be made to work
on the others: `ntpath.realpath` answers through `nt._getfinalpathname`, a C
call a Python patch cannot reach, so on Windows the fixtures' POSIX-resolver
assumptions were false; and on CPython before 3.13 a folded `lstat` answer
made `posixpath._joinrealpath` follow a link the spelling did not name and
raise out of `realpath`. Asking the parent is the part that is true
everywhere.
"""
import contextlib
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _folds(root):
    """Whether this parent resolves a name to the entry it already spells.

    Asked by creating a file and looking for it under another spelling, and
    answered by the filesystem rather than by the platform: a macOS or
    Windows volume says yes, an ext4 or vfat image says no, and the answer
    is the parent's either way.
    """
    stem = f'daedalus-fold-probe-aa{os.getpid()}'
    probe = Path(root) / stem
    try:
        probe.write_text('', encoding='utf-8')
    except OSError as why:
        _util.skip(f'{root} will not take a probe file: {why}')
    try:
        # The same letters, the other case: a parent that folds resolves it
        # to the entry just written, one that does not has nothing there.
        return os.path.exists(str(Path(root) / stem.upper()))
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def _folding_parent(tmp):
    """A real case-insensitive parent, or the reason this run has none.

    The parent named by `DAEDALUS_CASE_FOLD_ROOT` if there is one -- a vfat
    image, which is how this suite runs on a case-sensitive host -- and
    otherwise the host's own filesystem, which is how it runs on a macOS or
    Windows leg with nothing named. A host whose filesystem folds nothing
    skips, and the runner prints that with the reason.
    """
    named = os.environ.get('DAEDALUS_CASE_FOLD_ROOT')
    if named:
        if not os.path.isdir(named):
            _util.skip(f'DAEDALUS_CASE_FOLD_ROOT={named} is not a directory')
        if not _folds(named):
            _util.skip(f'{named} does not fold case')
        return Path(named)
    if _folds(tmp):
        return Path(tmp)
    _util.skip('this filesystem folds no case, and no case-folding parent is '
               'named by DAEDALUS_CASE_FOLD_ROOT')


def _folding_token(root, name):
    """A fresh subdirectory of the real folding parent to work in."""
    target = root / name
    if target.exists():
        _util.skip(f'{target} is already there from an earlier run')
    target.mkdir(parents=True)
    return target


def _symlinks(root):
    """Whether this parent can also hold a symlink, or say why not."""
    probe = root / f'daedalus-symlink-probe-{os.getpid()}'
    try:
        probe.symlink_to(root)
    except (OSError, NotImplementedError) as why:
        _util.skip(f'{root} will not hold a symlink: {why}')
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return True


@contextlib.contextmanager
def _recording_locks(store):
    """Record each stripe acquisition as (directory, key, lock id).

    All three, because each answers a different question. The directory is
    what the caller named; the key is what the stripe is decided on, and it
    is the half that is exact -- two keys can share one of 64 stripes by
    chance, so a lock count alone would let a keying mutation through once in
    sixty-four runs; and the lock identity is the consequence, which one key
    always produces.
    """
    seen = []
    real_lock_for = store.delivery_lock_for
    real_key_for = store.delivery_stripe_key

    def recording_lock_for(target_dir):
        lock = real_lock_for(target_dir)
        # The key is asked for, not remembered: the selector has just asked
        # the filesystem, and this is the only place a second answer exists.
        seen.append((target_dir, real_key_for(target_dir), id(lock)))
        return lock

    store.delivery_lock_for = recording_lock_for
    try:
        yield seen
    finally:
        store.delivery_lock_for = real_lock_for


def _one_stripe(seen):
    """The directories named, the distinct keys, and the stripes reached."""
    return ([Path(d).name for d, _k, _l in seen],
            len({key for _d, key, _l in seen}),
            len({lock for _d, _k, lock in seen}))


def _load(path, name):
    return _util.load(_util.ROOT / 'daedalus_bridge' / path, name)


def _roots(work):
    """A results root and a commands root of this fixture's own."""
    res_dir = work / 'results'
    cmd_dir = work / 'commands'
    res_dir.mkdir(parents=True, exist_ok=True)
    cmd_dir.mkdir(parents=True, exist_ok=True)
    return res_dir, cmd_dir


DELIVERY_CAP = 8


def test_the_guard_accepts_a_name_the_parent_folds(tmp):
    """A spelling the parent folds onto another one is the same target.

    The guard refuses a name that stands in for a *different* entry. On a
    parent that folds, `foo` and `Foo` are the same entry, so the refusal
    would be refusing the target the caller named -- the 400 this issue
    filed. Both spellings are accepted here and resolve to one entry.
    """
    store = _load('result_store.py', 'fold_guard_accepts')
    work = _folding_token(_folding_parent(tmp), Path(tmp).name)
    res_dir = work / 'results'
    on_disk = res_dir / 'deliveries' / 'foldtoken_Foo'
    on_disk.mkdir(parents=True)
    folded, _folded_file = store.delivery_result_paths(
        res_dir, 'foldtoken', 'foo', '123_1')
    exact, _exact_file = store.delivery_result_paths(
        res_dir, 'foldtoken', 'Foo', '123_1')
    assert os.path.samefile(folded, exact), (folded, exact)
    assert sorted(p.name for p in (res_dir / 'deliveries').iterdir()) == [
        'foldtoken_Foo']


def test_the_guard_still_refuses_a_symlink_the_parent_folds(tmp):
    """A folded name that stands in for a sibling directory is refused.

    The relaxation above must not become an accepted symlink, which is the
    alias the guard exists to catch. This needs a parent that folds *and*
    holds symlinks, so it skips on a parent that cannot: a vfat image has
    no symlinks at all, and the conjunction is not reachable there.

    ACCEPTANCE, shared with the two stream fixtures of the same shape: this
    has never executed on a developer machine. The only reachable folding
    parent on the box this branch was written on is a vfat image, which
    holds no symlinks; a casefold ext4 could not be mounted (the kernel
    exposes no `casefold` option) and `ciopfs` is not installed. These
    three run on the macOS and Windows CI legs, where the host's own
    volume folds and holds symlinks. The workflow takes **no step** to
    obtain the symlink privilege those legs need: `tests.yml`'s `suites`
    job is checkout, setup-python, pip cache, pip install, `run_tests.py`,
    and nothing in `.github/workflows/` mentions symlinks, Developer Mode
    or `SeCreateSymbolicLink`. It therefore rests on two ambient facts
    rather than on the tree: the `windows-latest` image runs its account as
    an administrator, which may create a symlink without Developer Mode,
    and CPython's `os.symlink` on Windows asks for
    `SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE`, which Developer Mode
    would satisfy for a non-administrator. If a future runner image lacks
    both, these three skip and the aggregate still passes -- so a
    reviewer should read a Windows or macOS leg's `test_case_fold_parent`
    line, not its exit code, before treating them as covered.
    """
    store = _load('result_store.py', 'fold_guard_symlink')
    work = _folding_token(_folding_parent(tmp), Path(tmp).name)
    _symlinks(work)
    res_dir = work / 'results'
    real = res_dir / 'deliveries' / 'foldalias_real'
    real.mkdir(parents=True)
    alias = res_dir / 'deliveries' / 'foldalias_Ext'
    alias.symlink_to(real, target_is_directory=True)
    refused = False
    try:
        store.delivery_result_paths(res_dir, 'foldalias', 'ext', '123_1')
    except ValueError:
        refused = True
    assert refused, 'a symlinked alias was accepted'
    assert not list(real.iterdir()), list(real.iterdir())


def test_the_guard_accepts_a_canonicalizing_resolver(tmp):
    """The relaxation reached through a resolver that canonicalises.

    `posixpath.realpath` answers with the caller's spelling on every POSIX
    platform, so on a folding vfat the guard's exact-case comparison would
    pass by itself and its relaxed branch would never be reached.
    `ntpath.realpath` answers through `nt._getfinalpathname` with the
    on-disk spelling, and that is the branch this pins: the resolver is
    replaced with one that canonicalises, decided on the *name it was
    asked about* so the injection fires whatever the host's own resolver
    does, and the marker is asserted.

    ACCEPTANCE, and it is narrow on purpose. The relaxation needs a parent
    that folds case **and** a resolver that canonicalises, and the
    reviewer measured that pair to exist on Windows alone: a Linux or
    macOS volume folds and its resolver does not, so the branch is
    unreachable there, and the injection is the only way in. So this
    fixture is exercised where the gate finds a folding parent that is not
    a POSIX-resolver one -- the Windows legs, and the vfat image when it is
    named -- and **a green Linux or macOS matrix carries no evidence at
    all about the guard's relaxed comparison.** With the exact-case
    comparison restored, every Linux leg of this repository is 6/6 in
    `test_result_store` and 0/8 here; that is the property being
    unreachable, not the fixture failing, and it is why the comparison
    below is the only pin for it.
    """
    store = _load('result_store.py', 'fold_guard_canonical')
    work = _folding_token(_folding_parent(tmp), Path(tmp).name)
    res_dir = work / 'results'
    on_disk = res_dir / 'deliveries' / 'canontok_Foo'
    on_disk.mkdir(parents=True)
    real_realpath = os.path.realpath
    asked = {}

    def canonical_realpath(path):
        """Answer with the on-disk spelling, as `ntpath.realpath` would."""
        if os.path.basename(os.fsdecode(path)) == 'canontok_foo':
            asked['name'] = True
        resolved = real_realpath(path)
        name = os.path.basename(resolved)
        if name == 'canontok_foo':
            return resolved[:-len('canontok_foo')] + 'canontok_Foo'
        return resolved

    os.path.realpath = canonical_realpath
    try:
        try:
            paths = store.delivery_result_paths(
                res_dir, 'canontok', 'foo', '123_1')
        except ValueError:
            accepted = False
        else:
            accepted = True
    finally:
        os.path.realpath = real_realpath
    assert asked.get('name'), 'the resolver was never asked the case'
    assert accepted, 'the guard refused a name the parent folds'
    assert paths[0].name == 'canontok_Foo', paths[0].name


def test_a_folded_target_is_one_directory_and_one_stripe(tmp):
    """Two spellings of one tab: one directory, one stripe, on a real parent.

    The filed scenario end to end, on a parent that actually folds: a
    delivery for `Foo` creates the directory, a delivery for `foo` reaches
    the same entry, and the two take one stripe at every site that locks the
    target -- the POST, the delivery read and the compatibility consume. The
    create-and-open half is why this needs a real parent: name resolution
    is not the only thing a folding parent changes, and `open()` folds too.
    """
    root = _folding_parent(tmp)
    work = _folding_token(root, Path(tmp).name)
    routes = _load('result_routes.py', 'fold_routes_one_stripe')
    store = routes.result_store
    res_dir, cmd_dir = _roots(work)
    token = 'realfoldtok'
    with _recording_locks(store) as seen:
        first = routes.accept_result(
            res_dir, cmd_dir, token,
            {'tabId': 'Foo', 'id': 'one', '_did': '1700000000000_a'},
            DELIVERY_CAP)
        second = routes.accept_result(
            res_dir, cmd_dir, token,
            {'tabId': 'foo', 'id': 'two', '_did': '1700000000000_b'},
            DELIVERY_CAP)
        landed = sorted(path.name for path in (
            res_dir / 'deliveries' / f'{token}_Foo').iterdir())
        read = routes.fetch_result(
            res_dir, token, {'tab': ['foo'], 'delivery': ['1700000000000_b']})
        kept = routes.fetch_result(
            res_dir, token,
            {'tab': ['Foo'], 'consume': ['1'],
             'expected': ['1700000000000_not_this_one']})
        consumed = routes.fetch_result(
            res_dir, token, {'tab': ['foo'], 'consume': ['1']})
    names, keys, stripes = _one_stripe(seen)
    assert first == (200, {'ok': True}), first
    assert second == (200, {'ok': True}), second
    assert read[0] == 200 and read[1].get('id') == 'two', read
    assert kept == (200, {'consumed': False}), kept
    assert consumed[0] == 200 and consumed[1].get('id') == 'two', consumed
    assert not (res_dir / 'deliveries' / f'{token}_Foo' / (
        '1700000000000_b.json')).exists(), 'the consumed copy stayed'
    # One entry on disk, both results in it, and one key for the lot.
    assert sorted(p.name for p in (res_dir / 'deliveries').iterdir()) == [
        f'{token}_Foo'], sorted(
            p.name for p in (res_dir / 'deliveries').iterdir())
    assert landed == ['1700000000000_a.json', '1700000000000_b.json'], (
        landed)
    # Two spellings reached it, which is the divergence the fixture exists
    # to drive, and one key and one lock came out the other side.
    assert set(names) == {f'{token}_Foo', f'{token}_foo'}, names
    assert keys == 1, seen
    assert stripes == 1, seen


class _FrameSink:
    """A wfile stand-in that records the frames the loop writes.

    `on_first_frame` runs inside the first command's write, which is the
    window the extension's own drain and the per-tab scan leave open: the
    command has been handed to the writer but not yet unlinked, and
    everything after it in this tick reads the same directory again. A
    producer that publishes from here publishes into that window, which is
    what the background does continuously and what a fixture that enqueues
    everything up front cannot reproduce.
    """

    def __init__(self, on_first_frame=None):
        self.frames = []
        self._on_first_frame = on_first_frame
        self._published = False

    def write(self, data):
        text = data.decode('utf-8')
        if text.startswith('event: command\n'):
            self.frames.append(
                json.loads(text.split('\ndata: ', 1)[1].strip()))
            if not self._published:
                self._published = True
                if self._on_first_frame is not None:
                    self._on_first_frame()

    def flush(self):
        pass

    def ids(self):
        return [frame.get('id') for frame in self.frames]

    def tags(self):
        return [frame.get('chromeTab') for frame in self.frames]


def _one_tick(route, sink, cmd_dir, token, tab):
    """Run serve_stream for exactly one delivery tick, then age it out."""
    steps = iter((0.0, 0.0, 0.0, 0.0))
    route._now = lambda: next(steps, 5000.0)
    targets = route.resolve_targets(cmd_dir, token, tab)
    assert targets is not None, 'the fixture targets were refused'
    route.serve_stream(
        sink, cmd_dir=cmd_dir, token=token, tab=tab, targets=targets,
        killed_event=threading.Event(), command_ttl=90, keepalive=15,
        max_age=3600, client_label='test')


def test_the_extension_queue_the_parent_folds_is_not_a_tabs(tmp):
    """The extension's own queue, spelled `Extension`, is not drained twice.

    On a parent that folds, the per-tab scan finds the extension's own queue
    under a name that reads as a tab called `Extension`, and a scan that
    compares the name to the reserved spelling drains whatever is in there
    a second time, tagged for a tab that does not exist.

    What makes that reachable is the window this fixture publishes into: the
    extension's own drain hands its first command to the writer and unlinks
    it *after*, and the per-tab scan reads the same directory again later in
    the same tick. A command published between those two steps is the
    extension's own, and the background publishes continuously, so a fixture
    that enqueues everything up front cannot see the state at all -- the
    extension's own drain would have emptied the directory before the scan
    ran. So the second command is published from inside the first frame's
    write, which is inside that window.

    The verdict is the parent's, so this needs one; the case-sensitive twin
    is in `test_stream_route`. On a case-sensitive parent the old
    comparison and the new one agree for every input a caller can produce,
    so a green there carries no evidence about this branch of the scan.
    """
    route = _load('stream_route.py', 'fold_stream_queue')
    cq = route.command_queue
    work = _folding_token(_folding_parent(tmp), Path(tmp).name)
    cq.enqueue(work, 'tok', 'Extension', {'id': 'queued-for-extension'},
               command_ttl=90)
    cq.enqueue(work, 'tok', 'realtab', {'id': 'another-tab'}, command_ttl=90)
    sink = _FrameSink(on_first_frame=lambda: cq.enqueue(
        work, 'tok', 'Extension', {'id': 'published-mid-tick'},
        command_ttl=90))

    _one_tick(route, sink, work, 'tok', 'extension')

    # The extension's own first command is delivered once, by the drain that
    # owns it and with no tab tag, and the other tab's once with its own.
    assert sorted(zip(sink.ids(), sink.tags())) == [
        ('another-tab', 'realtab'),
        ('queued-for-extension', None)], sink.frames
    # And the command published into the window is not delivered at all: a
    # scan that read the folded name as a tab drains it here, tagged
    # 'Extension' for a tab that does not exist.
    assert 'published-mid-tick' not in sink.ids(), sink.ids()
    # It is still queued, under whatever name the queue gave it: the scan
    # left it alone rather than delivering it.
    assert len(list((work / 'tok_Extension').iterdir())) == 1, (
        sorted(p.name for p in (work / 'tok_Extension').iterdir()))


def test_a_folded_symlinked_reserved_queue_is_still_reserved(tmp):
    """A reserved name reached through a symlink is still reserved.

    `same_entry` follows a symlink, which is what makes the folded
    comparison right and is also what could swallow a tab: on a parent that
    folds, the entry answers to the extension's own name even when it is a
    link, and that is the issue's own point. Needs a parent that both folds
    and holds symlinks, so it skips on one that cannot, and it has never
    executed on a developer machine for the reason the guard's symlink
    fixture above gives.
    """
    route = _load('stream_route.py', 'fold_stream_symlink')
    work = _folding_token(_folding_parent(tmp), Path(tmp).name)
    _symlinks(work)
    real = work / 'tok_behind_the_link'
    real.mkdir()
    (real / '1700000000000_000001.json').write_text(
        '{"id":"behind-the-link"}', encoding='utf-8')
    alias = work / 'tok_Extension'
    alias.symlink_to(real, target_is_directory=True)
    sink = _FrameSink()

    _one_tick(route, sink, work, 'tok', 'extension')

    assert sink.ids() == [], sink.ids()
    assert [p.name for p in real.iterdir()], 'a reserved entry was drained'
    assert alias.is_symlink()


def test_the_extensions_own_legacy_file_the_parent_folds(tmp):
    """The extension's and the dashboard's own legacy files are their own.

    Each delivered a second time, with a `chromeTab` tag the extension
    cannot use, the two comparisons here are name comparisons a folding
    parent defeats. The verdict is the parent's; the case-sensitive twin is
    in `test_stream_service`.

    DISCLOSURE, because unlike the guard's relaxation this is a platform
    property and not a dropped capability. On a parent that folds nothing,
    the reserved name resolves to no entry, `samefile` raises, and the
    exact-case comparison and `same_entry` therefore agree for every input
    a caller can produce -- so the two code paths are indistinguishable
    there, and restoring the old comparisons leaves `test_stream_service`
    34/34 as CI runs it. This fixture, on a folding parent, is the pin that
    separates them; a case-sensitive leg's green says nothing about which
    of the two is in the tree.
    """
    service = _load('stream_service.py', 'fold_stream_legacy')
    work = _folding_token(_folding_parent(tmp), Path(tmp).name)
    tab = work / 'tok_42.json'
    extension = work / 'tok_Extension.json'
    dashboard = work / 'tok_Dashboard.json'
    tab.write_text('{"id":"tab"}', encoding='utf-8')
    extension.write_text('{"id":"extension"}', encoding='utf-8')
    dashboard.write_text('{"id":"dashboard"}', encoding='utf-8')
    frames = []

    delivered = service.drain_legacy_ext(
        work, 'tok', None,
        extension_legacy_name='tok_extension.json',
        command_ttl=100, frame_writer=frames.append)

    assert delivered == 1, delivered
    assert frames == [{'id': 'tab', 'chromeTab': '42'}], frames
    assert extension.exists(), extension
    assert dashboard.exists(), dashboard


def test_a_folded_symlinked_legacy_file_is_still_reserved(tmp):
    """A reserved legacy name reached through a symlink is still reserved.

    Needs a parent that both folds and holds symlinks, and has never
    executed on a developer machine for the reason the guard's symlink
    fixture above gives; the control is in `test_stream_service`, where the
    same symlink is a tab's file because the parent names nothing by it.
    """
    service = _load('stream_service.py', 'fold_stream_legacy_symlink')
    work = _folding_token(_folding_parent(tmp), Path(tmp).name)
    _symlinks(work)
    real = work / 'tok_42.json'
    real.write_text('{"id":"tab"}', encoding='utf-8')
    alias = work / 'tok_Extension.json'
    alias.symlink_to(real)
    frames = []

    delivered = service.drain_legacy_ext(
        work, 'tok', None,
        extension_legacy_name='tok_extension.json',
        command_ttl=100, frame_writer=frames.append)

    assert delivered == 0, delivered
    assert frames == [], frames
    assert alias.is_symlink() and real.exists()


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='casefoldparent_')


if __name__ == '__main__':
    raise SystemExit(main())
