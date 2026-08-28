"""Evaluate and guard workflow jobs against implicit skip propagation."""
import re

from _ghexpr import evaluate_if
from _wfgraph import _job_if_expression, _job_names, _job_needs


_STATUS_CHECK = re.compile(
    r'\b(?:always|cancelled|failure)\s*\(\s*\)')


def evaluate_job_condition(workflow, job, event_name, needs, status):
    """Return a job's real condition and its value in one status context."""
    expression = _job_if_expression(workflow, job)
    assert expression is not None, job
    context = {
        'github': {'event_name': event_name},
        'needs': needs,
        'status': status,
    }
    return expression, evaluate_if(expression, context)


def implicit_skip_violations(workflow):
    """Describe dependants that can inherit a conditional ancestor's skip."""
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
        condition = _job_if_expression(workflow, job)
        if condition is not None and _STATUS_CHECK.search(condition):
            continue
        for ancestor in sorted(ancestors):
            if _job_if_expression(workflow, ancestor) is not None:
                violations.append(
                    f'{job}: skippable ancestor {ancestor}; '
                    f'condition={condition!r}')
    return violations
