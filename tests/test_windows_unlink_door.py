#!/usr/bin/env python3
"""A PLANT, to be dropped. Evidence for the Windows TemporaryDirectory door.

`tests/_noderun.py` raises `ChildDeadlineExceeded` from INSIDE two
`with tempfile.TemporaryDirectory(...)` blocks. On Windows a file another
process still holds open is not deletable, so a surviving grandchild
holding the inherited stdout/stderr write handle makes the `__exit__` of
either block raise `PermissionError` — and that propagates in place of the
classified error, leaving a reader a bare errno that names no child, no
deadline, no output and no cleanup report.

The branch's own comment already reasons about exactly this for the READ
door, and the read was reordered ahead of the cleanup to close it. The
UNLINK door is the same failure and was unnamed. This plant is the
reproduction: it forces the real detector against a real hung child that
spawns a real grandchild holding the real handles, and asserts which
exception escapes. `ChildDeadlineExceeded` is the pass; anything else —
`PermissionError` above all — is the red, and the message names the type.

It cannot be reproduced on POSIX, where an open handle does not stop
`rmtree`, so it is gated on `sys.platform == 'win32'` and SKIPS elsewhere.
A skip is a skip: on Linux and macOS this file asserts nothing and proves
nothing, and the only place it can run is a `windows-latest` leg.

**How to drop it.** This file is the whole commit. Revert it; nothing else
touches it.

    git revert <the SHA that adds this file>

No control is weakened, gated or disabled by being here: the launcher, the
`tests/_processtree.py` cleanup and the `Popen` path are all the real ones
and none of them is stubbed. The deadline is lowered at runtime and
restored, which is the pattern `tests/test_noderun_deadline.py` already
uses, so the detector fires in seconds rather than in 107.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _noderun  # noqa: E402
import _util  # noqa: E402

# The child: spawn a grandchild that INHERITS this process's stdout and
# stderr and then keeps writing to them, record the grandchild's pid, and
# hang. The inherited handles are the whole point — a hung child alone
# holds nothing once it is dead, so it cannot reproduce the unlink door.
# `process.argv[1]` is the first argument the launcher passes, because the
# prologue splices the program path out of argv.
_CHILD = """
const { spawn } = require('child_process');
const fs = require('fs');
const grandchild = spawn(process.execPath, ['-e',
  "setInterval(() => { process.stdout.write('g'); }, 100);"],
  { stdio: ['ignore', 'inherit', 'inherit'] });
fs.writeFileSync(process.argv[1], String(grandchild.pid));
setInterval(() => { process.stdout.write('p'); }, 100);
"""

# Long enough to outlive the detector and the cleanup, short enough that a
# leak is not a leaked CI worker.
_GRANDCHILD_LIFETIME_S = 600
# The detector is 107 s in the shipped launcher, which is right for a real
# child and far too long for a test. Lowered at runtime, restored after.
_DETECTOR_S = 3
_MARKER_POLL_S = 5


def _wait_for_marker(marker):
    """The grandchild's pid once the child has written it, else None."""
    deadline = time.monotonic() + _MARKER_POLL_S
    while time.monotonic() < deadline:
        if marker.exists() and marker.read_text(encoding='utf-8').strip():
            return marker.read_text(encoding='utf-8').strip()
        time.sleep(0.05)
    return None


def _kill_grandchild(marker):
    """Take the grandchild down, so a red plant does not leak a process.

    This runs after the escaping exception has been captured, so it cannot
    change what escaped; it only stops the runner inheriting a ten-minute
    node process.
    """
    recorded = _wait_for_marker(marker)
    if recorded is None:
        return 'the child never recorded a grandchild'
    pid = int(recorded)
    # No `/T` here on purpose. The grandchild is a single pid with nothing
    # of its own to take down, and
    # `tests/test_noderun_deadline.py::test_exactly_one_module_under_tests_
    # ends_a_child` requires the tree-kill invocation to appear in exactly
    # one tracked module under `tests/` — which is `_processtree.py`. A
    # second copy here would break a control this plant must not weaken.
    killer = (['taskkill', '/F', '/PID', str(pid)]
              if sys.platform == 'win32' else ['kill', str(pid)])
    try:
        subprocess.run(killer, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False, timeout=_GRANDCHILD_LIFETIME_S)
    except (OSError, subprocess.SubprocessError) as error:
        return f'could not take grandchild {pid} down: {error}'
    return f'took grandchild {pid} down'


def test_the_windows_unlink_door_does_not_replace_the_classified_error(tmp):
    """Force the detector and assert `ChildDeadlineExceeded` is what escapes.

    The assertion is the evidence. If the `TemporaryDirectory` unlink trips
    over the grandchild's inherited handle, `run_node_program` raises
    `PermissionError` and the reader loses the child, the deadline, the
    child's own output and the cleanup's report — which is the failure this
    plant exists to catch on a real Windows runner.
    """
    if sys.platform != 'win32':
        _util.skip('the TemporaryDirectory unlink door is Windows-only; '
                   'POSIX rmtree ignores an open handle')
    node = shutil.which('node')
    assert node, 'node is required to hang a real child'
    marker = Path(tmp) / 'grandchild.pid'
    real_deadline = _noderun.CHILD_DEADLINE_S
    _noderun.CHILD_DEADLINE_S = _DETECTOR_S
    escaped = None
    disposition = ''
    try:
        _noderun.run_node_program(node, _CHILD, [str(marker)], tmp)
    except BaseException as failure:  # noqa: BLE001
        escaped = failure
    finally:
        _noderun.CHILD_DEADLINE_S = real_deadline
        disposition = _kill_grandchild(marker)
    assert escaped is not None, (
        f'the detector did not fire within {_DETECTOR_S}s of a child that '
        f'never exits; {disposition}')
    assert isinstance(escaped, _noderun.ChildDeadlineExceeded), (
        'the classified error did not escape run_node_program, so the '
        'unlink door replaced it.\n'
        f'  what escaped: {type(escaped).__module__}.'
        f'{type(escaped).__name__}: {escaped}\n'
        f'  grandchild: {disposition}\n'
        '  a PermissionError here is TemporaryDirectory.__exit__ failing on '
        'a file the surviving grandchild still holds, because the raise '
        'sits inside the `with`.')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='windowsunlinkdoor_')


if __name__ == '__main__':
    raise SystemExit(main())
