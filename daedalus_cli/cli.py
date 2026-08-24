#!/usr/bin/env python3
"""Daedalus bridge CLI — drive the browser extension from a shell.

The entry point and nothing else: the parser is built in `parser`, the
handlers live in the `commands_*` modules beside it, and configuration and
transport are in `transport`. This module's only job is to map a subcommand
name onto the function that runs it.

`daedalus --help` is the surface; `daedalus_cli.transport.token` is what an
embedding application reaches for.
"""
from .commands_browser import (do_cdp, do_clear_cookies, do_close_tab,
                               do_cookies, do_ext_navigate, do_ext_reload,
                               do_ext_self_reload, do_fetch_timings,
                               do_focus_tab, do_open_tab, do_open_tabs,
                               do_remove_cookie, do_set_cookie)
from .commands_content import (do_block_requests, do_clear_hotfix,
                               do_clear_hotfixes, do_inject_css,
                               do_list_block_rules, do_list_hotfixes,
                               do_net_capture, do_net_capture_get,
                               do_net_capture_stop, do_remove_css,
                               do_set_permanent, do_store_hotfix,
                               do_unblock_requests)
from .commands_eval import (do_exec, do_navigate, do_ping, do_put, do_reload,
                            do_result, do_tabs, do_title, do_url)
from .commands_media import (do_screenshot, do_segment_job, do_segment_status,
                             do_uploads)
from .parser import build_parser

DISPATCH = {
    'tabs': do_tabs,
    'put': do_put,
    'exec': do_exec,
    'result': do_result,
    'ping': do_ping,
    'navigate': do_navigate,
    'reload': do_reload,
    'title': do_title,
    'url': do_url,
    'segment-job': do_segment_job,
    'segment-status': do_segment_status,
    'screenshot': do_screenshot,
    'cookies': do_cookies,
    'set-cookie': do_set_cookie,
    'remove-cookie': do_remove_cookie,
    'clear-cookies': do_clear_cookies,
    'cdp': do_cdp,
    'fetch-timings': do_fetch_timings,
    'ext-self-reload': do_ext_self_reload,
    'open-tab': do_open_tab,
    'open-tabs': do_open_tabs,
    'focus-tab': do_focus_tab,
    'ext-navigate': do_ext_navigate,
    'ext-reload': do_ext_reload,
    'close-tab': do_close_tab,
    'inject-css': do_inject_css,
    'remove-css': do_remove_css,
    'block-requests': do_block_requests,
    'unblock-requests': do_unblock_requests,
    'list-block-rules': do_list_block_rules,
    'net-capture': do_net_capture,
    'net-capture-stop': do_net_capture_stop,
    'net-capture-get': do_net_capture_get,
    'store-hotfix': do_store_hotfix,
    'clear-hotfix': do_clear_hotfix,
    'clear-hotfixes': do_clear_hotfixes,
    'list-hotfixes': do_list_hotfixes,
    'set-permanent': do_set_permanent,
    'uploads': do_uploads,
}


def main():
    # Stdio is configured by output.configure_stdio(), which runs when that
    # module is imported and which server.py and mcp_server.py also call
    # explicitly. Repeating it HERE pinned UTF-8 unconditionally and so
    # overrode the one case that function exists to respect: an explicit
    # PYTHONIOENCODING. The call is idempotent; this omission is deliberate.
    args = build_parser().parse_args()
    DISPATCH[args.cmd](args)


if __name__ == '__main__':
    main()
