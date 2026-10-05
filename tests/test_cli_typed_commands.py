#!/usr/bin/env python3
"""Every typed subcommand's wire payload, in one table.

One control walks every typed subcommand and checks the command the
bridge enqueues: the `type` the extension dispatches on and the fields
it reads off the command.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_helpers import (  # noqa: E402
    BRIDGE_ENV, TOK, _answer_one_ext_command, cli_env)


def test_every_typed_subcommand_sends_its_documented_command(tmp):
    """Each typed subcommand reaches the extension as the command it claims.

    These handlers were the largest unexercised region of the CLI, and what
    goes wrong in them is not a crash — it is a payload that names the wrong
    `type` or routes a browser tab id through `tab`, which the bridge would
    deliver to a queue nobody reads.
    """
    hotfix = Path(tmp) / 'fix.js'
    hotfix.write_text('console.log(1)\n', encoding='utf-8')
    cases = (
        (['cookies'], 'cookies', {}, []),
        (['set-cookie', 'https://example.com', 'sid', 'abc'], 'set-cookie',
         {'url': 'https://example.com', 'name': 'sid', 'value': 'abc'}, {}),
        (['remove-cookie', 'https://example.com', 'sid'], 'remove-cookie',
         {'url': 'https://example.com', 'name': 'sid'}, {}),
        (['clear-cookies', '-d', 'example.com'], 'clear-cookies',
         {'domain': 'example.com'}, {}),
        (['cdp', 'Page.enable'], 'cdp',
         {'method': 'Page.enable', 'params': {}}, {}),
        (['close-tab', '5'], 'close-tab', {'tabId': 5}, {}),
        (['close-tab', '5', '6'], 'close-tab', {'tabIds': [5, 6]}, {}),
        (['open-tab', 'https://example.com'], 'open-tab',
         {'url': 'https://example.com'}, {}),
        (['open-tabs', 'https://example.com/a', 'https://example.com/b'],
         'open-tabs',
         {'urls': ['https://example.com/a', 'https://example.com/b']}, {}),
        (['focus-tab', '7'], 'focus-tab', {'tabId': 7}, {}),
        (['ext-navigate', 'https://example.com'], 'navigate',
         {'url': 'https://example.com'}, {}),
        (['ext-reload'], 'reload', {}, {}),
        (['ext-self-reload'], 'ext-reload', {}, {}),
        (['inject-css', '--css', 'a{color:red}'], 'inject-css',
         {'css': 'a{color:red}'}, {}),
        (['remove-css', '--css', 'a{color:red}'], 'remove-css',
         {'css': 'a{color:red}'}, {}),
        (['block-requests', '*.example/*'], 'block-requests',
         {'pattern': '*.example/*'}, {}),
        (['unblock-requests'], 'unblock-requests', {}, {}),
        (['list-block-rules'], 'list-block-rules', {}, {}),
        (['net-capture'], 'net-capture', {}, {}),
        (['net-capture-stop'], 'net-capture-stop', {}, {}),
        (['net-capture-get'], 'net-capture-get', {}, {}),
        (['store-hotfix', 'fix1', '--file', str(hotfix)], 'store-hotfix',
         {'fixId': 'fix1', 'code': 'console.log(1)\n'}, {}),
        (['store-hotfix', 'fix2', '--code', 'console.log(2)'], 'store-hotfix',
         {'fixId': 'fix2', 'code': 'console.log(2)'}, {}),
        (['clear-hotfix', 'fix1'], 'clear-hotfix', {'fixId': 'fix1'}, {}),
        (['clear-hotfixes'], 'clear-all-hotfixes', {}, {}),
        (['list-hotfixes'], 'list-hotfixes', {}, {}),
        # `found` is what this one branches on: without it the CLI reports
        # that no such hotfix exists and exits nonzero.
        (['set-permanent', 'fix1', 'true'], 'set-permanent',
         {'fixId': 'fix1', 'permanent': True}, {'found': True}),
        (['fetch-timings'], 'fetch-timings', {}, {}),
    )
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        for argv, cmd_type, fields, result in cases:
            code, out, err, queued = _answer_one_ext_command(
                base, docroot, argv, result, env)
            assert code == 0, (argv, code, out, err)
            assert queued.get('type') == cmd_type, (argv, queued)
            # Routing is not in the payload: the bridge consumes `tab` and
            # `token` when it enqueues, so what proves a typed command reached
            # the extension worker is the queue it landed in, which is the
            # directory _answer_one_ext_command read it from.
            assert 'tab' not in queued, (argv, queued)
            for key, value in fields.items():
                assert queued.get(key) == value, (argv, key, queued)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
