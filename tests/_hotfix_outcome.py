"""How a replayed hotfix is read back out of its outcome.

`run_hotfix_case` answers a hotfix control with the whole run: the replay
entries each channel logged, and the map of document to the fixes that
document recorded. Two suites read the same two things off it, each with
its own copy of the reader, so a change to the shape of an outcome reached
one of them and not the other. They are here so there is one copy of each
to fix.

Both names are byte-identical moves. `_logs` is the same reader over the
other level and is bound only in `tests/test_hotfix_scope.py`, so it is not
a copy and stays where it is.
"""


def _errors(outcome):
    return [entry['text'] for entry in outcome['replay']
            if entry['level'] == 'error']


def _delivered(outcome):
    return {doc: hits for doc, hits in outcome['delivered'].items() if hits}
