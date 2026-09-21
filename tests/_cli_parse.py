"""The real CLI parser's verdict on one argv."""
import contextlib
import io
import sys

import _util

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli.parser import build_parser  # noqa: E402


def refused(argv):
    """(exit code, stderr): argparse refused argv at parse time."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            build_parser().parse_args(argv)
    except SystemExit as exit_request:
        return exit_request.code, err.getvalue()
    raise AssertionError(f'{argv} parsed instead of being refused')


def accepted(argv):
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            return build_parser().parse_args(argv)
    except SystemExit as exit_request:
        raise AssertionError(
            f'{argv} refused: exit {exit_request.code}, '
            f'{err.getvalue()!r}') from None
