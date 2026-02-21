from __future__ import annotations

import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ...clients.market_client import WarframeMarketClient
from ...components.event_ttl_cache import EventScopedTTLCache
from ...components.qq_official_webhook import QQOfficialWebhookPager
from ...constants import MARKET_PLATFORM_ALIASES, WM_BUY_ALIASES, WM_SELL_ALIASES
from ...helpers import split_tokens
from ...mappers.term_mapping import WarframeTermMapper
from .pager_common import filter_sort_wm_orders, render_wm_page_image


async def cmd_wm(
    *,
    context: object,
    event: AstrMessageEvent,
    raw_args: str,
    config: dict | None,
    term_mapper: WarframeTermMapper,
    market_client: WarframeMarketClient,
    pager_cache: EventScopedTTLCache,
    wm_pick_cache: EventScopedTTLCache,
    qq_pager: QQOfficialWebhookPager,
):
    try:
        event.should_call_llm(False)
    except Exception as exc:
        logger.debug(f"Failed to disable LLM for /wm: {exc!s}")

    arg_text = str(raw_args or "").strip()
    if not arg_text:
        yield event.plain_result(
            "用法：/wm <物品> [平台] [收/卖] [语言] [数量] 例如：/wm 猴p pc 收 zh 10",
        )
        return

    tokens = split_tokens(arg_text)
    if not tokens:
        yield event.plain_result(
            "用法：/wm <物品> [平台] [收/卖] [语言] [数量] 例如：/wm 猴p pc 收 zh 10",
        )
        return

    query = tokens[0]
    rest = tokens[1:]

    platform_norm = "pc"
    order_type = "sell"
    language = "zh"
    limit = 10

    for t in rest:
        t_norm = str(t).strip().lower()
        if not t_norm:
            continue
        if t_norm in MARKET_PLATFORM_ALIASES:
            platform_norm = MARKET_PLATFORM_ALIASES[t_norm]
            continue
        if t_norm in MARKET_PLATFORM_ALIASES.values():
            platform_norm = t_norm
            continue
        if t_norm in WM_BUY_ALIASES:
            order_type = "buy"
            continue
        if t_norm in WM_SELL_ALIASES:
            order_type = "sell"
            continue
        if t_norm.isdigit():
            limit = int(t_norm)
            continue
        if re.fullmatch(r"[a-z]{2}([\-_][a-z]{2,8})?", t_norm):
            language = t_norm.replace("_", "-")
            continue

    provider_id = str((config or {}).get("unknown_abbrev_provider_id") or "")

    item = await term_mapper.resolve_with_ai(
        context=context,
        event=event,
        query=query,
        provider_id=provider_id,
    )
    if not item:
        yield event.plain_result(f"未找到物品：{query}")
        return
    if not item.item_id:
        yield event.plain_result("物品信息不完整（缺少 item_id），请稍后重试。")
        return

    orders = await market_client.fetch_orders_by_item_id(item.item_id)
    if orders is None:
        yield event.plain_result("未获取到订单（接口请求失败或不可达）。")
        return
    if not orders:
        yield event.plain_result(f"{item.name}（{platform_norm}）暂无订单。")
        return

    filtered = filter_sort_wm_orders(
        orders,
        platform=platform_norm,
        order_type=order_type,
    )

    limit = max(1, min(int(limit), 20))
    page = 1

    # Cache paging context (used by /wfp prev|next and QQ button paging)
    reply_msg_id = None
    try:
        reply_msg_id = getattr(getattr(event, "message_obj", None), "message_id", None)
    except Exception:
        reply_msg_id = None
    pager_cache.put(
        event=event,
        state={
            "kind": "wm",
            "page": page,
            "limit": limit,
            "platform": platform_norm,
            "order_type": order_type,
            "language": language,
            "item": item,
            "reply_msg_id": str(reply_msg_id) if reply_msg_id else "",
        },
    )

    rendered, top = await render_wm_page_image(
        item=item,
        orders=filtered,
        platform=platform_norm,
        order_type=order_type,
        language=language,
        page=page,
        limit=limit,
    )

    action_cn = "收购" if order_type == "buy" else "出售"
    if not top:
        yield event.plain_result(
            f"{item.name}（{platform_norm}）暂无可用{action_cn}订单。"
        )
        return

    wm_pick_cache.put(
        event=event,
        state={
            "item_name_en": item.name,
            "order_type": order_type,
            "platform": platform_norm,
            "rows": [
                {"name": (o.ingame_name or "").strip(), "platinum": int(o.platinum)}
                for o in top
            ],
        },
    )

    if rendered:
        if qq_pager.enabled_for(event):
            ok = await qq_pager.send_result_markdown_with_keyboard(
                event,
                kind="/wm",
                page=page,
                image_path=rendered.path,
            )
            if ok:
                return

            try:
                await event.send(event.image_result(rendered.path))
                await qq_pager.send_pager_keyboard(event, kind="/wm", page=page)
                return
            except Exception:
                yield event.image_result(rendered.path)
                return

        yield event.image_result(rendered.path)
        return

    lines = [
        f"{item.get_localized_name(language)}（{platform_norm}）{action_cn} 低->高 前{len(top)}："
    ]
    for idx, o in enumerate(top, start=1):
        status = o.status or "unknown"
        name = o.ingame_name or "unknown"
        lines.append(f"{idx}. {o.platinum}p  {status}  {name}")
    yield event.plain_result("\n".join(lines))

    if qq_pager.enabled_for(event):
        try:
            await qq_pager.send_pager_keyboard(event, kind="/wm", page=page)
        except Exception:
            pass
