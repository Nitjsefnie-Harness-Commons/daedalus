#!/usr/bin/env python3
"""Element shapes the rendered-body parser refuses, and image text.

Kept out of tests/test_pr_body.py, which is at its size ceiling. What
an element boundary refuses and what an element records are one
reading of the parser, so they sit in one suite.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _prgate import PR_BODY, _html_body  # noqa: E402


def test_parser_rejects_a_self_closing_content_element(tmp):
    """A non-void element written self-closing is refused as one.

    The closing tag is what makes this input reach only that refusal:
    without it the element is left open and the unfinished-element
    refusal answers first, so the shape under test is never read.
    """
    del tmp
    try:
        PR_BODY.parse_rendered('<div/></div>', 'owner/repo')
    except ValueError as error:
        assert str(error) == (
            'rendered HTML contains a self-closing content element'), error
    else:
        raise AssertionError('a self-closing content element was accepted')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='prbodyelements_')


if __name__ == '__main__':
    raise SystemExit(main())
