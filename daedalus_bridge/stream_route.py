"""The command-delivery loop behind GET /stream.

Registration, the response headers and unregistration stay with the handler
that owns the socket; everything between them lives here. The loop takes its
namespaces, its limits and its wake event as values, so nothing in it reads
process configuration.
"""
import pathlib
import time
import typing
from functools import partial

from daedalus_bridge import command_queue
from daedalus_bridge import path_safety
from daedalus_bridge import stream_service


# Read through a module attribute so a control can step the clock and assert
# what the loop did rather than how long it took.
_now = time.time

IDLE_TICK_SECONDS = 1.0


class StreamTargets(typing.NamedTuple):
    """The command namespaces one stream reads, resolved on admission."""

    queue: pathlib.Path
    legacy: pathlib.Path
    broadcast_queue: pathlib.Path
    broadcast_legacy: pathlib.Path
    legacy_name: str


def resolve_targets(cmd_dir, token, tab):
    """Resolve one stream's namespaces, or None when a name is unsafe.

    Resolved once, here, rather than per tick: the loop runs for the life of
    the connection, and the namespace a stream reads from is decided when it
    is admitted, not re-decided every second.
    """
    try:
        queue_name, legacy_name = command_queue.command_target_names(
            token, tab)
        broadcast_name, broadcast_legacy_name = (
            command_queue.command_target_names(token))
        return StreamTargets(
            queue=path_safety.under(cmd_dir, queue_name),
            legacy=path_safety.under(cmd_dir, legacy_name),
            broadcast_queue=path_safety.under(cmd_dir, broadcast_name),
            broadcast_legacy=path_safety.under(
                cmd_dir, broadcast_legacy_name),
            legacy_name=legacy_name)
    except ValueError:
        print('[STREAM] REJECTED unsafe derived target', flush=True)
        return None


def serve_stream(wfile, *, cmd_dir, token, tab, targets, killed_event,
                 command_ttl, keepalive, max_age, client_label):
    """Run the delivery loop until killed, aged out or disconnected.

    Headers are already written by the caller, which also owns register and
    unregister. Waits on `command_queue.event(token)` — the same object
    `enqueue` sets — with `IDLE_TICK_SECONDS` as the fallback tick.
    """
    label = tab[:8] if tab else 'none'
    print(f'[STREAM] CONNECT token={token[:8]} tab={label} '
          f'from={client_label}', flush=True)
    writer = partial(stream_service.write_frame, wfile)
    last_ka = _now()
    stream_start = _now()
    try:
        while True:
            if killed_event and killed_event.is_set():
                print(f'[STREAM] KILLED-BY-RECONNECT tab={label}', flush=True)
                break
            if _now() - stream_start > max_age:
                print(f'[STREAM] MAX-AGE tab={label}', flush=True)
                break
            # Clear the wake event before scanning: event.set is sticky, so a
            # signal that lands during/after the scan is observed on the next
            # wait.
            ev = command_queue.event(token)
            ev.clear()
            delivered = 0
            if tab == 'dashboard':
                delivered += stream_service.drain_queue(
                    targets.queue, None, killed_event,
                    command_ttl=command_ttl, frame_writer=writer)
            elif tab == 'extension':
                # Typed commands addressed to the extension itself
                delivered += stream_service.drain_queue(
                    targets.queue, None, killed_event,
                    command_ttl=command_ttl, frame_writer=writer)
                delivered += stream_service.drain_legacy_file(
                    targets.legacy, None, command_ttl=command_ttl,
                    frame_writer=writer)
                # Per-tab eval queues for every other tab (tag chromeTab so
                # bg can route)
                prefix = f'{token}_'
                for entry in sorted(cmd_dir.iterdir()):
                    if (not entry.is_dir()
                            or not entry.name.startswith(prefix)):
                        continue
                    sub = entry.name[len(prefix):]
                    if sub in ('extension', 'dashboard'):
                        continue
                    delivered += stream_service.drain_queue(
                        entry, sub, killed_event, command_ttl=command_ttl,
                        frame_writer=writer)
                # Broadcast queue + legacy per-tab raw-file drops
                delivered += stream_service.drain_queue(
                    targets.broadcast_queue, None, killed_event,
                    command_ttl=command_ttl, frame_writer=writer)
                delivered += stream_service.drain_legacy_ext(
                    cmd_dir, token, killed_event, frame_writer=writer,
                    extension_legacy_name=targets.legacy_name,
                    command_ttl=command_ttl)
            else:  # specific-tab stream (rare — clients use tab=extension)
                delivered += stream_service.drain_queue(
                    targets.queue, None, killed_event,
                    command_ttl=command_ttl, frame_writer=writer)
                if tab:
                    delivered += stream_service.drain_queue(
                        targets.broadcast_queue, None, killed_event,
                        command_ttl=command_ttl, frame_writer=writer)
                    delivered += stream_service.drain_legacy_file(
                        targets.legacy, None, command_ttl=command_ttl,
                        frame_writer=writer)
            # Broadcast legacy raw-file — skip for dashboard so it doesn't
            # steal commands
            if tab != 'dashboard':
                delivered += stream_service.drain_legacy_file(
                    targets.broadcast_legacy, None, command_ttl=command_ttl,
                    frame_writer=writer)

            now = _now()
            if now - last_ka >= keepalive:
                wfile.write(b': keepalive\n\n')
                wfile.flush()
                last_ka = now
            if not delivered:
                # Wake immediately when a command is enqueued; the fallback
                # tick keeps the max-age / keepalive / kill checks live
                # during idle.
                ev.wait(timeout=IDLE_TICK_SECONDS)
    except (BrokenPipeError, ConnectionError, OSError) as e:
        print(f'[STREAM] DISCONNECT tab={label} err={type(e).__name__}',
              flush=True)
