"""Line-local console argument policy; lexical state comes from the mask."""
import re

from _jsread import blank_js_comments, js_bracket_end
from _jsroute_keys import decode_string_literal


def _console_arguments(mask):
    member = re.compile(
        r'(?<![\w$])console\s*(?:\)\s*)*(?:\??\.\s*[\w$]+|(?:\?\.\s*)?\[)')
    for sink in member.finditer(mask):
        end = sink.end()
        if mask[end - 1] == '[':
            # Every computed console member is a potential logging sink.
            end = js_bracket_end(mask, end - 1)
        grouped = re.match(r'\s*(?:\)\s*)*', mask[end:])
        end += grouped.end()
        wrapper = re.match(r'\??\.\s*([\w$]+)|(?:\?\.\s*)?\[', mask[end:])
        if wrapper:
            if wrapper.group(1) not in ('call', 'apply'):
                yield None
                continue
            end += wrapper.end()
        call = re.match(r'\s*(?:\?\.\s*)?\(', mask[end:])
        if call:
            start = end + call.end() - 1
            yield start + 1, js_bracket_end(mask, start) - 1
        elif wrapper:
            yield None
        elif not re.match(r'\s*[`;]', mask[end:]):
            stop = mask.find(';', end)
            yield end, len(mask) if stop < 0 else stop
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
        yield start + 1, pos
        pos += 1


def _logs_bridge_token(line, mask):
    if '\\' in mask or re.search(r'}\s*/', mask):
        return True
    line = line.encode('utf-8').decode('latin1')
    access = re.compile(
        r'\.\s*([\w$]+)|(?<=[\w$)\]])\s*(?:\?\.\s*)?\[')
    prefix = re.compile(
        r'\s*\.\s*(?:substring|slice)\s*\(\s*0\s*,\s*[1-8]\s*\)')
    for bounds in _console_arguments(mask):
        if bounds is None:
            return True
        start, end = bounds
        arguments = mask[start:end]
        for match in access.finditer(arguments):
            read_end = match.end()
            if match.group(1) is not None and match.group(1) != 'token':
                continue
            if arguments[read_end - 1] == '[':
                read_end = js_bracket_end(arguments, read_end - 1)
                raw = line[start + match.end():start + read_end - 1]
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
