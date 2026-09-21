"""The upload namespace and the routes that read or write it.

`GET /upload`, `POST /upload`, `DELETE /upload` and `GET /screenshot` each
return `(status, payload)` or a `FileAnswer`, and take the upload directory as
a parameter rather than importing `config`.
"""
import base64
import itertools
import os
import pathlib
import shutil
import time

from daedalus_bridge.route_answer import FileAnswer
from daedalus_bridge import atomic_file, path_safety


# One table for every place a screenshot format is decided: what /upload
# accepts, what /screenshot will discover on disk, and what content type it is
# served with. They were three separate lists, and `webp` was in the first
# only -- so the upload stored a file and answered 200 that /screenshot then
# reported as absent.
SCREENSHOT_TYPES = {
    'png': 'image/png',
    'jpeg': 'image/jpeg',
    'jpg': 'image/jpeg',
    'webp': 'image/webp',
}

# One bridge process owns a data root, so a per-process counter suffices.
_name_counter = itertools.count(1)


def screenshot_mime(fmt):
    """The content type a served file's format is written with."""
    mime_map = {**SCREENSHOT_TYPES,
                'json': 'application/json', 'txt': 'text/plain'}
    return mime_map.get(fmt, 'application/octet-stream')


def _reserved_name(name):
    """Whether `name` is the store's own in-progress reservation."""
    return name.endswith('.tmp')


def stored_uploads(token_dir, upload_id):
    """Every stored file, newest id first and by name within an id.

    os.scandir rather than iterdir: the kernel already said whether an entry
    is a file or a directory, and asking pathlib the same question is a stat
    per entry. Deciding WHICH entries exist is separated here from describing
    them, so a caller can count everything while statting only what it is
    about to return.

    The order is the one the listing has always had, because a page is only
    meaningful if the sequence it slices is stable between requests.
    """
    if upload_id:
        id_dirs = [path_safety.under(token_dir, upload_id)]
    else:
        try:
            with os.scandir(token_dir) as entries:
                dirs = list(entries)
        except OSError:
            # The token directory can disappear after the route's check.
            return
        dated_dirs = []
        for entry in dirs:
            try:
                if not entry.is_dir():
                    continue
                mtime = os.stat(entry.path).st_mtime
            except OSError:
                # A concurrent delete can remove an id before sorting.
                continue
            dated_dirs.append((pathlib.Path(entry.path), mtime))
        dated_dirs.sort(key=lambda item: item[1], reverse=True)
        id_dirs = [path for path, _mtime in dated_dirs]
    for id_dir in id_dirs:
        try:
            with os.scandir(id_dir) as entries:
                files = list(entries)
        except OSError:
            # A concurrent delete can remove the id being scanned.
            continue
        files.sort(key=lambda entry: entry.name)
        for entry in files:
            try:
                is_file = entry.is_file()
            except OSError:
                # A concurrent delete can make an entry's type check fail.
                continue
            if is_file and not _reserved_name(entry.name):
                yield id_dir.name, entry


def list_uploads(upload_dir, token, params):
    """GET /upload?token=X[&id=Y][&limit=N&offset=M] — list uploaded files.
    When limit or offset is provided, returns {items, total, limit, offset}.
    Without either, returns a bare array (back-compat)."""
    upload_id = params.get('id', [''])[0]
    limit_p = params.get('limit', [None])[0]
    offset_p = params.get('offset', [None])[0]
    if upload_id and path_safety.unsafe_component(upload_id):
        return 400, {'error': 'invalid path component'}
    # Before the directory is looked at, so that whether a query is well
    # formed does not depend on whether anything has been uploaded yet:
    # the shortcut below used to answer 200 for a malformed limit on an
    # empty data root and 400 for the same query once the directory
    # existed.
    paged = limit_p is not None or offset_p is not None
    lim, off = 200, 0
    if paged:
        try:
            lim = int(limit_p) if limit_p is not None else 200
            off = int(offset_p) if offset_p is not None else 0
        except ValueError:
            return 400, {'error': 'invalid limit/offset'}
        lim = max(1, min(lim, 1000))
        off = max(0, off)
    try:
        token_dir = path_safety.under(upload_dir, token)
    except ValueError:
        return 400, {'error': 'invalid path component'}
    try:
        token_exists = token_dir.is_dir()
    except OSError:
        # A concurrent delete can make the token's directory stat fail.
        token_exists = False
    if not token_exists:
        if paged:
            return 200, {'items': [], 'total': 0,
                         'limit': lim, 'offset': off}
        return 200, []
    # Counting is not describing. Every stored file is counted, because
    # `total` says how many pages there are; only the page's own files are
    # statted, because size and mtime are a syscall each. `limit=1` used
    # to stat every file in the namespace twice to describe one of them.
    window = range(off, off + lim) if paged else None
    results = []
    total = 0
    # The walk is inside the guard because stored_uploads re-roots the
    # named id under the token directory, and a listing must refuse an
    # escape the same way a delete does rather than dying mid-response.
    try:
        for index, (id_name, entry) in enumerate(
                stored_uploads(token_dir, upload_id)):
            total += 1
            if window is not None and index not in window:
                continue
            try:
                info = os.stat(entry.path)
            except OSError:
                # A concurrent delete can remove a page entry.
                continue
            results.append({
                'id': id_name,
                'filename': entry.name,
                'size': info.st_size,
                'mtime': int(info.st_mtime),
                'path': f'{token}/{id_name}/{entry.name}',
            })
    except ValueError:
        return 400, {'error': 'invalid path component'}
    if paged:
        return 200, {'items': results, 'total': total,
                     'limit': lim, 'offset': off}
    return 200, results


def store_upload(upload_dir, body):
    """POST /upload — store binary data.
    Body: {token, id, data (base64), filename (optional)}.
    Screenshots: omit filename, stored as <token>/<id>/<ms>_<n>.<format>
    Generic: provide filename, stored as <token>/<id>/<filename>
    """
    token = body.get('token', '')
    upload_id = body.get('id', '')
    data_b64 = body.get('data', '')
    filename = body.get('filename', '')
    fmt = body.get('format', 'png')
    # isinstance BEFORE the membership test: `[] in SCREENSHOT_TYPES`
    # raises TypeError rather than answering False, and an exception here
    # killed the request thread, so the caller got a dropped connection
    # instead of the refusal this line already knew how to write.
    if not isinstance(fmt, str) or fmt not in SCREENSHOT_TYPES:
        return 400, {'error': 'unsupported format'}
    if not upload_id:
        return 400, {'error': 'missing id'}
    if not data_b64:
        return 400, {'error': 'missing data'}
    for val in (token, upload_id, filename):
        if path_safety.unsafe_component(val):
            return 400, {'error': 'invalid path component'}
    # `.tmp` is the store's own reservation below, and the listing skips
    # it; an accepted upload must never be hidden by that skip.
    if _reserved_name(filename):
        return 400, {'error': 'invalid path component'}
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        return 400, {'error': 'invalid base64'}
    try:
        dest_dir = path_safety.under(upload_dir, token, upload_id)
        if filename:
            dest = path_safety.under(dest_dir, filename)
        else:
            ts = int(time.time() * 1000)
            dest = path_safety.under(
                dest_dir, f'{ts:013d}_{next(_name_counter):06d}.{fmt}')
    except ValueError:
        return 400, {'error': 'invalid path component'}
    # A sibling temp, unique per write, published by one replace as
    # command_queue does, so a reader never sees a partially written file
    # at the final name and two writers of one name never share a temp.
    tmp = dest.with_name(f'.{dest.name}.{next(_name_counter)}.tmp')
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        atomic_file.write_bytes_retrying(tmp, raw)
        atomic_file.replace_atomically(tmp, dest)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            # Best effort; the 500 below is the answer that matters.
            pass
        return 500, {'error': 'upload storage failure'}
    size = len(raw)
    del raw  # drop the decoded copy before responding
    # as_posix, not str: the wire format has to be one shape on
    # every platform, and the listing routes already build these
    # with forward slashes. str() yields backslashes on Windows,
    # so POST /upload and GET /uploads disagreed about the same
    # file and a client could not feed one to the other.
    rel = dest.relative_to(upload_dir).as_posix()
    print(f'[UPLOAD] {rel} ({size} bytes)', flush=True)
    return 200, {'ok': True, 'path': rel, 'size': size}


def delete_upload(upload_dir, body):
    """DELETE /upload — remove uploaded files.
    {token, id} — delete all files under token/id/
    {token, id, filename} — delete specific file
    {token} — delete all uploads for token
    """
    token = body['token']
    upload_id = body.get('id', '')
    filename = body.get('filename', '')
    for val in (upload_id, filename):
        if path_safety.unsafe_component(val):
            return 400, {'error': 'invalid path component'}
    if _reserved_name(filename):
        return 400, {'error': 'invalid path component'}
    # A filename names a file inside an id, so without one it matches
    # neither the file branch nor the id branch below and used to reach the
    # branch that removes the token's whole namespace: naming one file
    # deleted every upload the token had, and answered that as success.
    if filename and not upload_id:
        return 400, {'error': 'filename requires id'}
    # under rather than a join: this branch removes trees, so the
    # question that matters is where the path ended up, not how each
    # component looked. A ValueError here is the same refusal the shape
    # check above gives, reached by the other route.
    try:
        if filename and upload_id:
            target = path_safety.under(
                upload_dir, token, upload_id, filename)
            if not target.is_file():
                return 404, {'error': 'file not found'}
            target.unlink()
            print(f'[DELETE] {token}/{upload_id}/{filename}', flush=True)
        elif upload_id:
            target = path_safety.under(upload_dir, token, upload_id)
            if not target.is_dir():
                return 404, {'error': 'id not found'}
            shutil.rmtree(target)
            print(f'[DELETE] {token}/{upload_id}/', flush=True)
        else:
            target = path_safety.under(upload_dir, token)
            if not target.is_dir():
                return 404, {'error': 'token not found'}
            shutil.rmtree(target)
            print(f'[DELETE] {token}/', flush=True)
    except ValueError:
        return 400, {'error': 'invalid path component'}
    except OSError:
        return 500, {'error': 'upload delete failure'}
    return 200, {'ok': True}


def _format_of(path):
    """The suffix a stored file's type is decided by, case folded."""
    return path.suffix.lstrip('.').lower()


def _stored_file(upload_dir, token, parts, missing):
    """Serve `parts` from under the token's namespace, or say why not.

    The callers have checked each component; this checks the join, the
    way a delete does, because a symlink inside the namespace is made of
    components that are individually harmless. The type comes from the
    suffix, and a suffix the table does not know is served as bytes
    rather than refused: this is what a browser downloads any upload by.
    """
    try:
        target = path_safety.under(upload_dir, token, *parts)
    except ValueError:
        return 400, {'error': 'invalid path component'}
    if not target.is_file():
        return 404, {'error': missing}
    return FileAnswer(target, screenshot_mime(_format_of(target)))


def named_file(upload_dir, token, named):
    """GET /upload?path=<id>/<file> — serve that stored file.

    The canonical credential carrier is the Authorization: Bearer header.
    The selector is relative to the namespace the credential established,
    and it is those two components exactly. The listing's `path` begins
    with the token, and a request target is what a proxy access log
    records whether or not a link on a page carried it, so the route a
    browser fetches every upload by does not take that form: handed the
    listed path back, token-led or not, it answers 400 like any other
    shape rather than teaching a client to put the credential in a query.
    `named_upload` keeps the token-led form for the path a screenshot
    result carries.
    """
    parts = named.split('/')
    if len(parts) != 2 or not all(parts):
        return 400, {'error': 'path must be <id>/<file>'}
    if any(path_safety.unsafe_component(part) for part in parts):
        return 400, {'error': 'invalid path component'}
    if _reserved_name(parts[1]):
        return 400, {'error': 'invalid path component'}
    return _stored_file(upload_dir, token, parts, 'file not found')


def named_upload(upload_dir, token, named):
    """Serve exactly the screenshot a result named, not whatever is newest.

    `named` is the `path` POST /upload answered with and the result
    carries, token component included. Screenshot ids are reused — `_ss`
    is the default one — so an id identifies a directory rather than a
    capture, and the newest file in it belongs to whichever invocation
    finished last. Every component is checked the way each was checked
    on the way in, and the leading one has to be the caller's own token:
    one token's paths never name another's storage. The rest is resolved
    as any stored file is; only a screenshot type is answered here.
    """
    parts = named.split('/')
    if any(path_safety.unsafe_component(part) for part in parts):
        return 400, {'error': 'invalid path component'}
    if parts[0] != token:
        return 404, {'error': 'no screenshot'}
    answer = _stored_file(upload_dir, token, parts[1:], 'no screenshot')
    if not isinstance(answer, FileAnswer):
        return answer
    if _format_of(answer.path) not in SCREENSHOT_TYPES:
        return 404, {'error': 'no screenshot'}
    return answer


def latest_screenshot(upload_dir, token, params):
    """GET /screenshot?token=X&id=Y — serve the latest screenshot for
    that id. Or token only for the latest across all ids."""
    upload_id = params.get('id', [''])[0]
    if upload_id and path_safety.unsafe_component(upload_id):
        return 400, {'error': 'invalid path component'}
    try:
        token_dir = path_safety.under(upload_dir, token)
    except ValueError:
        return 400, {'error': 'invalid path component'}
    try:
        if not token_dir.is_dir():
            return 404, {'error': 'no uploads'}
        search_dirs = ([token_dir / upload_id] if upload_id
                       else sorted(token_dir.iterdir()))
    except OSError:
        # A concurrent delete can remove the token directory before its scan.
        return 404, {'error': 'no uploads'}
    latest = None
    latest_mtime = 0
    for d in search_dirs:
        try:
            if not d.is_dir():
                continue
            files = list(d.iterdir())
        except OSError:
            # A concurrent delete can remove an id between check and scan.
            continue
        for f in files:
            if _format_of(f) in SCREENSHOT_TYPES:
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    # A concurrent delete can remove a screenshot candidate.
                    continue
                if latest is None or mtime > latest_mtime:
                    latest = f
                    latest_mtime = mtime
    if not latest:
        return 404, {'error': 'no screenshot'}
    return FileAnswer(latest, screenshot_mime(_format_of(latest)))
