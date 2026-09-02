"""The bridge command each registered MCP tool is pinned to put on the bridge.

One entry per registered tool, keyed by the name it registers under. A case is
(argument overrides, expected bridge calls): the tool's required parameters are
filled from the suite's REQUIRED_ARGUMENTS, the overrides are applied, and the
expected calls are the exact (surface, details) pairs the call must record on
the probe bridge, in order.

A parameter no case exercises is named in UNPINNED_PARAMETERS, with the reason
it is still witnessed.
"""
MARKER = 'pinned'


def _ext(cmd_id, cmd_type, **fields):
    return ('ext_cmd', {'args': (cmd_id, cmd_type), 'kwargs': fields})


def _get(path, **params):
    return ('get', {'path': path, 'params': params})


def _put(path, payload):
    return ('put', {'path': path, 'payload': payload})


def _post(path, body):
    return ('post', {'path': path, 'body': body})


def _delete(path, body):
    return ('delete', {'path': path, 'body': body})


def _get_raw(endpoint, **params):
    return ('get_raw', {'endpoint': endpoint, 'params': params})


def _client_get(path, headers, **params):
    return ('http_client.get',
            {'path': path, 'params': params, 'headers': headers})


def _poll(tab, timeout, cmd_id):
    return ('poll_result', {'args': (tab, timeout),
                            'kwargs': {'expect_id': cmd_id,
                                       'expect_delivery': MARKER}})


def _waited(cmd_id, code, tab, timeout):
    """What a waited eval sends: the deadline check, the command, the poll."""
    payload = {'id': cmd_id, 'code': code}
    if tab:
        payload['tab'] = tab
    return [('checked_timeout', {'timeout': timeout}),
            _put('/command', payload),
            _poll(tab, timeout, cmd_id)]


TOOL_COMMANDS = {
    # tools_tabs
    'list_tabs': [({}, [_get('/tabs')])],
    'open_tab': [
        ({}, [_ext('_open_tab', 'open-tab', include_roundtrip=True,
                   url='https://example.com')]),
        ({'background': True, 'pinned': True},
         [_ext('_open_tab', 'open-tab', include_roundtrip=True,
               url='https://example.com', active=False, pinned=True)]),
    ],
    'open_tabs': [
        ({}, [_ext('_open_tabs', 'open-tabs', timeout=30,
                   include_roundtrip=True, urls=['https://example.com'])]),
        ({'background': True, 'pinned': True},
         [_ext('_open_tabs', 'open-tabs', timeout=30,
               include_roundtrip=True, urls=['https://example.com'],
               active=False, pinned=True)]),
    ],
    'focus_tab': [({}, [_ext('_focus', 'focus-tab', tabId=7)])],
    'close_tab': [
        ({}, [_ext('_close_tab', 'close-tab', tabId=7)]),
        ({'chrome_tabs': [7, 8]},
         [_ext('_close_tab', 'close-tab', tabIds=[7, 8])]),
    ],
    'ext_navigate': [
        ({}, [_ext('_nav', 'navigate', url='https://example.com')]),
        ({'chrome_tab': 7},
         [_ext('_nav', 'navigate', url='https://example.com', tabId=7)]),
    ],
    'ext_reload': [
        ({}, [_ext('_reload', 'reload')]),
        ({'chrome_tab': 7, 'bypass_cache': True},
         [_ext('_reload', 'reload', tabId=7, bypassCache=True)]),
    ],

    # tools_eval
    'exec': [
        ({}, _waited('cmd', '1 + 1', '', 15.0)),
        ({'tab_id': 'tab'}, _waited('cmd', '1 + 1', 'tab', 15.0)),
        ({'broadcast': True, 'tab_id': 'tab'},
         _waited('cmd', '1 + 1', '', 15.0)),
        ({'wait': False}, [_put('/command', {'id': 'cmd', 'code': '1 + 1'})]),
    ],
    'put': [
        ({}, _waited('cmd', '1 + 1', '', 15.0)),
        ({'tab_id': 'tab'}, _waited('cmd', '1 + 1', 'tab', 15.0)),
    ],
    'result': [
        ({}, [_get('/result')]),
        ({'tab_id': 'tab', 'consume': True},
         [_get('/result', tab='tab', consume='1')]),
    ],
    'ping': [
        ({}, [_put('/command', {'id': '_ping', 'code': 'document.title'}),
              _poll('', 10.0, '_ping')]),
        ({'tab_id': 'tab'},
         [_put('/command', {'id': '_ping', 'code': 'document.title',
                            'tab': 'tab'}),
          _poll('tab', 10.0, '_ping')]),
    ],
    'navigate': [
        ({}, [_put('/command',
                   {'id': '_nav',
                    'code': 'location.href = "https://example.com"'})]),
        ({'tab_id': 'tab'},
         [_put('/command',
               {'id': '_nav', 'tab': 'tab',
                'code': 'location.href = "https://example.com"'})]),
    ],
    'reload': [
        ({}, [_put('/command',
                   {'id': '_reload', 'code': 'location.reload()'})]),
        ({'tab_id': 'tab'},
         [_put('/command', {'id': '_reload', 'code': 'location.reload()',
                            'tab': 'tab'})]),
    ],
    'title': [
        ({}, _waited('_title', 'document.title', '', 10)),
        ({'tab_id': 'tab'},
         _waited('_title', 'document.title', 'tab', 10)),
    ],
    'url': [
        ({}, _waited('_url', 'location.href', '', 10)),
        ({'tab_id': 'tab'}, _waited('_url', 'location.href', 'tab', 10)),
    ],
    'ext_self_reload': [({}, [_ext('_ext_reload', 'ext-reload')])],

    # tools_media
    'screenshot': [
        ({}, [_ext('_ss', 'screenshot', timeout=15.0, format='png')]),
        ({'chrome_tab': 7, 'quality': 60},
         [_ext('_ss', 'screenshot', timeout=15.0, format='png', quality=60,
               tabId=7)]),
        ({'format': ''}, [_ext('_ss', 'screenshot', timeout=15.0)]),
        ({'include_image': True},
         [_ext('_ss', 'screenshot', timeout=15.0, format='png'),
          _get_raw('/screenshot', path=MARKER + '/shot.png')]),
    ],
    'segment_job': [({}, [_post('/segment-job', {'job': 'job'})])],
    'segment_status': [
        ({}, [('http_client', {}), ('auth', {}),
              _client_get('/segment-job', {'Authorization': MARKER},
                          job='job'),
              _client_get('/segment-status',
                          {'X-Daedalus-Segment-Sig': MARKER}, job='job')]),
    ],
    'uploads': [
        ({}, [_get('/upload')]),
        ({'upload_id': 'up', 'limit': 5, 'offset': 10},
         [_get('/upload', id='up', limit=5, offset=10)]),
    ],
    'delete_upload': [
        ({}, [_delete('/upload', {})]),
        ({'upload_id': 'up'}, [_delete('/upload', {'id': 'up'})]),
        ({'upload_id': 'up', 'filename': 'shot.png'},
         [_delete('/upload', {'id': 'up', 'filename': 'shot.png'})]),
    ],

    # tools_cookies
    'get_cookies': [
        ({}, [_ext('_cookies', 'cookies')]),
        ({'domain': 'example.com', 'target_url': 'https://example.com'},
         [_ext('_cookies', 'cookies', domain='example.com',
               url='https://example.com')]),
    ],
    'set_cookie': [
        ({}, [_ext('_set_cookie', 'set-cookie', url='https://example.com',
                   name='name', value='value')]),
        ({'domain': 'example.com', 'path': '/', 'http_only': True,
          'secure': True, 'same_site': 'Lax', 'expires': 1.5},
         [_ext('_set_cookie', 'set-cookie', url='https://example.com',
               name='name', value='value', domain='example.com', path='/',
               httpOnly=True, secure=True, sameSite='Lax',
               expirationDate=1.5)]),
    ],
    'remove_cookie': [
        ({}, [_ext('_rm_cookie', 'remove-cookie', url='https://example.com',
                   name='name')]),
    ],
    'clear_cookies': [
        ({}, [_ext('_clear_cookies', 'clear-cookies')]),
        ({'domain': 'example.com', 'target_url': 'https://example.com'},
         [_ext('_clear_cookies', 'clear-cookies', domain='example.com',
               url='https://example.com')]),
    ],

    # tools_css
    'inject_css': [
        ({}, [_ext('_inject_css', 'inject-css', css='body {}')]),
        ({'chrome_tab': 7, 'all_frames': True},
         [_ext('_inject_css', 'inject-css', css='body {}', tabId=7,
               allFrames=True)]),
    ],
    'remove_css': [
        ({}, [_ext('_remove_css', 'remove-css', css='body {}')]),
        ({'chrome_tab': 7, 'all_frames': True},
         [_ext('_remove_css', 'remove-css', css='body {}', tabId=7,
               allFrames=True)]),
    ],
    'block_requests': [
        ({}, [_ext('_block', 'block-requests', pattern='*://example.com/*')]),
        ({'chrome_tab': 7},
         [_ext('_block', 'block-requests', pattern='*://example.com/*',
               tabId=7)]),
    ],
    'unblock_requests': [
        ({}, [_ext('_unblock', 'unblock-requests')]),
        ({'rule_id': 3}, [_ext('_unblock', 'unblock-requests', ruleId=3)]),
    ],
    'list_block_rules': [({}, [_ext('_list_rules', 'list-block-rules')])],

    # tools_hotfixes
    'store_hotfix': [
        ({}, [_ext('_store_hf', 'store-hotfix', fixId='fix', code='1 + 1',
                   permanent=False)]),
        ({'permanent': True},
         [_ext('_store_hf', 'store-hotfix', fixId='fix', code='1 + 1',
               permanent=True)]),
    ],
    'clear_hotfix': [({}, [_ext('_clear_hf', 'clear-hotfix', fixId='fix')])],
    'clear_hotfixes': [
        ({}, [_ext('_clear_all_hf', 'clear-all-hotfixes',
                   includePermanent=False)]),
        ({'include_permanent': True},
         [_ext('_clear_all_hf', 'clear-all-hotfixes', includePermanent=True)]),
    ],
    'list_hotfixes': [({}, [_ext('_list_hf', 'list-hotfixes')])],
    'set_permanent': [
        ({}, [_ext('_set_perm', 'set-permanent', fixId='fix',
                   permanent=True)]),
    ],

    # tools_network
    'net_capture': [
        ({}, [_ext('_net_cap', 'net-capture', timeout=15, maxRequests=1000)]),
        ({'chrome_tab': 7, 'max_requests': 1},
         [_ext('_net_cap', 'net-capture', timeout=15, tabId=7,
               maxRequests=1)]),
        ({'max_requests': 20000},
         [_ext('_net_cap', 'net-capture', timeout=15, maxRequests=20000)]),
    ],
    'net_capture_stop': [
        ({}, [_ext('_net_stop', 'net-capture-stop', timeout=30)]),
        ({'chrome_tab': 7, 'bodies': True},
         [_ext('_net_stop', 'net-capture-stop', timeout=30, tabId=7,
               bodies=True)]),
    ],
    'net_capture_get': [
        ({}, [_ext('_net_get', 'net-capture-get', timeout=30)]),
        ({'chrome_tab': 7, 'url_filter': 'png', 'bodies': True},
         [_ext('_net_get', 'net-capture-get', timeout=30, tabId=7,
               filter='png', bodies=True)]),
    ],
    'cdp': [
        ({}, [_ext('_cdp', 'cdp', timeout=30, method='Page.getFrameTree',
                   params={})]),
        ({'chrome_tab': 7, 'params': {'depth': 1}, 'keep_session': True},
         [_ext('_cdp', 'cdp', timeout=30, method='Page.getFrameTree',
               params={'depth': 1}, tabId=7, keep_session=True)]),
    ],
    'fetch_timings': [
        ({}, [_ext('_fetch_timings', 'fetch-timings')]),
        ({'reset': True}, [_ext('_fetch_timings', 'fetch-timings',
                                reset=True)]),
    ],
}

UNPINNED_PARAMETERS = {
    # Per tool: parameters deliberately exercised by no TOOL_COMMANDS override,
    # each carrying the reason it is still witnessed. A stale entry - one the
    # signature does not have, or one a case already witnesses - is refused.
    'exec': {
        # every waited case pins checked_timeout at the default
        'timeout',
    },
    'put': {
        # fan-out to every tab is exercised by no case
        'broadcast',
        # only the waited path is pinned; put has no wait=False case
        'wait',
        # the waited cases pin checked_timeout at the default
        'timeout',
    },
    'reload': {
        # fan-out to every tab is exercised by no case
        'broadcast',
    },
    'screenshot': {
        # the default id is the ext_cmd args[0] in every case's expectation
        'cmd_id',
        # every case pins ext_cmd timeout at the default
        'timeout',
    },
}


def unpinned_parameter_gaps(tool_name, optional, covered):
    """Optional parameters neither the cases nor the allowlist account for.

    `optional` is a registered tool's defaulted parameter names, `covered` the
    override keys its TOOL_COMMANDS cases apply, and the tool's
    UNPINNED_PARAMETERS entry the parameters exercised by no case. A parameter
    outside both is one whose forwarding no case witnesses: deleting the single
    case naming it ships a renamed-field regression green. A stale allowlist
    entry - one the signature does not have, or one a case already witnesses -
    is refused too, so a signature or forwarding change surfaces instead of the
    entry rotting.
    """
    allowed = set(UNPINNED_PARAMETERS.get(tool_name, ()))
    optional = set(optional)
    covered = set(covered)
    return (
        [f'UNPINNED_PARAMETERS names {name!r}, which the signature does not '
         'have' for name in sorted(allowed - optional)]
        + [f'UNPINNED_PARAMETERS names {name!r}, which a case already '
           'witnesses' for name in sorted(allowed & covered)]
        + [f'no case witnesses {name!r}'
           for name in sorted(optional - covered - allowed)])


TOOL_REFUSALS = {
    'exec': [
        ({'cmd_id': ''}, ValueError, 'cmd_id is required'),
        ({'code': ''}, ValueError, 'code is empty'),
    ],
    'put': [({'code': ''}, ValueError, 'code is empty')],
    'inject_css': [({'css': ''}, ValueError, 'css required')],
    'remove_css': [({'css': ''}, ValueError, 'css required')],
    'store_hotfix': [({'code': ''}, ValueError, 'code required')],
    'net_capture': [
        ({'max_requests': True}, ValueError,
         'max_requests must be an integer'),
        ({'max_requests': 0}, ValueError,
         'max_requests must be an integer from 1 to 20000; got 0'),
        ({'max_requests': 20001}, ValueError,
         'max_requests must be an integer from 1 to 20000'),
    ],
}
