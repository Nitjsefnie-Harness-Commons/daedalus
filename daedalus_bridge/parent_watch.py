"""Exit an opted-in bridge when its launching process disappears."""
import importlib
import os
import stat
import subprocess
import threading


ENV_NAME = 'DAEDALUS_PARENT_WATCH_FD'


def spawn(args, *, cwd, env, stdout, stderr, text):
    read_fd, write_fd = os.pipe()
    child_env = dict(env)
    if os.name == 'nt':
        msvcrt = importlib.import_module('msvcrt')
        read_handle = msvcrt.get_osfhandle(read_fd)
        set_handle_inheritable = getattr(os, 'set_handle_inheritable')
        set_handle_inheritable(read_handle, True)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.lpAttributeList = {'handle_list': [read_handle]}
        child_env[ENV_NAME] = str(read_handle)
    else:
        child_env[ENV_NAME] = str(read_fd)
    try:
        try:
            if os.name == 'nt':
                proc = subprocess.Popen(
                    args, cwd=cwd, env=child_env, stdout=stdout,
                    stderr=stderr, text=text, startupinfo=startupinfo)
            else:
                proc = subprocess.Popen(
                    args, cwd=cwd, env=child_env, stdout=stdout,
                    stderr=stderr, text=text, pass_fds=(read_fd,))
        finally:
            try:
                if os.name == 'nt':
                    set_handle_inheritable(read_handle, False)
            finally:
                os.close(read_fd)
    except BaseException:
        os.close(write_fd)
        raise
    return proc, write_fd


def _exit_at_eof(fd):
    try:
        while os.read(fd, 1):
            pass
    finally:
        os._exit(0)


def start():
    raw = os.environ.get(ENV_NAME)
    if raw is None:
        return
    try:
        descriptor = int(raw)
        if os.name == 'nt':
            msvcrt = importlib.import_module('msvcrt')
            descriptor = msvcrt.open_osfhandle(descriptor, os.O_RDONLY)
        if not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            raise ValueError('descriptor is not a pipe')
    except (OSError, ValueError) as exc:
        raise SystemExit(f'{ENV_NAME} must name an inherited pipe') from exc
    threading.Thread(
        target=_exit_at_eof, args=(descriptor,),
        name='parent-watch', daemon=True).start()
