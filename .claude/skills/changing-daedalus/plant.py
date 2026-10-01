#!/usr/bin/env python3
"""Save a file's bytes before planting a defect, and put them back after.

A path-scoped VCS restore is a statement about the whole path: `git
checkout -- f` also sets `f` to whatever HEAD holds, so undoing a planted
probe with one silently discards the uncommitted work that path carried.
This writes the bytes it read back instead, in one atomic step onto what
the path resolves to, and refuses a store it cannot read.
"""
import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone

GIT_TIMEOUT = 30


def _refuse(message):
    print(message, file=sys.stderr)
    return 1


def _entry(store, path):
    return os.path.join(store, hashlib.sha256(
        path.encode('utf-8', 'surrogateescape')).hexdigest())


def _git(path, *args):
    return subprocess.run(
        ['git', '-C', os.path.dirname(path), *args],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False)


def _head_state(path):
    """Clean, dirty, or 'unknown' - a signal, never a gate."""
    try:
        inside = _git(path, 'rev-parse', '--is-inside-work-tree')
        if inside.returncode or inside.stdout.strip() != 'true':
            return 'unknown'
        status = _git(path, 'status', '--porcelain', '--',
                      os.path.basename(path))
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    if status.returncode:
        return 'unknown'
    return 'dirty' if status.stdout.strip() else 'clean'


def _publish(target, payload):
    """Replace `target` in one step, never a partial file - the shape of
    `daedalus_bridge/result_store.py`'s atomic publish. Truncate-then-write
    needs write permission on the file; `os.replace` needs the directory's,
    so a read-only target still restores."""
    # Publish onto what the path resolves to: replacing the path itself
    # would destroy a symlinked target and leave the file behind it
    # holding the planted bytes.
    target = os.path.realpath(target)
    temp = os.path.join(
        os.path.dirname(target),
        f'.{os.path.basename(target)}.{uuid.uuid4().hex}.tmp')
    try:
        with open(temp, 'wb') as handle:
            handle.write(payload)
        # Windows refuses to replace a read-only target, so clear the flag
        # on the target itself; the caller re-applies the recorded mode.
        if os.path.exists(target) and not os.access(target, os.W_OK):
            try:
                os.chmod(target, stat.S_IMODE(os.stat(target).st_mode)
                         | stat.S_IWRITE)
            except OSError:
                # A file we may not chmod is not necessarily one we may
                # not replace, and raising here would replace the error
                # that explains what happened with one about a flag.
                pass
        os.replace(temp, target)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _differs_from(path, payload):
    """True when the target's bytes differ, None when they could not be
    read: a comparison that was not made is not one that found no
    difference."""
    if not os.path.exists(path):
        return True
    try:
        with open(path, 'rb') as handle:
            return handle.read() != payload
    except OSError:
        return None


def _recorded_field(entry, name):
    try:
        with open(os.path.join(entry, name), 'r', encoding='utf-8',
                  errors='replace') as handle:
            return handle.read().strip()
    except OSError:
        return None


def _vouched_state(state):
    """The recorded state this helper stands behind, or 'unknown'."""
    return state if state in ('clean', 'dirty') else 'unknown'


def _recorded_state(entry):
    return _vouched_state(_recorded_field(entry, 'head-state'))


# Keyed by `_differs_from`'s own polarity, which is what every caller
# reads: True is a difference, False is not, None is no comparison.
_WHAT_PUBLISHED = {
    True: 'the file did not already hold these bytes',
    False: 'the file already held these bytes',
    None: 'the comparison with the file could not be made',
}


def _published_note(state, changed):
    clauses = [f'captured {state} against HEAD']
    # `unknown` also covers a work tree whose status could not be read,
    # and the bytes there may be exactly HEAD's.
    if state == 'dirty':
        clauses.append(
            "the published bytes are the worktree's, not what HEAD holds")
    clauses.append(_WHAT_PUBLISHED[changed])
    return '; ' + '; '.join(clauses)


def _stored_matches(path, entry):
    """True, False, or None when either side could not be read: a store
    that will not open its own bytes is not evidence that the file moved
    on."""
    try:
        with open(os.path.join(entry, 'bytes'), 'rb') as handle:
            stored = handle.read()
    except OSError:
        return None
    differs = _differs_from(path, stored)
    return None if differs is None else not differs


def _entry_advice(path, entry):
    """Advice that never recommends a restore it cannot show is lossless."""
    matched = _stored_matches(path, entry)
    if matched is None:
        return ('but neither that copy nor the file could be read, so '
                'whether restoring it would lose anything is not known - '
                f'discard the entry with `plant.py clear {path}` and save '
                'again')
    if matched:
        return ('the file still matches that copy, so restoring it loses '
                'nothing - run `plant.py restore` for this path, or '
                f'`plant.py clear {path}` to discard the entry')
    return ('but the file has changed since that copy was taken, and '
            'restoring it would overwrite the newer change - discard the '
            f'entry with `plant.py clear {path}` and save again')


def _entry_detail(entry):
    """What the entry recorded; its directory is named by a hash."""
    parts = []
    for name, label in (('path', 'path'), ('saved-at', 'saved')):
        value = _recorded_field(entry, name)
        if value:
            parts.append(f'{label} {value}')
    state = _recorded_field(entry, 'head-state')
    if state is not None:
        parts.append(f'captured {_vouched_state(state)}')
    return f' ({", ".join(parts)})' if parts else ''


def _remove_entry(entry, restored=None):
    """Discard `entry`, or name why it is still there.

    `restore` reaches this after the bytes are published, so a refusal
    that reads as a save to redo would send the operator back into a
    store that already holds a copy; it names what did land instead.
    """
    try:
        shutil.rmtree(entry)
    except OSError as why:
        done = f'{restored} was restored and ' if restored else ''
        return _refuse(f'{done}the stored copy at {entry} could not be '
                       f'removed ({why}); it is still there')
    return 0


def clear(path, store):
    entry = _entry(store, path)
    if not os.path.isdir(entry):
        return _refuse(f'no stored copy of {path} under {store}; there is '
                       'nothing to clear')
    # Read before the removal, which takes the entry with it: printed
    # afterwards so a refusal is not preceded by a line announcing a
    # discard that did not happen.
    detail = _entry_detail(entry)
    removed = _remove_entry(entry)
    if removed:
        return removed
    print(f'discarding the stored copy of {path} at {entry}{detail}')
    return 0


def save(path, store):
    entry = _entry(store, path)
    if os.path.exists(entry):
        return _refuse(f'{path} already has a stored copy at {entry}; '
                       f'{_entry_advice(path, entry)}')
    try:
        with open(path, 'rb') as handle:
            payload = handle.read()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        state = _head_state(path)
        os.makedirs(entry)
        _publish(os.path.join(entry, 'bytes'), payload)
        _publish(os.path.join(entry, 'mode'), f'{mode:o}\n'.encode('ascii'))
        _publish(os.path.join(entry, 'path'),
                 path.encode('utf-8', 'surrogateescape'))
        _publish(os.path.join(entry, 'saved-at'),
                 datetime.now(timezone.utc).isoformat(
                     timespec='seconds').encode('ascii'))
        _publish(os.path.join(entry, 'head-state'),
                 f'{state}\n'.encode('ascii'))
    except OSError as why:
        return _refuse(f'cannot save {path}: {why}')
    print(f'saved {path}: {state} against HEAD, '
          f'{len(payload)} bytes in {entry}')
    return 0


def restore(path, store):
    entry = _entry(store, path)
    payload_path = os.path.join(entry, 'bytes')
    if not os.path.isfile(payload_path):
        return _refuse(
            f'no stored copy of {path} under {store}; save it before '
            'planting, because a restore that does nothing is the failure '
            'this exists to prevent')
    try:
        with open(payload_path, 'rb') as handle:
            payload = handle.read()
        with open(os.path.join(entry, 'mode'), 'r',
                  encoding='ascii') as handle:
            mode = int(handle.read().strip(), 8)
        state = _recorded_state(entry)
        # Decided from the bytes on disk before the publish, so the
        # report never has to read back a write it cannot prove.
        changed = _differs_from(path, payload)
        _publish(path, payload)
    except OSError as why:
        return _refuse(f'cannot restore {path}: {why}; the stored copy is '
                       f'still at {entry}')
    # Past here the bytes are published, so no refusal may read as a
    # restore that did not happen. This chmod arm has no in-suite
    # control: the target is the inode this process just created, so
    # chmod refuses only on EROFS, an immutable attribute or a Windows
    # sharing violation, none of which a suite can arrange portably.
    try:
        os.chmod(path, mode)
    except OSError as why:
        return _refuse(f'{path} was restored, but its recorded mode '
                       f'{mode:o} was not applied ({why}); the stored copy '
                       f'at {entry} is still there')
    removed = _remove_entry(entry, path)
    if removed:
        return removed
    print(f'restored {path}: {len(payload)} bytes published'
          f'{_published_note(state, changed)}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='plant.py',
        description='Save a file before planting a defect, restore it after.')
    parser.add_argument('action', choices=('save', 'restore', 'clear'))
    parser.add_argument('file', help='the path to save, restore or clear')
    parser.add_argument(
        '--store', default=os.path.join(tempfile.gettempdir(),
                                        'daedalus-plants'),
        help='where the copies live (default: %(default)s)')
    args = parser.parse_args(argv)
    path = os.path.abspath(args.file)
    store = os.path.abspath(args.store)
    if args.action == 'save':
        os.makedirs(store, exist_ok=True)
    return {'save': save, 'restore': restore,
            'clear': clear}[args.action](path, store)


if __name__ == '__main__':
    sys.exit(main())
