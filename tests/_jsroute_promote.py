"""Which names can carry a sender, decided by mention alone.

A binding promotes when the text giving it a value names a sender or
anything that promotes, whatever wraps the mention; so does a
receiver written through, a class over its body, a pattern over its
source. No syntax decides it, so no unseen wrapper hides a sender.
"""
import re
from bisect import bisect_left, bisect_right

from _jsroute_source import record_work

IDENTIFIER = re.compile(r'(?<![\w$])([\w$]+)(?![\w$])')
_MEMBER_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*(?:\.\s*[\w$#]+|\[[^\]\n]*\])+\s*'
    r'(?:=(?!=|>)|\|\|=|&&=|\?\?=)')
_OBJECT_TARGET = re.compile(
    r'(?<![\w$])Object\s*\.\s*(?:assign|defineProperty|defineProperties)'
    r'\s*\(')
_CLASS_NAMED = re.compile(r'(?<![\w$])class\s+([\w$]+)(?![\w$])')
_PATTERN_BOUND = re.compile(r'[\}\]]\s*(?:=(?!=)|of(?![\w$]))')
# No identifier spells this: it names a write to a judged binding.
_ROUTED_WRITE = '\0routed'


class Promotion:
    def __init__(self, receivers, work=None, routed=()):
        self.receivers = receivers
        self.mask = receivers.mask
        self.work = work
        # A body WRITING a judged binding carries what the caller
        # hands it, so those writes seed the fixpoint too.
        self.writes = sorted(
            when for binding in routed
            for when, _span in receivers.values.get(binding, ()))
        self.seeds = set(receivers.senders)
        if self.writes:
            self.seeds.add(_ROUTED_WRITE)
        self.names = self._resolve()
        self.starts, self.reach = self._containers()

    def _containers(self):
        """Every literal span a promoting handle already stands for.

        A name written into a literal is reached only by reading it
        out again, and every handle on that is watched. A function
        body is not one: it runs statements the walks account for.
        """
        found = []
        for binding in self.promoting_bindings():
            for _when, span in self.receivers.values.get(binding, ()):
                if span is None:
                    continue
                left, right = self.receivers._unwrap(span)
                if self.mask[left:left + 1] in '{[':
                    found.append((left, right))
        for name, _heritage, opening, close in self._class_heads():
            if close is not None and name in self.names:
                found.append((opening, close))
        found.sort()
        starts, reach, furthest = [], [], -1
        for start, end in found:
            furthest = max(furthest, end)
            starts.append(start)
            reach.append(furthest)
        return starts, reach

    def held(self, position):
        record_work(self.work, 'net_span_lookups')
        index = bisect_right(self.starts, position) - 1
        return index >= 0 and self.reach[index] > position

    def tokens(self, start, end):
        record_work(self.work, 'net_promotion_tokens', max(0, end - start))
        found = {match.group(1)
                 for match in IDENTIFIER.finditer(self.mask, start, end)}
        index = bisect_left(self.writes, start)
        if index < len(self.writes) and self.writes[index] < end:
            found.add(_ROUTED_WRITE)
        return found

    def text_promotes(self, start, end):
        record_work(self.work, 'net_container_queries')
        return bool(self.tokens(start, end) & self.names)

    def span_promotes(self, span):
        return span is not None and self.text_promotes(span[0], span[1])

    def binding_promotes(self, binding):
        return any(self.span_promotes(span) for _when, span
                   in self.receivers.values.get(binding, ()))

    def promoting_bindings(self):
        return {binding for binding in self.receivers.values
                if self.binding_promotes(binding)}

    def _resolve(self):
        dependents = {}
        for name, tokens in self._edges().items():
            for token in tokens:
                dependents.setdefault(token, set()).add(name)
        promoting = set(self.seeds)
        pending = list(promoting)
        while pending:
            for name in dependents.pop(pending.pop(), ()):
                if name not in promoting:
                    promoting.add(name)
                    pending.append(name)
        return promoting

    def _edges(self):
        sources = {}

        def add(name, tokens):
            sources.setdefault(name, set()).update(tokens)

        for binding, entries in self.receivers.values.items():
            for _when, span in entries:
                if span is not None:
                    add(binding[0], self.tokens(span[0], span[1]))
        self._class_edges(add)
        self._write_edges(add)
        self._pattern_edges(add)
        return sources

    def _class_heads(self):
        """Per class: name, heritage span, body opening, body close.

        Any expression stands as heritage; one this reader cannot
        spell must not cost the class its body.
        """
        mask = self.mask
        for match in _CLASS_NAMED.finditer(mask):
            cursor = match.end()
            while cursor < len(mask) and mask[cursor] != '{':
                jump = self.receivers.pair_end.get(cursor)
                cursor = jump if jump is not None else cursor + 1
            if cursor >= len(mask):
                continue
            yield (match.group(1), (match.end(), cursor), cursor,
                   self.receivers.pair_end.get(cursor))

    def _class_edges(self, add):
        for name, heritage, opening, close in self._class_heads():
            tokens = self.tokens(*heritage)
            if close is not None:
                tokens |= self.tokens(opening, close)
            add(name, tokens)

    def _write_edges(self, add):
        mask = self.mask
        for match in _MEMBER_WRITE.finditer(mask):
            end = self.receivers.expression_end(mask, match.end())
            add(match.group(1), self.tokens(match.end(), end))
        for match in _OBJECT_TARGET.finditer(mask):
            opening = mask.index('(', match.end() - 1)
            close = self.receivers.pair_end.get(opening)
            if close is None:
                continue
            spans = self.receivers.split(
                mask, self.receivers.text, opening + 1, close - 1)
            if len(spans) < 2:
                continue
            target = mask[spans[0][0]:spans[0][1]].strip()
            if re.fullmatch(r'[\w$]+', target):
                for span in spans[1:]:
                    add(target, self.tokens(span[0], span[1]))

    def _pattern_edges(self, add):
        mask = self.mask
        opens = {close: opening for opening, close
                 in self.receivers.pair_end.items()}
        for match in _PATTERN_BOUND.finditer(mask):
            opening = opens.get(match.start() + 1)
            if opening is None or mask[opening] not in '{[':
                continue
            source = self.receivers.expression_end(mask, match.end())
            tokens = self.tokens(match.end(), source)
            for name in self.tokens(opening, match.start()):
                add(name, tokens)
