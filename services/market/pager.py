from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from ...clients.market_client import WarframeMarketClient
from ...components.event_ttl_cache import EventScopedTTLCache
from ...components.qq_official_webhook import QQOfficialWebhookPager
from ...helpers import uniq_lower
from .pager_common import (
    filter_sort_wm_orders,
    rank_wmr_auctions,
    render_wm_page_image,
    render_wmr_page_image,
)


def parse_direction(text: str) -> str:
    raw = (text or "").strip().lower()
    if raw in {"prev", "previous", "上一页", "上", "up"}:
        return "prev"
    if raw in {"next", "下一页", "下", "down"}:
        return "next"
    if raw.endswith(":prev"):
        return "prev"
    if raw.endswith(":next"):
        return "next"
    return "next"


async def cmd_wfp(
    *,
    event: AstrMessageEvent,
    raw_args: str,
    pager_cache: EventScopedTTLCache,
    wm_pick_cache: EventScopedTTLCache,
    market_client: WarframeMarketClient,
    qq_pager: QQOfficialWebhookPager,
):
    try:
        event.should_call_llm(False)
    except Exception:
        pass

    direction = parse_direction(str(raw_args))

    state = pager_cache.get(event)
    if not state:
        if qq_pager.enabled_for(event):
            await qq_pager.send_markdown_notice(
                event,
                title="翻页",
                content="没有可翻页的记录，请先执行 /wm 或 /wmr。",
            )
            return
        yield event.plain_result("没有可翻页的记录，请先执行 /wm 或 /wmr。")
        return

    reply_msg_id = str(state.get("reply_msg_id") or "").strip() or None

    kind = str(state.get("kind") or "").strip().lower()
    page = int(state.get("page") or 1)
    limit = max(1, min(int(state.get("limit") or 10), 20))

    if direction == "prev":
        if page <= 1:
            if qq_pager.enabled_for(event):
                await qq_pager.send_markdown_notice(
                    event,
                    title="翻页",
                    content="已经是第一页。",
                    reply_to_msg_id=reply_msg_id,
                )
                return
            yield event.plain_result("已经是第一页。")
            return
        page -= 1
    else:
        page += 1

    state["page"] = page
    state["limit"] = limit
    pager_cache.put(event=event, state=state)

    if kind == "wm":
        item = state.get("item")
        platform_norm = str(state.get("platform") or "pc")
        order_type = str(state.get("order_type") or "sell")
        language = str(state.get("language") or "zh")
        if not item or not getattr(item, "item_id", None):
            yield event.plain_result("分页信息已过期，请重新执行 /wm。")
            return

        orders = await market_client.fetch_orders_by_item_id(item.item_id)
        if not orders:
            yield event.plain_result("未获取到订单（可能是网络限制或接口不可达）。")
            return

        filtered = filter_sort_wm_orders(
            orders,
            platform=platform_norm,
            order_type=order_type,
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

        if not top:
            if qq_pager.enabled_for(event):
                await qq_pager.send_markdown_notice(
                    event,
                    title="翻页",
                    content="没有更多结果了。",
                    reply_to_msg_id=reply_msg_id,
                )
                return
            yield event.plain_result("没有更多结果了。")
            return

        wm_pick_cache.put(
            event=event,
            state={
                "item_name_en": getattr(item, "name", "") or "",
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
                    reply_to_msg_id=reply_msg_id,
                )
                if ok:
                    return

                await event.send(event.image_result(rendered.path))
                await qq_pager.send_pager_keyboard(
                    event,
                    kind="/wm",
                    page=page,
                    reply_to_msg_id=reply_msg_id,
                )
                return
            yield event.image_result(rendered.path)
            return

        action_cn = "收购" if order_type == "buy" else "出售"
        lines = [
            f"{item.get_localized_name(language)}（{platform_norm}）{action_cn} 第{page}页："
        ]
        for idx, o in enumerate(top, start=1):
            status = o.status or "unknown"
            name = o.ingame_name or "unknown"
            lines.append(f"{idx}. {o.platinum}p  {status}  {name}")
        yield event.plain_result("\n".join(lines))
        if qq_pager.enabled_for(event):
            await qq_pager.send_pager_keyboard(
                event,
                kind="/wm",
                page=page,
                reply_to_msg_id=reply_msg_id,
            )
        return

    if kind == "wmr":
        weapon = state.get("weapon")
        if not weapon or not getattr(weapon, "url_name", None):
            yield event.plain_result("分页信息已过期，请重新执行 /wmr。")
            return

        platform_norm = str(state.get("platform") or "pc")
        language = str(state.get("language") or "zh")
        weapon_query = str(state.get("weapon_query") or "")
        positive_stats = [
            str(x).strip()
            for x in (state.get("positive_stats") or [])
            if str(x).strip()
        ]
        negative_stats = [
            str(x).strip()
            for x in (state.get("negative_stats") or [])
            if str(x).strip()
        ]
        negative_required = bool(state.get("negative_required") or False)

        mastery_rank_min = state.get("mastery_rank_min")
        if mastery_rank_min is not None:
            try:
                mastery_rank_min = int(mastery_rank_min)
            except Exception:
                mastery_rank_min = None

        polarity = state.get("polarity")
        polarity = str(polarity).strip().lower() if polarity else None

        auctions = await market_client.fetch_riven_auctions(
            weapon.url_name,
            platform=platform_norm,
            positive_stats=positive_stats,
            negative_stats=negative_stats,
            mastery_rank_min=mastery_rank_min,
            polarity=polarity,
            buyout_policy="direct",
        )
        if not auctions:
            yield event.plain_result(
                "未获取到紫卡拍卖数据（可能是网络限制或接口不可达）。"
            )
            return

        ranked = rank_wmr_auctions(
            auctions,
            platform=platform_norm,
            positive_stats=positive_stats,
            negative_stats=negative_stats,
            negative_required=negative_required,
            mastery_rank_min=mastery_rank_min,
            polarity=polarity,
        )

        rendered, top, summary = await render_wmr_page_image(
            weapon=weapon,
            weapon_query=weapon_query,
            auctions_ranked=ranked,
            platform=platform_norm,
            language=language,
            positive_stats=uniq_lower(positive_stats),
            negative_stats=uniq_lower(negative_stats),
            negative_required=negative_required,
            mastery_rank_min=mastery_rank_min,
            polarity=polarity,
            page=page,
            limit=limit,
        )

        if not top:
            if qq_pager.enabled_for(event):
                await qq_pager.send_markdown_notice(
                    event,
                    title="翻页",
                    content="没有更多结果了。",
                    reply_to_msg_id=reply_msg_id,
                )
                return
            yield event.plain_result("没有更多结果了。")
            return

        if rendered:
            if qq_pager.enabled_for(event):
                ok = await qq_pager.send_result_markdown_with_keyboard(
                    event,
                    kind="/wmr",
                    page=page,
                    image_path=rendered.path,
                    reply_to_msg_id=reply_msg_id,
                )
                if ok:
                    return

                await event.send(event.image_result(rendered.path))
                await qq_pager.send_pager_keyboard(
                    event,
                    kind="/wmr",
                    page=page,
                    reply_to_msg_id=reply_msg_id,
                )
                return
            yield event.image_result(rendered.path)
            return

        fallback_name = (
            weapon.item_name
            if language.startswith("en")
            else (weapon_query or weapon.item_name)
        )
        lines = [f"紫卡 {fallback_name}（{platform_norm}）{summary} 第{page}页："]
        for idx, a in enumerate(top, start=1):
            name = a.owner_name or "unknown"
            status = a.owner_status or "unknown"
            pol = a.polarity or "?"
            mr = a.mastery_level if a.mastery_level is not None else "?"
            rr = a.re_rolls if a.re_rolls is not None else "?"
            lines.append(
                f"{idx}. {a.buyout_price}p  {status}  {name}  MR{mr}  {pol}  洗练{rr}"
            )
        yield event.plain_result("\n".join(lines))
        if qq_pager.enabled_for(event):
            await qq_pager.send_pager_keyboard(
                event,
                kind="/wmr",
                page=page,
                reply_to_msg_id=reply_msg_id,
            )
        return

    yield event.plain_result("当前记录不支持翻页，请重新执行 /wm 或 /wmr。")
