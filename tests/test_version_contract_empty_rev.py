#!/usr/bin/env python3
"""The empty --rev refusal: one message, every mode, whatever the tree holds.

A present-but-empty --rev builds the `:<path>` spec, which git reads as the
index, so a run asking for a revision was answered about some other source.
test_version_contract.py holds the checker's other contracts; this test was
moved here when that file met its 700-line ceiling.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_version_contract import (  # noqa: E402
    _run_checker,
    _versioned_git_tree,
)


def test_check_versions_refuses_an_empty_revision_name(tmp):
    """--rev '' is a present --rev with no name in it, and the spec it builds
    (`:<path>`) is what git reads as the index, so an empty --rev silently
    describes something other than the revision the run asked for. Every
    mode has to refuse it rather than answer about some other source."""
    copy_root, checker = _versioned_git_tree(tmp)
    for argv, normal_run in (
            (['--rev', ''], False), (['--rev='], False),
            (['--rev', 'HEAD', '--rev', ''], False),
            (['--rev', '', '--rev', 'HEAD'], True),
            (['--print', '--rev', ''], False),
            (['--staged', '--rev', ''], False)):
        r = _run_checker(copy_root, *argv)
        if normal_run:
            # Repeated --rev is last-wins, so this run asked for HEAD. This
            # direction separates the parsed-value guard from an argv-scan
            # mutant, which refuses every run it saw `--rev` in.
            assert r.returncode == 0, (argv, r.returncode, r.stderr)
            continue
        assert r.returncode != 0, (argv, r.returncode, r.stdout, r.stderr)
        if argv[0] == '--print':
            # --print feeds another program, so its stdout stays empty.
            assert r.stdout == '', (argv, r.stdout)
        if argv[0] == '--staged':
            # argparse rejects the combination before main() runs: refused
            # as a conflict, not by the empty-rev refusal the rest reach.
            assert '--staged' in r.stderr and '--rev' in r.stderr, r.stderr
            continue
        assert 'cannot read an empty revision name' in r.stderr, (
            argv, r.stdout, r.stderr)

    # Nor may the refusal depend on the tree being clean: the dirty tree is
    # where the old behavior mislabeled the index as the working tree.
    manifest = copy_root / 'extension' / 'manifest.json'
    text = manifest.read_text(encoding='utf-8')
    dirty_text, n = re.subn(r'"version"\s*:\s*"[^"]+"',
                            '"version": "9.9.9.9"', text)
    assert n == 1, text
    manifest.write_text(dirty_text, encoding='utf-8')
    before = {p: (copy_root / p).read_bytes() for p, _, _ in checker.SITES}
    dirty = _run_checker(copy_root, '--rev', '')
    assert dirty.returncode != 0, (dirty.returncode, dirty.stdout)
    assert 'cannot read an empty revision name' in dirty.stderr, dirty.stderr
    for path, body in before.items():
        assert (copy_root / path).read_bytes() == body, path


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='versioncontract_empty_rev_')


if __name__ == '__main__':
    raise SystemExit(main())
