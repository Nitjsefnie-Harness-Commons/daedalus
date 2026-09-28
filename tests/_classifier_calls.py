"""The two things a change-classifier control hands the classifier.

`classify_changes.classify` takes a workflow event and a `run` callable it
reads the changed paths through, so driving it needs a webhook event and a
stand-in for that read. Both classifier suites built both, each with its own
copy, so a change to either shape reached one of them and not the other.
They are here so there is one copy of each to fix.

`_recording_run` is a rename. It was `_recorder`, and the short name is
already held by two other suites over different bodies —
`tests/test_aggregate_gate.py` builds a `gh` read answering the own-run
query, and `tests/test_ci_gate.py` builds a callable installed onto a fake
gate caller — so a shared helper that adopted it would have made both of
them offenders of it (`test_helper_reimplementation.py`).

`_event` is a byte-identical move under its own name: it is bound nowhere
else in `tests/`.
"""


def _event(name='pull_request', repository='octo/daedalus',
           sha='a' * 40, pull_request='248', before='b' * 40):
    return {'name': name, 'repository': repository, 'sha': sha,
            'pull_request': pull_request, 'before': before}


def _recording_run(stdout):
    calls = []

    def run(argv):
        calls.append(argv)
        return stdout

    return calls, run
