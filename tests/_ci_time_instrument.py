"""The timing instrument both timing suites drive.

Each suite loads the module afresh so the recording under test is the
copy it is about to call, not one an earlier case already imported.
"""
import _util
from _repo import ROOT


def _time_tests():
    return _util.load(ROOT / 'scripts' / 'ci' / 'time_tests.py')
