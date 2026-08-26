#!/usr/bin/env python3
"""Nothing about this machine may reach the published tree.

The two release scanners read every tracked file looking for a host, an
absolute path or a URL that belongs to one deployment, and these tests pin
both what they catch and what they are handed: an enumeration that came back
empty is a broken scan, not a clean one.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, iter_tree_files  # noqa: E402


def test_release_scanners_reject_empty_git_enumeration(tmp):
    """Both scanners reject a successful empty tracked-file enumeration."""
    global ROOT
    release = Path(tmp) / 'empty-release'
    release.mkdir()
    subprocess.run(['git', '-C', str(release), 'init', '-q'], check=True)

    real_root = ROOT
    ROOT = release
    accepted = []
    try:
        scanners = (
            test_no_deployment_strings_in_tree,
            test_no_hardcoded_deployment_urls,
        )
        for scanner in scanners:
            try:
                scanner(tmp)
            except AssertionError as failure:
                message = str(failure)
                assert 'Git returned no tracked release paths' in message, failure
            else:
                accepted.append(scanner.__name__)
    finally:
        ROOT = real_root
    assert not accepted, f'scanners accepted an empty Git enumeration: {accepted}'


def test_release_scanner_enumeration_matches_tracked_files(tmp):
    """The scanner input is the non-empty set of 190 tracked release paths."""
    del tmp
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True,
        check=True, timeout=30)
    tracked = {
        ROOT / os.fsdecode(path)
        for path in listed.stdout.split(b'\0') if path
    }
    enumerated = set(iter_tree_files(ROOT))
    assert tracked, 'Git returned no tracked release paths'
    assert len(tracked) == 190, (
        f'expected 190 tracked paths, found {len(tracked)}')
    assert tracked - enumerated == set(), (
        f'tracked paths omitted from scanner input: {tracked - enumerated}')
    assert enumerated - tracked == set(), (
        f'non-tracked paths included in scanner input: {enumerated - tracked}')


def test_no_deployment_strings_in_tree(tmp):
    """No shipped file may name a host or an absolute path off this machine.

    This asserts a PROPERTY rather than a list of forbidden strings. The
    previous version carried the private hostname and docroot as needles, split
    across a concatenation so the file would not match itself — which published
    the very strings the scrub existed to remove, reconstructible by anyone who
    read the test. A rule that can only be written by quoting the secret is the
    wrong rule.
    """
    # Hosts a public release may legitimately contact or document.
    allowed_hosts = {'127.0.0.1', 'localhost', 'github.com'}
    # The reserved names (RFC 2606/6761) are allowed as a family rather than
    # one at a time: the harnesses in these suites name a dozen of them, and
    # listing each would make this a list of fixtures instead of a policy.
    # `.invalid` is deliberately NOT among them — the test below proves these
    # scanners still catch something by planting a host under it.
    reserved = re.compile(r'(?:^|\.)(?:example\.(?:com|org|net)|test)$')
    # Absolute paths that describe one machine's layout rather than a standard
    # location. /srv and /tmp are generic; a home directory or a webroot is not.
    private_roots = re.compile(r'(?<![\w.])/(?:var/www|root|home/[a-z])[\w./-]*')
    url_host = re.compile(r'https?://([a-zA-Z0-9._-]+(?::\d+)?)')

    violations = []
    for path in iter_tree_files(ROOT):
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        if path.name == os.path.basename(__file__):
            continue                      # the patterns above, not findings
        for match in url_host.finditer(text):
            host = match.group(1).split(':')[0]
            if host not in allowed_hosts and not reserved.search(host):
                violations.append(f'{path}: non-allowlisted host {host}')
        for match in private_roots.finditer(text):
            violations.append(f'{path}: machine-specific path {match.group(0)}')
    assert not violations, 'deployment strings in the release tree:\n' + '\n'.join(violations)


def test_no_hardcoded_deployment_urls(tmp):
    # A public tree may reference documentation hosts only. Anything else in an
    # https:// URL is either a deployment endpoint or a call home, and neither
    # ships.
    #
    # The font hosts used to be allowed here, which meant this test would have
    # watched the dashboard fetch a webfont from Google on every load without
    # objecting. The stylesheet now uses a local font stack, so the allowance
    # goes with it: a re-introduced @import fails this test.
    allowed_exact = {'github.com'}
    violations = []
    for path in iter_tree_files(ROOT):
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            for host in re.findall(r'https://([A-Za-z0-9.-]+)', line):
                ok = (host in allowed_exact or host == 'example.com'
                      or host.endswith('.example.com'))
                if not ok:
                    violations.append(f'{rel}:{i}: https://{host}')
    assert not violations, '\n'.join(violations)


def test_release_scanners_ignore_caches_and_scan_published_files(tmp):
    """Both scanners reject tracked violations and ignore untracked caches."""
    global ROOT
    release = Path(tmp) / 'release'
    release.mkdir()
    published = release / 'published.txt'
    published.write_text('public release content\n', encoding='utf-8')
    unmanifested = release / 'unmanifested.txt'
    unmanifested.write_text('tracked release content\n', encoding='utf-8')
    (release / '.gitignore').write_text(
        '*\n!/.gitignore\n!/published.txt\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(release), 'init', '-q'], check=True)
    subprocess.run(
        ['git', '-C', str(release), 'add', '-f', '.gitignore',
         'published.txt', 'unmanifested.txt'], check=True)

    cache_url = 'https://' + 'cache-only' + '.invalid'
    for directory in ('.pytest_cache', '__pycache__'):
        cache = release / directory
        cache.mkdir()
        (cache / 'content.txt').write_text(cache_url, encoding='utf-8')

    real_root = ROOT
    ROOT = release
    try:
        scanners = (
            test_no_deployment_strings_in_tree,
            test_no_hardcoded_deployment_urls,
        )
        for scanner in scanners:
            scanner(tmp)

        for path in (unmanifested, published):
            path.write_text(
                'https://' + 'tracked-violation' + '.invalid', encoding='utf-8')
            for scanner in scanners:
                try:
                    scanner(tmp)
                except AssertionError as failure:
                    assert path.name in str(failure), failure
                else:
                    raise AssertionError(
                        f'{scanner.__name__} missed tracked {path.name}')
            path.write_text('tracked release content\n', encoding='utf-8')
    finally:
        ROOT = real_root


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='releasescan_')


if __name__ == '__main__':
    raise SystemExit(main())
