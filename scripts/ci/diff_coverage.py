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

Lines the diff added that coverage.py does not consider executable are
excluded: blank lines, comments, docstrings and `else:` are not measured
statements, and counting them would make the percentage depend on formatting.
For any changed Python source the XML does measure, every added executable
statement must have an XML record; an absent record is an invalid report,
never a smaller denominator.

Two reports feed the number: the Python Cobertura XML from `coverage xml`
(--coverage) and, when given, the JavaScript one from js_coverage.py
(--js-coverage), which records the shipped files under extension/ and
dashboard/. The comment says which languages a run measured, so a number
from one report never reads as though it described the whole change.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from coverage import Coverage
from coverage.exceptions import CoverageException

# `+++ b/path`, with git's optional quoting and the /dev/null of a deletion.
_TARGET = re.compile(r'^\+\+\+ (.*)$')
# `@@ -old,count +new,count @@`; the counts are optional and mean 1.
_HUNK = re.compile(r'^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
# Shipped JavaScript is exactly the roots js_coverage.py measures.
_SHIPPED_JAVASCRIPT_ROOTS = ('extension/', 'dashboard/')
# Every language a run can measure, in comment order.
_LANGUAGES = ('Python', 'JavaScript')


def _decode_git_path(value):
    """Decode a quoted Git path and remove its diff-side prefix."""
    if not value.startswith('"'):
        value = value.split('\t', 1)[0]
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
            if number is None:
                raise ValueError(f'missing line number for {filename}')
            try:
                number_value = int(number)
            except ValueError as error:
                raise ValueError(
                    f'invalid line number for {filename}: {number!r}'
                ) from error
            if number_value <= 0:
                raise ValueError(
                    f'invalid line number for {filename}: {number!r} '
                    '(must be positive)')
            if hits is None:
                raise ValueError(
                    f'missing hits for {filename}:{number_value}')
            try:
                hits_value = int(hits)
            except ValueError as error:
                raise ValueError(
                    f'invalid hits for {filename}:{number_value}: '
                    f'{hits!r}') from error
            # A file can appear as more than one <class>; take the best hit
            # count so a line reached by any of them counts as covered.
            lines[number_value] = max(lines.get(number_value, 0), hits_value)
            usable = True
    if not usable:
        raise ValueError('no usable line entries')
    return measured


def _merge(measured, other):
    """Fold a second report's records in, keeping the best hit count.

    The Python and JavaScript reports name disjoint trees in practice, but
    a shared path must keep the coverage either report saw rather than let
    one silently replace the other.
    """
    for path, lines in other.items():
        known = measured.setdefault(path, {})
        for number, hits in lines.items():
            known[number] = max(known.get(number, 0), hits)


def added_lines(diff_text):
    """Return {path: {line numbers this diff adds}} from a unified diff."""
    added = {}
    path = None
    line_number = 0
    in_hunk = False
    old_remaining = new_remaining = 0
    for line in diff_text.splitlines():
        if line.startswith('Binary files '):
            raise ValueError(
                f'binary diff record is not measurable: {line}')
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
            old_remaining = int(hunk.group(1) or 1)
            line_number = int(hunk.group(2))
            new_remaining = int(hunk.group(3) or 1)
            in_hunk = bool(old_remaining or new_remaining)
            continue
        if path is None or not in_hunk:
            continue
        if line.startswith('+'):
            added.setdefault(path, set()).add(line_number)
            line_number += 1
            new_remaining -= 1
        elif line.startswith('-'):
            old_remaining -= 1
        elif line.startswith(' ') or line == '':
            line_number += 1
            old_remaining -= 1
            new_remaining -= 1
        # A `-` line exists only in the old file and moves nothing.
        if old_remaining == 0 and new_remaining == 0:
            in_hunk = False
    return added


def validate_statement_records(measured, added):
    """Reject a measured source whose added statements are absent from XML.

    Absence is a hard error: it must never silently remove an executable line
    from the denominator and turn incomplete measurement into flattering news.
    """
    analyzer = Coverage(config_file=True)
    for path in sorted(set(measured) & set(added)):
        # Deliberately Python-only: the coverage.py statement analyzer is
        # the oracle for which added lines are executable statements, and
        # there is no JavaScript equivalent to validate against. Do not
        # "fix" this filter to cover .js.
        if not path.lower().endswith('.py'):
            continue
        _source, statements, _excluded, _missing, _formatted = (
            analyzer.analysis2(path))
        required = set(statements) & added[path]
        absent = required.difference(measured[path])
        if absent:
            raise ValueError(
                f'missing executable statement records for {path}: '
                f'{_ranges(absent)}')


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


def _shipped_javascript(path):
    """Whether a path is shipped JavaScript the report can name."""
    return (path.endswith('.js')
            and path.startswith(_SHIPPED_JAVASCRIPT_ROOTS))


def _measured_source(path):
    """Whether some coverage report is expected to name this changed path.

    Python coverage measures non-test Python; the JavaScript report
    measures shipped JavaScript, which is exactly the two roots
    js_coverage.py scans. Examples and test fixtures are not shipped
    source, so their absence from a report says nothing.
    """
    if path.lower().endswith('.py'):
        return not path.startswith('tests/')
    # Case-sensitive where the Python branch above is not, on purpose:
    # the JavaScript measurer scans case-sensitive `.js` under exactly
    # extension/ and dashboard/, so a lowercased guard could demand a
    # file the measurer is incapable of naming.
    return _shipped_javascript(path)


def report_languages(measured):
    """Name the languages a merged report measured, from its own paths.

    Which flag a report arrived under is not what it measured: the
    JavaScript XML parses just as well behind --coverage, and the scope
    note must describe the run that happened — a measured JavaScript row
    above "added JavaScript lines are not measured" is the failure this
    exists to rule out.
    """
    languages = []
    if any(path.lower().endswith('.py') for path in measured):
        languages.append('Python')
    if any(_shipped_javascript(path) for path in measured):
        languages.append('JavaScript')
    return languages


def unmeasured_sources(measured, added):
    """Return changed source paths the reports never named.

    A systematic path-spelling mismatch — the report naming
    `src/pkg/mod.py` where the diff says `pkg/mod.py` — is otherwise
    indistinguishable from a change confined to `tests/`. Returning every
    absent path matters when one changed source file is measured and
    another is not: a boolean all-or-nothing guard would hide the latter.
    """
    return {path for path in added
            if _measured_source(path) and path not in measured}


def _scope_note(languages):
    """Name the languages this run measured, and any it was not given."""
    missing = [name for name in _LANGUAGES if name not in languages]
    if not missing:
        return ['This number covers added Python and JavaScript lines.']
    present = ' and '.join(languages)
    absent = ' and '.join(missing)
    return [f'Only the {present} report was given, so added {absent} '
            f'lines are not measured.']


def render(rows, covered, total, unmeasured=(), languages=_LANGUAGES):
    """Render the markdown comment body for one run."""
    unmeasured = set(unmeasured)
    out = ['### Coverage of this change', '']
    if total == 0 and unmeasured:
        sentence = ('This change added lines to source files a coverage '
                    'report should measure, but the report names none of '
                    'the changed paths. Nothing here was measured, which '
                    'is not the same as nothing needing to be')
        if len(languages) == len(_LANGUAGES):
            # Every language was measured, so an absent record really is
            # a spelling mismatch. With a language unmeasured the scope
            # note below names it, and guessing a cause here asserts one
            # the reader can see is wrong.
            sentence += (': the report most likely spells paths '
                         'differently than the diff does.')
        else:
            sentence += '.'
        out.append(sentence)
        out.append('')
        out.append('Unmeasured changed source files:')
        out.extend(f'- `{path}`' for path in sorted(unmeasured))
    elif total == 0:
        # Said in full rather than as "0 lines": the common case here is a
        # change that only touches `tests/`, which the run omits, and a bare
        # "nothing added" reads like the tool failed to find the diff.
        out.append('No **measured** lines were added. Coverage omits '
                   '`tests/*`, so a change confined to test files or to '
                   'files the report does not measure has no patch coverage '
                   'to report — that is not the same as none of it running.')
    else:
        percent = 100.0 * covered / total
        if covered < total:
            percent = min(percent, 99.9)
        subject = 'measured added lines' if unmeasured else 'added lines'
        out.append(f'**{percent:.1f}%** of {subject} covered '
                   f'({covered}/{total}).')
        out.append('')
        out.append('| File | Covered | Added | Missed lines |')
        out.append('| --- | ---: | ---: | --- |')
        for path, file_covered, file_total, missed in rows:
            detail = _ranges(missed) if missed else '—'
            out.append(
                f'| `{path}` | {file_covered} | {file_total} | {detail} |')
        if unmeasured:
            out.append('')
            out.append('The coverage reports did not measure these changed '
                       'source files:')
            out.extend(f'- `{path}`' for path in sorted(unmeasured))
        if covered == total:
            out.append('')
            if unmeasured:
                out.append('Every measured added line was reached; the '
                           'files above were not measured.')
            else:
                out.append('Every added line was reached.')
    out.append('')
    out.extend(_scope_note(languages))
    return '\n'.join(out) + '\n'


def main():
    """Read the coverage reports and a diff; print the markdown body."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', required=True,
                        help='Cobertura XML written by `coverage xml`')
    parser.add_argument('--js-coverage',
                        help='Cobertura XML written by `js_coverage.py '
                             '--xml`, covering shipped JavaScript')
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
        if args.js_coverage is not None:
            _merge(measured, executable_lines(args.js_coverage))
        added = added_lines(diff_text)
        validate_statement_records(measured, added)
    except (CoverageException, ET.ParseError, ValueError) as error:
        print(f'coverage report invalid: {error}', file=sys.stderr)
        return 1
    if not measured:
        # An empty report means the measurement did not happen, and printing
        # "no lines added" for it would read as good news.
        print('coverage report names no measured file', file=sys.stderr)
        return 1
    rows, covered, total = measure(measured, added)
    sys.stdout.write(render(rows, covered, total,
                            unmeasured_sources(measured, added),
                            languages=report_languages(measured)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
