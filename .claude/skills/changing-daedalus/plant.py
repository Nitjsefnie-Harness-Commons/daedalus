#!/usr/bin/env python3
"""Save a file's bytes before planting a defect, and put them back after.

A path-scoped VCS restore is a statement about the whole path: `git
checkout -- f` also sets `f` to whatever HEAD holds, so using it to undo a
planted probe silently discards the uncommitted work that path carried.
This helper writes back the bytes it read instead, and re-reads the file to
prove the write landed, so the proof is in its own output.
"""
import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile

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
    """Whether the path is clean against HEAD, or 'unknown'.

    A signal for the operator, not a gate: saving a dirty file is exactly
    what makes the restore safe, so a dirty path is never a refusal.
    """
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


def _write(target, payload):
    with open(target, 'wb') as handle:
        handle.write(payload)


def save(path, store):
    entry = _entry(store, path)
    if os.path.exists(entry):
        return _refuse(
            f'{path} already has a stored copy at {entry}; restore it '
            'before saving this path again')
    try:
        with open(path, 'rb') as handle:
            payload = handle.read()
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError as why:
        return _refuse(f'cannot save {path}: {why}')
    os.makedirs(entry)
    _write(os.path.join(entry, 'bytes'), payload)
    _write(os.path.join(entry, 'mode'), f'{mode:o}\n'.encode('ascii'))
    print(f'saved {path}: {_head_state(path)} against HEAD, '
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
        _write(path, payload)
        os.chmod(path, mode)
        with open(path, 'rb') as handle:
            written = handle.read()
    except OSError as why:
        return _refuse(f'cannot restore {path}: {why}')
    if written != payload:
        return _refuse(
            f'{path} read back {len(written)} bytes after {len(payload)} '
            f'were written; the stored copy is still at {entry}')
    shutil.rmtree(entry)
    print(f'restored {path}: {len(written)} bytes re-read, match')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='plant.py',
        description='Save a file before planting a defect, restore it after.')
    parser.add_argument('action', choices=('save', 'restore'))
    parser.add_argument('file', help='the path to save or restore')
    parser.add_argument(
        '--store', default=os.path.join(tempfile.gettempdir(),
                                        'daedalus-plants'),
        help='where the copies live (default: %(default)s)')
    args = parser.parse_args(argv)
    path = os.path.abspath(args.file)
    store = os.path.abspath(args.store)
    if args.action == 'save':
        os.makedirs(store, exist_ok=True)
        return save(path, store)
    return restore(path, store)


if __name__ == '__main__':
    sys.exit(main())
