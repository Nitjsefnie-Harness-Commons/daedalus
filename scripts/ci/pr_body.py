#!/usr/bin/env python3
"""Read GitHub-rendered pull-request HTML for repository admission checks."""
import re
import sys
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import urlsplit


_BACKTICKS = re.compile(r'`+')
_TEMPLATE_COMMENT = re.compile(r'<!--(?P<text>.*?)-->', re.DOTALL)
_TEMPLATE_HEADING = re.compile(
    r'^##[ \t]+(?P<text>.+?)[ \t]*$', re.MULTILINE)
_TEMPLATE_TAG = re.compile(
    r'<!--\s*(?P<tag>required|conditional|optional)\b')
_HEADING_TAGS = frozenset(f'h{depth}' for depth in range(1, 7))
_MAX_ISSUE_DIGITS = 19
_SECTION = 'related issues and pull requests'


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
        super().__init__(convert_charrefs=True)
        self.repository = repository
        self.sections = []
        self.saw_element = False
        self._heading_tag = None
        self._heading_parts = []
        self._name = None
        self._key = None
        self._text = []
        self._issues = []

    def handle_starttag(self, tag, attrs):
        self.saw_element = True
        tag = tag.casefold()
        if tag in _HEADING_TAGS:
            self._finish_section()
            self._heading_tag = tag
            self._heading_parts = []
            return
        if tag != 'a' or self._heading_tag is not None or self._key is None:
            return
        href = next((value for name, value in attrs
                     if name.casefold() == 'href'), None)
        number = _issue_number(href, self.repository)
        if number is not None:
            self._issues.append(number)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag.casefold() != self._heading_tag:
            return
        name = _heading_text(''.join(self._heading_parts))
        self._name = name
        self._key = name.casefold()
        self._text = []
        self._issues = []
        self._heading_tag = None
        self._heading_parts = []

    def handle_data(self, data):
        if self._heading_tag is not None:
            self._heading_parts.append(data)
        elif self._key is not None:
            self._text.append(data)

    def finish(self):
        super().close()
        if self._heading_tag is not None:
            raise ValueError('rendered HTML contains an unfinished heading')
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


def _parse_rendered(rendered, repository=None):
    rendered = rendered or ''
    parser = _RenderedBodyParser(repository)
    parser.feed(rendered)
    parser.finish()
    if rendered.strip() and not parser.saw_element:
        raise ValueError('input is not usable rendered HTML')
    return parser.sections


def _template_rules(template):
    headings = list(_TEMPLATE_HEADING.finditer(template))
    rules = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) \
            else len(template)
        tag = _TEMPLATE_TAG.search(template[heading.end():end])
        if tag is None:
            continue
        name = _heading_text(heading.group('text'))
        rules[name.casefold()] = _Rule(name, tag.group('tag'), index)
    return rules


def instruction_fingerprints(template):
    fingerprints = []
    for match in _TEMPLATE_COMMENT.finditer(template):
        lines = [line.strip() for line in match.group('text').splitlines()
                 if line.strip()]
        if lines:
            fingerprints.append(lines[0])
    return fingerprints


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
        if not section.text.strip():
            errors.append(f'Section "{rule.name}" is empty.')
    for key, rule in rules.items():
        if rule.tag == 'required' and key not in seen:
            errors.append(f'Required section "{rule.name}" is missing.')
    return errors


def layout_errors(rendered, template):
    return _layout_errors(_parse_rendered(rendered), template)


def analyze(rendered, repository, template):
    sections = _parse_rendered(rendered, repository)
    return _referenced_issues(sections), _layout_errors(sections, template)


def _read_template(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _usage():
    print(f'usage: {sys.argv[0]} repository template', file=sys.stderr)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--instruction-fingerprints':
        try:
            template = _read_template(sys.argv[2])
        except OSError as error:
            print(f'could not read template: {error}', file=sys.stderr)
            return 2
        for fingerprint in instruction_fingerprints(template):
            print(fingerprint)
        return 0
    if len(sys.argv) != 3:
        _usage()
        return 2
    rendered = sys.stdin.read()
    try:
        template = _read_template(sys.argv[2])
        issues, errors = analyze(rendered, sys.argv[1], template)
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
