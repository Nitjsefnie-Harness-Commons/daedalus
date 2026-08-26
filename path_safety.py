"""Path-component safety.

Caller-controlled path components use these helpers. Dashboard asset URLs
apply the same checks to each component before a resolved-root containment
check. The helpers are shared so every component validation gets the same
traversal, drive, and Windows-name rules.

Backslash is rejected alongside the forward slash because it is a separator
on Windows; leaving it out made `a\\b` a nested path there and a plain name
everywhere else.
"""
import os
import pathlib


WINDOWS_DEVICE_NAMES = frozenset({
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
})
WINDOWS_INVALID_PATH_CHARS = frozenset('<>:"/\\|?*')
MAX_COMPONENT_BYTES = 240


def unsafe_component(value):
    """Return whether `value` violates the bridge's component policy.

    Rejects non-strings, traversal markers (`..`), C0/C1 control characters,
    surrogate code points, every Windows-invalid path character
    (`<>:"/\\|?*`), Windows device names (`CON`, `PRN`, `AUX`, `NUL`,
    `COM1`-`COM9`, `LPT1`-`LPT9`, case-insensitively, with or without an
    extension), trailing dots or spaces, and UTF-8 encodings longer than 240
    bytes. Derived result filenames and command-queue target directories are
    checked after construction. Legacy command files, dashboard event files,
    screenshot names, and segment record/data/temp names use fixed
    server-generated affixes whose complete components are bounded by
    construction. This policy does not claim later filesystem operations
    cannot fail for unrelated reasons.
    """
    if not isinstance(value, str):
        return True
    invalid_codepoint = any(
        ord(char) < 32 or 127 <= ord(char) <= 159
        or 0xD800 <= ord(char) <= 0xDFFF
        for char in value)
    if ('..' in value
            or any(char in WINDOWS_INVALID_PATH_CHARS for char in value)
            or invalid_codepoint or value.endswith(('.', ' '))):
        return True
    if len(value.encode('utf-8')) > MAX_COMPONENT_BYTES:
        return True
    device_stem = value.split('.', 1)[0].rstrip(' .').upper()
    return device_stem in WINDOWS_DEVICE_NAMES


_EXTENDED_PREFIX = '\\\\?\\'
_EXTENDED_UNC_PREFIX = '\\\\?\\UNC\\'


def path_key(path):
    r"""A comparable spelling of an already-resolved path.

    Two `realpath` results under one root are not guaranteed to be spelled the
    same way. On Windows the `\\?\` extended-length prefix that
    `_getfinalpathname` returns is stripped only when a second call on the
    stripped path agrees, or fails with the same winerror as the first — and
    that verification call fails with a sharing violation while another thread
    is replacing the file, where the first call had succeeded. The directory,
    which nothing is replacing, then comes back bare while the file under it
    keeps the prefix, and comparing the two as strings says the file escaped
    its root. Every filesystem-backed route shares that comparison, so a
    concurrent writer could turn any of them into a 400.

    Case is normalized for the same reason and not as a concession: these are
    two spellings of one path, and on a case-insensitive filesystem a
    case-sensitive comparison answers a question nobody asked.
    """
    text = os.fspath(path)
    if text.startswith(_EXTENDED_UNC_PREFIX):
        text = '\\\\' + text[len(_EXTENDED_UNC_PREFIX):]
    elif text.startswith(_EXTENDED_PREFIX):
        text = text[len(_EXTENDED_PREFIX):]
    return os.path.normcase(text)


def under(root, *parts):
    """Join `parts` under `root` and refuse a result that lands outside it.

    This asks a different question from `unsafe_component`. That one is a
    shape check on one string — does this component look dangerous. This one
    is about the result: did the path I built end up where I meant. A
    component blacklist cannot answer that, because a symlink inside the root
    pointing out of it is made of components that are individually harmless,
    and neither can a caller that validated its parts and then joined them
    somewhere else.

    Both are kept. The component check is what turns a bad request into a
    specific 400 naming the field; this is the backstop that makes the answer
    true regardless of which components were involved.

    The path returned is the one that was checked, resolved. Returning the
    unresolved join would mean the check was performed on a different object
    from the one used, which is the gap the check exists to close.

    Raises ValueError, which is what the routes taking these values already
    answer 400 for.
    """
    resolved_root = os.path.realpath(root)
    candidate = os.path.realpath(
        os.path.join(resolved_root, *(str(part) for part in parts)))
    root_key = path_key(resolved_root)
    candidate_key = path_key(candidate)
    if (candidate_key != root_key
            and not candidate_key.startswith(root_key + os.sep)):
        raise ValueError(f'path escapes its root: {parts!r}')
    # The resolved path, not the normalized key: the key exists to compare two
    # spellings, and the caller needs the one the filesystem answers to.
    return pathlib.Path(candidate)


def bad_token(token):
    """True when `token` is unusable as a directory name.

    Stricter than unsafe_component on two counts. A token may not contain a
    dot, so it can never collide with one of the `<token>.json` files beside
    it. And it may not contain an underscore, because per-tab paths are
    `<token>_<tab>`: without that rule the token `victim_x` and the pair
    (token `victim`, tab `x`) name the same file, so one caller reads another's
    results and both write the same command queue. Tokens are generated UUIDs,
    which contain neither.
    """
    return (not token or unsafe_component(token)
            or '.' in token or '_' in token)


def normalized_tab_id(value):
    """Normalize string or integer tab-id JSON values; return None
    otherwise."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def derived_component(value):
    """Return a checked derived path component, or raise ValueError."""
    if unsafe_component(value):
        raise ValueError('unsafe derived path component')
    return value


def command_target_names(token, tab=''):
    """Return the checked queue directory and bounded legacy filename."""
    queue_name = derived_component(
        f'{token}_{tab}' if tab else token)
    return queue_name, f'{queue_name}.json'
