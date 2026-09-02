"""The two `/result` routes: storing a posted result and reading one back.

`accept_result` and `fetch_result` each return `(status, payload)` and take
the result directory as a parameter. `result_store` owns the locks, the slot
primitives and the accepted-delivery record; both routes call it exactly as
the request handler used to.

`res_dir` is the results root, and every file either route writes is
resolved under it: the slot files directly, and the delivery files through
`result_store.delivery_root`. One root therefore names one tree.
"""
import json
import time
import uuid

from daedalus_bridge import command_queue
from daedalus_bridge.log_safe import log_safe
from daedalus_bridge import path_safety
from daedalus_bridge import result_store


# How many times a compatibility consume re-reads a slot whose delivery id
# changed under it before giving up on the mirrored cleanup.
COMPAT_CONSUME_RETRY_ATTEMPTS = 8


def accept_result(res_dir, cmd_dir, token, body, max_delivery_results):
    """Store one posted result and publish its dashboard event."""
    tab_id = body.get('tabId', '')
    if tab_id and path_safety.unsafe_component(tab_id):
        return 400, {'error': 'invalid path component'}
    try:
        token_result_slot = path_safety.under(
            res_dir, path_safety.derived_component(f'{token}.json'))
        tab_result_slot = (
            path_safety.under(
                res_dir,
                path_safety.derived_component(
                    f'{token}_{tab_id}.json'))
            if tab_id else None)
    except ValueError:
        return 400, {'error': 'invalid path component'}
    print(
        f'[RESULT] tab={tab_id[:8] if tab_id else "none"} '
        f'id={log_safe(body.get("id", ""))}', flush=True)
    # Full server-observed roundtrip: _did's leading ms is the enqueue
    # instant (same clock as now), so no skew. Skip if _did is absent or
    # malformed. _did remains internal on the extension wire. Surface its
    # value as deliveryId so waiters can correlate a result with this
    # invocation.
    did = body.pop('_did', '')
    # Only a string delivery id is one: anything else is not a key the
    # dedup record can hold, and pushing it in there would raise.
    if not isinstance(did, str):
        did = ''
    try:
        delivery_dir, delivery_file = (
            result_store.delivery_result_paths(
                res_dir, token, tab_id, did) if did else (None, None))
    except ValueError:
        return 400, {'error': 'invalid path component'}
    body.pop('deliveryId', None)
    body['resultGeneration'] = uuid.uuid4().hex
    if did:
        body['deliveryId'] = did
    if '_' in did:
        try:
            body['roundtrip_ms'] = (
                int(time.time() * 1000) - int(did.split('_')[0]))
        except ValueError:
            # A delivery id is not required to carry a millisecond
            # prefix. When it does not, the reading is absent from the
            # result rather than the result being refused.
            pass
    try:
        serialized = json.dumps(
            body, ensure_ascii=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        return 400, {'error': 'result is not encodable'}
    duplicate = False
    try:
        if delivery_dir is not None:
            assert delivery_file is not None
            with result_store.delivery_lock_for(
                    result_store.result_key(token, tab_id)):
                with result_store.result_lock:
                    duplicate = result_store.delivery_recorded(did)
                if not duplicate:
                    delivery_dir.mkdir(parents=True, exist_ok=True)
                    entries = result_store.scan_delivery_results(
                        delivery_dir)
                    with result_store.result_lock:
                        duplicate = (
                            result_store.delivery_recorded(did))
                        if not duplicate:
                            if tab_result_slot is not None:
                                result_store.atomic_result_write(
                                    tab_result_slot, serialized)
                            result_store.atomic_result_write(
                                token_result_slot, serialized)
                    if not duplicate:
                        result_store.atomic_result_write(
                            delivery_file, serialized)
                        stamp = result_store.mark_delivery_result(
                            delivery_file, entries)
                        with result_store.result_lock:
                            result_store.record_delivery(did)
                        if stamp is not None and entries is not None:
                            entries = [
                                (old_stamp, name)
                                for old_stamp, name in entries
                                if name != delivery_file.name]
                            entries.append((stamp, delivery_file.name))
                            try:
                                result_store.evict_delivery_results(
                                    delivery_dir, entries,
                                    max_delivery_results)
                            except OSError:
                                # The result is stored and its caller
                                # can read it. Failing to trim
                                # retention must not drop that answer,
                                # and the next write to this target
                                # evicts what this one could not.
                                pass
        else:
            with result_store.result_lock:
                if tab_result_slot is not None:
                    result_store.atomic_result_write(
                        tab_result_slot, serialized)
                result_store.atomic_result_write(
                    token_result_slot, serialized)
    except OSError:
        return 500, {'error': 'result storage failure'}
    if duplicate:
        # A retry of a delivery already stored. Answering 200 is what
        # stops the extension retrying again; rewriting the slots is
        # what would lose a newer result, so this does the first and
        # not the second, and publishes no second dashboard event.
        return 200, {'ok': True, 'duplicate': True}
    command_queue.notify_dashboard(cmd_dir, token, {
        'type': 'result',
        'tabId': str(tab_id) if tab_id else '',
        'resultId': body.get('id', ''),
        'world': body.get('world', ''),
        'ok': body.get('error') is None,
        'ts': body.get('ts', int(time.time() * 1000)),
    })
    return 200, {'ok': True}


def fetch_result(res_dir, token, params):
    """Fetch a slot or delivery result, optionally consuming it."""
    tab = params.get('tab', [''])[0]
    delivery = params.get('delivery', [''])[0]
    consume = params.get('consume', [''])[0] == '1'
    expected = params.get('expected', [''])[0]
    if tab and path_safety.unsafe_component(tab):
        return 400, {'error': 'invalid path component'}
    delivery_tab = ''
    delivery_dir = None
    try:
        if delivery:
            delivery_dir, res_file, delivery_tab = (
                result_store.find_delivery_result(
                    res_dir, token, tab, delivery)
            )
        else:
            # A requested tab selects its own slot; otherwise use the
            # token slot.
            res_file = path_safety.under(
                res_dir,
                path_safety.derived_component(
                    f'{result_store.result_key(token, tab)}.json'))
    except ValueError:
        return 400, {'error': 'invalid path component'}
    try:
        if delivery:
            assert delivery_dir is not None
            with result_store.delivery_lock_for(
                    result_store.result_key(token, delivery_tab)):
                with result_store.result_lock:
                    response, _ = result_store.read_result_file(
                        res_file, consume, expected)
                    if consume:
                        consumed = (response.get('consumed') is True
                                    if expected
                                    else 'resultGeneration' in response)
                        if consumed:
                            generation = response.get(
                                'resultGeneration', '')
                            slot_names = [f'{token}.json']
                            if delivery_tab:
                                slot_names.append(
                                    f'{token}_{delivery_tab}.json')
                            for slot_name in slot_names:
                                slot = path_safety.under(
                                    res_dir,
                                    path_safety.derived_component(
                                        slot_name))
                                result_store.remove_matching_result_file(
                                    slot, generation)
        elif consume:
            for _attempt in range(COMPAT_CONSUME_RETRY_ATTEMPTS):
                with result_store.result_lock:
                    preview, _ = result_store.read_result_file(
                        res_file, False, '')
                preview_delivery = (preview.get('deliveryId', '')
                                    if isinstance(preview, dict) else '')
                if (not isinstance(preview_delivery, str)
                        or not preview_delivery
                        or path_safety.unsafe_component(preview_delivery)):
                    with result_store.result_lock:
                        current, _current_delivery = (
                            result_store.read_result_file(
                                res_file, False, '')
                        )
                        current_delivery = (current.get('deliveryId', '')
                                            if isinstance(current, dict)
                                            else '')
                        if current_delivery != preview_delivery:
                            continue
                        response, _result_delivery = (
                            result_store.read_result_file(
                                res_file, True, expected)
                        )
                    break
                try:
                    candidate_dir, candidate_file, candidate_tab = (
                        result_store.find_delivery_result(
                            res_dir, token, tab, preview_delivery))
                except ValueError:
                    candidate_dir = None
                if candidate_dir is None:
                    with result_store.result_lock:
                        response, _result_delivery = (
                            result_store.read_result_file(
                                res_file, True, expected)
                        )
                    break
                changed = False
                with result_store.delivery_lock_for(
                        result_store.result_key(token, candidate_tab)):
                    with result_store.result_lock:
                        current, _current_delivery = (
                            result_store.read_result_file(
                                res_file, False, '')
                        )
                        current_delivery = (current.get('deliveryId', '')
                                            if isinstance(current, dict)
                                            else '')
                        if current_delivery != preview_delivery:
                            changed = True
                        else:
                            response, _result_delivery = (
                                result_store.read_result_file(
                                    res_file, True, expected))
                            consumed = (
                                response.get('consumed') is True
                                if expected
                                else 'resultGeneration' in response)
                            if consumed:
                                generation = response.get(
                                    'resultGeneration', '')
                                result_store.remove_matching_result_file(
                                    candidate_file, generation)
                if not changed:
                    break
            else:
                # The slot's delivery id kept changing under us. Consume on
                # the caller's own terms and leave the mirrored copy for
                # eviction: cross-copy cleanup is best effort, but the
                # caller's generation precondition is not.
                with result_store.result_lock:
                    response, _ = result_store.read_result_file(
                        res_file, True, expected)
        else:
            with result_store.result_lock:
                response, _ = result_store.read_result_file(
                    res_file, consume, expected)
    except (OSError, json.JSONDecodeError, ValueError):
        return 500, {'error': 'result storage failure'}
    return 200, response
