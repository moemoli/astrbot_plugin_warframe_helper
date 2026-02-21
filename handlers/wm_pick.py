from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Reply

from ..components.event_ttl_cache import EventScopedTTLCache


async def handle_wm_pick_number(
    *, event: AstrMessageEvent, wm_pick_cache: EventScopedTTLCache
):
    try:
        event.should_call_llm(False)
    except Exception:
        pass

    comps = event.get_messages() or []
    if not any(isinstance(c, Reply) for c in comps):
        return

    rec = wm_pick_cache.get(event)
    if not rec:
        return

    text = (event.get_message_str() or "").strip()
    try:
        idx = int(text)
    except Exception:
        return

    rows = rec.get("rows")
    if not isinstance(rows, list) or not rows:
        return

    if idx < 1 or idx > len(rows):
        yield event.plain_result(f"请输入 1~{len(rows)} 的数字。")
        return

    row = rows[idx - 1]
    if not isinstance(row, dict):
        return

    row_name = row.get("name")
    name = row_name.strip() if isinstance(row_name, str) else ""
    platinum = row.get("platinum")
    if not name or not isinstance(platinum, int):
        return

    item_name_en = (
        rec.get("item_name_en") if isinstance(rec.get("item_name_en"), str) else ""
    )
    order_type = (
        rec.get("order_type") if isinstance(rec.get("order_type"), str) else "sell"
    )

    verb = "buy" if order_type == "sell" else "sell"
    whisper = (
        f'/w {name} Hi! I want to {verb}: "{item_name_en}" '
        f"for {platinum} platinum. (warframe.market)"
    )
    yield event.plain_result(whisper)
