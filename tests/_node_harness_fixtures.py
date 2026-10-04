"""The fixtures the Node-subprocess dashboard, overlap and boundary suites each
copied.

Four helpers travelled here rather than staying local: two suites built the
dashboard Node harness from a source string the same way, two overlap suites
wrote a temporary `background.js` the same way, a dashboard retry suite
and an overlap bound suite read the crediting record a bound writes back the
same way, and a dashboard command-line suite and the extension boundary
suite measured the argv Windows refuses the same way. Each was a
byte-identical copy, so a fix to one shape reached one suite and not the
other.

The names say what the fixture does in these suites rather than what it is
generically. `_harness` cannot be kept: `tests/test_dashboard_tab_events.py`
binds a `_harness` of its own with a different body, and the retired
reserved-name guard read that owner set as a set, so a shared module could
not publish the name. The builder is therefore named for the
harness it returns, and the temporary file for the file it writes.
"""
import json
import re
from pathlib import Path

import _dashnode


def _node_harness(source, bounded_steps=0, module=False):
    return _dashnode.DashboardNodeHarness(
        source, bounded_steps=bounded_steps, module=module)


def _bound_record(result):
    """The crediting record the one bound in a child wrote when it settled."""
    records = re.findall(r'^\[bound\] (.+)$', result.stderr, re.MULTILINE)
    assert len(records) == 1, (records, result.stderr)
    return json.loads(records[0])


def _background_worker_file(tmp, source):
    path = Path(tmp) / 'background.js'
    path.write_text(source, encoding='utf-8')
    return path


def _command_line_length(argv):
    """The length Windows measures: the arguments joined by one space."""
    return sum(len(argument) + 1 for argument in argv)
