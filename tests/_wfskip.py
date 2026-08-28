"""Find graph edges where a conditional ancestor still controls a job."""
from _wfgraph import (
    _job_condition_runs, _job_if_expression, _job_names, _job_needs,
)


def _probe_context(needs, success, failure=False, cancelled=False):
    # Successful needs and full-run outputs are neutral for the graph probe.
    # Unknown output paths stay absent so an unsupported condition fails loud.
    outputs = {'workflows': 'true', 'docs_only': 'false'}
    return {
        'github': {'event_name': 'pull_request'},
        'needs': {
            job: {'result': 'success', 'outputs': outputs} for job in needs
        },
        'status': {
            'success': success,
            'failure': failure,
            'cancelled': cancelled,
        },
    }


def _skip_sensitive(workflow, job, needs):
    condition = _job_if_expression(workflow, job)
    if condition is None:
        return True
    outcomes = {
        'success': _job_condition_runs(
            workflow, job, context=_probe_context(needs, True)),
        'skipped': _job_condition_runs(
            workflow, job, context=_probe_context(needs, False)),
        'failure': _job_condition_runs(
            workflow, job,
            context=_probe_context(needs, False, failure=True)),
        'cancelled': _job_condition_runs(
            workflow, job,
            context=_probe_context(needs, False, cancelled=True)),
    }
    if outcomes['success'] != outcomes['skipped']:
        return not outcomes['skipped']
    return (not outcomes['skipped']
            and (outcomes['failure'] or outcomes['cancelled']))


def implicit_skip_violations(workflow):
    """Name dependants whose runnable states exclude an ancestor skip."""
    jobs = _job_names(workflow)
    direct_needs = {job: set(_job_needs(workflow, job)) for job in jobs}
    violations = []
    for job in jobs:
        ancestors = set()
        pending = list(direct_needs[job])
        while pending:
            ancestor = pending.pop()
            if ancestor in ancestors:
                continue
            ancestors.add(ancestor)
            pending.extend(direct_needs[ancestor])
        skippable = [
            ancestor for ancestor in sorted(ancestors)
            if _job_if_expression(workflow, ancestor) is not None
        ]
        if not skippable or not _skip_sensitive(
                workflow, job, direct_needs[job]):
            continue
        condition = _job_if_expression(workflow, job)
        for ancestor in skippable:
            violations.append(
                f'{job}: skippable ancestor {ancestor}; '
                f'condition={condition!r}')
    return violations
