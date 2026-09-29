"""Which workflow jobs run the test suites, read off what the jobs run.

Shared, because `run_tests.py` gives every suite its own process: a
control that imported a sibling suite for this would re-execute that
suite's whole body and read a private copy, which is the defect
`tests/test_suite_import_boundaries.py` names. Two controls read it from
here, so they cannot disagree about which jobs run suites.

The marker is a substring of a step's `run:` text, so it recognises every
invocation spelling of the sanctioned runners and nothing else. The
complement — routes into the suite tree that no runner basename names — is
claimed explicitly by the control that owns it, because a predicate over
two names is a fingerprint of the property rather than the property.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _wfgraph import _job_names  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402

ROOT = _util.ROOT
WORKFLOW_DIR = ROOT / '.github' / 'workflows'
SUITE_RUNNERS = ('run_tests.py', 'coverage_suites.py')


def _ordered_job_runs(workflow, job):
    """The ordered `run:` values of every step in one workflow job."""
    mapping = complete_job_mapping(workflow, job)
    assert mapping is not None, f'the workflow has no {job} job'
    return [step.get('run', '') for step in mapping['steps']]


def _workflow_jobs(marker):
    """Every job in every workflow with a step whose `run:` names `marker`.

    Read off what the jobs run, not off a list of their names: a job that
    globs the suites belongs to this control whichever workflow file it was
    added to, and a control that could only see one file was exactly the
    defect a third job in a second file walked past.
    """
    found = []
    for source in sorted(WORKFLOW_DIR.glob('*.yml')):
        workflow = source.read_text(encoding='utf-8')
        for job in _job_names(workflow):
            runs = _ordered_job_runs(workflow, job)
            if any(marker in run for run in runs):
                found.append((source.name, job, runs))
    return found
