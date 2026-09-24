"""Machinery for staging a command candidate in a test's temporary tree.

Not a suite itself — run_tests.py only loads `test_*.py`. The helpers build
the command-candidate shapes the queue and legacy rows assert on: the queue
module loaded by path, a plain command file, a hard-linked or symlinked
alias of one, and a skip when the filesystem will not hold that alias.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_queue(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'command_queue.py', name=name)


def _write_command(path, identifier):
    path.write_text(
        json.dumps({'id': identifier, 'code': '1'}), encoding='utf-8')


def _hard_link(source, destination):
    try:
        os.link(source, destination)
    except (OSError, NotImplementedError):
        _util.skip('this filesystem will not hold a hard link')


def _symlink(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        _util.skip('this filesystem will not hold a symlink')
