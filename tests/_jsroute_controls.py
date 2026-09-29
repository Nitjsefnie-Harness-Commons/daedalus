"""The frame each tab-routing JavaScript control is run in.

A control is one family body run in one direction: `send` is seeded with
the sender the direction promotes and the body writes the other, so the
runtime routes exactly when the body ran before the focus-tab send. The
family list is the suite's own and differs between the closure and reach
controls, so it travels as an argument rather than as a global.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _jsroute_harness import (  # noqa: E402
    runtime_and_guard as _runtime_and_guard)

_SEND = "send('focus-tab', { tab: chromeTab });\n"


def _focus_program(body, promotes):
    seed = 'ordinary' if promotes else 'extCmd'
    return ("let send = " + seed + ";\n"
            + body.replace('%S', 'extCmd' if promotes else 'ordinary')
            + _SEND)


def _family_observations(tmp, name, promotes, families):
    path = Path(tmp) / name
    return [(label, *_runtime_and_guard(_focus_program(body, promotes), path))
            for label, body in families]
