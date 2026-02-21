from __future__ import annotations

import time
from collections.abc import Callable

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger


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
        max_entries: int = 512,
    ) -> None:
        self._ttl_sec = float(ttl_sec)
        self._key_fn = key_fn
        self._max_entries = max(10, int(max_entries))
        self._data: dict[str, dict] = {}

    def _cleanup(self) -> None:
        now = time.time()
        expired: list[str] = []
        for k, v in self._data.items():
            if not isinstance(v, dict):
                expired.append(k)
                continue
            ts = v.get("ts")
            if not isinstance(ts, (int, float)):
                expired.append(k)
                continue
            if (now - float(ts)) > self._ttl_sec:
                expired.append(k)
        for k in expired:
            self._data.pop(k, None)

        over = len(self._data) - self._max_entries
        if over <= 0:
            return
        items = sorted(
            self._data.items(),
            key=lambda kv: float(kv[1].get("ts", 0.0)),
        )
        for k, _ in items[:over]:
            self._data.pop(k, None)

    def put(self, *, event: AstrMessageEvent, state: dict) -> None:
        try:
            rec = dict(state or {})
            rec["ts"] = time.time()
            self._data[self._key_fn(event)] = rec
            self._cleanup()
        except Exception as exc:
            logger.debug(f"EventScopedTTLCache.put failed: {exc!s}")
            return

    def put_by_key(self, *, key: str, state: dict) -> None:
        if not key:
            return
        try:
            rec = dict(state or {})
            rec["ts"] = time.time()
            self._data[str(key)] = rec
            self._cleanup()
        except Exception as exc:
            logger.debug(f"EventScopedTTLCache.put_by_key failed: {exc!s}")
            return

    def get(self, event: AstrMessageEvent) -> dict | None:
        key = None
        try:
            key = self._key_fn(event)
        except Exception as exc:
            logger.debug(f"EventScopedTTLCache.get key failed: {exc!s}")
            return None

        return self.get_by_key(key)

    def get_by_key(self, key: str | None) -> dict | None:
        if not key:
            return None

        rec = self._data.get(str(key))
        if not isinstance(rec, dict):
            return None

        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            self._data.pop(str(key), None)
            return None

        if (time.time() - float(ts)) > self._ttl_sec:
            self._data.pop(str(key), None)
            return None

        return rec

    def get_by_origin_sender(self, *, origin: str, sender_id: str) -> dict | None:
        key = f"{origin}|{sender_id}" if origin and sender_id else ""
        return self.get_by_key(key)

    def put_by_origin_sender(self, *, origin: str, sender_id: str, state: dict) -> None:
        key = f"{origin}|{sender_id}" if origin and sender_id else ""
        self.put_by_key(key=key, state=state)
