#!/usr/bin/env python3
"""How much of what THIS change added is covered, and what it missed.

The ratchet next door reports one number for the whole tree, and that number
barely moves: a pull request can add fifty uncovered lines to a 12,000-line
codebase and the total falls by a fraction of a point, which is inside the
buffer the floor already allows. So the tree-level figure cannot tell a
reviewer whether the code in front of them was tested — only whether the
repository as a whole still is.

This reports the other number: of the lines this change ADDED that coverage
considers executable, how many did the suites reach. It is deliberately NOT a
gate. A refactor that moves code between files, a change that only deletes,
and a fix whose test lives behind an optional dependency all produce a low
patch figure for reasons a reviewer should judge rather than a threshold
should block on.

Lines the diff added that do NOT appear in the coverage report are excluded
rather than counted as missed: blank lines, comments, docstrings and `else:`
are not statements coverage measures, and counting them would make the
percentage depend on formatting.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# `+++ b/path`, with git's optional quoting and the /dev/null of a deletion.
_TARGET = re.compile(r'^\+\+\+ (?:b/)?(.*)$')
# `@@ -old,count +new,count @@`; the counts are optional and mean 1.
_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def executable_lines(coverage_xml):
    """Return {path: {line number: times hit}} from a Cobertura report."""
    root = ET.parse(coverage_xml).getroot()
    measured = {}
    for class_node in root.iter('class'):
        filename = class_node.get('filename')
        if not filename:
            continue
        lines = measured.setdefault(filename, {})
        for line_node in class_node.iter('line'):
            number = line_node.get('number')
            hits = line_node.get('hits')
            if number is None or hits is None:
                continue
            # A file can appear as more than one <class>; take the best hit
            # count so a line reached by any of them counts as covered.
            lines[int(number)] = max(lines.get(int(number), 0), int(hits))
    return measured


def added_lines(diff_text):
    """Return {path: {line numbers this diff adds}} from a unified diff."""
    added = {}
    path = None
    line_number = 0
    for line in diff_text.splitlines():
        target = _TARGET.match(line)
        if target is not None:
            name = target.group(1).strip()
            path = None if name == '/dev/null' else name
            continue
        hunk = _HUNK.match(line)
        if hunk is not None:
            line_number = int(hunk.group(1))
            continue
        if path is None:
            continue
        # `+++` is consumed above, so a leading `+` here is real content.
        if line.startswith('+'):
            added.setdefault(path, set()).add(line_number)
            line_number += 1
        elif line.startswith(' ') or line == '':
            line_number += 1
        # A `-` line exists only in the old file and moves nothing.
    return added


def _ranges(numbers):
    """Collapse sorted line numbers into `3`, `5-9` spans for readability."""
    spans = []
    for number in sorted(numbers):
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ', '.join(str(low) if low == high else f'{low}-{high}'
                     for low, high in spans)


def measure(measured, added):
    """Return (per-file rows, covered, total) over the added statements."""
    rows = []
    covered = total = 0
    for path in sorted(added):
        lines = measured.get(path)
        if not lines:
            # Not a file this report measures — a workflow, a fixture, a
            # Markdown file. Absent is not the same as uncovered.
            continue
        touched = sorted(number for number in added[path] if number in lines)
        if not touched:
            continue
        missed = [number for number in touched if lines[number] == 0]
        rows.append((path, len(touched) - len(missed), len(touched), missed))
        covered += len(touched) - len(missed)
        total += len(touched)
    return rows, covered, total


def render(rows, covered, total):
    """Render the markdown comment body for one run."""
    out = ['### Coverage of this change', '']
    if total == 0:
        # Said in full rather than as "0 lines": the common case here is a
        # change that only touches `tests/`, which the run omits, and a bare
        # "nothing added" reads like the tool failed to find the diff.
        out.append('No **measured** lines were added. Coverage omits '
                   '`tests/*`, so a change confined to test files or to '
                   'files the report does not measure has no patch coverage '
                   'to report — that is not the same as none of it running.')
        return '\n'.join(out) + '\n'
    percent = 100.0 * covered / total
    out.append(f'**{percent:.1f}%** of added lines covered '
               f'({covered}/{total}).')
    out.append('')
    out.append('| File | Covered | Added | Missed lines |')
    out.append('| --- | ---: | ---: | --- |')
    for path, file_covered, file_total, missed in rows:
        detail = _ranges(missed) if missed else '—'
        out.append(f'| `{path}` | {file_covered} | {file_total} | {detail} |')
    if covered == total:
        out.append('')
        out.append('Every added line was reached.')
    return '\n'.join(out) + '\n'


def main():
    """Read a coverage report and a diff; print the markdown body."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', required=True,
                        help='Cobertura XML written by `coverage xml`')
    parser.add_argument('--diff', default='-',
                        help='unified diff to read, or - for stdin')
    args = parser.parse_args()

    diff_text = (sys.stdin.read() if args.diff == '-'
                 else Path(args.diff).read_text(encoding='utf-8'))
    measured = executable_lines(args.coverage)
    if not measured:
        # An empty report means the measurement did not happen, and printing
        # "no lines added" for it would read as good news.
        print('coverage report names no measured file', file=sys.stderr)
        return 1
    rows, covered, total = measure(measured, added_lines(diff_text))
    sys.stdout.write(render(rows, covered, total))
    return 0


if __name__ == '__main__':
    sys.exit(main())
