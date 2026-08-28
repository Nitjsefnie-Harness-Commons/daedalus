#!/usr/bin/env python3
"""Parse the pull-request body for repository admission checks."""
import re
import sys


_ATX_HEADING = re.compile(
    r'^ {0,3}#{1,6}(?:[ \t]+(?P<text>.*?))?[ \t]*$')
_FENCE = re.compile(r'^ {0,3}(?P<fence>`{3,}|~{3,})(?P<rest>.*)$')
_SETEXT_UNDERLINE = re.compile(r'^ {0,3}(?:=+|-+)[ \t]*$')
_THEMATIC_BREAK = re.compile(
    r'^ {0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$')
_BLOCK_PREFIX = re.compile(
    r'^ {0,3}(?:>[ \t]?|(?:[*+-]|[0-9]{1,9}[.)])[ \t]+)')
_EMPHASIS = re.compile(
    r'^(?P<marker>\*{1,3}|_{1,3})(?P<text>.+)(?P=marker)$')
_BACKTICKS = re.compile(r'`+')
_REFERENCE = re.compile(r'(?<![\w#&])#([0-9]+)')
_SECTION = 'related issues and pull requests'


def _strip_comments(line, in_comment):
    visible = []
    cursor = 0
    while cursor < len(line):
        if in_comment:
            end = line.find('-->', cursor)
            if end < 0:
                visible.append(' ' * (len(line) - cursor))
                break
            visible.append(' ' * (end + 3 - cursor))
            cursor = end + 3
            in_comment = False
            continue
        start = line.find('<!--', cursor)
        if start < 0:
            visible.append(line[cursor:])
            break
        visible.append(line[cursor:start])
        visible.append(' ' * 4)
        cursor = start + 4
        in_comment = True
    return ''.join(visible), in_comment


def _remove_hidden_markdown(body):
    visible_lines = []
    fence_close = None
    in_comment = False
    for line in body.split('\n'):
        if fence_close is not None:
            visible_lines.append('')
            if fence_close.fullmatch(line):
                fence_close = None
            continue
        opening = None if in_comment else _FENCE.match(line)
        if opening:
            fence = opening.group('fence')
            if fence[0] == '~' or '`' not in opening.group('rest'):
                fence_close = re.compile(
                    rf'^ {{0,3}}{re.escape(fence[0])}'
                    rf'{{{len(fence)},}}[ \t]*$')
                visible_lines.append('')
                continue
        visible, in_comment = _strip_comments(line, in_comment)
        visible_lines.append(visible)
    return visible_lines


def _heading_text(text):
    text = text.strip()
    if text.endswith(':'):
        text = text[:-1].rstrip()
    while True:
        emphasis = _EMPHASIS.fullmatch(text)
        if emphasis is None:
            return text.casefold()
        text = emphasis.group('text').strip()


def _is_paragraph_line(line):
    if not line.strip() or line.startswith(('    ', '\t')):
        return False
    if _ATX_HEADING.fullmatch(line) or _SETEXT_UNDERLINE.fullmatch(line):
        return False
    return not (_BLOCK_PREFIX.match(line) or _THEMATIC_BREAK.fullmatch(line))


def _heading_at(lines, index):
    heading = _ATX_HEADING.fullmatch(lines[index])
    if heading is not None:
        text = heading.group('text') or ''
        text = re.sub(r'[ \t]+#+[ \t]*$', '', text)
        return _heading_text(text), 1
    if (index + 1 < len(lines)
            and _is_paragraph_line(lines[index])
            and _SETEXT_UNDERLINE.fullmatch(lines[index + 1])):
        return _heading_text(lines[index]), 2
    return None


def _remove_indented_code(lines):
    visible = []
    may_start = True
    in_code = False
    for line in lines:
        indented = line.startswith(('    ', '\t'))
        if indented and (may_start or in_code):
            visible.append('')
            in_code = True
            continue
        if not line.strip():
            visible.append(line)
            may_start = True
            continue
        visible.append(line)
        may_start = False
        in_code = False
    return visible


def referenced_issues(body):
    if body is None or body == '':
        return []

    body = body.replace('\r\n', '\n').replace('\r', '\n')
    lines = _remove_hidden_markdown(body)

    section_start = None
    section_end = len(lines)
    index = 0
    while index < len(lines):
        heading = _heading_at(lines, index)
        if heading is None:
            index += 1
            continue
        text, width = heading
        if section_start is not None:
            section_end = index
            break
        if text == _SECTION:
            section_start = index + width
        index += width
    if section_start is None:
        return []

    source = '\n'.join(
        _remove_indented_code(lines[section_start:section_end]))
    without_code = []
    cursor = 0
    while True:
        opening = _BACKTICKS.search(source, cursor)
        if opening is None:
            without_code.append(source[cursor:])
            break
        width = opening.end() - opening.start()
        closing = re.compile(
            rf'(?<!`)`{{{width}}}(?!`)').search(source, opening.end())
        if closing is None:
            without_code.append(source[cursor:opening.end()])
            cursor = opening.end()
            continue
        without_code.append(source[cursor:opening.start()])
        cursor = closing.end()

    found = []
    seen = set()
    source = ''.join(without_code)
    for match in _REFERENCE.finditer(source):
        slash = match.start() - 1
        while slash >= 0 and source[slash] == '\\':
            slash -= 1
        if (match.start() - slash - 1) % 2:
            continue
        number = int(match.group(1))
        if number and number not in seen:
            seen.add(number)
            found.append(number)
    return found


def main():
    for number in referenced_issues(sys.stdin.read()):
        print(number)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
