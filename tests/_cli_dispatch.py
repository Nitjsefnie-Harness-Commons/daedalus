"""One CLI subcommand, run through the real parser and the real DISPATCH.

`ext_cmd` is faked to record what the handler was asked to send, so the
command id, the wire type and the fields are pinned exactly as the
extension receives them, and the printed output is pinned exactly as an
operator reads it. Everything ahead of the socket stays real: the argv goes
through `build_parser` and the namespace goes through the dispatch table.
"""
import contextlib
import io

import _cli_parse

from daedalus_cli import commands_content
from daedalus_cli.cli import DISPATCH


class RecordingExtCmd:
    """Records each ext_cmd call and replays canned answers in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, cmd_id, cmd_type, timeout=10, **fields):
        self.calls.append((cmd_id, cmd_type, fields))
        return self.answers.pop(0)


def run_cli(argv, answers):
    """Parse argv with the real parser, dispatch, return (calls, stdout)."""
    recorded = RecordingExtCmd(answers)
    args = _cli_parse.accepted(argv)
    out = io.StringIO()
    original = commands_content.ext_cmd
    commands_content.ext_cmd = recorded
    try:
        with contextlib.redirect_stdout(out):
            DISPATCH[args.cmd](args)
    finally:
        commands_content.ext_cmd = original
    return recorded, out.getvalue()
