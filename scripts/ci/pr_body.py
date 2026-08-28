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
_INLINE_LINK = re.compile(
    r'(?P<image>!)?\[(?P<label>[^]\n]*)\]\((?P<target>[^)\n]*)\)')
_REFERENCE_LINK = re.compile(
    r'(?P<image>!)?\[(?P<label>[^]\n]*)\]\[(?P<target>[^]\n]*)\]')
_LINK_DEFINITION = re.compile(r'^ {0,3}\[[^]\n]+\]:[ \t]*\S')
_HTML_TAG = re.compile(r'</?[A-Za-z][^>\n]*>')
_RAW_TAG = re.compile(
    r'^ {0,3}</?(?P<tag>[A-Za-z][A-Za-z0-9-]*)(?:[ \t/>]|$)')
_RAW_TEXT_TAGS = frozenset(('pre', 'script', 'style', 'textarea'))
_RAW_BLOCK_TAGS = frozenset((
    'address', 'article', 'aside', 'base', 'basefont', 'blockquote',
    'body', 'caption', 'center', 'col', 'colgroup', 'dd', 'details',
    'dialog', 'dir', 'div', 'dl', 'dt', 'fieldset', 'figcaption',
    'figure', 'footer', 'form', 'frame', 'frameset', 'h1', 'h2', 'h3',
    'h4', 'h5', 'h6', 'head', 'header', 'hr', 'html', 'iframe',
    'legend', 'li', 'link', 'main', 'menu', 'menuitem', 'nav', 'noframes',
    'ol', 'optgroup', 'option', 'p', 'param', 'search', 'section',
    'summary', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'title',
    'tr', 'track', 'ul',
))
# This admits the full positive signed-64-bit width while bounding conversion.
_MAX_ISSUE_DIGITS = 19
_REFERENCE = re.compile(
    rf'(?<![\w#&])#([0-9]{{1,{_MAX_ISSUE_DIGITS}}})(?![0-9])')
_EMPTY_MARKER = re.compile(
    r'^(?:>|[*+-]|[0-9]{1,9}[.)])(?:[ \t]+|$)')
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


def _mask_inline_code(source):
    masked = list(source)
    code_lines = set()
    cursor = 0
    while True:
        opening = _BACKTICKS.search(source, cursor)
        if opening is None:
            break
        width = opening.end() - opening.start()
        closing = re.compile(
            rf'(?<!`)`{{{width}}}(?!`)').search(source, opening.end())
        if closing is None:
            cursor = opening.end()
            continue
        end = closing.end()
        first_line = source.count('\n', 0, opening.start())
        last_line = source.count('\n', 0, end)
        code_lines.update(range(first_line, last_line + 1))
        for index in range(opening.start(), end):
            if source[index] != '\n':
                masked[index] = ' '
        cursor = end
    return ''.join(masked), code_lines


def _mask_block_code(lines):
    visible = []
    code_lines = set()
    fence_close = None
    comment = None
    for index, line in enumerate(lines):
        appended = False
        if fence_close is not None:
            visible.append('')
            code_lines.add(index)
            if fence_close.fullmatch(line):
                fence_close = None
            continue
        if comment is not None:
            comment_line, comment, _ = _strip_comments(line, comment)
            visible.append(line)
            appended = True
            if comment is not None:
                continue
        else:
            if line.strip() and line.startswith(('    ', '\t')):
                visible.append('')
                code_lines.add(index)
                continue
            inline_line, _ = _mask_inline_code(line)
            comment_line, comment, _ = _strip_comments(
                inline_line, comment)
        opening = _FENCE.match(comment_line)
        if opening:
            fence = opening.group('fence')
            if fence[0] == '~' or '`' not in opening.group('rest'):
                fence_close = re.compile(
                    rf'^ {{0,3}}{re.escape(fence[0])}'
                    rf'{{{len(fence)},}}[ \t]*$')
                if appended:
                    visible[-1] = ''
                else:
                    visible.append('')
                code_lines.add(index)
                continue
        if not appended:
            visible.append(line)
    return visible, code_lines


def _strip_html_comments(lines):
    visible = []
    comments = []
    comment = None
    for line in lines:
        line, comment, found = _strip_comments(line, comment)
        visible.append(line)
        comments.extend(found)
    return visible, comments


def _mask_raw_html(lines):
    visible = []
    raw_close = None
    until_blank = False
    for line in lines:
        folded = line.casefold()
        if raw_close is not None:
            visible.append('')
            if raw_close in folded:
                raw_close = None
            continue
        if until_blank:
            if not line.strip():
                until_blank = False
                visible.append(line)
            else:
                visible.append('')
            continue
        opening = _RAW_TAG.match(line)
        tag = opening.group('tag').casefold() if opening else ''
        if tag in _RAW_TEXT_TAGS:
            visible.append('')
            close = f'</{tag}>'
            if close not in folded:
                raw_close = close
            continue
        if tag in _RAW_BLOCK_TAGS:
            visible.append('')
            until_blank = True
            continue
        visible.append(line)
    return visible


def _mask_link(match):
    if match.group('image'):
        return ' ' * len(match.group(0))
    label = f'[{match.group("label")}]'
    return label + ' ' * (len(match.group(0)) - len(label))


def _mask_nontext(line):
    if _LINK_DEFINITION.match(line):
        return ''
    line = _INLINE_LINK.sub(_mask_link, line)
    line = _REFERENCE_LINK.sub(_mask_link, line)
    return _HTML_TAG.sub(lambda match: ' ' * len(match.group(0)), line)


def _scan_markdown(body):
    lines = body.split('\n')
    without_blocks, block_code = _mask_block_code(lines)
    inline, inline_code = _mask_inline_code('\n'.join(without_blocks))
    without_comments, comments = _strip_html_comments(inline.split('\n'))
    rendered = []
    code_lines = block_code | inline_code
    for index, line in enumerate(without_comments):
        suffix = ' rendered-content' if index in code_lines else ''
        rendered.append(line + suffix)
    structure = [_mask_nontext(line)
                 for line in _mask_raw_html(without_comments)]
    return structure, rendered, comments


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


def _heading_at(lines, raw_lines, index):
    heading = _ATX_HEADING.fullmatch(lines[index])
    if heading is not None:
        text = heading.group('text') or ''
        text = re.sub(r'[ \t]+#+[ \t]*$', '', text)
        key = _heading_text(text).casefold()
        raw_heading = _ATX_HEADING.fullmatch(raw_lines[index])
        raw_text = raw_heading.group('text') if raw_heading else text
        raw_text = re.sub(r'[ \t]+#+[ \t]*$', '', raw_text or '')
        return _heading_text(raw_text), key, 1
    if (index + 1 < len(lines)
            and _is_paragraph_line(lines[index])
            and _SETEXT_UNDERLINE.fullmatch(lines[index + 1])):
        name = _heading_text(raw_lines[index])
        key = _heading_text(lines[index]).casefold()
        return name, key, 2
    return None


def _split_sections(body):
    body = (body or '').replace('\r\n', '\n').replace('\r', '\n')
    body_lines = body.split('\n')
    heading_lines, _, comments = _scan_markdown(body)
    sections = []
    current = None
    index = 0
    while index < len(heading_lines):
        heading = _heading_at(heading_lines, body_lines, index)
        if heading is None:
            index += 1
            continue
        name, key, width = heading
        if current is not None:
            old_name, old_key, start = current
            sections.append(_Section(
                old_name, old_key,
                '\n'.join(heading_lines[start:index]),
                '\n'.join(body_lines[start:index])))
        current = name, key, index + width
        index += width
    if current is not None:
        old_name, old_key, start = current
        sections.append(_Section(
            old_name, old_key, '\n'.join(heading_lines[start:]),
            '\n'.join(body_lines[start:])))
    return sections, comments


def referenced_issues(body):
    if body is None or body == '':
        return []

    sections, _ = _split_sections(body)
    section = next(
        (item for item in sections if item.key == _SECTION), None)
    if section is None:
        return []

    found = []
    seen = set()
    source = section.content
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


def _has_rendered_content(content):
    _, rendered, _ = _scan_markdown(content)
    for line in rendered:
        text = line.strip()
        while text:
            marker = _EMPTY_MARKER.match(text)
            if marker is None:
                return True
            text = text[marker.end():].lstrip()
    return False


def _code_span(text):
    runs = [len(run) for run in _BACKTICKS.findall(text)]
    fence = '`' * (max(runs, default=0) + 1)
    # Boundary spaces keep content backticks from joining the delimiter run.
    if text.startswith('`') or text.endswith('`') or not text.strip():
        text = f' {text} '
    return f'{fence}{text}{fence}'


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
                f'Section {_code_span(section.name)} is not defined by '
                'the template.')
            continue
        name, _, index = rule
        if section.key in seen:
            errors.append(f'Section "{name}" appears more than once.')
        seen.add(section.key)
        if index < last_index:
            errors.append(f'Section "{name}" is out of order.')
        else:
            last_index = index
        if not _has_rendered_content(section.raw_content):
            errors.append(f'Section "{name}" is empty.')

    for key, (name, tag, _) in rules.items():
        if tag == 'required' and key not in seen:
            errors.append(f'Required section "{name}" is missing.')
    return errors


def main():
    body = sys.stdin.read()
    if len(sys.argv) == 1:
        for number in referenced_issues(body):
            print(number)
        return 0
    if len(sys.argv) != 2:
        print(f'usage: {sys.argv[0]} [template]', file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding='utf-8') as handle:
        template = handle.read()
    for number in referenced_issues(body):
        print(f'issue:{number}')
    for error in layout_errors(body, template):
        print(f'layout:{error}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
