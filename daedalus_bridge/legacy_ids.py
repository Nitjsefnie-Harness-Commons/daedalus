"""The delivery id a delivered legacy command file carries.

The id is a suppression key: the extension's persisted ledger skips a frame
whose `_did` it has already recorded, and the bridge stores a result under
the one the consumer posts back. Every component therefore has to be a value
the platform guarantees to be the same on every read of one object, or the
suppression silently stops suppressing.

**What is kept, and on what authority.** `st_dev` is the device the object
lives on: it changes on a remount, not between two reads of one file
(POSIX `stat`). `st_ino` is the file serial number, unique among the files
that exist on that device and unchanged for as long as the file exists — it
does not move when the file is opened, read, or closed, and on Windows it is
the filesystem's own file index. Both are properties of the object rather
than of any observation of it.

**What is gone, and why.** `st_ctime_ns` was the third component and is not
one. POSIX allows a filesystem that does not maintain access times to report
them through `st_ctime`, and a bridge that cannot tell whether a candidate is
a command has to open and read it — so on such a filesystem the act of
deciding moves the very value the key is built from, and the redelivery of
one file arrives under an id the ledger has never seen. That is what macOS
does (daedalus issue 1411), and it is a property of reading a file, not of
the filesystem: the same two reads, microseconds apart, agree, which is why
an in-process control cannot see it and an end-to-end one can.

**What takes its place.** The generation: how many times this drain has
vacated that name. It has to cover the one way an object at a name can change
while keeping the name's inode — this drain removing the file and the name
later being filled by a file that got the inode back — and it covers it by
counting its own removals. It needs no second signal for the other case: a
publisher replacing a file whose removal failed is already a different inode,
because a file that is still there holds its own and no replacement can have
it. That was measured, not assumed — a generation that advanced only on an
object change and not on a vacate passed every control there was, because
the inode was already separating those drops on its own.

A redelivery fires no signal at all, which is what makes its id stable.

The generation lives in this process, so a restart and the eviction past the
bound both return a name to its first value. That residual is unchanged by
this derivation and is disclosed in `stream_service`'s own docstring.
"""
import threading

# {name: generation}, the number of times this drain has vacated that name.
# Bounded like the refusal registry beside it in the stream service, and
# forgotten the same way: past the bound a name starts again from its first
# value, which costs the residual a restart already costs.
_generations = {}
_GENERATION_LIMIT = 4096
_lock = threading.Lock()


def _trim():
    while len(_generations) > _GENERATION_LIMIT:
        del _generations[next(iter(_generations))]


def stamp(data, name, ident):
    """Give a delivered legacy command the delivery id its redelivery needs.

    A legacy file is published by an external writer and carries no `_did`,
    unlike a queued command, so a removal that fails redelivers it with
    nothing for the consumer to deduplicate on. A publisher's own id is kept
    when it is a non-empty string — the extension's frame handler tests
    `_did` for truth, so an empty one deduplicates nothing — and the id is
    spelled with characters the delivery-result path accepts, because the
    consumer posts it back as a delivery id.
    """
    if isinstance(data.get('_did'), str) and data['_did']:
        return
    device, inode = ident[0], ident[1]
    data['_did'] = f'legacy-{device}-{inode}-{_generation(name)}'


def _generation(name):
    """The generation `name` has reached."""
    with _lock:
        return _generations.get(name, 0)


def vacated(name):
    """Record that this drain removed the file at `name`.

    The unlink is the one event that says the next file there is a new
    command: it is the only way an object at a name can change while
    keeping the name's inode, because a file that is still there holds its
    own and no replacement can have it. So a publisher replacing a file
    whose removal failed is already a different inode and needs nothing
    from here, and a removal that did succeed is the case this covers.
    """
    with _lock:
        _generations[name] = _generations.get(name, 0) + 1
        _trim()
