from __future__ import annotations

from typing import cast

from astrbot.api.platform import MessageType

from ..clients.market_client import WarframeMarketClient
from ..components.event_ttl_cache import EventScopedTTLCache
from ..components.qq_official_webhook import QQOfficialWebhookPager
from ..services.market.pager_common import (
    filter_sort_wm_orders,
    rank_wmr_auctions,
    render_wm_page_image,
    render_wmr_page_image,
)


async def handle_qq_interaction_create(
    *,
    bot: object,
    interaction: object,
    qq_pager: QQOfficialWebhookPager,
    pager_cache: EventScopedTTLCache,
    market_client: WarframeMarketClient,
) -> None:
    if not qq_pager.enable_markdown_reply:
        return

    try:
        resolved = getattr(getattr(interaction, "data", None), "resolved", None)
        button_data = getattr(resolved, "button_data", None)
        button_id = getattr(resolved, "button_id", None)
        raw = str(button_data or button_id or "").strip().lower()
    except Exception:
        raw = ""

    if not raw:
        return

    direction = None
    if raw in {
        "wfp:prev",
        "prev",
        "previous",
        "上一页",
        "上",
        "up",
    } or raw.endswith(":prev"):
        direction = "prev"
    elif raw in {"wfp:next", "next", "下一页", "下", "down"} or raw.endswith(":next"):
        direction = "next"

    if not direction:
        return

    # ACK after we confirm it's our button.
    try:
        interaction_id = getattr(interaction, "id", None)
        if interaction_id and getattr(bot, "api", None):
            await bot.api.on_interaction_result(str(interaction_id), 0)  # type: ignore[attr-defined]
    except Exception:
        pass

    platform = getattr(bot, "platform", None)
    if not platform:
        return

    platform_id = ""
    try:
        platform_id = str(platform.meta().id)
    except Exception:
        return

    session_id = ""
    sender_id = ""
    message_type = MessageType.GROUP_MESSAGE
    reply_to_msg_id: str | None = None

    try:
        group_openid = getattr(interaction, "group_openid", None)
        user_openid = getattr(interaction, "user_openid", None)
        channel_id = getattr(interaction, "channel_id", None)
        group_member_openid = getattr(interaction, "group_member_openid", None)
        resolved_user_id = getattr(resolved, "user_id", None)

        if group_openid:
            session_id = str(group_openid)
            message_type = MessageType.GROUP_MESSAGE
            sender_id = str(group_member_openid or resolved_user_id or "")
            try:
                platform.remember_session_scene(session_id, "group")
            except Exception:
                pass
        elif user_openid:
            session_id = str(user_openid)
            message_type = MessageType.FRIEND_MESSAGE
            sender_id = str(user_openid)
        elif channel_id:
            session_id = str(channel_id)
            message_type = MessageType.GROUP_MESSAGE
            sender_id = str(resolved_user_id or "")
            try:
                platform.remember_session_scene(session_id, "channel")
            except Exception:
                pass
        else:
            return
    except Exception:
        return

    if not session_id or not sender_id:
        return

    origin = f"{platform_id}:{message_type.value}:{session_id}"
    state = pager_cache.get_by_origin_sender(origin=origin, sender_id=sender_id)
    if not state:
        await qq_pager.send_markdown_notice_interaction(
            bot,
            interaction,
            title="翻页",
            content="没有可翻页的记录，请先执行 /wm 或 /wmr。",
            reply_to_msg_id=reply_to_msg_id,
        )
        return

    # Only use the msg_id of the original user command (stored when /wm or /wmr ran).
    reply_to_msg_id = str(state.get("reply_msg_id") or "").strip() or None
    if not reply_to_msg_id:
        # Do not fallback to other msg_ids (to avoid proactive messages).
        return

    kind = str(state.get("kind") or "").strip().lower()
    page = int(state.get("page") or 1)
    limit = max(1, min(int(state.get("limit") or 10), 20))

    if direction == "prev":
        if page <= 1:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="已经是第一页。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return
        new_page = page - 1
    else:
        new_page = page + 1

    if kind == "wm":
        item = state.get("item")
        platform_norm = str(state.get("platform") or "pc")
        order_type = str(state.get("order_type") or "sell")
        language = str(state.get("language") or "zh")
        if not item or not getattr(item, "item_id", None):
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="分页信息已过期，请重新执行 /wm。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return

        orders = await market_client.fetch_orders_by_item_id(item.item_id)
        if orders is None:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="未获取到订单（接口请求失败或不可达）。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return
        if not orders:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="暂无订单。",
                reply_to_msg_id=reply_to_msg_id,
            )
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
            page=new_page,
            limit=limit,
        )

        if not top:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="没有更多结果了。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return

        if not rendered:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="图片渲染失败，请稍后重试。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return

        ok = await qq_pager.send_result_markdown_with_keyboard_interaction(
            bot,
            interaction,
            kind="/wm",
            page=new_page,
            image_path=rendered.path,
            reply_to_msg_id=reply_to_msg_id,
        )
        if ok:
            state["page"] = new_page
            state["limit"] = limit
            pager_cache.put_by_origin_sender(
                origin=origin, sender_id=sender_id, state=state
            )
        # For interaction callbacks, do not fallback to image sending.
        # A successful callback implies markdown+keyboard has worked before.
        return

    if kind == "wmr":
        weapon = state.get("weapon")
        if not weapon or not getattr(weapon, "url_name", None):
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="分页信息已过期，请重新执行 /wmr。",
                reply_to_msg_id=reply_to_msg_id,
            )
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
        if auctions is None:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="未获取到紫卡拍卖数据（接口请求失败或不可达）。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return
        if not auctions:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="没有符合条件的一口价紫卡拍卖。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return

        ranked = rank_wmr_auctions(
            auctions,
            platform=platform_norm,
            positive_stats=positive_stats,
            negative_stats=negative_stats,
            negative_required=negative_required,
            mastery_rank_min=cast(int | None, mastery_rank_min),
            polarity=polarity,
        )

        rendered, top, _ = await render_wmr_page_image(
            weapon=weapon,
            weapon_query=weapon_query,
            auctions_ranked=ranked,
            platform=platform_norm,
            language=language,
            positive_stats=positive_stats,
            negative_stats=negative_stats,
            negative_required=negative_required,
            mastery_rank_min=cast(int | None, mastery_rank_min),
            polarity=polarity,
            page=new_page,
            limit=limit,
        )

        if not top:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="没有更多结果了。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return

        if not rendered:
            await qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="图片渲染失败，请稍后重试。",
                reply_to_msg_id=reply_to_msg_id,
            )
            return

        ok = await qq_pager.send_result_markdown_with_keyboard_interaction(
            bot,
            interaction,
            kind="/wmr",
            page=new_page,
            image_path=rendered.path,
            reply_to_msg_id=reply_to_msg_id,
        )
        if ok:
            state["page"] = new_page
            state["limit"] = limit
            pager_cache.put_by_origin_sender(
                origin=origin, sender_id=sender_id, state=state
            )
        # For interaction callbacks, do not fallback to image sending.
        return

    await qq_pager.send_markdown_notice_interaction(
        bot,
        interaction,
        title="翻页",
        content="当前记录不支持翻页，请重新执行 /wm 或 /wmr。",
        reply_to_msg_id=reply_to_msg_id,
    )
