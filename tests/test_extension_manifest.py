#!/usr/bin/env python3
"""The shipped manifest, read the way Chrome's installer reads it.

A manifest Chrome refuses fails at install time, which no suite here could
see: the one that loads the extension skips where Chromium is absent. This
one needs no browser and no Node, so it fails on every runner Chrome would.

Enforced: the known keys' shapes (fail-closed), a closed allowlist of
permission names, the match-pattern grammar, and that every named path is a
file inside the extension root. An explicit JSON null on a known key is a
value of the wrong type, not an absent key, and is refused. NOT enforced: the
version's spelling, which `scripts/check_versions.py` and the version-contract
suites own (a non-string or empty version is still refused here as a shape
violation), the CSP, the `icons` sizes, whether a permission is useful rather
than granted, and unknown keys — legal MV3, ignored by Chrome, never refused.
Known model gaps, recorded rather than enforced: a bare leading dot in a
match-pattern host is accepted here where Chrome's grammar wants the
wildcard first; the `urn:` form is modeled narrower than Chrome's; a content
script naming neither js nor css is accepted where Chrome requires one of
the two; and background.type is modeled single-valued (`module`), so an
over-refusal there would fail loudly in CI rather than silently.
"""
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

EXTENSION_ROOT = ROOT / 'extension'
MANIFEST_PATH = EXTENSION_ROOT / 'manifest.json'

# Closed on purpose: a typo must read as a refusal, not an accepted grant.
PERMISSIONS = frozenset({
    'tabs', 'activeTab', 'cookies', 'scripting', 'storage', 'debugger',
    'notifications', 'clipboardWrite', 'downloads', 'alarms',
    'declarativeNetRequest',
})

# `<all_urls>` is matched first; `urn` is spelled `urn:<value>`, not `urn://`.
PATTERN_SCHEMES = frozenset(
    {'*', 'http', 'https', 'ws', 'wss', 'ftp', 'file', 'urn'})

RUN_AT = ('document_start', 'document_end', 'document_idle')
WORLD = ('MAIN', 'ISOLATED')


def _pattern_violation(key, pattern):
    """Why Chrome would refuse one match pattern at `key`, or None.

    `<all_urls>`, or `scheme://host/path` — host the star, `*.<rest>` with a
    nonempty rest, or a starless literal, path beginning with a slash.
    """
    if not isinstance(pattern, str) or not pattern:
        return f'{key}: a pattern must be a nonempty string'
    if pattern == '<all_urls>':
        return None
    if pattern.startswith('urn:'):
        value = pattern[len('urn:'):]
        if not value or value.startswith('//'):
            return f'{key}: {pattern} is not a urn: value'
        return None
    scheme, divider, remainder = pattern.partition('://')
    if not divider:
        return (f'{key}: {pattern} is not <all_urls>, scheme://host/path, '
                'or a urn: value')
    if scheme not in PATTERN_SCHEMES:
        return (f'{key}: {pattern} names the scheme {scheme}, which Chrome '
                'does not grant here')
    host, slash, _tail = remainder.partition('/')
    if not slash:
        return f'{key}: {pattern} has no path'
    if scheme == 'file':
        if host:
            return f'{key}: {pattern} gives the file scheme a host'
        return None
    if not host:
        return f'{key}: {pattern} has no host'
    if host == '*':
        return None
    if host.startswith('*.'):
        if host == '*.':
            return f'{key}: {pattern} has an empty star-dot rest'
        if '*' in host[2:]:
            return f'{key}: {pattern} has a star outside the star-dot form'
        return None
    if '*' in host:
        return f'{key}: {pattern} has a star outside the star-dot form'
    return None


def _file_violation(key, name, ext_root):
    """Why `key` does not name a shipped file, or None; containment first."""
    if not isinstance(name, str) or not name:
        return f'{key}: a path must be a nonempty string'
    # A backslash is invisible to PurePosixPath (on Windows the disk read
    # below would resolve outside the root); a colon reads as a URL scheme.
    if '\\' in name or ':' in name:
        return f'{key}: {name} names a path with a backslash or a colon'
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts:
        return f'{key}: {name} leaves the extension root'
    if not (Path(ext_root) / name).is_file():
        return f'{key}: {name} is not a file the extension ships'
    return None


def _glob_violation(key, value):
    """Why Chrome would refuse one glob at `key`, or None (no file check)."""
    if not isinstance(value, str) or not value:
        return f'{key}: a glob must be a nonempty string'
    return None


def _root_free(check):
    """Adapt a check that reads no files to the dispatcher's signature."""
    return lambda key, value, ext_root: check(key, value)


_ABSENT = object()


def _each_entry(key, entries, check, ext_root):
    """`check` against every entry, each violation naming its own index."""
    found = []
    for index, entry in enumerate(entries):
        problem = check(f'{key}[{index}]', entry, ext_root)
        if problem:
            found.append(problem)
    return found


def _required_entries(key, entries, check, ext_root):
    """A list Chrome requires to be there and to name something."""
    if not isinstance(entries, list) or not entries:
        return [f'{key}: is not a nonempty list']
    return _each_entry(key, entries, check, ext_root)


def _optional_entries(key, entries, check, ext_root, nonempty):
    """Violations for an optional list; an explicit null is wrong-typed."""
    if entries is _ABSENT:
        return []
    if not isinstance(entries, list):
        return [f'{key}: is not a list']
    if nonempty and not entries:
        return [f'{key}: is present but is empty']
    return _each_entry(key, entries, check, ext_root)


def manifest_violations(manifest, ext_root):
    """Every reason Chrome would refuse `manifest` at install time."""
    if not isinstance(manifest, dict):
        return ['manifest: the root is not a JSON object']
    violations = []

    def add(key, reason):
        violations.append(f'{key}: {reason}')

    version = manifest.get('manifest_version')
    if isinstance(version, bool) or not isinstance(version, int):
        add('manifest_version', 'is not an integer')
    elif version != 3:
        add('manifest_version', 'is not 3')

    for key in ('name', 'version'):
        value = manifest.get(key)
        if not isinstance(value, str) or not value:
            add(key, 'is not a nonempty string')

    if 'description' in manifest and not isinstance(
            manifest['description'], str):
        add('description', 'is present but is not a string')

    if 'permissions' in manifest:
        value = manifest['permissions']
        if not isinstance(value, list):
            add('permissions', 'is not a list')
        else:
            seen = set()
            for index, entry in enumerate(value):
                if not isinstance(entry, str) or not entry:
                    add(f'permissions[{index}]', 'is not a string')
                elif entry in seen:
                    add(f'permissions[{index}]', f'repeats {entry}')
                elif entry not in PERMISSIONS:
                    add(f'permissions[{index}]',
                        f'is not a permission Chrome grants: {entry}')
                seen.add(entry)

    violations.extend(_optional_entries(
        'host_permissions', manifest.get('host_permissions', _ABSENT),
        _root_free(_pattern_violation), ext_root, nonempty=False))

    if 'background' in manifest:
        value = manifest['background']
        if not isinstance(value, dict):
            add('background', 'is not an object')
        else:
            problem = _file_violation('background.service_worker',
                                      value.get('service_worker'), ext_root)
            if problem:
                violations.append(problem)
            if 'type' in value and value['type'] != 'module':
                add('background.type', 'is not module')

    if 'content_scripts' in manifest:
        value = manifest['content_scripts']
        if not isinstance(value, list):
            add('content_scripts', 'is not a list')
        else:
            for index, script in enumerate(value):
                where = f'content_scripts[{index}]'
                if not isinstance(script, dict):
                    add(where, 'is not an object')
                    continue
                violations.extend(_required_entries(
                    f'{where}.matches', script.get('matches'),
                    _root_free(_pattern_violation), ext_root))
                violations.extend(_optional_entries(
                    f'{where}.js', script.get('js', _ABSENT), _file_violation,
                    ext_root, nonempty=True))
                violations.extend(_optional_entries(
                    f'{where}.css', script.get('css', _ABSENT),
                    _file_violation, ext_root, nonempty=True))
                violations.extend(_optional_entries(
                    f'{where}.exclude_matches',
                    script.get('exclude_matches', _ABSENT),
                    _root_free(_pattern_violation), ext_root, nonempty=True))
                violations.extend(_optional_entries(
                    f'{where}.include_globs',
                    script.get('include_globs', _ABSENT),
                    _root_free(_glob_violation), ext_root, nonempty=True))
                violations.extend(_optional_entries(
                    f'{where}.exclude_globs',
                    script.get('exclude_globs', _ABSENT),
                    _root_free(_glob_violation), ext_root, nonempty=True))
                if 'run_at' in script and script['run_at'] not in RUN_AT:
                    add(f'{where}.run_at',
                        f'is not one of {", ".join(RUN_AT)}')
                if 'all_frames' in script and not isinstance(
                        script['all_frames'], bool):
                    add(f'{where}.all_frames', 'is not a boolean')
                if 'world' in script and script['world'] not in WORLD:
                    add(f'{where}.world',
                        f'is not one of {", ".join(WORLD)}')

    if 'options_ui' in manifest:
        value = manifest['options_ui']
        if not isinstance(value, dict):
            add('options_ui', 'is not an object')
        else:
            problem = _file_violation('options_ui.page', value.get('page'),
                                      ext_root)
            if problem:
                violations.append(problem)
            if 'open_in_tab' in value and not isinstance(
                    value['open_in_tab'], bool):
                add('options_ui.open_in_tab', 'is not a boolean')

    if 'icons' in manifest:
        value = manifest['icons']
        if not isinstance(value, dict):
            add('icons', 'is not an object')
        else:
            for size, name in value.items():
                problem = _file_violation(f'icons[{size}]', name, ext_root)
                if problem:
                    violations.append(problem)
    return violations


def _real_manifest():
    """The shipped manifest, parsed fresh so a plant on disk is seen."""
    return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))


_DROP = object()


def _setting(key, value):
    """Build a mutant with one top-level property replaced."""
    def build(manifest):
        manifest[key] = value
        return manifest
    return build


def _dropping(key):
    """Build a mutant with one top-level property removed."""
    def build(manifest):
        manifest.pop(key, None)
        return manifest
    return build


def _inside(key, subkey, value):
    """Build a mutant with one property of one sub-object replaced."""
    def build(manifest):
        holder = manifest[key]
        if value is _DROP:
            holder.pop(subkey, None)
        else:
            holder[subkey] = value
        return manifest
    return build


def _entry(index, key, subkey, value):
    """Build a mutant with one property of one list entry replaced."""
    def build(manifest):
        holder = manifest[key][index]
        if value is _DROP:
            holder.pop(subkey, None)
        else:
            holder[subkey] = value
        return manifest
    return build


def _extension_root(tmp, *names):
    """An extension root in `tmp` holding exactly `names` as empty files."""
    root = Path(tmp) / 'extension'
    root.mkdir()
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    return root


def _assert_one_refusal(label, build, where, reason, ext_root=EXTENSION_ROOT):
    """A mutant dies once, at the property it mutated, for that reason."""
    manifest = build(_real_manifest())
    violations = manifest_violations(manifest, ext_root)
    assert len(violations) == 1, f'{label}: {violations}'
    assert where in violations[0] and reason in violations[0], (
        f'{label}: {violations}')


def test_the_shipped_manifest_is_accepted(tmp):
    """The shipped manifest is accepted; a JSON error fails the suite."""
    del tmp
    assert manifest_violations(_real_manifest(), EXTENSION_ROOT) == []


def test_a_minimal_manifest_is_accepted(tmp):
    """Four keys are enough: the rest of the manifest is optional."""
    root = _extension_root(tmp, 'background.js')
    minimal = {
        'manifest_version': 3,
        'name': 'Minimal',
        'version': '1.0',
        'background': {'service_worker': 'background.js'},
    }
    assert manifest_violations(minimal, root) == []


def test_every_optional_key_stays_optional(tmp):
    """Optional keys are accepted absent, and an unknown key is not refused."""
    del tmp
    variants = (
        ('world ISOLATED', _entry(1, 'content_scripts', 'world', 'ISOLATED')),
        ('world absent', _entry(1, 'content_scripts', 'world', _DROP)),
        ('run_at absent', _entry(0, 'content_scripts', 'run_at', _DROP)),
        ('icons absent', _dropping('icons')),
        ('options_ui absent', _dropping('options_ui')),
        ('open_in_tab absent', _inside('options_ui', 'open_in_tab', _DROP)),
        ('all_frames absent',
         _entry(0, 'content_scripts', 'all_frames', _DROP)),
        ('an unknown top-level key', _setting('deployed_by', 'CI')),
        ('an unknown content-script key',
         _entry(0, 'content_scripts', 'exclude_js', ['no-such-file.js'])),
    )
    for label, build in variants:
        manifest = build(_real_manifest())
        found = manifest_violations(manifest, EXTENSION_ROOT)
        assert found == [], f'{label}: {found}'


def test_valid_twins_are_accepted(tmp):
    """The valid twin of each refusal branch is accepted."""
    root = _extension_root(tmp, 'background.js', 'icon.svg', 'style.css')
    twin = {
        'manifest_version': 3,
        'name': 'Twins',
        'version': '1.0',
        'background': {'service_worker': 'background.js', 'type': 'module'},
        'content_scripts': [{'matches': ['<all_urls>'], 'css':
                             ['style.css']}],
        'icons': {'16': 'icon.svg'},
    }
    assert manifest_violations(twin, root) == []

    del tmp
    variants = (
        ('every pattern form', _setting('host_permissions', [
            '<all_urls>', '*://*/*', 'http://*/*', 'https://*.example.com/*',
            'http://example.com/path', 'ws://*/*', 'wss://*/*', 'ftp://*/*',
            'file:///*', 'urn:*'])),
        ('a permission subset', _setting('permissions', ['storage'])),
        ('background type module', _inside('background', 'type', 'module')),
        ('document_end', _entry(0, 'content_scripts', 'run_at',
                                'document_end')),
        ('document_idle', _entry(0, 'content_scripts', 'run_at',
                                 'document_idle')),
        ('exclude_matches naming a real pattern',
         _entry(0, 'content_scripts', 'exclude_matches',
                ['https://*.example.com/*'])),
        ('include_globs naming globs',
         _entry(0, 'content_scripts', 'include_globs', ['*://*/*'])),
        ('exclude_globs naming globs',
         _entry(0, 'content_scripts', 'exclude_globs', ['*/nested/*'])),
        ('open_in_tab true', _inside('options_ui', 'open_in_tab', True)),
        ('all_frames true',
         _entry(0, 'content_scripts', 'all_frames', True)),
        ('an empty host_permissions list',
         _setting('host_permissions', [])),
    )
    for label, build in variants:
        manifest = build(_real_manifest())
        found = manifest_violations(manifest, EXTENSION_ROOT)
        assert found == [], f'{label}: {found}'


def test_the_root_and_scalar_branches_are_refused(tmp):
    """A root that is not an object, and a scalar key of the wrong shape."""
    del tmp
    rows = (
        ('root-not-an-object', lambda manifest: ['not', 'an', 'object'],
         'manifest', 'not a JSON object'),
        ('manifest_version-not-an-integer',
         _setting('manifest_version', '3'), 'manifest_version',
         'is not an integer'),
        # manifest_version 2 is a valid MV2 manifest and still refused here.
        ('manifest_version-not-3', _setting('manifest_version', 2),
         'manifest_version', 'is not 3'),
        ('manifest_version-true', _setting('manifest_version', True),
         'manifest_version', 'is not an integer'),
        ('name-not-a-string', _setting('name', 7), 'name',
         'is not a nonempty string'),
        ('name-empty', _setting('name', ''), 'name',
         'is not a nonempty string'),
        ('version-not-a-string', _setting('version', 24), 'version',
         'is not a nonempty string'),
        ('version-empty', _setting('version', ''), 'version',
         'is not a nonempty string'),
        ('description-not-a-string', _setting('description', 7),
         'description', 'is present but is not a string'),
        ('description-null', _setting('description', None), 'description',
         'is present but is not a string'),
    )
    for label, build, where, reason in rows:
        _assert_one_refusal(label, build, where, reason)


def test_the_permission_branches_are_refused(tmp):
    """A permissions value that is not a list of known names, once each."""
    del tmp
    rows = (
        ('permissions-not-a-list', _setting('permissions', 'tabs'),
         'permissions', 'is not a list'),
        ('permissions-entry-not-a-string',
         _setting('permissions', ['tabs', 9]), 'permissions[1]',
         'is not a string'),
        ('permissions-unknown-name',
         _setting('permissions', ['tabs', 'tab']), 'permissions[1]',
         'a permission Chrome grants'),
        ('permissions-duplicate',
         _setting('permissions', ['tabs', 'tabs', 'storage']),
         'permissions[1]', 'repeats tabs'),
    )
    for label, build, where, reason in rows:
        _assert_one_refusal(label, build, where, reason)


def test_the_host_pattern_branches_are_refused(tmp):
    """Every way a match pattern can miss the grammar Chrome parses."""
    del tmp
    # A host with a slash in it is not representable — no control plants one.
    rows = (
        ('host_permissions-not-a-list',
         _setting('host_permissions', '<all_urls>'), 'host_permissions',
         'is not a list'),
        ('host_permissions-entry-not-a-string',
         _setting('host_permissions', [7]), 'host_permissions[0]',
         'must be a nonempty string'),
        ('host_permissions-empty-pattern',
         _setting('host_permissions', ['']), 'host_permissions[0]',
         'must be a nonempty string'),
        ('host-pattern-no-scheme-separator',
         _setting('host_permissions', ['example.com/*']),
         'host_permissions[0]', 'scheme://host/path'),
        ('host-pattern-unknown-scheme',
         _setting('host_permissions', ['gopher://*/*']),
         'host_permissions[0]', 'gopher'),
        ('host-pattern-no-path',
         _setting('host_permissions', ['https://example.com']),
         'host_permissions[0]', 'has no path'),
        ('host-pattern-no-host',
         _setting('host_permissions', ['https:///path']),
         'host_permissions[0]', 'has no host'),
        ('host-pattern-empty-star-dot-rest',
         _setting('host_permissions', ['https://*./path']),
         'host_permissions[0]', 'empty star-dot rest'),
        ('host-pattern-star-in-a-literal-host',
         _setting('host_permissions', ['https://*example.com/*']),
         'host_permissions[0]', 'star outside the star-dot form'),
        ('host-pattern-star-in-star-dot-rest',
         _setting('host_permissions', ['https://*.ex*ample.com/*']),
         'host_permissions[0]', 'star outside the star-dot form'),
        ('host-pattern-file-with-host',
         _setting('host_permissions', ['file://host/path']),
         'host_permissions[0]', 'gives the file scheme a host'),
        ('host-pattern-urn-with-authority',
         _setting('host_permissions', ['urn://host']),
         'host_permissions[0]', 'not a urn: value'),
    )
    for label, build, where, reason in rows:
        _assert_one_refusal(label, build, where, reason)


def test_the_background_branches_are_refused(tmp):
    """A background the MV3 service-worker model cannot load, once each."""
    del tmp
    rows = (
        ('background-not-an-object', _setting('background', []),
         'background', 'is not an object'),
        ('background-service_worker-missing',
         _inside('background', 'service_worker', _DROP),
         'background.service_worker', 'must be a nonempty string'),
        ('background-service_worker-empty',
         _inside('background', 'service_worker', ''),
         'background.service_worker', 'must be a nonempty string'),
        ('background-service_worker-not-a-string',
         _inside('background', 'service_worker', 7),
         'background.service_worker', 'must be a nonempty string'),
        ('background-service_worker-missing-file',
         _inside('background', 'service_worker', 'no-such-worker.js'),
         'background.service_worker', 'is not a file the extension ships'),
        ('background-type-not-module',
         _inside('background', 'type', 'classic'), 'background.type',
         'is not module'),
    )
    for label, build, where, reason in rows:
        _assert_one_refusal(label, build, where, reason)


def test_the_content_script_branches_are_refused(tmp):
    """One mutant per refusal branch of the content_scripts entries."""
    del tmp
    # `matches` is required; the other lists are checked only when present.
    rows = (
        ('content_scripts-not-a-list', _setting('content_scripts', {}),
         'content_scripts', 'is not a list'),
        ('content_scripts-entry-not-an-object',
         _setting('content_scripts', ['content.js']), 'content_scripts[0]',
         'is not an object'),
        ('matches-missing',
         _entry(0, 'content_scripts', 'matches', _DROP),
         'content_scripts[0].matches', 'is not a nonempty list'),
        ('matches-not-a-list',
         _entry(0, 'content_scripts', 'matches', '<all_urls>'),
         'content_scripts[0].matches', 'is not a nonempty list'),
        ('matches-empty', _entry(0, 'content_scripts', 'matches', []),
         'content_scripts[0].matches', 'is not a nonempty list'),
        ('matches-entry-not-a-string',
         _entry(0, 'content_scripts', 'matches', [7]),
         'content_scripts[0].matches[0]', 'must be a nonempty string'),
        ('matches-bad-pattern',
         _entry(0, 'content_scripts', 'matches', ['gopher://*/*']),
         'content_scripts[0].matches[0]', 'gopher'),
        ('js-not-a-list', _entry(0, 'content_scripts', 'js', '<all_urls>'),
         'content_scripts[0].js', 'is not a list'),
        ('js-empty', _entry(0, 'content_scripts', 'js', []),
         'content_scripts[0].js', 'is present but is empty'),
        ('css-not-a-list', _entry(0, 'content_scripts', 'css', 'content.js'),
         'content_scripts[0].css', 'is not a list'),
        ('css-empty', _entry(0, 'content_scripts', 'css', []),
         'content_scripts[0].css', 'is present but is empty'),
        ('css-entry-not-a-string', _entry(0, 'content_scripts', 'css', [7]),
         'content_scripts[0].css[0]', 'must be a nonempty string'),
        ('css-missing-file',
         _entry(0, 'content_scripts', 'css', ['no-such.css']),
         'content_scripts[0].css[0]', 'is not a file the extension ships'),
        ('js-entry-not-a-string', _entry(0, 'content_scripts', 'js', [7]),
         'content_scripts[0].js[0]', 'must be a nonempty string'),
        ('js-missing-file',
         _entry(0, 'content_scripts', 'js', ['no-such-file.js']),
         'content_scripts[0].js[0]', 'is not a file the extension ships'),
        ('run_at-not-in-the-enum',
         _entry(0, 'content_scripts', 'run_at', 'document_whenever'),
         'content_scripts[0].run_at', 'is not one of'),
        ('run_at-null', _entry(0, 'content_scripts', 'run_at', None),
         'content_scripts[0].run_at', 'is not one of'),
        ('all_frames-not-a-bool',
         _entry(0, 'content_scripts', 'all_frames', 'false'),
         'content_scripts[0].all_frames', 'is not a boolean'),
        ('world-not-in-the-enum',
         _entry(1, 'content_scripts', 'world', 'main'),
         'content_scripts[1].world', 'is not one of'),
        ('exclude_matches-not-a-list',
         _entry(0, 'content_scripts', 'exclude_matches', '<all_urls>'),
         'content_scripts[0].exclude_matches', 'is not a list'),
        ('exclude_matches-empty',
         _entry(0, 'content_scripts', 'exclude_matches', []),
         'content_scripts[0].exclude_matches', 'is present but is empty'),
        ('exclude_matches-bad-pattern',
         _entry(0, 'content_scripts', 'exclude_matches',
                ['example.com/*']), 'content_scripts[0].exclude_matches[0]',
         'scheme://host/path'),
        ('include_globs-not-a-list',
         _entry(0, 'content_scripts', 'include_globs', '*'),
         'content_scripts[0].include_globs', 'is not a list'),
        ('include_globs-empty',
         _entry(0, 'content_scripts', 'include_globs', []),
         'content_scripts[0].include_globs', 'is present but is empty'),
        ('include_globs-entry-not-a-string',
         _entry(0, 'content_scripts', 'include_globs', [7]),
         'content_scripts[0].include_globs[0]', 'must be a nonempty string'),
        ('include_globs-empty-string-entry',
         _entry(0, 'content_scripts', 'include_globs', ['']),
         'content_scripts[0].include_globs[0]', 'must be a nonempty string'),
        ('exclude_globs-not-a-list',
         _entry(0, 'content_scripts', 'exclude_globs', '*'),
         'content_scripts[0].exclude_globs', 'is not a list'),
        ('exclude_globs-entry-not-a-string',
         _entry(0, 'content_scripts', 'exclude_globs', [7]),
         'content_scripts[0].exclude_globs[0]', 'must be a nonempty string'),
        ('exclude_globs-empty',
         _entry(0, 'content_scripts', 'exclude_globs', []),
         'content_scripts[0].exclude_globs', 'is present but is empty'),
    )
    for label, build, where, reason in rows:
        _assert_one_refusal(label, build, where, reason)


def test_the_options_and_icon_branches_are_refused(tmp):
    """A settings page or an icon the package cannot ship, once each."""
    del tmp
    rows = (
        ('options_ui-not-an-object', _setting('options_ui', []),
         'options_ui', 'is not an object'),
        ('options_ui-page-missing',
         _inside('options_ui', 'page', _DROP), 'options_ui.page',
         'must be a nonempty string'),
        ('options_ui-page-not-a-string',
         _inside('options_ui', 'page', 7), 'options_ui.page',
         'must be a nonempty string'),
        ('options_ui-page-missing-file',
         _inside('options_ui', 'page', 'no-such-page.html'), 'options_ui.page',
         'is not a file the extension ships'),
        ('options_ui-open_in_tab-not-a-bool',
         _inside('options_ui', 'open_in_tab', 'no'), 'options_ui.open_in_tab',
         'is not a boolean'),
        ('icons-not-an-object', _setting('icons', []), 'icons',
         'is not an object'),
        ('icons-value-not-a-string', _setting('icons', {'16': 16}),
         'icons[16]', 'must be a nonempty string'),
        ('icons-value-missing-file',
         _setting('icons', {'16': 'no-such-icon.png'}), 'icons[16]',
         'is not a file the extension ships'),
    )
    for label, build, where, reason in rows:
        _assert_one_refusal(label, build, where, reason)


def test_the_path_safety_branches_are_refused(tmp):
    """A named path that leaves the extension root is refused."""
    del tmp
    rows = (
        ('background-service_worker-absolute-path',
         _inside('background', 'service_worker', '/escaped.js'),
         'background.service_worker', 'leaves the extension root'),
        ('js-parent-traversal',
         _entry(0, 'content_scripts', 'js', ['../server.py']),
         'content_scripts[0].js[0]', 'leaves the extension root'),
        ('js-backslash-traversal',
         _entry(0, 'content_scripts', 'js', ['..\\server.py']),
         'content_scripts[0].js[0]', 'backslash'),
        ('js-drive-letter-path',
         _entry(0, 'content_scripts', 'js', ['C:/escaped.js']),
         'content_scripts[0].js[0]', 'colon'),
        ('options_ui-page-parent-traversal',
         _inside('options_ui', 'page', '../README.md'), 'options_ui.page',
         'leaves the extension root'),
    )
    for label, build, where, reason in rows:
        _assert_one_refusal(label, build, where, reason)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='extmanifest_')


if __name__ == '__main__':
    raise SystemExit(main())
