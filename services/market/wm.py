from __future__ import annotations

import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ...clients.market_client import WarframeMarketClient
from ...components.event_ttl_cache import EventScopedTTLCache
from ...components.qq_official_webhook import QQOfficialWebhookPager
from ...constants import MARKET_PLATFORM_ALIASES, WM_BUY_ALIASES, WM_SELL_ALIASES
from ...constants import market_status_to_cn
from ...helpers import split_tokens
from ...mappers.term_mapping import WarframeTermMapper
from .pager_common import filter_sort_wm_orders, render_wm_page_image

# Chinese numeral to integer mapping for level parsing.
_CN_NUM_SINGLE: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# Patterns to detect level tokens.
_LEVEL_KEYWORDS: set[str] = {"满级", "满等", "满", "max"}
_LEVEL_SUFFIX_PATTERN = re.compile(r"^(.*?)\s*[级等]$")


def _parse_level_token(token: str) -> int | str | None:
    """Parse a single token as a level filter.

    Returns:
        int  — specific mod rank (e.g. 5 for "五级" / "5级")
        "max" — max rank requested ("满级")
        None — token is not a level expression
    """
    t = str(token or "").strip()
    if not t:
        return None

    t_lower = t.lower()
    if t_lower in _LEVEL_KEYWORDS:
        return "max"

    # Handle "五级" / "5级" / "十级" style.
    m = _LEVEL_SUFFIX_PATTERN.match(t)
    if m:
        inner = m.group(1).strip()
        if not inner:
            return None
        # Arabic: "5级" -> 5
        if inner.isdigit():
            return int(inner)
        # Single Chinese numeral: "五级" -> 5
        if inner in _CN_NUM_SINGLE:
            return _CN_NUM_SINGLE[inner]
        # "十几" pattern: "十二级" -> 12
        if inner.startswith("十") and len(inner) >= 2:
            suffix = inner[1:]
            if suffix in _CN_NUM_SINGLE:
                return 10 + _CN_NUM_SINGLE[suffix]
        # "几十" pattern: "二十级" -> 20
        if len(inner) >= 2 and inner.endswith("十"):
            prefix = inner[:-1]
            if prefix in _CN_NUM_SINGLE:
                return _CN_NUM_SINGLE[prefix] * 10
        # "几十几" pattern: "二十五级" -> 25
        if "十" in inner:
            parts = inner.split("十", 1)
            if len(parts) == 2 and parts[0] in _CN_NUM_SINGLE and parts[1] in _CN_NUM_SINGLE:
                return _CN_NUM_SINGLE[parts[0]] * 10 + _CN_NUM_SINGLE[parts[1]]
        return None

    return None


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
        event.should_call_llm(True)
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
    mod_rank_level: int | str | None = None  # int=特定等级, "max"=满级

    # Detect level keyword embedded in query token (e.g. "满级xxx", "xxx五级").
    _LEVEL_PREFIXES = ("满级", "满等", "满", "max")
    for prefix in _LEVEL_PREFIXES:
        if query.startswith(prefix) and len(query) > len(prefix):
            mod_rank_level = "max"
            query = query[len(prefix):]
            break
    if mod_rank_level is None:
        for prefix in _LEVEL_PREFIXES:
            if query.endswith(prefix) and len(query) > len(prefix):
                mod_rank_level = "max"
                query = query[:-len(prefix)]
                break
    # Also check query suffix for "X级" / "X等" pattern (e.g. "xxx5级").
    if mod_rank_level is None:
        m_sfx = re.match(r"^(.*?)(\d+)\s*[级等]$", query)
        if m_sfx:
            query = m_sfx.group(1)
            mod_rank_level = int(m_sfx.group(2))

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

        # Check level token BEFORE isdigit (since "5级" is not pure digits)
        level = _parse_level_token(t)
        if level is not None:
            mod_rank_level = level
            continue

        if t_norm.isdigit():
            limit = int(t_norm)
            continue
        if re.fullmatch(r"[a-z]{2}([\-_][a-z]{2,8})?", t_norm):
            language = t_norm.replace("_", "-")
            continue

    item = await term_mapper.resolve(query)
    if not item:
        yield event.plain_result(f"没有找到相关物品：{query}")
        return
    if not item.slug:
        yield event.plain_result("物品信息不完整（缺少 slug），请稍后重试。")
        return

    orders = await market_client.fetch_orders_by_item_slug(
        item.slug,
        platform=platform_norm,
    )
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
        mod_rank=mod_rank_level,
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
            "mod_rank": mod_rank_level,
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
                title="市场订单",
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
        status = market_status_to_cn(o.status)
        name = o.ingame_name or "unknown"
        lines.append(f"{idx}. {o.platinum}p  {status}  {name}")
    yield event.plain_result("\n".join(lines))

    if qq_pager.enabled_for(event):
        try:
            await qq_pager.send_pager_keyboard(event, kind="/wm", page=page)
        except Exception:
            pass
