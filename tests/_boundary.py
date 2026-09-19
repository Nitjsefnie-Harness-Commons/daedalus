"""The extension-boundary scenarios, and how one is run.

Not a suite itself — run_tests.py only loads `test_*.py`.

Each scenario drives the shipped background script through one boundary —
relay capacity, delivery-id dedup across a restart, a rejected upload, a
partitioned cookie — inside the fake browser from _boundary_env, and returns
what the worker did as JSON.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boundary_env import ENVIRONMENT, run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _boundary_scenarios import SCENARIOS  # noqa: E402

HARNESS = ENVIRONMENT + SCENARIOS


def run_extension_result_boundary(scenario):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension result path'
    result = run_node_program(
        node, HARNESS,
        [str(EXTENSION_ROOT / 'background.js'), scenario], cwd=ROOT)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_extension_capability_routes(routes, background_path=None):
    """Probe a module's published command routes in one worker process."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension command route'
    if background_path is None:
        background_path = EXTENSION_ROOT / 'background.js'
    result = run_node_program(
        node, HARNESS,
        [str(background_path), 'capability-routes'], cwd=ROOT,
        payload=json.dumps(routes))
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_extension_command_result(command):
    """Dispatch one command and return every result the worker posted."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension command route'
    result = run_node_program(
        node, HARNESS,
        [str(EXTENSION_ROOT / 'background.js'), 'unknown-command'], cwd=ROOT,
        payload=json.dumps(command))
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def observe_extension_worker_paths():
    """Return the honest worker's loader trace of requested module paths.

    This consumes a Node vm trace, not a security boundary. Host functions
    expose host-realm intrinsics to deliberately hostile worker source, which
    can therefore forge the recorded array. The inventory guard uses this to
    catch honest split drift only.
    """
    node = shutil.which('node')
    assert node, 'node is required to observe extension worker modules'
    result = run_node_program(
        node, HARNESS,
        [str(EXTENSION_ROOT / 'background.js'), 'worker-sources'], cwd=ROOT)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return tuple(Path(item) for item in json.loads(result.stdout))
