"""Deliberately failing suite: plants a genuine coverage failure for issue 933."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _util  # noqa: E402


def test_deliberate_failure():
    assert False, 'issue 933 plant: this suite must fail'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
