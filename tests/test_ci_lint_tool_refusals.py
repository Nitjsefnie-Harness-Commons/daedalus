#!/usr/bin/env python3
"""Every statement in the lint-tool installer that exists to REFUSE.

The installer is read by a runner on which every one of those inputs is
already right: the pinned requirements file is readable, one pip download
brings one wheel, the transfer is under the ceiling, the wheel is a zip, and
each tool resolves inside the directory this install published. A healthy CI
run therefore reaches none of them, and a green run says nothing about
whether they still refuse -- the same defect the installer was written to
close one level down, because a check that cannot fail is not a check.

Each control drives the real function with the input it was written to
refuse, through the stand-ins in `tests/_lint_tool_refusals.py`, and
asserts on the tokens the refusal's own message must carry: the file it
could not read, the pin it found twice, the wheel that was not there, the
tool that resolved elsewhere. A refusal naming none of those is the shape
this file exists to rule out.

`tests/test_ci_lint_tools.py` is the other half: it holds the jobs to the
tool set this installer declares, and the transfer boundary the download
crosses. The split is of subject -- what the installer refuses against
what it declares -- and the stand-ins both halves read stay in the helper,
so nothing about a refusal is written twice.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _lint_tool_refusals import (  # noqa: E402
    no_pin, no_wheel, oversize_wheel, recorded_missing,
    recorded_outside_the_tool_dir, resolved_outside_the_tool_dir,
    several_pins, too_many_wheels, unreadable_requirements, unreadable_wheel)


def test_a_requirements_file_the_installer_cannot_read_is_refused(tmp):
    """The pin is read out of a file; a file that cannot be read is refused."""
    del tmp
    unreadable_requirements()


def test_a_requirements_file_that_pins_no_version_is_refused(tmp):
    """No pin at all is the supply route gone, and it is refused by name."""
    no_pin(Path(tmp))


def test_a_requirements_file_that_pins_it_twice_is_refused(tmp):
    """Two pins for one file: refused with the count and both lines."""
    several_pins(Path(tmp))


def test_a_download_that_brought_no_wheel_is_refused(tmp):
    """An sdist is not a wheel, and the refusal names it and the pin."""
    no_wheel(Path(tmp))


def test_a_download_that_brought_two_wheels_is_refused(tmp):
    """One pin must name one wheel; both names and the count are stated."""
    too_many_wheels(Path(tmp))


def test_a_wheel_over_the_transfer_ceiling_is_refused(tmp):
    """The ceiling is decided on the file's real size, before it is opened."""
    oversize_wheel(Path(tmp))


def test_a_download_that_is_not_a_zip_is_refused(tmp):
    """A file at a wheel's name that is not an archive is refused by name."""
    unreadable_wheel(Path(tmp))


def test_a_tool_that_resolved_outside_the_tool_dir_is_not_the_installed_one(
        tmp):
    """Both answers of the comparison `_record` refuses on, asserted."""
    resolved_outside_the_tool_dir(tmp)


def test_a_tool_resolved_from_outside_the_tool_dir_is_refused(tmp):
    """The refusal names the tool, the directory, and the path that was
    found."""
    recorded_outside_the_tool_dir(tmp)


def test_a_tool_that_does_not_resolve_is_refused(tmp):
    """Neither binary answering is a failure, named one by one."""
    del tmp
    recorded_missing()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='lintrefusals_')


if __name__ == '__main__':
    raise SystemExit(main())
