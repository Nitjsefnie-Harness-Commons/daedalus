#!/usr/bin/env python3
"""Find claimed issue references in a pull-request body."""
import re
import sys


_ATX_HEADING = re.compile(
    r'^ {0,3}#{1,6}(?:[ \t]+(?P<text>.*?))?[ \t]*$')
_HTML_COMMENT = re.compile(r'<!--.*?(?:-->|$)', re.DOTALL)
_FENCE = re.compile(r'^ {0,3}(?P<fence>`{3,}|~{3,})(?P<rest>.*)$')
_BACKTICKS = re.compile(r'`+')
_REFERENCE = re.compile(r'(?<![\w#])#([0-9]+)')
_SECTION = 'related issues and pull requests'


def referenced_issues(body):
    if body is None or body == '':
        return []

    body = body.replace('\r\n', '\n').replace('\r', '\n')
    body = _HTML_COMMENT.sub('', body)
    lines = body.split('\n')

    section_start = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        heading = _ATX_HEADING.fullmatch(line)
        if heading is None:
            continue
        if section_start is not None:
            section_end = index
            break
        text = heading.group('text') or ''
        text = re.sub(r'[ \t]+#+[ \t]*$', '', text).strip()
        if text.casefold() == _SECTION:
            section_start = index + 1
    if section_start is None:
        return []

    unfenced = []
    fence_close = None
    for line in lines[section_start:section_end]:
        if fence_close is not None:
            if fence_close.fullmatch(line):
                fence_close = None
            continue
        opening = _FENCE.match(line)
        if opening is not None:
            fence = opening.group('fence')
            if fence[0] == '~' or '`' not in opening.group('rest'):
                fence_close = re.compile(
                    rf'^ {{0,3}}{re.escape(fence[0])}'
                    rf'{{{len(fence)},}}[ \t]*$')
                continue
        unfenced.append(line)

    source = '\n'.join(unfenced)
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
    for match in _REFERENCE.finditer(''.join(without_code)):
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
