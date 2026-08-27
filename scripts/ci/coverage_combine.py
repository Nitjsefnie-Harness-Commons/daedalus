#!/usr/bin/env python3
"""Combine parallel coverage data files without dropping non-duplicates.

`coverage combine` skips any parallel data file whose embedded content hash
it has already seen. Equal hashes do not imply equal data: one hash value was
observed covering 15 distinct datasets across 145 of this repo's own parallel
files (issue #266), and `combine` kept one file from each collision and threw
the rest away. Every combinable file is unioned here instead; nothing is
skipped by hash.
"""
import sys

import coverage
import coverage.data


def main():
    # Reference the real method first: if coverage.py ever renames or
    # removes DataFileClassifier.classify, this line raises AttributeError
    # -- the bare assignment below would otherwise create an unused
    # attribute while combine() quietly reverts to skipping by hash, the
    # exact silent-undercount shape this script exists to end.
    assert callable(coverage.data.DataFileClassifier.classify)
    coverage.data.DataFileClassifier.classify = staticmethod(
        lambda f: "combine")

    cov = coverage.Coverage()
    cov.combine(strict=True, keep=False)
    cov.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
