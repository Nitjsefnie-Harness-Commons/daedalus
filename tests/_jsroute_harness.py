"""Running one JavaScript control under node and under the guard."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402
from _noderun import run_node_argv  # noqa: E402

RUNTIME_PREFIX = """const calls = [];
const extCmd = (...args) => calls.push(args);
const ordinary = () => undefined;
const chromeTab = 41;
"""


def runtime_and_guard(source, path):
    script = (RUNTIME_PREFIX + source
              + "\nconst routed = calls.some(args => args.some(\n"
              + "  value => value && value.tab === chromeTab));\n"
              + "process.stdout.write(routed ? '1' : '0');\n")
    path.write_text(script, encoding='utf-8')
    node = shutil.which('node')
    assert node, 'node is required to execute JavaScript routing controls'
    ran = run_node_argv(node, [str(path)], _util.ROOT)
    assert ran.returncode == 0, (ran.returncode, ran.stdout, ran.stderr)
    guard = bool(js_tab_routing_violations(path, path.name))
    return ran.stdout == '1', guard
