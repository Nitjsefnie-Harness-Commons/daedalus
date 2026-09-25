"""The extension worker's command route table, in one copy.

``(worker module, published handler, command type)`` per row. The module
boundary suite reads the module and symbol columns; the command-type
enumeration guard reads the third and checks it against the switch in
``dispatchCommand`` and against what every shipped client transmits. The
inventory lives here so neither suite keeps a private copy that can drift
from the other.
"""

ROUTES = [
    ('worker/capture.js', 'handleScreenshot', 'screenshot'),
    ('worker/cookies.js', 'handleCookies', 'cookies'),
    ('worker/cookies.js', 'handleSetCookie', 'set-cookie'),
    ('worker/cookies.js', 'handleRemoveCookie', 'remove-cookie'),
    ('worker/cookies.js', 'handleClearCookies', 'clear-cookies'),
    ('worker/blocking.js', 'handleBlockRequests', 'block-requests'),
    ('worker/blocking.js', 'handleUnblockRequests', 'unblock-requests'),
    ('worker/blocking.js', 'handleListBlockRules', 'list-block-rules'),
    ('worker/tabs.js', 'handleCloseTab', 'close-tab'),
    ('worker/tabs.js', 'handleOpenTab', 'open-tab'),
    ('worker/tabs.js', 'handleOpenTabs', 'open-tabs'),
    ('worker/tabs.js', 'handleFocusTab', 'focus-tab'),
    ('worker/tabs.js', 'handleNavigate', 'navigate'),
    ('worker/tabs.js', 'handleReload', 'reload'),
    ('worker/tabs.js', 'handleInjectCss', 'inject-css'),
    ('worker/tabs.js', 'handleRemoveCss', 'remove-css'),
    ('worker/tabs.js', 'handleExtReload', 'ext-reload'),
    ('worker/tabs.js', 'handleFetchTimings', 'fetch-timings'),
    ('worker/cdp.js', 'handleCdp', 'cdp'),
    ('worker/netcapture.js', 'handleNetCapture', 'net-capture'),
    ('worker/netcapture.js', 'handleNetCaptureStop', 'net-capture-stop'),
    ('worker/netcapture.js', 'handleNetCaptureGet', 'net-capture-get'),
    ('worker/hotfixes.js', 'handleStoreHotfix', 'store-hotfix'),
    ('worker/hotfixes.js', 'handleClearHotfix', 'clear-hotfix'),
    ('worker/hotfixes.js', 'handleClearAllHotfixes',
     'clear-all-hotfixes'),
    ('worker/hotfixes.js', 'handleListHotfixes', 'list-hotfixes'),
    ('worker/hotfixes.js', 'handleSetPermanent', 'set-permanent'),
    ('worker/segment_mint.js', 'handleAllowSegmentOrigin',
     'allow-segment-origin'),
    ('worker/segment_mint.js', 'handleRevokeSegmentOrigin',
     'revoke-segment-origin'),
    ('worker/segment_mint.js', 'handleListSegmentOrigins',
     'list-segment-origins'),
    ('worker/evaluate.js', 'handleEval', 'eval'),
]
