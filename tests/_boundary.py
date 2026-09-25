"""The extension-boundary scenarios, and how one is run.

Each scenario drives the shipped background script through one boundary —
relay capacity, delivery-id dedup across a restart, a rejected upload, a
partitioned cookie — inside the fake browser from _boundary_env, and returns
what the worker did as JSON. Every run is checked against the scenario's
declared plan with `assert_gate_clean` before the answer is handed back, so
a request the worker invents is loud even when the scenario reads only a
projection of the record.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boundary_env import (  # noqa: E402
    ENVIRONMENT, RESULT, SCENARIO_PLANS, run_node_program)
from _noderun import ChildDeadlineExceeded  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _boundary_scenarios import SCENARIOS  # noqa: E402
from _hotfix_quota_scenario import HOTFIX_SCENARIOS  # noqa: E402
from _stream_fake import assert_gate_clean  # noqa: E402

HARNESS = ENVIRONMENT + SCENARIOS + HOTFIX_SCENARIOS


def _run(scenario, background_path=None, payload=None):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension boundary'
    if background_path is None:
        background_path = EXTENSION_ROOT / 'background.js'
    result = run_node_program(
        node, HARNESS, [str(background_path), scenario], cwd=ROOT,
        payload=payload)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    outcome = json.loads(result.stdout)
    # The real harness always emits a {result, gate} object; the argv- and
    # temp-file controls deliberately substitute a stub program that emits
    # something else. `result` is read by index: `.get` would type the
    # runner's answer as Optional and make every scenario that subscripts it
    # a type error, for a key the harness cannot omit.
    if isinstance(outcome, dict) and 'gate' in outcome:
        _assert_scenario_gate(scenario, outcome['gate'])
        return outcome['result']
    return outcome


def _assert_plan_clean(planned: dict, gate: dict) -> None:
    assert_gate_clean(
        contract_faults=gate.get('contractFaults', []),
        records=gate.get('records', []), refused=gate.get('refused', []),
        bad_origins=gate.get('badOrigins', []),
        stream_answered=gate.get('streamAnswered', []),
        planned=list(planned['planned']),
        planned_stream=list(planned['planned_stream']))


def _assert_scenario_gate(scenario, gate: dict) -> None:
    _assert_plan_clean(SCENARIO_PLANS[scenario], gate)


def run_extension_result_boundary(scenario):
    return _run(scenario)


def run_extension_hotfix_quota(plan):
    """Drive the hotfix store's byte bound through the shipped worker."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension hotfix path'
    # One dispatched command per step and one posted result for each, so the
    # result count is the caller's step count. The gate is given the same
    # list: a request past it is refused, and the run is checked against it.
    declared = dict(SCENARIO_PLANS['hotfix-quota'])
    declared['planned'] = (list(declared['planned'])
                           + [RESULT] * len(plan['steps']))
    program = HARNESS + '\nplan = ' + json.dumps(declared) + ';\n'
    try:
        result = run_node_program(
            node, program,
            [str(EXTENSION_ROOT / 'background.js'), 'hotfix-quota'],
            cwd=ROOT, payload=json.dumps(plan))
    except ChildDeadlineExceeded as failure:
        # The two ways a worker stops answering are different failures and
        # are reported differently. A promise that never resolves drains
        # node's event loop, so the child EXITS with an empty stdout and the
        # assertion below names it. A worker in a loop that never returns
        # never exits, and this is the branch that names it: the launcher's
        # detector killed the child, the cleanup reaped it, and the evidence
        # is the worker's own output plus what the cleanup did. Without this
        # the suite ceiling would end the run instead — and it SIGTERMs the
        # suite, leaving the child alive and reparented, naming no test.
        raise AssertionError(
            'the hotfix-quota scenario never finished: the worker stopped '
            'answering a command it had been given') from failure
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    assert result.stdout.strip(), (
        'the hotfix-quota scenario produced no answer: the worker stopped '
        f'answering (rc={result.returncode}, '
        f'stderr={result.stderr[:400]!r})')
    outcome = json.loads(result.stdout)
    _assert_plan_clean(declared, outcome['gate'])
    return outcome['result']


def run_extension_capability_routes(routes, background_path=None):
    """Probe a module's published command routes in one worker process."""
    return _run('capability-routes', background_path=background_path,
                payload=json.dumps(routes))


def run_extension_command_result(command):
    """Dispatch one command and return every result the worker posted."""
    return _run('unknown-command', payload=json.dumps(command))


def observe_extension_worker_paths():
    """Return the honest worker's loader trace of requested module paths.

    This consumes a Node vm trace, not a security boundary. Host functions
    expose host-realm intrinsics to deliberately hostile worker source, which
    can therefore forge the recorded array. The inventory guard uses this to
    catch honest split drift only.
    """
    return tuple(Path(item) for item in _run('worker-sources'))
