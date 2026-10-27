"""Coalesce cache-miss command discovery without locking imports or filesystem I/O."""

import threading
from concurrent.futures import Future

_inflight: dict[tuple, tuple[int, Future]] = {}
_WAIT_SECONDS = 30.0


def coalesced_scan(key: tuple, previous: dict | None) -> dict:
    from agent import skill_commands as sc

    owner = threading.get_ident()
    with sc._publish_lock:
        # A builder may have completed between the caller's lookup and admission.
        published = sc._skill_commands_by_key.get(key)
        if published is not None and published is not previous:
            return published
        flight = _inflight.get(key)
        if flight is None:
            future = Future()
            _inflight[key] = (owner, future)
        elif flight[0] == owner:
            # Deferred imports can re-enter discovery on the builder thread. Never
            # wait for ourselves or recursively start another cold scan.
            return published if published is not None else {}
        else:
            future = flight[1]
    if flight is not None:
        return future.result(timeout=_WAIT_SECONDS)
    try:
        result = sc.scan_skill_commands()
        future.set_result(result)
        return result
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with sc._publish_lock:
            _inflight.pop(key, None)
