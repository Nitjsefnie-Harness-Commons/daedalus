"""Public result metadata, separate from authenticated bridge selectors."""


def relative_upload_path(path):
    parts = path.split('/')
    return '/'.join(parts[1:]) if len(parts) == 3 else path


def public_result(body):
    result = dict(body)
    credential = result.pop('token', None)
    value = result.get('result')
    if isinstance(value, dict):
        path = value.get('path')
        if (isinstance(path, str) and credential
                and path.startswith(credential + '/')):
            result['result'] = {
                **value, 'path': relative_upload_path(path)}
    return result
