"""The two things a change-classifier control hands the classifier.

`classify_changes.classify` takes a workflow event and a `run` callable it
reads the changed paths through, so driving it needs a webhook event and a
stand-in for that read. Both classifier suites use the copies here, so a
change to either shape reaches both of them.

`_recording_run` cannot be `_recorder`: that short name is already held by
`tests/test_aggregate_gate.py` over a different body — a `gh` read answering
the own-run query — so a shared helper does not adopt it.

`_event` is bound nowhere else in `tests/`, so it keeps its own name.
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
