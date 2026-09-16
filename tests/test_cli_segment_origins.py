#!/usr/bin/env python3
"""The CLI's segment-origin subcommands, against a faked extension.

Each handler runs through the real parser and the real DISPATCH entry with
`ext_cmd` faked to record what it was asked to send, so the command id, the
wire type and the fields are pinned exactly as the extension receives them,
and each printed line is pinned exactly as an operator reads it.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

from daedalus_cli import commands_content  # noqa: E402
from daedalus_cli.cli import DISPATCH  # noqa: E402
from daedalus_cli.parser import build_parser  # noqa: E402

ALLOWED = 'https://allowed.example.com'
OTHER = 'https://other.example.com'
NON_CANONICAL = 'https://allowed.example.com/some/path'


class RecordingExtCmd:
    """Records each ext_cmd call and replays canned answers in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, cmd_id, cmd_type, timeout=10, **fields):
        self.calls.append((cmd_id, cmd_type, fields))
        return self.answers.pop(0)


def parse(argv):
    """The parsed namespace, or an assertion naming argparse's refusal."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            return build_parser().parse_args(argv)
    except SystemExit as exit_request:
        raise AssertionError(
            f'{argv} was refused (exit {exit_request.code}): '
            f'{err.getvalue()}') from None


def run_cli(argv, answers):
    """Parse argv with the real parser, dispatch, return (calls, stdout)."""
    recorded = RecordingExtCmd(answers)
    args = parse(argv)
    out = io.StringIO()
    original = commands_content.ext_cmd
    commands_content.ext_cmd = recorded
    try:
        with contextlib.redirect_stdout(out):
            DISPATCH[args.cmd](args)
    finally:
        commands_content.ext_cmd = original
    return recorded, out.getvalue()


def test_allow_segment_origin_sends_the_extension_command(tmp):
    del tmp
    canned = {'origin': ALLOWED, 'origins': [ALLOWED], 'added': True}
    recorded, out = run_cli(['allow-segment-origin', ALLOWED], [canned])
    assert recorded.calls == [
        ('_allow_seg_origin', 'allow-segment-origin',
         {'origin': ALLOWED})], recorded.calls
    assert out == f'Allowed {ALLOWED}\n{ALLOWED}\n', repr(out)


def test_allow_segment_origin_reports_an_origin_already_stored(tmp):
    del tmp
    canned = {'origin': ALLOWED, 'origins': [ALLOWED], 'added': False}
    recorded, out = run_cli(['allow-segment-origin', ALLOWED], [canned])
    assert recorded.calls == [
        ('_allow_seg_origin', 'allow-segment-origin',
         {'origin': ALLOWED})], recorded.calls
    assert out == f'Already allowed {ALLOWED}\n{ALLOWED}\n', repr(out)


def test_allow_segment_origin_prints_the_workers_canonical_origin(tmp):
    del tmp
    canned = {'origin': ALLOWED, 'origins': [ALLOWED], 'added': True}
    recorded, out = run_cli(
        ['allow-segment-origin', NON_CANONICAL], [canned])
    assert recorded.calls == [
        ('_allow_seg_origin', 'allow-segment-origin',
         {'origin': NON_CANONICAL})], recorded.calls
    assert out == f'Allowed {ALLOWED}\n{ALLOWED}\n', repr(out)


def test_revoke_segment_origin_sends_the_extension_command(tmp):
    del tmp
    canned = {'origin': OTHER, 'origins': [], 'found': True}
    recorded, out = run_cli(['revoke-segment-origin', OTHER], [canned])
    assert recorded.calls == [
        ('_revoke_seg_origin', 'revoke-segment-origin',
         {'origin': OTHER})], recorded.calls
    assert out == f'Revoked {OTHER}\n', repr(out)


def test_revoke_segment_origin_prints_the_workers_canonical_origin(tmp):
    del tmp
    canned = {'origin': ALLOWED, 'origins': [], 'found': True}
    recorded, out = run_cli(
        ['revoke-segment-origin', NON_CANONICAL], [canned])
    assert recorded.calls == [
        ('_revoke_seg_origin', 'revoke-segment-origin',
         {'origin': NON_CANONICAL})], recorded.calls
    assert out == f'Revoked {ALLOWED}\n', repr(out)


def test_revoke_segment_origin_exits_for_an_origin_never_stored(tmp):
    del tmp
    canned = {'origin': OTHER, 'origins': [ALLOWED], 'found': False}
    try:
        run_cli(['revoke-segment-origin', OTHER], [canned])
    except SystemExit as exit_request:
        expected = f'No such origin "{OTHER}"'
        assert exit_request.code == expected, exit_request.code
    else:
        assert False, 'revoking an absent origin must not report success'


def test_list_segment_origins_prints_each_origin_on_its_own_line(tmp):
    del tmp
    recorded, out = run_cli(
        ['list-segment-origins'], [{'origins': [ALLOWED, OTHER]}])
    assert recorded.calls == [
        ('_list_seg_origins', 'list-segment-origins', {})], recorded.calls
    assert out == f'{ALLOWED}\n{OTHER}\n', repr(out)


def test_list_segment_origins_says_so_when_none_is_allowed(tmp):
    del tmp
    recorded, out = run_cli(['list-segment-origins'], [{'origins': []}])
    assert recorded.calls == [
        ('_list_seg_origins', 'list-segment-origins', {})], recorded.calls
    assert out == '(no origins allowed)\n', repr(out)


def test_parser_refuses_a_missing_origin(tmp):
    del tmp
    for argv in (['allow-segment-origin'], ['revoke-segment-origin']):
        refuse_missing_origin(argv)


def refuse_missing_origin(argv):
    """argparse rejects the bare command before any command is sent."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            build_parser().parse_args(argv)
    except SystemExit as exit_request:
        message = err.getvalue()
        assert exit_request.code != 0, (argv, exit_request.code, message)
        required = 'the following arguments are required: origin'
        assert required in message, message
    else:
        assert False, f'{argv} parsed without its origin'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
