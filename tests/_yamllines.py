"""The line-level view of a YAML document the workflow readers share.

Nothing here decodes a scalar: these split a document into lines, read one
mapping line's indent and remainder, and bound the lines a child owns.
"""
from dataclasses import dataclass

from _yamlscalar import YAMLReadError


@dataclass(frozen=True)
class _Entry:
    """One validated mapping or sequence entry."""

    index: int
    indent: int
    rest: str = ''


def _lines(workflow):
    """Split source while retaining whether each physical line ended."""
    if not isinstance(workflow, str):
        raise YAMLReadError('workflow must be a string')
    return [
        (line.rstrip('\r\n'), line.endswith(('\r', '\n')))
        for line in workflow.splitlines(keepends=True)
    ]


def _entry(line):
    """Return a mapping line's indent and text after its first colon."""
    text, _ended = line
    indent = len(text) - len(text.lstrip(' '))
    body = text[indent:]
    name, colon, rest = body.partition(':')
    if (not colon or not name or name != name.strip(' ')
            or rest and rest[0] != ' '):
        raise YAMLReadError(f'unsupported YAML mapping line: {text!r}')
    return indent, rest


def _meaningful(line):
    """Whether a line contributes YAML structure rather than whitespace."""
    text, _ended = line
    return bool(text.strip(' ')) and not text.lstrip(' ').startswith('#')


def _comment(line):
    """Whether a line is a YAML comment."""
    text, _ended = line
    return text.lstrip(' ').startswith('#')


def _indent(line):
    """Return a line's space indentation, rejecting tabs."""
    text, _ended = line
    count = len(text) - len(text.lstrip(' '))
    if '\t' in text[:count] or text[:1] == '\t':
        raise YAMLReadError('tabs in YAML indentation are unsupported')
    return count


def _top_level_entry(lines, name):
    """Find one top-level mapping entry, refusing duplicate declarations."""
    matches = []
    for index, line in enumerate(lines):
        if not _meaningful(line) or _indent(line) != 0:
            continue
        _entry(line)
        text, _ended = line
        key = text.partition(':')[0]
        if key == name:
            matches.append(index)
    if len(matches) > 1:
        raise YAMLReadError(f'duplicate top-level key: {name}')
    return matches[0] if matches else None


def _section(lines, start, parent_indent):
    """Return the half-open range for a mapping or sequence child."""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if _meaningful(line) and _indent(line) <= parent_indent:
            end = index
            break
    return start + 1, end


def _first_child(lines, start, end, parent_indent):
    """Return the first meaningful child line, if one exists."""
    for index in range(start, end):
        line = lines[index]
        if _meaningful(line) and _indent(line) > parent_indent:
            return index
    return None
