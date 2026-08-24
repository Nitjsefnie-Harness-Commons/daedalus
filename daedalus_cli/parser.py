"""The command-line surface: every subcommand and its arguments.

Separate from the handlers because it is a description rather than a
behaviour — one long, flat declaration that says what `daedalus --help`
prints, with nothing to reason about beyond whether each option is spelled
the way its handler reads it.
"""
import argparse

from . import __version__
from .commands_content import (_boolean_argument,  # noqa: F401
                               _positive_rule_id)
from .transport import NET_CAPTURE_MAX, capture_limit, positive_timeout


def build_parser():
    """The full argument parser, ready to parse."""
    p = argparse.ArgumentParser(
        prog='daedalus',
        description='Daedalus bridge CLI',
        epilog='Env: DAEDALUS_TOKEN (bridge token), ID (tab id, optional), '
               'DAEDALUS_URL (default http://127.0.0.1:8081)',
    )
    p.add_argument('--version', action='version',
                   version=f'daedalus {__version__}')
    sub = p.add_subparsers(dest='cmd', required=True)

    # tabs
    p_tabs = sub.add_parser('tabs', help='List active tabs')
    p_tabs.add_argument('--json', action='store_true',
                        help='Emit JSON array of tab objects')

    # put
    s = sub.add_parser('put', help='Send JS file as command')
    s.add_argument('id', help='Command ID')
    s.add_argument('file', help='JS file path (or - for stdin)')
    s.add_argument('--no-result', action='store_true', help="Don't wait for result")
    s.add_argument('-b', '--broadcast', action='store_true', help='Send to all tabs (ignore ID)')
    s.add_argument('-t', '--timeout', type=positive_timeout, default=15, help='Result timeout seconds (default 15)')

    # exec
    s = sub.add_parser('exec', help='Send inline JS code')
    s.add_argument('id', help='Command ID')
    s.add_argument('code', help='JS code string')
    s.add_argument('--no-result', action='store_true', help="Don't wait for result")
    s.add_argument('-b', '--broadcast', action='store_true', help='Send to all tabs (ignore ID)')
    s.add_argument('-t', '--timeout', type=positive_timeout, default=15, help='Result timeout seconds (default 15)')

    # result
    s = sub.add_parser('result', help='Fetch latest result')
    s.add_argument('-c', '--consume', action='store_true', help='Delete result after reading')
    s.add_argument('--raw', action='store_true', help='Print raw JSON')

    # ping
    sub.add_parser('ping', help='Test round-trip to tab')

    # navigate
    s = sub.add_parser('navigate', help='Navigate tab to URL')
    s.add_argument('url', help='Target URL')

    # reload
    sub.add_parser('reload', help='Reload tab page')

    # title
    sub.add_parser('title', help='Get tab document.title')

    # url
    sub.add_parser('url', help='Get tab location.href')

    # segment-job
    s = sub.add_parser('segment-job', help='Mint (or re-fetch) the HLS segment capability for a job and print it')
    s.add_argument('job', help='Job name')

    # segment-status
    s = sub.add_parser('segment-status', help='HLS segment job status')
    s.add_argument('job', help='Job name')

    # screenshot (extension)
    s = sub.add_parser('screenshot', help='Capture screenshot via extension')
    s.add_argument('--id', default='_ss', help='Command ID (default _ss)')
    s.add_argument('-o', '--output', help='Save screenshot to local file')
    s.add_argument('-f', '--format', choices=['png', 'jpeg'], help='Image format (default png)')
    s.add_argument('-q', '--quality', type=int, help='JPEG quality (1-100)')
    s.add_argument('--chrome-tab', help='Chrome tab ID (default: active tab)')
    s.add_argument('-t', '--timeout', type=positive_timeout, default=15, help='Timeout seconds')

    # cookies (extension)
    s = sub.add_parser('cookies', help='Get cookies via extension')
    s.add_argument('-d', '--domain', help='Filter by domain')
    s.add_argument('-u', '--url', help='Filter by URL')
    s.add_argument('--raw', action='store_true', help='Print full JSON (no truncation)')
    s.add_argument('-t', '--timeout', type=positive_timeout, default=0,
                   help='Result timeout seconds (default 10)')

    # set-cookie (extension)
    s = sub.add_parser('set-cookie', help='Set a cookie via extension')
    s.add_argument('url', help='URL for the cookie (e.g. https://example.com)')
    s.add_argument('name', help='Cookie name')
    s.add_argument('value', help='Cookie value')
    s.add_argument('-d', '--domain', help='Cookie domain')
    s.add_argument('--path', help='Cookie path')
    s.add_argument('--http-only', action='store_true', help='HttpOnly flag')
    s.add_argument('--secure', action='store_true', help='Secure flag')
    s.add_argument('--same-site', choices=['no_restriction', 'lax', 'strict'], help='SameSite policy')
    s.add_argument('--expires', help='Expiration as Unix timestamp')

    # remove-cookie (extension)
    s = sub.add_parser('remove-cookie', help='Remove a specific cookie')
    s.add_argument('url', help='URL for the cookie')
    s.add_argument('name', help='Cookie name')

    # clear-cookies (extension)
    s = sub.add_parser('clear-cookies', help='Clear all cookies for a domain')
    s.add_argument('-d', '--domain', help='Domain to clear')
    s.add_argument('-u', '--url', help='URL to clear')
    s.add_argument('-t', '--timeout', type=positive_timeout, default=0,
                   help='Result timeout seconds (default 10)')

    # cdp (extension)
    s = sub.add_parser('cdp', help='Send raw CDP command via extension')
    s.add_argument('method', help='CDP method (e.g. Page.captureScreenshot)')
    s.add_argument('-p', '--params', help='JSON params string')
    s.add_argument('--chrome-tab', help='Chrome tab ID')
    s.add_argument('--keep-session', action='store_true',
                   help='Hold the chrome.debugger session after this call (for Profiler.enable/start/stop, HeapProfiler, Tracing)')
    s.add_argument('--raw', action='store_true', help='Print raw JSON')

    # fetch-timings (extension diagnostics)
    s = sub.add_parser('fetch-timings', help='Fetch background fetch-relay timing ring buffer')
    s.add_argument('-n', type=int, default=30, help='Show last N entries (default 30)')
    s.add_argument('--reset', action='store_true', help='Clear ring buffer after reading')
    s.add_argument('--raw', action='store_true', help='Print raw JSON')

    # ext-self-reload (extension)
    sub.add_parser('ext-self-reload', help='Reload extension from disk (chrome.runtime.reload)')

    # open-tab (extension)
    s = sub.add_parser('open-tab', help='Open a new tab via extension')
    s.add_argument('url', help='URL to open')
    s.add_argument('--background', action='store_true', help='Open in background (inactive)')
    s.add_argument('--pinned', action='store_true', help='Pin the tab')

    # open-tabs (extension)
    s = sub.add_parser('open-tabs', help='Open many tabs in one round-trip')
    s.add_argument('urls', nargs='+', help='URLs to open')
    s.add_argument('--background', action='store_true', help='Open in background (inactive)')
    s.add_argument('--pinned', action='store_true', help='Pin the tabs')

    # focus-tab (extension)
    s = sub.add_parser('focus-tab', help='Focus a Chrome tab via extension')
    s.add_argument('chrome_tab', help='Chrome tab ID to focus')

    # ext-navigate (extension)
    s = sub.add_parser('ext-navigate', help='Navigate tab to URL via extension (works on chrome:// pages)')
    s.add_argument('url', help='Target URL')
    s.add_argument('--chrome-tab', help='Chrome tab ID (default: active tab)')

    # ext-reload (extension)
    s = sub.add_parser('ext-reload', help='Reload tab via extension (supports cache bypass)')
    s.add_argument('--chrome-tab', help='Chrome tab ID (default: active tab)')
    s.add_argument('--bypass-cache', action='store_true', help='Bypass cache on reload')

    # close-tab (extension)
    s = sub.add_parser('close-tab', help='Close Chrome tab(s) via extension')
    s.add_argument('chrome_tabs', nargs='+', help='Chrome tab ID(s) to close')

    # inject-css (extension)
    s = sub.add_parser('inject-css', help='Inject CSS into a tab')
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument('--css', help='CSS string to inject')
    g.add_argument('--file', help='CSS file to inject')
    s.add_argument('--chrome-tab', help='Chrome tab ID (default: active tab)')
    s.add_argument('--all-frames', action='store_true', help='Inject into all frames')

    # remove-css (extension)
    s = sub.add_parser('remove-css', help='Remove injected CSS from a tab')
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument('--css', help='CSS string to remove (must match injected)')
    g.add_argument('--file', help='CSS file to remove')
    s.add_argument('--chrome-tab', help='Chrome tab ID (default: active tab)')
    s.add_argument('--all-frames', action='store_true', help='Remove from all frames')

    # block-requests (extension)
    s = sub.add_parser('block-requests', help='Block page requests matching a URL pattern')
    s.add_argument('pattern', help='URL filter pattern (e.g. "*.cdn.example.com/vod/*/seg-*")')
    s.add_argument('--chrome-tab', help='Restrict to specific Chrome tab ID')

    # unblock-requests (extension)
    s = sub.add_parser('unblock-requests', help='Remove block rules')
    s.add_argument('--rule-id', type=_positive_rule_id,
                   help='Specific rule ID to remove (default: all)')

    # list-block-rules (extension)
    sub.add_parser('list-block-rules', help='List active block rules')

    # net-capture (extension)
    s = sub.add_parser('net-capture', help='Start CDP network capture on a tab')
    s.add_argument('--chrome-tab', help='Chrome tab ID (default: active)')
    s.add_argument('--max', type=capture_limit, default=1000,
                   help=f'Max requests to buffer, 1-{NET_CAPTURE_MAX} (default: 1000)')

    # net-capture-stop (extension)
    s = sub.add_parser('net-capture-stop', help='Stop network capture and dump results')
    s.add_argument('--chrome-tab', help='Chrome tab ID (default: active)')
    s.add_argument('--bodies', action='store_true', help='Fetch response bodies before stopping')
    s.add_argument('--raw', action='store_true', help='Print raw JSON')

    # net-capture-get (extension)
    s = sub.add_parser('net-capture-get', help='Get current network capture buffer')
    s.add_argument('--chrome-tab', help='Chrome tab ID (default: active)')
    s.add_argument('--filter', help='Regex filter on URL or type')
    s.add_argument('--bodies', action='store_true', help='Fetch response bodies')
    s.add_argument('--raw', action='store_true', help='Print raw JSON')

    # store-hotfix (extension)
    s = sub.add_parser('store-hotfix', help='Store a persistent hotfix in the extension')
    s.add_argument('fix_id', help='Hotfix identifier')
    # Two named modes rather than one positional the filesystem disambiguates.
    # The old form asked whether the string was a path that existed, so a
    # mistyped path became persistent page code instead of an error, and
    # inline source that happened to name a file was replaced by that file's
    # contents. Neither miss was visible in the output.
    source = s.add_mutually_exclusive_group(required=True)
    source.add_argument('--file', help='Read the hotfix source from this path')
    source.add_argument('--code', help='Use this string as the hotfix source')
    s.add_argument('--permanent', action='store_true', help='Mark fix as permanent (survives extension version bumps)')

    # clear-hotfix (extension)
    s = sub.add_parser('clear-hotfix', help='Remove a specific hotfix')
    s.add_argument('fix_id', help='Hotfix identifier to remove')

    # clear-hotfixes (extension)
    s = sub.add_parser('clear-hotfixes', help='Remove all hotfixes (permanents skipped by default)')
    s.add_argument('--include-permanent', action='store_true', help='Also remove permanent hotfixes')

    # list-hotfixes (extension)
    sub.add_parser('list-hotfixes', help='List stored hotfixes')

    # set-permanent (extension)
    s = sub.add_parser('set-permanent', help='Toggle the permanent flag on an existing hotfix')
    s.add_argument('fix_id', help='Hotfix identifier')
    s.add_argument('permanent', type=_boolean_argument,
                   help='true|1|yes|y|on or false|0|no|n|off')

    # uploads
    s = sub.add_parser('uploads', help='List or delete uploads')
    s.add_argument('--id', help='Filter by upload ID')
    s.add_argument('--filename', help='Specific filename (for delete)')
    s.add_argument('--delete', action='store_true', help='Delete instead of list')
    return p
