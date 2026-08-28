#!/usr/bin/env python3
"""Read GitHub-rendered pull-request HTML for repository admission checks."""
import re
import sys
from html import unescape
from html.parser import HTMLParser
from typing import NamedTuple
from unicodedata import category
from urllib.parse import urlsplit

if __package__:
    # pylint: disable-next=relative-beyond-top-level
    from .pr_render import strip_render_sentinel
else:
    from pr_render import strip_render_sentinel


_BACKTICKS = re.compile(r'`+')
_TEMPLATE_COMMENT = re.compile(r'<!--(?P<text>.*?)-->', re.DOTALL)
_TEMPLATE_HEADING = re.compile(
    r'^##[ \t]+(?P<text>.+?)[ \t]*$', re.MULTILINE)
_SOURCE_HEADING = re.compile(
    r'^[ \t]{0,3}#{1,6}[ \t]+(?P<text>.+?)[ \t]*#*[ \t]*$', re.MULTILINE)
_TEMPLATE_TAG = re.compile(
    r'<!--\s*(?P<tag>required|conditional|optional)\b')
_HEADING_TAGS = frozenset(f'h{depth}' for depth in range(1, 7))
_VOID_TAGS = frozenset((
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
))
_MAX_ISSUE_DIGITS = 19
_SECTION = 'related issues and pull requests'
_INVISIBLE_CHARACTERS = frozenset((
    '\u034f', '\u115f', '\u1160', '\u2800', '\u3164', '\uffa0',
))


class _Section(NamedTuple):
    name: str
    key: str
    text: str
    issues: tuple


class _Rule(NamedTuple):
    name: str
    tag: str
    index: int


def _heading_text(text):
    text = ' '.join(text.split())
    if text.endswith(':'):
        text = text[:-1].rstrip()
    return text


def _repository_parts(repository):
    if repository is None:
        return None
    parts = repository.strip('/').split('/')
    if len(parts) != 2 or not all(parts):
        raise ValueError('repository must have the form owner/name')
    return tuple(part.casefold() for part in parts)


def _issue_number(href, repository):
    if not href:
        return None
    target = urlsplit(href)
    if target.scheme and target.scheme.casefold() not in ('http', 'https'):
        return None
    if target.netloc and (target.hostname or '').casefold() != 'github.com':
        return None
    parts = target.path.strip('/').split('/')
    if len(parts) != 4 or parts[2].casefold() != 'issues':
        return None
    expected = _repository_parts(repository)
    if expected is not None:
        actual = tuple(part.casefold() for part in parts[:2])
        if actual != expected:
            return None
    digits = parts[3]
    if (not digits.isascii() or not digits.isdecimal()
            or len(digits) > _MAX_ISSUE_DIGITS):
        return None
    number = int(digits)
    return number or None


class _RenderedBodyParser(HTMLParser):
    def __init__(self, repository):
        super().__init__(convert_charrefs=False)
        _repository_parts(repository)
        self.repository = repository
        self.sections = []
        self.saw_element = False
        self.visible_text = []
        self._open_tags = []
        self._heading_tag = None
        self._heading_parts = []
        self._name = None
        self._key = None
        self._text = []
        self._issues = []
        self._footnote_depth = 0

    def handle_starttag(self, tag, attrs):
        self.saw_element = True
        tag = tag.casefold()
        if tag not in _VOID_TAGS:
            self._open_tags.append(tag)
        if self._footnote_depth:
            if tag not in _VOID_TAGS:
                self._footnote_depth += 1
            return
        attributes = {
            name.casefold(): value or '' for name, value in attrs}
        # GitHub appends this generated heading after contributor sections.
        if (tag == 'section' and 'data-footnotes' in attributes
                and 'footnotes' in attributes.get('class', '').split()):
            self._footnote_depth = 1
            return
        if tag in _HEADING_TAGS:
            if self._heading_tag is not None:
                raise ValueError(
                    'rendered HTML contains nested headings')
            self._finish_section()
            self._heading_tag = tag
            self._heading_parts = []
            return
        if tag == 'img':
            alt = attributes.get('alt', '')
            self._record_text(alt)
            zero_size = any(
                attributes.get(name, '').strip() == '0'
                for name in ('width', 'height'))
            if (self._heading_tag is None and self._key is not None
                    and attributes.get('src') and not zero_size):
                self._text.append('\ufffc')
            return
        if tag != 'a':
            return
        destinations = [value for name, value in attrs
                        if name.casefold() == 'href']
        if not destinations:
            return
        if self._heading_tag is not None or self._key is None:
            return
        href = destinations[0]
        number = _issue_number(href, self.repository)
        if number is not None:
            self._issues.append(number)

    def handle_startendtag(self, tag, attrs):
        tag = tag.casefold()
        if tag not in _VOID_TAGS:
            raise ValueError(
                'rendered HTML contains a self-closing content element')
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if not self._open_tags or self._open_tags[-1] != tag:
            raise ValueError(
                'rendered HTML contains mismatched element boundaries')
        self._open_tags.pop()
        if self._footnote_depth:
            self._footnote_depth -= 1
            return
        if tag != self._heading_tag:
            return
        name = _heading_text(''.join(self._heading_parts))
        self._name = name
        self._key = name.casefold()
        self._text = []
        self._issues = []
        self._heading_tag = None
        self._heading_parts = []

    def handle_data(self, data):
        self._record_text(data)

    def handle_entityref(self, name):
        source = f'&{name};'
        # Source spelling stops visible entities cancelling removed comments.
        self._record_text(unescape(source), source)

    def handle_charref(self, name):
        source = f'&#{name};'
        # Numeric spellings need the same protection as named entities.
        self._record_text(unescape(source), source)

    def _record_text(self, data, source=None):
        if self._footnote_depth:
            return
        self.visible_text.append(data if source is None else source)
        if self._heading_tag is not None:
            self._heading_parts.append(data)
        elif self._key is not None:
            self._text.append(data)

    def finish(self):
        if not self.saw_element:
            raise ValueError('input is not usable rendered HTML')
        if self.rawdata:
            raise ValueError('rendered HTML is structurally incomplete')
        super().close()
        if self._heading_tag is not None:
            raise ValueError('rendered HTML contains an unfinished heading')
        if any(tag not in _HEADING_TAGS for tag in self._open_tags):
            raise ValueError('rendered HTML contains an unfinished element')
        self._finish_section()

    def _finish_section(self):
        if self._key is None:
            return
        self.sections.append(_Section(
            self._name, self._key, ''.join(self._text),
            tuple(self._issues)))
        self._name = None
        self._key = None
        self._text = []
        self._issues = []


def _rendered_body(rendered, repository=None):
    rendered = rendered or ''
    parser = _RenderedBodyParser(repository)
    parser.feed(rendered)
    parser.finish()
    return parser


def _parse_rendered(rendered, repository=None):
    return _rendered_body(rendered, repository).sections


def _template_rules(template):
    headings = list(_TEMPLATE_HEADING.finditer(template))
    rules = {}
    for index, heading in enumerate(headings):
        name = _heading_text(heading.group('text'))
        end = headings[index + 1].start() if index + 1 < len(headings) \
            else len(template)
        tag = _TEMPLATE_TAG.search(template[heading.end():end])
        if tag is None:
            raise ValueError(
                f'template section "{name}" has no instruction comment')
        rules[name.casefold()] = _Rule(name, tag.group('tag'), index)
    return rules


def _normalise_newlines(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _retains_instruction_comment(source, rendered_text, template):
    source = _normalise_newlines(source)
    rendered_text = _normalise_newlines(rendered_text)
    for match in _TEMPLATE_COMMENT.finditer(template):
        comment = _normalise_newlines(match.group(0))
        # Occurrence counts expose comments removed beside identical prose.
        if source.count(comment) > rendered_text.count(comment):
            return True
    return False


def _referenced_issues(sections):
    section = next(
        (item for item in sections if item.key == _SECTION), None)
    if section is None:
        return []
    found = []
    seen = set()
    for number in section.issues:
        if number not in seen:
            seen.add(number)
            found.append(number)
    return found


def referenced_issues(rendered, repository=None):
    return _referenced_issues(_parse_rendered(rendered, repository))


def _code_span(text):
    runs = [len(run) for run in _BACKTICKS.findall(text)]
    fence = '`' * (max(runs, default=0) + 1)
    # Boundary spaces keep content backticks from joining the delimiter run.
    if text.startswith('`') or text.endswith('`') or not text.strip():
        text = f' {text} '
    return f'{fence}{text}{fence}'


def _layout_errors(sections, template):
    rules = _template_rules(template)
    errors = []
    seen = set()
    last_index = -1
    for section in sections:
        rule = rules.get(section.key)
        if rule is None:
            errors.append(
                f'Section {_code_span(section.name)} is not defined by '
                'the template.')
            continue
        if section.key in seen:
            errors.append(f'Section "{rule.name}" appears more than once.')
        seen.add(section.key)
        if rule.index < last_index:
            errors.append(f'Section "{rule.name}" is out of order.')
        else:
            last_index = rule.index
        if not _has_visible_text(section.text):
            errors.append(f'Section "{rule.name}" is empty.')
    for key, rule in rules.items():
        if rule.tag == 'required' and key not in seen:
            errors.append(f'Required section "{rule.name}" is missing.')
    return errors


def _has_visible_text(text):
    for char in text:
        if char.isspace() or char in _INVISIBLE_CHARACTERS:
            continue
        if category(char) in ('Cc', 'Cf', 'Cs'):
            continue
        if ('\ufe00' <= char <= '\ufe0f'
                or '\U000e0100' <= char <= '\U000e01ef'):
            continue
        return True
    return False


def _source_has_missing_rendered_content(source, sections, template):
    source = _normalise_newlines(source)
    headings = list(_SOURCE_HEADING.finditer(source))
    rules = _template_rules(template)
    rendered = {item.key for item in sections
                if _has_visible_text(item.text)}
    for index, heading in enumerate(headings):
        name = _heading_text(heading.group('text')).strip('*_~` ')
        key = name.casefold()
        if key not in rules:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) \
            else len(source)
        content = _TEMPLATE_COMMENT.sub('', source[heading.end():end])
        # This crude scan is only a veto; it can never authorize an action.
        if content.strip() and key not in rendered:
            return True
    return False


def layout_errors(rendered, template):
    return _layout_errors(_parse_rendered(rendered), template)


def analyze(rendered, repository, template, source=None):
    document = _rendered_body(rendered, repository)
    if source is not None and _source_has_missing_rendered_content(
            source, document.sections, template):
        raise ValueError('rendered HTML omits source section content')
    errors = _layout_errors(document.sections, template)
    if source is not None and _retains_instruction_comment(
            source, ''.join(document.visible_text), template):
        errors.append('Remove the template instruction comments.')
    return _referenced_issues(document.sections), errors


def _read_template(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _usage():
    print(f'usage: {sys.argv[0]} [--sentinel value] repository template '
          '[source-body]',
          file=sys.stderr)


def main():
    args = sys.argv[1:]
    sentinel = None
    if args[:1] == ['--sentinel']:
        if len(args) < 3:
            _usage()
            return 2
        sentinel = args[1]
        args = args[2:]
    if len(args) not in (2, 3):
        _usage()
        return 2
    rendered = sys.stdin.read()
    try:
        if sentinel is not None:
            rendered = strip_render_sentinel(rendered, sentinel)
        template = _read_template(args[1])
        source = _read_template(args[2]) if len(args) == 3 else None
        issues, errors = analyze(rendered, args[0], template, source)
    except (OSError, ValueError) as error:
        print(f'could not analyze rendered HTML: {error}', file=sys.stderr)
        return 2
    for number in issues:
        print(f'issue:{number}')
    for error in errors:
        print(f'layout:{error}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
