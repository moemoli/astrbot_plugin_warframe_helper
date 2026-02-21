from __future__ import annotations

import time
from collections.abc import Callable

from astrbot.api.event import AstrMessageEvent


def default_event_cache_key(event: AstrMessageEvent) -> str:
    return f"{event.unified_msg_origin}|{event.get_sender_id()}"


class EventScopedTTLCache:
    """A small TTL cache scoped by (origin + sender).

    Value is always a dict; a `ts` field is injected and used for expiry.
    """

    def __init__(
        self,
        *,
        ttl_sec: float,
        key_fn: Callable[[AstrMessageEvent], str] = default_event_cache_key,
    ) -> None:
        self._ttl_sec = float(ttl_sec)
        self._key_fn = key_fn
        self._data: dict[str, dict] = {}

    def put(self, *, event: AstrMessageEvent, state: dict) -> None:
        try:
            rec = dict(state or {})
            rec["ts"] = time.time()
            self._data[self._key_fn(event)] = rec
        except Exception:
            return

    def get(self, event: AstrMessageEvent) -> dict | None:
        key = None
        try:
            key = self._key_fn(event)
        except Exception:
            return None

        rec = self._data.get(key)
        if not isinstance(rec, dict):
            return None

        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            self._data.pop(key, None)
            return None

        if (time.time() - float(ts)) > self._ttl_sec:
            self._data.pop(key, None)
            return None

        return rec
