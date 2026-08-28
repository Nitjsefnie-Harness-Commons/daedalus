"""Validate and remove terminal evidence from rendered pull-request HTML."""
import re
from html.parser import HTMLParser


_SENTINEL = re.compile(r'pr-gate-sentinel-[0-9a-f]{64}')
_VOID_TAGS = frozenset((
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
))


class _RenderSentinelParser(HTMLParser):
    def __init__(self, rendered, sentinel):
        super().__init__(convert_charrefs=False)
        self.rendered = rendered
        self.sentinel = sentinel
        self.elements = []
        self._stack = []
        self._current = None
        self._line_offsets = [
            0, *(match.end() for match in re.finditer('\n', rendered))]

    def _offset(self):
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def _tag_end(self):
        end = self.rendered.find('>', self._offset())
        return end + 1

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        if not self._stack:
            attributes = {
                name.casefold(): value or '' for name, value in attrs}
            kind = 'other'
            if (tag == 'section' and 'data-footnotes' in attributes
                    and 'footnotes' in attributes.get('class', '').split()):
                kind = 'footnotes'
            text = [] if tag == 'p' else None
            self._current = [tag, kind, self._offset(), text]
            if tag in _VOID_TAGS:
                self.elements.append((kind, self._offset(), self._tag_end()))
                self._current = None
                return
        elif self._current is not None:
            self._current[3] = None
        if tag not in _VOID_TAGS:
            self._stack.append(tag)

    def handle_startendtag(self, tag, _attrs):
        if self._stack and self._current is not None:
            self._current[3] = None
        elif not self._stack:
            self.elements.append(('other', self._offset(), self._tag_end()))

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if not self._stack or self._stack[-1] != tag:
            raise ValueError(
                'rendered HTML contains mismatched element boundaries')
        if len(self._stack) == 1:
            root, kind, start, text = self._current
            # One data child prevents markup from being spliced into evidence.
            if root == 'p' and text == [self.sentinel]:
                kind = 'sentinel'
            self.elements.append((kind, start, self._tag_end()))
            self._current = None
        self._stack.pop()

    def handle_data(self, data):
        if not self._stack:
            if data.strip():
                self.elements.append(('other', self._offset(), self._offset()))
        elif len(self._stack) == 1 and self._current[3] is not None:
            self._current[3].append(data)

    def handle_comment(self, _data):
        self._record_non_data()

    def handle_entityref(self, _name):
        self._record_non_data()

    def handle_charref(self, _name):
        self._record_non_data()

    def handle_pi(self, _data):
        self._record_non_data()

    def handle_decl(self, _decl):
        self._record_non_data()

    def unknown_decl(self, _data):
        self._record_non_data()

    def _record_non_data(self):
        if self._stack and self._current is not None:
            self._current[3] = None
        elif not self._stack:
            self.elements.append(('other', self._offset(), self._offset()))

    def finish(self):
        if self.rawdata:
            raise ValueError('rendered HTML is structurally incomplete')
        super().close()
        if self._stack:
            raise ValueError('rendered HTML contains an unfinished element')


def strip_render_sentinel(rendered, sentinel):
    if _SENTINEL.fullmatch(sentinel) is None:
        raise ValueError('render sentinel has an invalid shape')
    parser = _RenderSentinelParser(rendered, sentinel)
    parser.feed(rendered)
    parser.finish()
    positions = [index for index, element in enumerate(parser.elements)
                 if element[0] == 'sentinel']
    if len(positions) != 1:
        raise ValueError('rendered HTML has no unique terminal sentinel')
    index = positions[0]
    # GitHub moves its generated footnotes behind appended body content.
    if [item[0] for item in parser.elements[index + 1:]] not in (
            [], ['footnotes']):
        raise ValueError('render sentinel is not terminal')
    _, start, end = parser.elements[index]
    if start and rendered[start - 1] == '\n':
        start -= 1
        if start and rendered[start - 1] == '\r':
            start -= 1
    return rendered[:start] + rendered[end:]
