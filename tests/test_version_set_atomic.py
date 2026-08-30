#!/usr/bin/env python3
"""--set is none or all: a run that aborts partway rewrites nothing.

apply() planned and wrote one file at a time, so a tree inconsistent at a
later file left the earlier ones already rewritten. test_version_contract.py
holds the checker's other contracts; this module holds the atomicity one.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_version_contract import (  # noqa: E402
    _copy_versioned_tree,
    _duplicate_the_page_js_version,
    _run_checker,
)


def test_check_versions_set_writes_every_site_or_none(tmp):
    """--set rewrites every site or none, however argparse spells the flag.

    The issue's repro appends a same-quote duplicate to page.js; the
    duplicate check refuses the site, and under the defect the files
    planned before it were already on disk when the run aborted."""
    spellings = (['--set', '1.2.3'], ['--set=1.2.3'])
    for i, argv in enumerate(spellings):
        dirty_root = Path(tmp) / f'dirty{i}' / 'tree'
        checker = _copy_versioned_tree(dirty_root)
        _duplicate_the_page_js_version(dirty_root, quote="'")
        before = {p: (dirty_root / p).read_bytes()
                  for p, _, _ in checker.SITES}
        aborted = _run_checker(dirty_root, *argv)
        assert aborted.returncode != 0, (
            argv, aborted.returncode, aborted.stdout, aborted.stderr)
        assert 'FAIL' in aborted.stderr, (argv, aborted.stderr)
        # The refusal is for the reason the repro sets up: the duplicate,
        # not some unrelated refusal a wrong-reason abort would hide.
        assert 'matches 2 times' in aborted.stderr, (argv, aborted.stderr)
        # _find_site writes the FAIL during planning; the `set <path> ->`
        # progress output is what waits for planning, so an abort leaves
        # it unprinted.
        assert not any(line.startswith('set ')
                       for line in aborted.stdout.splitlines()), (
            argv, aborted.stdout)
        for path, body in before.items():
            assert (dirty_root / path).read_bytes() == body, (argv, path)

    # The positive control: the same tree without the duplicate still gets
    # every site written, so the atomic path did not stop writing at all.
    for i, argv in enumerate(spellings):
        clean_root = Path(tmp) / f'clean{i}' / 'tree'
        checker = _copy_versioned_tree(clean_root)
        clean = _run_checker(clean_root, *argv)
        assert clean.returncode == 0, (argv, clean.returncode, clean.stderr)
        for path, desc, pattern in checker.SITES:
            site_text = (clean_root / path).read_text(encoding='utf-8')
            site = re.search(pattern, site_text)
            assert site and site.group('v') == '1.2.3', (argv, path, desc)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='version_set_atomic_')


if __name__ == '__main__':
    raise SystemExit(main())
