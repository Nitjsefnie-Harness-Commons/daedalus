#!/usr/bin/env python3
"""The serviced bounds of the same-id overlap prelude, driven directly.

The harness source is cut before its own entry IIFE, so each control drives
one bound in a real node child; starvation is a deterministic busy-wait
freeze on the single node thread, and the crediting a bound spent is read
back from the [bound] settlement record the bound writes on stderr.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _util  # noqa: E402


_SAMPLE_MS = 100
_CREDIT_CAP_MS = 2 * _SAMPLE_MS

# The entry IIFE is the one statement that drives the whole cookie flow;
# everything before it is the bound machinery under test. The newline
# anchor is load-bearing: `await waitFor(async () => {` also matches a
# bare opening brace pair mid-function.
_ENTRY_IIFE = '\n(async () => {'

_SETTLE_DRIVER = """
(async () => {
  let outcome = 'resolved';
  try {
    await %s;
  } catch (error) { outcome = error.message; }
  process.stderr.write('\\n', () => process.stdout.write(
    outcome, () => process.exit(0)));
})();
"""

# A freeze past the whole bound, with the work settling one timers phase
# after the thaw. A wall guard whose deadline passed inside the freeze is
# swept in that same timers phase, ahead of the resolution; a setImmediate
# settle would land in the thaw iteration's check phase, which runs before
# the guard's timers phase and would let finished work win the race.
_FREEZE_THEN_SETTLE = """
const work = new Promise((resolve) => {
  setTimeout(() => {
    const until = Date.now() + 900;
    while (Date.now() < until) {}
    setTimeout(resolve, 0);
  }, 0);
});
"""

# The work arrives only after a freeze outrunning the wait's whole budget,
# so a deadline kept on the wall clock is already spent at the first poll
# after the thaw.
_STARVED_PREDICATE = """
let arrived = false;
const predicate = async () => arrived;
setTimeout(() => {
  const until = Date.now() + 900;
  while (Date.now() < until) {}
}, 0);
setTimeout(() => { arrived = true; }, 100);
"""

# A real caller's wait over the shipped plumbing: loadConfig arms the
# freeze, so the handler-start wait is starved past a wall deadline that
# was already spent when the loop was serviced again.
_CALLER_FREEZE_WORKER = """
async function loadConfig() {
  setTimeout(() => {
    const until = Date.now() + 900;
    while (Date.now() < until) {}
  }, 0);
}

async function dispatchCommand(command) {
  await new Promise((resolve) => setTimeout(resolve, 10));
  const result = await chrome.cookies.getAll({ domain: command.domain });
  await fetch('test-bridge/result', {
    method: 'POST',
    body: JSON.stringify({ id: command.id, result }),
  });
}
"""

_HUNG_WORK = """
const work = new Promise(() => {});
"""


def _bound_source(work, call):
    """The shipped prelude's bound machinery, driven by one control."""
    prelude = _overlap._BACKGROUND_OVERLAP_HARNESS.split(
        _ENTRY_IIFE, maxsplit=1)[0]
    return prelude.replace('__IMPORT_SCRIPTS_STUB__', ';') + work + (
        _SETTLE_DRIVER % (call,))


def _bound_run(source, timeout_s=30):
    """Run one bound control in a node child, streams captured as text."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension worker'
    result = subprocess.run(
        [node, '-e', source, '', '[]', '[]', '', 'bound-token', '0', '1000'],
        capture_output=True, text=True, timeout=timeout_s, check=False)
    # The source is the bulk of a child's repr; naming the streams keeps a
    # failure one readable diagnostic instead of the whole harness text.
    assert result.returncode == 0, (result.returncode, result.stderr)
    return result


def _bound_record(result):
    """The crediting record the one bound in a child wrote when it settled."""
    records = re.findall(r'^\[bound\] (.+)$', result.stderr, re.MULTILINE)
    assert len(records) == 1, (records, result.stderr)
    return json.loads(records[0])


def _worker(tmp, source):
    path = Path(tmp) / 'background.js'
    path.write_text(source, encoding='utf-8')
    return path


def test_bounded_survives_a_freeze_longer_than_its_bound(tmp):
    """Starved work that settles after the thaw is not rejected.

    Kills the wall-clock guard mutation: the old bound armed one wall
    setTimeout for its whole budget, and a freeze longer than the budget
    let the guard's rejection be swept in the thaw's timer phase, before
    the work's setImmediate turn, so completed work was rejected.
    """
    del tmp
    result = _bound_run(_bound_source(
        _FREEZE_THEN_SETTLE, "bounded(work, 'frozen work', 300)"))
    assert result.stdout == 'resolved', result.stdout


def test_wait_for_survives_a_starve_past_its_whole_budget(tmp):
    """A starved wait keeps polling instead of spending a wall deadline.

    Kills the wall-clock deadline mutation of the caller: waitFor holds
    its deadline in the serviced clock, so a freeze past the whole budget
    is not time the wait was given and the poll after the thaw still has
    budget left to find the work that arrived during the freeze.
    """
    del tmp
    result = _bound_run(_bound_source(
        _STARVED_PREDICATE,
        "waitFor(predicate, 'the starved wait to finish', 400)"))
    assert result.stdout == 'resolved', result.stdout


def test_a_non_settling_step_rejects_once_serviced_for_its_budget(tmp):
    """A hung step still rejects on its label, on serviced evidence only.

    Kills the no-bound mutation: without the serviced sampler the label
    would have to come back from a wall timer, and the record proving the
    budget was spent in capped credits would not exist. The rejection is
    asserted after serviced time at least the budget, never after wall
    time.
    """
    del tmp
    result = _bound_run(_bound_source(
        _HUNG_WORK, "bounded(work, 'a step that never settles', 300)"))
    assert result.stdout == (
        'timed out waiting for a step that never settles'), result.stdout
    record = _bound_record(result)
    assert record['servicedMs'] >= 300, record
    assert record['maxCreditMs'] <= _CREDIT_CAP_MS, record


def test_a_freeze_is_credited_its_cap_not_its_length(tmp):
    """One freeze credits at most twice the sampler interval.

    Kills the uncapped-credit mutation: a gap credited at its wall length
    would let one deep freeze spend the whole budget, and a starved child
    would reach an inner label instead of the outer backstop.
    """
    del tmp
    result = _bound_run(_bound_source(
        _FREEZE_THEN_SETTLE, "bounded(work, 'frozen work', 3000)"))
    assert result.stdout == 'resolved', result.stdout
    record = _bound_record(result)
    assert record['samples'] >= 1, record
    assert record['maxCreditMs'] == _CREDIT_CAP_MS, record
    assert record['servicedMs'] <= (
        record['samples'] * _CREDIT_CAP_MS), record


def test_the_harness_wait_survives_a_starved_child(tmp):
    """The handler-start wait keeps its budget across a starved childhood.

    Kills the caller-reverted mutation (only waitFor's deadline back to
    Date.now() while bounded stays serviced): the wait is driven here
    through the shipped caller, whose budget arrives as the innerWait
    argument, so the deadline arithmetic that must be serviced is the
    caller's own, not a helper's.
    """
    actual = _overlap.run_background_overlap(
        _worker(tmp, _CALLER_FREEZE_WORKER),
        [{'id': '_cookies', 'domain': 'owner-a'}],
        ['owner-a'], inner_wait=0.5)
    assert actual == [{
        'id': '_cookies',
        'owner': 'owner-a',
        'deliveryId': None,
    }], actual


def test_a_bound_record_cannot_enter_a_step_trace(tmp):
    """The settlement record is inert to the step-trace regex."""
    del tmp
    result = _bound_run(_bound_source(
        _HUNG_WORK, "bounded(work, 'a step that never settles', 300)"))
    assert '[bound] ' in result.stderr, result.stderr
    steps = re.findall(r'^\[step\] (.+)$', result.stderr, re.MULTILINE)
    assert steps == [], steps


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
