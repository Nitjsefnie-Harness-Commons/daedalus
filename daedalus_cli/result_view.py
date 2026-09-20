"""Public result metadata, separate from authenticated bridge selectors."""


def relative_upload_path(path):
    parts = path.split('/')
    return '/'.join(parts[1:]) if len(parts) == 3 else path


def public_result(body):
    result = dict(body)
    result.pop('token', None)
    return result
