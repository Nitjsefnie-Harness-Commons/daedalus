#!/usr/bin/env python3
"""Parse the pull-request body for repository admission checks."""
import re
import sys
from typing import NamedTuple


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


class _Section(NamedTuple):
    name: str
    key: str
    content: str
    raw_content: str


def _strip_comments(line, comment):
    visible = []
    completed = []
    cursor = 0
    while cursor < len(line):
        if comment is not None:
            end = line.find('-->', cursor)
            if end < 0:
                comment.append(line[cursor:])
                visible.append(' ' * (len(line) - cursor))
                break
            comment.append(line[cursor:end])
            completed.append('\n'.join(comment))
            visible.append(' ' * (end + 3 - cursor))
            cursor = end + 3
            comment = None
            continue
        start = line.find('<!--', cursor)
        if start < 0:
            visible.append(line[cursor:])
            break
        visible.append(line[cursor:start])
        visible.append(' ' * 4)
        cursor = start + 4
        comment = []
    return ''.join(visible), comment, completed


def _scan_markdown(body):
    heading_lines = []
    content_lines = []
    comments = []
    fence_close = None
    comment = None
    for line in body.split('\n'):
        if fence_close is not None:
            heading_lines.append('')
            content_lines.append(line)
            if fence_close.fullmatch(line):
                fence_close = None
            continue
        opening = None if comment is not None else _FENCE.match(line)
        if opening:
            fence = opening.group('fence')
            if fence[0] == '~' or '`' not in opening.group('rest'):
                fence_close = re.compile(
                    rf'^ {{0,3}}{re.escape(fence[0])}'
                    rf'{{{len(fence)},}}[ \t]*$')
                heading_lines.append('')
                content_lines.append(line)
                continue
        visible, comment, found = _strip_comments(line, comment)
        heading_lines.append(visible)
        content_lines.append(visible)
        comments.extend(found)
    return heading_lines, content_lines, comments


def _heading_text(text):
    text = text.strip()
    if text.endswith(':'):
        text = text[:-1].rstrip()
    while True:
        emphasis = _EMPHASIS.fullmatch(text)
        if emphasis is None:
            return text
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
        name = _heading_text(text)
        return name, name.casefold(), 1
    if (index + 1 < len(lines)
            and _is_paragraph_line(lines[index])
            and _SETEXT_UNDERLINE.fullmatch(lines[index + 1])):
        name = _heading_text(lines[index])
        return name, name.casefold(), 2
    return None


def _split_sections(body):
    body = (body or '').replace('\r\n', '\n').replace('\r', '\n')
    heading_lines, content_lines, comments = _scan_markdown(body)
    sections = []
    current = None
    index = 0
    while index < len(heading_lines):
        heading = _heading_at(heading_lines, index)
        if heading is None:
            index += 1
            continue
        name, key, width = heading
        if current is not None:
            old_name, old_key, start = current
            sections.append(_Section(
                old_name, old_key,
                '\n'.join(content_lines[start:index]),
                '\n'.join(body.split('\n')[start:index])))
        current = name, key, index + width
        index += width
    if current is not None:
        old_name, old_key, start = current
        sections.append(_Section(
            old_name, old_key, '\n'.join(content_lines[start:]),
            '\n'.join(body.split('\n')[start:])))
    return sections, comments


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

    sections, _ = _split_sections(body)
    section = next(
        (item for item in sections if item.key == _SECTION), None)
    if section is None:
        return []

    lines, _, _ = _scan_markdown(section.content)
    source = '\n'.join(_remove_indented_code(lines))
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


def layout_errors(body, template):
    sections, comments = _split_sections(body)
    template_sections, template_comments = _split_sections(template)
    rules = {}
    for index, section in enumerate(template_sections):
        tag = re.search(
            r'<!--\s*(required|conditional|optional)\b',
            section.raw_content)
        if tag is not None:
            rules[section.key] = section.name, tag.group(1), index

    errors = []
    instructions = {comment.strip() for comment in template_comments}
    if any(comment.strip() in instructions for comment in comments):
        errors.append('Remove the template instruction comments.')

    seen = set()
    last_index = -1
    for section in sections:
        rule = rules.get(section.key)
        if rule is None:
            errors.append(
                f'Section "{section.name}" is not defined by the template.')
            continue
        name, _, index = rule
        if section.key in seen:
            errors.append(f'Section "{name}" appears more than once.')
        seen.add(section.key)
        if index < last_index:
            errors.append(f'Section "{name}" is out of order.')
        else:
            last_index = index
        if not section.content.strip():
            errors.append(f'Section "{name}" is empty.')

    for key, (name, tag, _) in rules.items():
        if tag == 'required' and key not in seen:
            errors.append(f'Required section "{name}" is missing.')
    return errors


def main():
    for number in referenced_issues(sys.stdin.read()):
        print(number)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
