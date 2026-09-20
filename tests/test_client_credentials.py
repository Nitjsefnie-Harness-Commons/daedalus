"""Client output must not publish bridge-owned credentials."""
import asyncio
import base64
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import test_cli as cli  # noqa: E402
import test_mcp_server as mcp  # noqa: E402
import test_mcp_tools as mcp_tools  # noqa: E402

from daedalus_cli.output import print_result  # noqa: E402


def _no_token(value, token):
    assert token not in json.dumps(value), ('bridge token leaked', value)


def _eval(tmp, name):
    mcp._need_deps()
    with _util.bridge(tmp, env=mcp.BRIDGE_ENV) as (base, root):
        mod = mcp._load_mcp(base)
        arguments = {'tab_id': 'page'}
        if name in ('exec', 'put'):
            arguments.update(cmd_id='example', code='42')
        value, _ = mcp._answer_mcp_command(
            base, root, mod, lambda: getattr(mod, name)(**arguments),
            {'token': 'application-value', 'answer': 42}, tab='page')
        _no_token(value, mcp.TOK)
        assert 'token' not in value, value
        assert value['value'] == {'token': 'application-value', 'answer': 42}
        assert value['tabId'] == 'page', value
        assert value['deliveryId'] and value['resultGeneration'], value


def test_mcp_exec_drops_bridge_token(tmp):
    _eval(tmp, 'exec')


def test_mcp_put_drops_bridge_token(tmp):
    _eval(tmp, 'put')


def test_mcp_title_drops_bridge_token(tmp):
    _eval(tmp, 'title')


def test_mcp_url_drops_bridge_token(tmp):
    _eval(tmp, 'url')


def test_mcp_result_drops_only_envelope_token(tmp):
    mcp._need_deps()
    with _util.bridge(tmp, env=mcp.BRIDGE_ENV) as (base, _root):
        mod = mcp._load_mcp(base)
        mod._token.set(mcp.TOK)
        for consume in (False, True):
            status, body = _util.post_json(base + '/result', {
                'token': mcp.TOK, 'id': 'shot', 'error': None,
                'result': {'path': f'{mcp.TOK}/shot/image.png', 'size': 4}})
            assert status == 200, body
            value = asyncio.run(mod.result(consume=consume))
            assert 'token' not in value, value
            assert value['value'] == {
                'path': f'{mcp.TOK}/shot/image.png', 'size': 4}, value


def test_cli_raw_result_drops_bridge_token(tmp):
    with _util.bridge(tmp, env=cli.BRIDGE_ENV) as (base, _root):
        status, body = _util.post_json(base + '/result', {
            'token': cli.TOK, 'id': 'example', 'result': 42,
            'error': None, 'ts': 1})
        assert status == 200, body
        result = cli.run_cli(['result', '--raw'], cli.cli_env(
            DAEDALUS_URL=base, DAEDALUS_TOKEN=cli.TOK))
        assert result.returncode == 0, result.stderr
        _no_token(result.stdout, cli.TOK)
        value = json.loads(result.stdout)
        assert 'token' not in value, value
        assert value['result'] == 42, value


def test_cli_raw_cdp_drops_bridge_token(tmp):
    with _util.bridge(tmp, env=cli.BRIDGE_ENV) as (base, root):
        code, out, err, _ = cli._answer_one_ext_command(
            base, root, ['cdp', 'Page.enable', '--raw'], {'enabled': True},
            cli.cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=cli.TOK))
        assert code == 0, err
        _no_token(out, cli.TOK)
        value = json.loads(out)
        assert 'token' not in value, value
        assert value['result'] == {'enabled': True}, value


def test_cli_generic_result_preserves_screenshot_path(tmp):
    del tmp
    for raw in (False, True):
        body = {'token': 'bridge-secret', 'id': 'shot', 'ts': 1,
                'error': None,
                'result': {'path': 'bridge-secret/shot/image.png', 'size': 4}}
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(errors):
            print_result(body, raw=raw)
        if raw:
            assert 'token' not in json.loads(output.getvalue())
        assert 'bridge-secret/shot/image.png' in output.getvalue()
        assert errors.getvalue() == '', errors.getvalue()
        assert body['token'] == 'bridge-secret', body
        assert body['result']['path'].startswith('bridge-secret/'), body


def _upload(base, token):
    data = b'exact-capture'
    status, body = _util.post_json(base + '/upload', {
        'token': token, 'id': 'shot&café', 'filename': 'image.png',
        'data': base64.b64encode(data).decode()})
    assert status == 200, body
    return body, data


def test_cli_screenshot_metadata_omits_token_and_download_works(tmp):
    output = Path(tmp) / 'capture.png'
    with _util.bridge(tmp, env=cli.BRIDGE_ENV) as (base, root):
        uploaded, data = _upload(base, cli.TOK)
        code, out, err, _ = cli._answer_one_ext_command(
            base, root, ['screenshot', '--output', str(output)], uploaded,
            cli.cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=cli.TOK))
        assert code == 0, err
        assert output.read_bytes() == data
        _no_token(out, cli.TOK)
        assert 'uploaded: shot&café/image.png' in out, out


def _screenshot(tmp, include_image):
    mcp._need_deps()
    with _util.bridge(tmp, env=mcp.BRIDGE_ENV) as (base, root):
        mod = mcp._load_mcp(base)
        uploaded, data = _upload(base, mcp.TOK)
        value, _ = mcp._answer_mcp_command(
            base, root, mod,
            lambda: mod.screenshot(include_image=include_image), uploaded)
        meta = value[0] if include_image else value
        _no_token(meta, mcp.TOK)
        assert meta == {'path': 'shot&café/image.png', 'size': len(data)}
        if include_image:
            assert value[1].data == data


def test_mcp_screenshot_metadata_omits_token(tmp):
    _screenshot(tmp, False)


def test_mcp_screenshot_image_metadata_omits_token(tmp):
    _screenshot(tmp, True)


def _uploads(tmp, paged):
    mcp._need_deps()
    with _util.bridge(tmp, env=mcp.BRIDGE_ENV) as (base, _root):
        mod = mcp._load_mcp(base)
        mod._token.set(mcp.TOK)
        _upload(base, mcp.TOK)
        args = {'limit': 1, 'offset': 0} if paged else {}
        value = asyncio.run(mod.uploads(**args))
        _no_token(value, mcp.TOK)
        items = value['items'] if paged else value
        assert items[0]['path'] == 'shot&café/image.png', items
        if paged:
            paging = (value['total'], value['limit'], value['offset'])
            assert paging == (1, 1, 0), value
        status, raw = _util.request(
            base + '/upload?path=shot%26caf%C3%A9%2Fimage.png',
            headers={'Authorization': f'Bearer {mcp.TOK}'})
        assert (status, raw) == (200, b'exact-capture'), (status, raw)


def test_mcp_uploads_paths_omit_token(tmp):
    _uploads(tmp, False)


def test_mcp_paged_uploads_paths_omit_token(tmp):
    _uploads(tmp, True)


def _application_envelopes():
    for path in ('project/src/main.py', 'mcptok/assets/config.json'):
        yield {'token': 'mcptok', 'id': 'application', 'ts': 1,
               'error': None, 'world': 'page-main',
               'result': {'token': 'application-token', 'path': path}}


def _cli_preserves_application_paths(raw):
    for body in _application_envelopes():
        expected = json.dumps(body['result'], indent=2)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_result(body, raw=raw)
        if raw:
            value = json.loads(output.getvalue())
            assert 'token' not in value, value
            rendered = json.dumps(value['result'], indent=2)
        else:
            rendered = output.getvalue().split('\n', 1)[1].rstrip('\n')
        assert rendered == expected, (
            'application path rewritten', raw, expected, rendered)


def test_cli_raw_preserves_application_paths(tmp):
    del tmp
    _cli_preserves_application_paths(True)


def test_cli_formatted_preserves_application_paths(tmp):
    del tmp
    _cli_preserves_application_paths(False)


def _mcp_preserves_application_paths(name):
    mod = mcp_tools._load_composition('application-paths-' + name)
    arguments = {'cmd_id': 'application', 'code': 'value'} \
        if name in ('exec', 'put') else {}
    for body in _application_envelopes():
        expected = json.dumps(body['result'])
        mod.bridge.poll_body = body
        mod.bridge.get_bodies['/result'] = body
        value = asyncio.run(mod.mcp.registered[name](**arguments))
        assert 'token' not in value, value
        assert value['world'] == 'page-main', value
        assert json.dumps(value['value']) == expected, (
            'application path rewritten', name, body['result'], value)


def test_mcp_exec_preserves_application_paths(tmp):
    del tmp
    _mcp_preserves_application_paths('exec')


def test_mcp_put_preserves_application_paths(tmp):
    del tmp
    _mcp_preserves_application_paths('put')


def test_mcp_result_preserves_application_paths(tmp):
    del tmp
    _mcp_preserves_application_paths('result')


def test_mcp_title_preserves_application_paths(tmp):
    del tmp
    _mcp_preserves_application_paths('title')


def test_mcp_url_preserves_application_paths(tmp):
    del tmp
    _mcp_preserves_application_paths('url')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(globals())))
