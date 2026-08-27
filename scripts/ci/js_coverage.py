#!/usr/bin/env python3
"""Merge Node V8 dumps into coverage for tracked JavaScript files."""
import base64
import binascii
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes, urlsplit
from urllib.request import url2pathname

from js_lines import code_lines


@dataclass
class FileCoverage:
    """Executable and covered physical lines for one tracked file."""

    executable_lines: set
    covered_lines: set


@dataclass
class CoverageReport:
    """Coverage plus attribution accounting for all script records."""

    files: dict
    records_seen: int
    ignored_builtins: int
    ignored_other: int

    @property
    def unattributed_records(self):
        return self.ignored_builtins + self.ignored_other


def tracked_sources(root):
    """Return tracked shipped JavaScript as repository path to source."""
    root = Path(root).resolve()
    listed = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z', '--',
         'extension', 'dashboard'],
        capture_output=True, check=True, timeout=30)
    paths = []
    for raw in listed.stdout.split(b'\0'):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        if rel.endswith('.js') and rel.split('/', 1)[0] in {
                'extension', 'dashboard'}:
            paths.append(rel)
    return {
        rel: (root / rel).read_text(encoding='utf-8')
        for rel in sorted(paths)
    }


def _data_source(url):
    header, separator, payload = url[5:].partition(',')
    if not separator:
        return None
    try:
        raw = unquote_to_bytes(payload)
        if header.lower().endswith(';base64'):
            raw = base64.b64decode(raw, validate=True)
        return raw.decode('utf-8')
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def _resolve_data(url, sources):
    decoded = _data_source(url)
    if decoded is None:
        return None
    matches = sorted(
        rel for rel, source in sources.items() if source == decoded)
    if len(matches) > 1:
        joined = ', '.join(matches)
        raise ValueError(f'ambiguous data URL matches: {joined}')
    return matches[0] if matches else None


def _file_url_path(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
        return None
    return Path(url2pathname(unquote(parsed.path)))


def resolve_script(url, root, sources):
    """Resolve one V8 script URL to a tracked repository path."""
    root = Path(root).resolve()
    if url.startswith('data:'):
        return _resolve_data(url, sources)
    if url.startswith('file:'):
        candidate = _file_url_path(url)
        if candidate is None:
            return None
    else:
        candidate = Path(url)
        if not candidate.is_absolute() and urlsplit(url).scheme:
            return None
        if not candidate.is_absolute():
            candidate = root / candidate
    resolved = candidate.resolve()
    tracked = {
        (root / rel).resolve(): rel
        for rel in sources
    }
    return tracked.get(resolved)


def _record_counts(record, source_length):
    counts = [0] * source_length
    ranges = [
        coverage_range
        for function in record.get('functions', ())
        for coverage_range in function.get('ranges', ())
    ]
    ordered = sorted(
        ranges,
        key=lambda item: (item['startOffset'], -item['endOffset']))
    for coverage_range in ordered:
        start = coverage_range['startOffset']
        end = coverage_range['endOffset']
        if start < 0 or end < start or end > source_length:
            raise ValueError(
                f'coverage range {start}:{end} outside source length '
                f'{source_length}')
        counts[start:end] = [coverage_range['count']] * (end - start)
    return counts


def merge_records(records, source_length):
    """Merge nested ranges and add counts from separate script records."""
    merged = [0] * source_length
    for record in records:
        current = _record_counts(record, source_length)
        merged = [left + right for left, right in zip(merged, current)]
    return merged


def _line_spans(source):
    start = 0
    line = 1
    index = 0
    while index < len(source):
        char = source[index]
        if char == '\r':
            yield line, start, index
            index += 1
            if index < len(source) and source[index] == '\n':
                index += 1
            start = index
            line += 1
            continue
        if char in '\n\u2028\u2029':
            yield line, start, index
            index += 1
            start = index
            line += 1
            continue
        index += 1
    yield line, start, len(source)


def _file_coverage(source, rel, records):
    executable = code_lines(source, rel)
    # V8 offsets count UTF-16 code units. Shipped files contain no astral
    # characters, so each offset is also a Python string index.
    counts = merge_records(records, len(source))
    covered = {
        line for line, start, end in _line_spans(source)
        if line in executable and any(counts[start:end])
    }
    return FileCoverage(executable, covered)


def collect_coverage(coverage_dir, root):
    """Read V8 dumps and report line coverage for every tracked file."""
    root = Path(root).resolve()
    sources = tracked_sources(root)
    attributed = {rel: [] for rel in sources}
    records_seen = 0
    ignored_builtins = 0
    ignored_other = 0
    for dump_path in sorted(Path(coverage_dir).glob('coverage-*.json')):
        dump = json.loads(dump_path.read_text(encoding='utf-8'))
        for record in dump['result']:
            records_seen += 1
            url = record.get('url', '')
            rel = resolve_script(url, root, sources)
            if rel is not None:
                attributed[rel].append(record)
            elif url.startswith('node:'):
                ignored_builtins += 1
            else:
                ignored_other += 1
    files = {
        rel: _file_coverage(sources[rel], rel, attributed[rel])
        for rel in sources
    }
    return CoverageReport(
        files, records_seen, ignored_builtins, ignored_other)
