"""Console argument policy over whole sources; the mask carries lexical
state."""
import re

from _jsread import blank_js_comments, js_bracket_end
from _jsroute_keys import decode_string_literal


def _console_arguments(mask):
    member = re.compile(
        r'(?<![\w$])console\s*(?:\)\s*)*(?:\??\.\s*[\w$]+|(?:\?\.\s*)?\[)')
    tagged = {}
    for sink in member.finditer(mask):
        anchor = sink.start()
        end = sink.end()
        if mask[end - 1] == '[':
            # Every computed console member is a potential logging sink.
            end = js_bracket_end(mask, end - 1)
        grouped = re.match(r'\s*(?:\)\s*)*', mask[end:])
        end += grouped.end()
        wrapper = re.match(r'\??\.\s*([\w$]+)|(?:\?\.\s*)?\[', mask[end:])
        if wrapper:
            if wrapper.group(1) not in ('call', 'apply'):
                yield anchor, None
                continue
            end += wrapper.end()
        tag = re.match(r'\s*`', mask[end:])
        call = re.match(r'\s*(?:\?\.\s*)?\(', mask[end:])
        if call:
            start = end + call.end() - 1
            yield anchor, (start + 1, js_bracket_end(mask, start) - 1)
        elif wrapper:
            yield anchor, None
        elif tag:
            tagged[end + tag.end() - 1] = anchor
        elif not re.match(r'\s*;', mask[end:]):
            stop = mask.find(';', end)
            yield anchor, (end, len(mask) if stop < 0 else stop)
    pos = 0
    while pos < len(mask):
        start = mask.find('`', pos)
        if start < 0:
            break
        pos = start + 1
        while pos < len(mask) and mask[pos] != '`':
            if mask[pos:pos + 2] == '${':
                pos = js_bracket_end(mask, pos + 1)
            else:
                pos += 1
        yield tagged.get(start, start), (start + 1, pos)
        pos += 1


def _refuses_argument_reads(text, mask, start, end):
    """Whether the region mask[start:end] reads the token without the prefix
    exemption; `text` is the source the mask was built from, latin1-mapped
    so its offsets are the mask's byte offsets."""
    access = re.compile(
        r'\.\s*([\w$]+)|(?<=[\w$)\]])\s*(?:\?\.\s*)?\[')
    prefix = re.compile(
        r'\s*\.\s*(?:substring|slice)\s*\(\s*0\s*,\s*[1-8]\s*\)')
    arguments = mask[start:end]
    for match in access.finditer(arguments):
        read_end = match.end()
        if match.group(1) is not None and match.group(1) != 'token':
            continue
        if arguments[read_end - 1] == '[':
            read_end = js_bracket_end(arguments, read_end - 1)
            raw = text[start + match.end():start + read_end - 1]
            raw = raw.encode('latin1').decode('utf-8')
            raw = blank_js_comments(raw).strip()
            # The shared decoder does not resolve legacy numeric escapes.
            if re.search(r'\\[0-9\u2028\u2029]', raw):
                return True
            key = decode_string_literal(raw)
            if key is None:
                if re.fullmatch(r'[0-9]+', raw):
                    continue
                return True
            if key != 'token':
                continue
        # A prefix exempts this read only, never a neighbouring read.
        if not prefix.match(arguments, read_end):
            return True
    return False


def _logs_bridge_token(line, mask):
    if '\\' in mask or re.search(r'}\s*/', mask):
        return True
    line = line.encode('utf-8').decode('latin1')
    for _, bounds in _console_arguments(mask):
        if bounds is None:
            return True
        if _refuses_argument_reads(line, mask, *bounds):
            return True
    return False


def _token_log_offenders(source, mask):
    """(line number, line text) per console sink this source may log the
    whole token through."""
    encoded = source.encode('utf-8').decode('latin1')
    lines = source.split('\n')
    offenders = []
    for anchor, bounds in _console_arguments(mask):
        if bounds is not None and not _refuses_argument_reads(
                encoded, mask, *bounds):
            continue
        number = mask.count('\n', 0, anchor) + 1
        offenders.append((number, lines[number - 1]))
    return offenders
