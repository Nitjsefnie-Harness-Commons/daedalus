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

**What takes its place.** The generation, which this module keeps per name:
how many times the object standing at that name has changed. It advances
whenever either signal fires — the drain vacated the name by unlinking a file
it had delivered, or a different object arrived under the name. Those two
signals are what the generation has to distinguish, and between them they
close both ways an object at a name can change identity: a name this drain
emptied and whose inode was then recycled, and a name whose file it could not
remove and whose publisher replaced in place — the second impossible to miss,
since a file that is still there holds its inode and a replacement cannot have
it. A redelivery fires neither signal, which is exactly what makes its id
stable.

The generation lives in this process, so a restart and the eviction past the
bound both return a name to its first value. That residual is unchanged by
this derivation and is disclosed in `stream_service`'s own docstring.
"""
import threading

# {name: ((st_dev, st_ino), generation)}. Bounded like the refusal registry
# beside it in the stream service, and forgotten the same way: past the bound
# a name starts again from its first value, which costs the same residual the
# restart already costs.
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
    data['_did'] = f'legacy-{device}-{inode}-{_generation(name, device, inode)}'


def _generation(name, device, inode):
    """The generation `name` has reached for the object standing at it."""
    with _lock:
        recorded = _generations.get(name)
        if recorded is not None and recorded[0] == (device, inode):
            return recorded[1]
        generation = 0 if recorded is None else recorded[1] + 1
        _generations[name] = ((device, inode), generation)
        _trim()
        return generation


def vacated(name):
    """Record that this drain removed the file at `name`, so the next file
    there is a new command.

    The generation advances without recording which object is gone, so the
    next object at the name advances again whether or not it landed on the
    inode just freed.
    """
    with _lock:
        recorded = _generations.get(name)
        _generations[name] = (
            recorded[0] if recorded is not None else (None, None),
            (0 if recorded is None else recorded[1]) + 1)
        _trim()