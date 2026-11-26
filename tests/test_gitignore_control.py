#!/usr/bin/env python3
"""The committed `.gitignore` is what the shipped generator derives.

The control, and the rule it implements, are stated in
`tests/_gitignore_control.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gitignore_control import control  # noqa: E402
from _repo import ROOT  # noqa: E402


def test_the_committed_ignore_file_is_what_the_generator_derives(tmp):
    del tmp
    verdict = control(ROOT)
    assert verdict.status == 'green', verdict.detail
    assert verdict.scope, 'the control compared against nothing'
