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
_TARGET = re.compile(r'^\+\+\+ (.*)$')
# `@@ -old,count +new,count @@`; the counts are optional and mean 1.
_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def _decode_git_path(value):
    """Decode a quoted Git path and remove its diff-side prefix."""
    if not value.startswith('"'):
        return value[2:] if value.startswith('b/') else value
    escaped = value[1:-1] if value.endswith('"') else value[1:]
    escapes = {
        '\\': b'\\', '"': b'"', 'a': b'\a', 'b': b'\b',
        'f': b'\f', 'n': b'\n', 'r': b'\r', 't': b'\t',
        'v': b'\v',
    }
    decoded = bytearray()
    index = 0
    while index < len(escaped):
        char = escaped[index]
        if char != '\\':
            decoded.extend(char.encode('utf-8'))
            index += 1
            continue
        octal = escaped[index + 1:index + 4]
        if len(octal) == 3 and all(item in '01234567' for item in octal):
            decoded.append(int(octal, 8))
            index += 4
            continue
        if index + 1 == len(escaped):
            decoded.extend(b'\\')
            index += 1
            continue
        escaped_char = escaped[index + 1]
        decoded.extend(escapes.get(escaped_char,
                                   escaped_char.encode('utf-8')))
        index += 2
    value = decoded.decode('utf-8')
    return value[2:] if value.startswith('b/') else value


def executable_lines(coverage_xml):
    """Return {path: {line number: times hit}} from a Cobertura report."""
    root = ET.parse(coverage_xml).getroot()
    if root.tag != 'coverage':
        raise ValueError('root is not <coverage>')
    measured = {}
    usable = False
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
            try:
                number_value = int(number)
                hits_value = int(hits)
            except ValueError:
                continue
            # A file can appear as more than one <class>; take the best hit
            # count so a line reached by any of them counts as covered.
            lines[number_value] = max(lines.get(number_value, 0), hits_value)
            usable = True
    if not usable:
        raise ValueError('no usable line entries')
    return measured


def added_lines(diff_text):
    """Return {path: {line numbers this diff adds}} from a unified diff."""
    added = {}
    path = None
    line_number = 0
    in_hunk = False
    for line in diff_text.splitlines():
        # `--- ` counts as a file header only outside a hunk. Git renders a
        # REMOVED line whose content begins `-- ` as `--- ...`, and taking
        # that for a header clears the path and silently drops every later
        # hunk of the file. The `+++` match below is guarded the same way.
        if line.startswith('diff --git ') or (
                not in_hunk and line.startswith('--- ')):
            path = None
            in_hunk = False
            continue
        target = _TARGET.match(line) if not in_hunk else None
        if target is not None:
            name = _decode_git_path(target.group(1))
            path = None if name == '/dev/null' else name
            continue
        hunk = _HUNK.match(line)
        if hunk is not None:
            line_number = int(hunk.group(1))
            in_hunk = True
            continue
        if path is None or not in_hunk:
            continue
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


def unmeasured_source(measured, added):
    """True when the report names none of the source paths the diff added.

    A systematic path-spelling mismatch — the report naming
    `src/pkg/mod.py` where the diff says `pkg/mod.py` — is otherwise
    indistinguishable from a change confined to `tests/`: both measure
    nothing and both render the reassuring paragraph.
    """
    source = [path for path in added
              if path.endswith('.py') and not path.startswith('tests/')]
    return bool(source) and not any(path in measured for path in added)


def render(rows, covered, total, unmeasured=False):
    """Render the markdown comment body for one run."""
    out = ['### Coverage of this change', '']
    if total == 0 and unmeasured:
        out.append('This change added lines to Python files outside '
                   '`tests/`, but the coverage report names none of the '
                   'changed paths. Nothing here was measured, which is not '
                   'the same as nothing needing to be: the report most '
                   'likely spells paths differently than the diff does.')
        return '\n'.join(out) + '\n'
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
    if covered < total:
        percent = min(percent, 99.9)
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
    try:
        # Inside the guard with its sibling: a git-quoted path whose bytes
        # are not UTF-8 raises UnicodeDecodeError, a ValueError subclass,
        # and outside it that died as a traceback.
        measured = executable_lines(args.coverage)
        added = added_lines(diff_text)
    except (ET.ParseError, ValueError) as error:
        print(f'coverage report invalid: {error}', file=sys.stderr)
        return 1
    if not measured:
        # An empty report means the measurement did not happen, and printing
        # "no lines added" for it would read as good news.
        print('coverage report names no measured file', file=sys.stderr)
        return 1
    rows, covered, total = measure(measured, added)
    sys.stdout.write(render(rows, covered, total,
                            unmeasured_source(measured, added)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
