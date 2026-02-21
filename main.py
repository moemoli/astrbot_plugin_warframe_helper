import json
import re
from typing import cast

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Reply
from astrbot.api.platform import MessageType
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from .clients.drop_data_client import DropDataClient
from .clients.market_client import WarframeMarketClient
from .clients.public_export_client import PublicExportClient
from .clients.worldstate_client import WarframeWorldstateClient
from .components.event_ttl_cache import EventScopedTTLCache
from .components.qq_official_webhook import QQOfficialWebhookPager
from .constants import (
    MARKET_PLATFORM_ALIASES,
    RIVEN_POLARITY_CN,
    RIVEN_STAT_ALIASES,
    RIVEN_STAT_CN,
    WM_BUY_ALIASES,
    WM_SELL_ALIASES,
)
from .helpers import (
    presence_rank,
    split_tokens,
    uniq_lower,
)
from .http_utils import set_proxy_url
from .mappers.riven_mapping import WarframeRivenWeaponMapper
from .mappers.riven_stats_mapping import WarframeRivenStatMapper
from .mappers.term_mapping import WarframeTermMapper
from .renderers.wm_render import render_wm_orders_image_to_file
from .renderers.wmr_render import render_wmr_auctions_image_to_file
from .renderers.worldstate_render import (
    WorldstateRow,
    render_worldstate_rows_image_to_file,
)
from .services import drop_data_commands, public_export_commands, worldstate_commands
from .services.subscriptions import SubscriptionService
from .utils.platforms import worldstate_platform_from_tokens

QQ_OFFICIAL_WEBHOOK_PAGER_TEMPLATE_ID_DEFAULT = "102070299_1771653647"


@register("warframe_helper", "moemoli", "Warframe 助手", "v0.0.1")
class WarframeHelperPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self.config = config

        try:
            proxy_url = self.config.get("proxy_url") if self.config else None
            set_proxy_url(proxy_url)
        except Exception:
            # Never fail plugin init due to proxy config.
            set_proxy_url(None)

        self.term_mapper = WarframeTermMapper()
        self.riven_weapon_mapper = WarframeRivenWeaponMapper()
        self.riven_stat_mapper = WarframeRivenStatMapper()
        self.market_client = WarframeMarketClient()
        self.worldstate_client = WarframeWorldstateClient()
        self.public_export_client = PublicExportClient()
        self.drop_data_client = DropDataClient()

        # 最近一次 /wm 的 TopN 结果缓存（用于“回复图片发数字”快速生成 /w 话术）
        self._wm_pick_cache = EventScopedTTLCache(ttl_sec=8 * 60)

        # /wm, /wmr pagination cache for QQ official webhook button paging.
        self._pager_cache = EventScopedTTLCache(ttl_sec=10 * 60)

        # QQ official webhook keyboard template id (message buttons).
        # Note: QQ button templates do NOT support variables.
        try:
            cfg_val = (
                self.config.get("qq_official_webhook_pager_keyboard_template_id")
                if self.config
                else None
            )
        except Exception:
            cfg_val = None

        qq_tpl = (
            str(cfg_val or "").strip() or QQ_OFFICIAL_WEBHOOK_PAGER_TEMPLATE_ID_DEFAULT
        )

        try:
            md_tpl = (
                self.config.get("qq_official_webhook_markdown_template_id")
                if self.config
                else None
            )
        except Exception:
            md_tpl = None

        self._qq_pager = QQOfficialWebhookPager(
            keyboard_template_id=qq_tpl,
            markdown_template_id=str(md_tpl or "").strip(),
            enable_markdown_reply=bool(
                (self.config or {}).get("qq_official_webhook_enable_markdown_reply")
            ),
        )

        self._qq_pager.set_interaction_handler(self._on_qq_interaction_create)

        # Fissure subscription (proactive notifications)
        self._subscriptions = SubscriptionService(
            context=self.context,
            worldstate_client=self.worldstate_client,
            config=self.config,
        )

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        await self.term_mapper.initialize()
        await self.riven_weapon_mapper.initialize()
        await self.riven_stat_mapper.initialize()

        # Start subscription polling loop after the event loop is ready.
        self._subscriptions.start()

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        await self._subscriptions.stop()

    async def _on_qq_interaction_create(self, bot: object, interaction: object) -> None:
        """Handle QQ official message button callbacks (action.type=1).

        This must stay inside plugin code (no AstrBot core changes).
        """

        if not self._qq_pager.enable_markdown_reply:
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
        elif raw in {"wfp:next", "next", "下一页", "下", "down"} or raw.endswith(
            ":next"
        ):
            direction = "next"

        if not direction:
            # Not our button.
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

        try:
            group_openid = getattr(interaction, "group_openid", None)
            user_openid = getattr(interaction, "user_openid", None)
            channel_id = getattr(interaction, "channel_id", None)
            group_member_openid = getattr(interaction, "group_member_openid", None)
            resolved_user_id = getattr(resolved, "user_id", None)
            resolved_message_id = getattr(resolved, "message_id", None)

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

            if resolved_message_id:
                try:
                    platform.remember_session_message_id(
                        session_id, str(resolved_message_id)
                    )
                except Exception:
                    pass
        except Exception:
            return

        if not session_id or not sender_id:
            return

        origin = f"{platform_id}:{message_type.value}:{session_id}"
        state = self._pager_cache.get_by_origin_sender(
            origin=origin, sender_id=sender_id
        )
        if not state:
            await self._qq_pager.send_markdown_notice_interaction(
                bot,
                interaction,
                title="翻页",
                content="没有可翻页的记录，请先执行 /wm 或 /wmr。",
            )
            return

        kind = str(state.get("kind") or "").strip().lower()
        page = int(state.get("page") or 1)
        limit = int(state.get("limit") or 10)
        limit = max(1, min(int(limit), 20))

        if direction == "prev":
            if page <= 1:
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="已经是第一页。",
                )
                return
            page -= 1
        else:
            page += 1

        state["page"] = page
        state["limit"] = limit
        self._pager_cache.put_by_origin_sender(
            origin=origin, sender_id=sender_id, state=state
        )

        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Image
            from astrbot.core.platform.message_session import MessageSession
        except Exception:
            return

        session = MessageSession(platform_id, message_type, session_id)

        if kind == "wm":
            item = state.get("item")
            platform_norm = str(state.get("platform") or "pc")
            order_type = str(state.get("order_type") or "sell")
            language = str(state.get("language") or "zh")
            if not item or not getattr(item, "item_id", None):
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="分页信息已过期，请重新执行 /wm。",
                )
                return

            orders = await self.market_client.fetch_orders_by_item_id(item.item_id)
            if not orders:
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="未获取到订单（可能是网络限制或接口不可达）。",
                )
                return

            filtered = [
                o
                for o in orders
                if o.visible
                and o.order_type == order_type
                and (o.platform or "").lower() == platform_norm
            ]
            filtered.sort(
                key=lambda o: (
                    presence_rank(o.status),
                    o.platinum,
                    (o.ingame_name or ""),
                ),
            )
            offset = (page - 1) * limit
            top = filtered[offset : offset + limit]
            if not top:
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="没有更多结果了。",
                )
                return

            rendered = await render_wm_orders_image_to_file(
                item=item,
                orders=top,
                platform=platform_norm,
                action_cn=("收购" if order_type == "buy" else "出售"),
                language=language,
                limit=limit,
            )
            if not rendered:
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="图片渲染失败，请稍后重试。",
                )
                return

            try:
                await platform.send_by_session(
                    session,
                    MessageChain([Image.fromFileSystem(rendered.path)]),
                )
            except Exception as exc:
                logger.warning(f"QQ interaction paging send image failed: {exc!s}")
                return

            await self._qq_pager.send_pager_keyboard_interaction(
                bot,
                interaction,
                kind="/wm",
                page=page,
            )
            return

        if kind == "wmr":
            weapon = state.get("weapon")
            if not weapon or not getattr(weapon, "url_name", None):
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="分页信息已过期，请重新执行 /wmr。",
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

            auctions = await self.market_client.fetch_riven_auctions(
                weapon.url_name,
                platform=platform_norm,
                positive_stats=positive_stats,
                negative_stats=negative_stats,
                mastery_rank_min=mastery_rank_min,
                polarity=polarity,
                buyout_policy="direct",
            )
            if not auctions:
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="未获取到紫卡拍卖数据（可能是网络限制或接口不可达）。",
                )
                return

            filtered = [
                a
                for a in auctions
                if a.visible
                and (not a.closed)
                and a.is_direct_sell
                and (a.platform or "").lower() == platform_norm
            ]
            if negative_required and not negative_stats:
                filtered = [
                    a for a in filtered if any((not x.positive) for x in a.attributes)
                ]

            req_pos = set(uniq_lower(positive_stats))
            req_neg = set(uniq_lower(negative_stats))

            def stat_fit_score(a) -> int:
                a_pos = {x.url_name for x in a.attributes if x.positive}
                a_neg = {x.url_name for x in a.attributes if not x.positive}

                score = 0
                score += 10 * len(req_pos & a_pos)
                score += 10 * len(req_neg & a_neg)
                score -= 50 * len(req_pos - a_pos)
                score -= 50 * len(req_neg - a_neg)

                if negative_required and not a_neg:
                    score -= 20

                if req_pos:
                    score -= len(a_pos - req_pos)
                if req_neg:
                    score -= len(a_neg - req_neg)

                if polarity:
                    if (a.polarity or "").strip().lower() == polarity.strip().lower():
                        score += 5
                    else:
                        score -= 5

                if mastery_rank_min is not None and a.mastery_level is not None:
                    score -= abs(int(a.mastery_level) - int(mastery_rank_min))

                return score

            scored: list[tuple[int, object]] = [
                (stat_fit_score(a), a) for a in filtered
            ]
            scored.sort(
                key=lambda x: (
                    -int(x[0]),
                    presence_rank(getattr(x[1], "owner_status", None)),
                    int(getattr(x[1], "buyout_price", 0) or 0),
                    (getattr(x[1], "auction_id", "") or ""),
                ),
            )
            ranked = [a for _, a in scored]
            offset = (page - 1) * limit
            picked = ranked[offset : offset + limit]
            if not picked:
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="没有更多结果了。",
                )
                return

            picked.sort(
                key=lambda a: (
                    presence_rank(getattr(a, "owner_status", None)),
                    int(getattr(a, "buyout_price", 0) or 0),
                    (getattr(a, "owner_name", "") or ""),
                ),
            )
            top = cast(list, picked)

            def fmt_stats(stats: list[str]) -> str:
                return "+".join([RIVEN_STAT_CN.get(s, s) for s in stats])

            parts: list[str] = []
            if positive_stats:
                parts.append("正:" + fmt_stats(positive_stats))
            if negative_stats:
                parts.append("负:" + fmt_stats(negative_stats))
            elif negative_required:
                parts.append("负:任意")
            if mastery_rank_min is not None:
                parts.append(f"MR≥{mastery_rank_min}")
            if polarity:
                parts.append("极性" + RIVEN_POLARITY_CN.get(polarity, polarity))
            summary = " ".join(parts) if parts else "(无筛选)"

            rendered = await render_wmr_auctions_image_to_file(
                weapon=weapon,
                weapon_display_name=("" if language.startswith("en") else weapon_query),
                auctions=top,
                platform=platform_norm,
                summary=summary,
                limit=len(top),
            )
            if not rendered:
                await self._qq_pager.send_markdown_notice_interaction(
                    bot,
                    interaction,
                    title="翻页",
                    content="图片渲染失败，请稍后重试。",
                )
                return

            try:
                await platform.send_by_session(
                    session,
                    MessageChain([Image.fromFileSystem(rendered.path)]),
                )
            except Exception as exc:
                logger.warning(f"QQ interaction paging send image failed: {exc!s}")
                return

            await self._qq_pager.send_pager_keyboard_interaction(
                bot,
                interaction,
                kind="/wmr",
                page=page,
            )
            return

        await self._qq_pager.send_markdown_notice_interaction(
            bot,
            interaction,
            title="翻页",
            content="当前记录不支持翻页，请重新执行 /wm 或 /wmr。",
        )
        return

    @filter.command("订阅")
    async def wf_subscribe(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """订阅提醒。

        - 裂缝：/订阅 钢铁赛中 [次数|永久]
        - 平原：/订阅 夜灵平原 黑夜 [次数|永久]
        """

        event.should_call_llm(False)

        raw_args = str(args)
        msg, chain = await self._subscriptions.subscribe(event=event, raw_args=raw_args)
        if chain is not None:
            yield event.chain_result(chain.chain)
            return
        if msg:
            yield event.plain_result(msg)
            return

    @filter.command("退订", alias={"取消订阅"})
    async def wf_unsubscribe(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """退订提醒。

        - 裂缝：/退订 钢铁赛中
        - 平原：/退订 夜灵平原 黑夜
        """

        event.should_call_llm(False)

        msg = await self._subscriptions.unsubscribe(event=event, raw_args=str(args))
        yield event.plain_result(msg)

    @filter.command("订阅列表")
    async def wf_subscribe_list(self, event: AstrMessageEvent):
        """查看当前会话的订阅列表。"""

        event.should_call_llm(False)
        chain = await self._subscriptions.render_list(event=event)
        yield event.chain_result(chain.chain)

    @filter.command("执行官猎杀", alias={"archon", "执行官"})
    async def wf_archon_hunt(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询执行官猎杀（Archon Hunt）。"""

        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_archon_hunt(
            platform=platform_norm, language="zh"
        )
        if not info:
            yield event.plain_result(
                "未获取到执行官猎杀信息（可能是网络限制或接口不可达）。"
            )
            return

        header_lines: list[str] = [f"平台：{platform_norm}"]
        if info.boss:
            header_lines.append(f"Boss：{info.boss}")
        if info.faction:
            header_lines.append(f"阵营：{info.faction}")

        rows: list[WorldstateRow] = []
        if info.stages:
            for idx, s in enumerate(info.stages, start=1):
                mod = f" | {s.modifier}" if s.modifier else ""
                rows.append(
                    WorldstateRow(
                        title=f"{idx}. {s.mission_type}",
                        subtitle=f"{s.node}{mod}",
                        right=f"剩余{info.eta}",
                    )
                )
        else:
            rows.append(WorldstateRow(title="(暂无任务详情)", right=f"剩余{info.eta}"))

        rendered = await render_worldstate_rows_image_to_file(
            title="执行官猎杀",
            header_lines=header_lines,
            rows=rows,
            accent=(239, 68, 68, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        head_parts: list[str] = [f"执行官猎杀（{platform_norm}）"]
        if info.boss:
            head_parts.append(str(info.boss))
        if info.faction:
            head_parts.append(str(info.faction))
        head_parts.append(f"剩余{info.eta}")
        lines: list[str] = [" ".join(head_parts)]
        if not info.stages:
            lines.append("(暂无任务详情)")
            yield event.plain_result("\n".join(lines))
            return
        for idx, s in enumerate(info.stages, start=1):
            mod = f" | {s.modifier}" if s.modifier else ""
            lines.append(f"{idx}. {s.mission_type} - {s.node}{mod}")
        yield event.plain_result("\n".join(lines))

    @filter.command("钢铁奖励", alias={"steelreward", "sp奖励"})
    async def wf_steel_reward(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询钢铁之路当前奖励轮换（Steel Path）。"""

        event.should_call_llm(False)

        tokens = split_tokens(str(args))
        platform_norm = worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_steel_path_reward(
            platform=platform_norm, language="zh"
        )
        if not info:
            yield event.plain_result(
                "未获取到钢铁奖励信息（可能是网络限制或接口不可达）。"
            )
            return

        reward = info.reward or "(未知奖励)"
        rows = [
            WorldstateRow(
                title=f"当前奖励：{reward}",
                subtitle=None,
                right=f"剩余{info.eta}",
            )
        ]
        rendered = await render_worldstate_rows_image_to_file(
            title="钢铁奖励",
            header_lines=[f"平台：{platform_norm}"],
            rows=rows,
            accent=(100, 116, 139, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        yield event.plain_result(
            f"钢铁奖励（{platform_norm}）\n- 当前：{reward}\n- 剩余{info.eta}"
        )

    # 注册指令的装饰器。指令名为 helloworld。注册成功后，发送 `/helloworld` 就会触发这个指令，并回复 `你好, {user_name}!`
    @filter.command("helloworld")
    async def helloworld(self, event: AstrMessageEvent):
        """这是一个 hello world 指令"""  # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        event.should_call_llm(False)
        user_name = event.get_sender_name()
        message_str = event.message_str  # 用户发的纯文本消息字符串
        message_chain = (
            event.get_messages()
        )  # 用户所发的消息的消息链 # from astrbot.api.message_components import *
        logger.info(message_chain)

        rendered = await render_worldstate_rows_image_to_file(
            title="Warframe Helper",
            header_lines=[f"Hello, {user_name}"],
            rows=[WorldstateRow(title=f"你发了：{message_str}")],
            accent=(79, 70, 229, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        yield event.plain_result(
            f"Hello, {user_name}, 你发了 {message_str}!"
        )  # 发送一条纯文本消息

    @filter.command("wfmap", alias={"wf映射"})
    async def wfmap(self, event: AstrMessageEvent, query: str = ""):
        """将常用简写/别名映射为 warframe.market 官方词条（例如：猴p -> Wukong Prime Set）"""
        event.should_call_llm(False)
        query = (query or "").strip()
        if not query:
            yield event.plain_result("用法：/wfmap 猴p")
            return

        item = await self.term_mapper.resolve_with_ai(
            context=self.context,
            event=event,
            query=query,
            provider_id=(
                self.config.get("unknown_abbrev_provider_id") if self.config else ""
            ),
        )
        if not item:
            yield event.plain_result(f"未找到可映射的词条：{query}")
            return

        header = [f"{query} -> {item.name}", f"slug: {item.slug}"]
        rows = (
            [WorldstateRow(title=f"Wiki: {item.wiki_link}")]
            if item.wiki_link
            else [WorldstateRow(title="(无 Wiki 链接)")]
        )
        rendered = await render_worldstate_rows_image_to_file(
            title="WF 映射",
            header_lines=header,
            rows=rows,
            accent=(79, 70, 229, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        extra = f"\nWiki: {item.wiki_link}" if item.wiki_link else ""
        yield event.plain_result(f"{query} -> {item.name} ({item.slug}){extra}")

    @filter.command("wm")
    async def wm(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询 warframe.market 订单。

        用法：
        - /wm 猴p
        - /wm 猴p pc
        - /wm 猴p pc 收
        - /wm 猴p pc 收 zh 10
        """

        arg_text = str(args).strip()
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

        provider_id = (
            self.config.get("unknown_abbrev_provider_id") if self.config else ""
        )
        item = await self.term_mapper.resolve_with_ai(
            context=self.context,
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

        orders = await self.market_client.fetch_orders_by_item_id(item.item_id)
        if not orders:
            yield event.plain_result("未获取到订单（可能是网络限制或接口不可达）。")
            return

        filtered = [
            o
            for o in orders
            if o.visible
            and o.order_type == order_type
            and (o.platform or "").lower() == platform_norm
        ]

        # 优先展示“游戏中”的玩家，其次在线，再离线；同一状态内按价格升序
        filtered.sort(
            key=lambda o: (
                presence_rank(o.status),
                o.platinum,
                (o.ingame_name or ""),
            ),
        )
        limit = max(1, min(int(limit), 20))
        page = 1
        offset = (page - 1) * limit
        top = filtered[offset : offset + limit]

        action_cn = "收购" if order_type == "buy" else "出售"
        if not top:
            yield event.plain_result(
                f"{item.name}（{platform_norm}）暂无可用{action_cn}订单。",
            )
            return

        # Cache paging context (used by /wfp prev|next)
        self._pager_cache.put(
            event=event,
            state={
                "kind": "wm",
                "page": page,
                "limit": limit,
                "platform": platform_norm,
                "order_type": order_type,
                "language": language,
                "item": item,
            },
        )

        # 缓存本次 TopN（用于后续“回复图片发数字”）
        self._wm_pick_cache.put(
            event=event,
            state={
                "item_name_en": item.name,
                "order_type": order_type,
                "platform": platform_norm,
                "rows": [
                    {
                        "name": (o.ingame_name or "").strip(),
                        "platinum": int(o.platinum),
                    }
                    for o in top
                ],
            },
        )

        rendered = await render_wm_orders_image_to_file(
            item=item,
            orders=top,
            platform=platform_norm,
            action_cn=action_cn,
            language=language,
            limit=limit,
        )
        if rendered:
            # QQ official webhook: send image + a markdown message with pager buttons.
            if self._qq_pager.enabled_for(event):
                try:
                    await event.send(event.image_result(rendered.path))
                    await self._qq_pager.send_pager_keyboard(
                        event,
                        kind="/wm",
                        page=page,
                    )
                except Exception:
                    # Fallback to default yield path
                    yield event.image_result(rendered.path)
                return

            yield event.image_result(rendered.path)
            return

        # 图片渲染失败时回退为纯文本
        lines = [
            f"{item.get_localized_name(language)}（{platform_norm}）{action_cn} 低->高 前{len(top)}："
        ]
        for idx, o in enumerate(top, start=1):
            status = o.status or "unknown"
            name = o.ingame_name or "unknown"
            lines.append(f"{idx}. {o.platinum}p  {status}  {name}")
        yield event.plain_result("\n".join(lines))

        # If qq_official_webhook and template configured, still try to append pager buttons.
        if self._qq_pager.enabled_for(event):
            try:
                await self._qq_pager.send_pager_keyboard(
                    event,
                    kind="/wm",
                    page=page,
                )
            except Exception:
                pass

    @filter.command("wmr")
    async def wmr(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询 warframe.market 紫卡（Riven）一口价拍卖。

        示例：/wmr 绝路 双暴 负任意 12段 r槽
        语义：武器=绝路，正面=暴击率+暴击伤害，负面任意（但需要有负面），MR>=12，极性=R(zenurik)
        """

        arg_text = str(args).strip()
        if not arg_text:
            yield event.plain_result(
                "用法：/wmr <武器> [条件...] 例如：/wmr 绝路 双暴 负任意 12段 r槽",
            )
            return

        tokens = split_tokens(arg_text)
        if not tokens:
            yield event.plain_result(
                "用法：/wmr <武器> [条件...] 例如：/wmr 绝路 双暴 负任意 12段 r槽",
            )
            return

        weapon_query = tokens[0]
        rest = tokens[1:]

        def normalize_key(text: str) -> str:
            return re.sub(r"\s+", "", str(text).strip().lower())

        platform_norm = "pc"
        limit = 10
        language = "zh"

        positive_stats: list[str] = []
        negative_stats: list[str] = []
        negative_required = False
        mastery_rank_min: int | None = None
        polarity: str | None = None

        pending_stats: list[tuple[str, bool]] = []

        unknown_tokens: list[str] = []

        for t in rest:
            t_norm = normalize_key(t)
            if not t_norm:
                continue

            if t_norm in MARKET_PLATFORM_ALIASES:
                platform_norm = MARKET_PLATFORM_ALIASES[t_norm]
                continue
            if t_norm in MARKET_PLATFORM_ALIASES.values():
                platform_norm = t_norm
                continue

            # limit
            if t_norm.isdigit():
                limit = int(t_norm)
                continue

            # language (keep consistent with /wm)
            if re.fullmatch(r"[a-z]{2}([\-_][a-z]{2,8})?", t_norm):
                language = t_norm.replace("_", "-")
                continue

            # mastery: 12段 / MR12
            m = re.fullmatch(r"mr?(\d{1,2})", t_norm)
            if m:
                mastery_rank_min = int(m.group(1))
                continue
            m = re.fullmatch(r"(\d{1,2})段", t_norm)
            if m:
                mastery_rank_min = int(m.group(1))
                continue

            # polarity: support common forms
            # - v槽/d槽/-槽/r槽
            # - v极性/d极性/-极性/r极性
            # - 极性v/极性d/极性-/极性r
            # - madurai/vazarin/naramon/zenurik
            m = re.fullmatch(r"([vd\-r])槽", t_norm)
            if not m:
                m = re.fullmatch(r"([vd\-r])极性", t_norm)
            if not m:
                m = re.fullmatch(r"极性([vd\-r])", t_norm)
            if m:
                p = m.group(1)
                if p == "v":
                    polarity = "madurai"
                elif p == "d":
                    polarity = "vazarin"
                elif p == "-":
                    polarity = "naramon"
                elif p == "r":
                    polarity = "zenurik"
                continue

            if t_norm in {"madurai", "vazarin", "naramon", "zenurik"}:
                polarity = t_norm
                continue

            # shorthand: composite token support, e.g. "双爆毒" -> 双暴 + 毒
            if "双暴" in t_norm or "双爆" in t_norm:
                positive_stats.extend(["critical_chance", "critical_damage"])

                rest_tok = t_norm.replace("双暴", "").replace("双爆", "")
                # Common elemental single-char shorthands
                if "毒" in rest_tok:
                    positive_stats.append("toxin_damage")
                if "火" in rest_tok:
                    positive_stats.append("heat_damage")
                if "冰" in rest_tok:
                    positive_stats.append("cold_damage")
                if "电" in rest_tok:
                    positive_stats.append("electric_damage")
                # A few other common shorthands
                if "多重" in rest_tok:
                    positive_stats.append("multishot")
                if "伤害" in rest_tok:
                    positive_stats.append("base_damage_/_melee_damage")
                if "穿刺" in rest_tok:
                    positive_stats.append("puncture_damage")
                if "切割" in rest_tok:
                    positive_stats.append("slash_damage")
                if "冲击" in rest_tok:
                    positive_stats.append("impact_damage")
                continue

            # negative rules
            if t_norm in {"负任意", "任意负", "有负", "要负"} or "负任意" in t_norm:
                negative_required = True
                continue
            if t_norm in {"无负", "不要负", "不带负"}:
                negative_required = False
                negative_stats = []
                continue
            if t_norm.startswith("负") and len(t_norm) > 1:
                negative_required = True
                key = t_norm[1:]
                if key in RIVEN_STAT_ALIASES:
                    url_name = RIVEN_STAT_ALIASES[key]
                    if url_name == "damage_vs_sentient":
                        pending_stats.append((url_name, True))
                    else:
                        negative_stats.append(url_name)
                continue

            # explicit positive stat tokens
            if t_norm in RIVEN_STAT_ALIASES:
                url_name = RIVEN_STAT_ALIASES[t_norm]
                if url_name == "damage_vs_sentient":
                    pending_stats.append((url_name, False))
                else:
                    positive_stats.append(url_name)
                continue

            # 兼容“正面词条暴击率/暴击伤害”等组合写法
            if "暴击率" in t_norm:
                positive_stats.append("critical_chance")
                continue
            if "暴击伤害" in t_norm or "暴伤" in t_norm:
                positive_stats.append("critical_damage")
                continue

            # 未识别 token：先记录，稍后交给 AI 兜底（避免在循环里频繁调用）
            unknown_tokens.append(str(t).strip())

        # AI 兜底：尝试把未知简写解析成 riven 属性 url_name
        provider_id = (
            self.config.get("unknown_abbrev_provider_id") if self.config else ""
        )

        await self.riven_stat_mapper.initialize()

        for url_name, is_negative in pending_stats:
            if self.riven_stat_mapper.is_valid_url_name(url_name):
                if is_negative:
                    negative_stats.append(url_name)
                else:
                    positive_stats.append(url_name)
                continue

            yield event.plain_result(
                "warframe.market 当前不支持“对Sentient伤害（S歧视）”紫卡词条。"
                "目前仅支持：对Grineer/Corpus/Infested伤害（G/C/I歧视）。"
            )
            return

        async def ai_split_stat_token(token: str) -> list[str]:
            """Use LLM to split a composite shorthand token into smaller stat tokens.

            Example: "双爆毒" -> ["双爆", "毒"], "暴伤毒" -> ["暴伤", "毒"]
            """

            tok = (token or "").strip()
            if not tok or not provider_id:
                return []

            system_prompt = (
                "You split a Warframe Riven stat shorthand token into individual stat tokens. "
                "Return JSON only."
            )
            prompt = (
                "Split the following user token into 1~6 smaller stat tokens (Chinese or common abbreviations).\n"
                "Rules:\n"
                '- Output MUST be valid JSON: {"tokens": ["..."]}.\n'
                "- Do NOT output explanations.\n"
                "- Keep tokens minimal and meaningful for riven stat parsing.\n"
                "Examples:\n"
                '- "双爆毒" -> {"tokens":["双爆","毒"]}\n'
                '- "暴伤多重" -> {"tokens":["暴伤","多重"]}\n'
                f"Token: {tok}\n"
                "JSON:"
            )

            try:
                llm_resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=0,
                    timeout=15,
                )
            except TypeError:
                try:
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        temperature=0,
                    )
                except Exception:
                    return []
            except Exception:
                return []

            text = (llm_resp.completion_text or "").strip()
            obj = None
            try:
                obj = json.loads(text)
            except Exception:
                m = re.search(r"\{[\s\S]*\}", text)
                if m:
                    try:
                        obj = json.loads(m.group(0))
                    except Exception:
                        obj = None

            arr = obj.get("tokens") if isinstance(obj, dict) else None
            if isinstance(arr, str):
                arr = [arr]
            if not isinstance(arr, list):
                return []

            out: list[str] = []
            seen: set[str] = set()
            for s in arr:
                if not isinstance(s, str):
                    continue
                s2 = s.strip()
                s2 = re.sub(r"^(正面|正|负面|负)[:：]?", "", s2)
                s2 = s2.strip(" ,，+\t\r\n")
                if not s2:
                    continue
                k = s2.lower()
                if k in seen:
                    continue
                seen.add(k)
                out.append(s2)
                if len(out) >= 6:
                    break
            return out

        for tok in unknown_tokens:
            tok2 = (tok or "").strip()
            if not tok2:
                continue

            is_negative = False
            query_tok = tok2
            if tok2.startswith("负") and len(tok2) > 1:
                is_negative = True
                negative_required = True
                query_tok = tok2[1:]

            # 先尝试 alias_map（防止 AI 调用）
            resolved = self.riven_stat_mapper.resolve_from_alias(
                query_tok, alias_map=RIVEN_STAT_ALIASES
            )
            if not resolved:
                resolved = await self.riven_stat_mapper.resolve_with_ai(
                    context=self.context,
                    event=event,
                    token=query_tok,
                    provider_id=provider_id,
                )

            if not resolved:
                # Still not resolved: try AI-based splitting, then resolve each part.
                parts = await ai_split_stat_token(query_tok)
                for part in parts:
                    part_resolved = self.riven_stat_mapper.resolve_from_alias(
                        part, alias_map=RIVEN_STAT_ALIASES
                    )
                    if not part_resolved:
                        part_resolved = await self.riven_stat_mapper.resolve_with_ai(
                            context=self.context,
                            event=event,
                            token=part,
                            provider_id=provider_id,
                        )
                    if not part_resolved:
                        continue
                    if is_negative:
                        negative_stats.append(part_resolved)
                    else:
                        positive_stats.append(part_resolved)
                continue

            if not resolved:
                continue
            if is_negative:
                negative_stats.append(resolved)
            else:
                positive_stats.append(resolved)

        positive_stats = uniq_lower(positive_stats)
        negative_stats = uniq_lower(negative_stats)

        weapon = await self.riven_weapon_mapper.resolve_weapon(
            context=self.context,
            event=event,
            query=weapon_query,
            provider_id=provider_id,
        )
        if not weapon:
            yield event.plain_result(
                f"未识别武器：{weapon_query}（可尝试输入英文名，如 soma）"
            )
            return

        auctions = await self.market_client.fetch_riven_auctions(
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

        filtered = [
            a
            for a in auctions
            if a.visible
            and (not a.closed)
            and a.is_direct_sell
            and (a.platform or "").lower() == platform_norm
        ]

        if negative_required and not negative_stats:
            filtered = [
                a for a in filtered if any((not x.positive) for x in a.attributes)
            ]

        req_pos = set(positive_stats)
        req_neg = set(negative_stats)

        def stat_fit_score(a) -> int:
            a_pos = {x.url_name for x in a.attributes if x.positive}
            a_neg = {x.url_name for x in a.attributes if not x.positive}

            score = 0

            # Reward hits, strongly penalize missing requested stats.
            score += 10 * len(req_pos & a_pos)
            score += 10 * len(req_neg & a_neg)
            score -= 50 * len(req_pos - a_pos)
            score -= 50 * len(req_neg - a_neg)

            # Prefer having a negative when user requires it.
            if negative_required and not a_neg:
                score -= 20

            # If user specified some stats, prefer fewer unrelated extras.
            if req_pos:
                score -= len(a_pos - req_pos)
            if req_neg:
                score -= len(a_neg - req_neg)

            # Polarity match preference.
            if polarity:
                if (a.polarity or "").strip().lower() == polarity.strip().lower():
                    score += 5
                else:
                    score -= 5

            # If MR threshold specified, prefer closer to the threshold.
            if mastery_rank_min is not None and a.mastery_level is not None:
                score -= abs(int(a.mastery_level) - int(mastery_rank_min))

            return score

        # Two-stage sorting:
        # 1) Rank all auctions by "fit to parameters" (stable tie-breakers).
        # 2) Pick one page (page size = limit) from the ranking.
        # 3) Within the picked page, sort by online status & price for display.
        limit = max(1, min(int(limit), 20))
        page = 1
        offset = (page - 1) * limit

        scored: list[tuple[int, object]] = [(stat_fit_score(a), a) for a in filtered]
        scored.sort(
            key=lambda x: (
                -int(x[0]),
                presence_rank(getattr(x[1], "owner_status", None)),
                int(getattr(x[1], "buyout_price", 0) or 0),
                (getattr(x[1], "auction_id", "") or ""),
            )
        )
        ranked = [a for _, a in scored]
        picked = ranked[offset : offset + limit]

        picked.sort(
            key=lambda a: (
                presence_rank(getattr(a, "owner_status", None)),
                int(getattr(a, "buyout_price", 0) or 0),
                (getattr(a, "owner_name", "") or ""),
            )
        )
        top = cast(list, picked)
        if not top:
            yield event.plain_result("没有符合条件的一口价紫卡拍卖。")
            return

        # Cache paging context (used by /wfp prev|next)
        self._pager_cache.put(
            event=event,
            state={
                "kind": "wmr",
                "page": page,
                "limit": limit,
                "platform": platform_norm,
                "language": language,
                "weapon_query": weapon_query,
                "weapon": weapon,
                "positive_stats": list(positive_stats),
                "negative_stats": list(negative_stats),
                "negative_required": bool(negative_required),
                "mastery_rank_min": mastery_rank_min,
                "polarity": polarity,
            },
        )

        # summary
        def fmt_stats(stats: list[str]) -> str:
            return "+".join([RIVEN_STAT_CN.get(s, s) for s in stats])

        parts: list[str] = []
        if positive_stats:
            parts.append("正:" + fmt_stats(positive_stats))
        if negative_stats:
            parts.append("负:" + fmt_stats(negative_stats))
        elif negative_required:
            parts.append("负:任意")
        if mastery_rank_min is not None:
            parts.append(f"MR≥{mastery_rank_min}")
        if polarity:
            parts.append("极性" + RIVEN_POLARITY_CN.get(polarity, polarity))
        summary = " ".join(parts) if parts else "(无筛选)"

        rendered = await render_wmr_auctions_image_to_file(
            weapon=weapon,
            weapon_display_name=("" if language.startswith("en") else weapon_query),
            auctions=top,
            platform=platform_norm,
            summary=summary,
            limit=len(top),
        )
        if rendered:
            if self._qq_pager.enabled_for(event):
                try:
                    await event.send(event.image_result(rendered.path))
                    await self._qq_pager.send_pager_keyboard(
                        event,
                        kind="/wmr",
                        page=page,
                    )
                except Exception:
                    yield event.image_result(rendered.path)
                return

            yield event.image_result(rendered.path)
            return

        # fallback text
        fallback_name = weapon.item_name if language.startswith("en") else weapon_query
        lines = [f"紫卡 {fallback_name}（{platform_norm}）{summary} 前{len(top)}："]
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

        if self._qq_pager.enabled_for(event):
            try:
                await self._qq_pager.send_pager_keyboard(
                    event,
                    kind="/wmr",
                    page=page,
                )
            except Exception:
                pass

    @filter.command("wfp")
    async def wf_page(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """Pagination helper for /wm and /wmr.

        Usage:
        - /wfp prev
        - /wfp next

        Designed to be used by QQ official webhook "command" buttons.
        """

        text = str(args).strip().lower()
        direction = "next"
        if text in {"prev", "previous", "上一页", "上", "up"}:
            direction = "prev"
        elif text in {"next", "下一页", "下", "down"}:
            direction = "next"

        state = self._pager_cache.get(event)
        if not state:
            if self._qq_pager.enabled_for(event):
                await self._qq_pager.send_markdown_notice(
                    event,
                    title="翻页",
                    content="没有可翻页的记录，请先执行 /wm 或 /wmr。",
                )
                return
            yield event.plain_result("没有可翻页的记录，请先执行 /wm 或 /wmr。")
            return

        kind = str(state.get("kind") or "").strip().lower()
        page = int(state.get("page") or 1)
        limit = int(state.get("limit") or 10)
        limit = max(1, min(int(limit), 20))

        if direction == "prev":
            if page <= 1:
                if self._qq_pager.enabled_for(event):
                    await self._qq_pager.send_markdown_notice(
                        event,
                        title="翻页",
                        content="已经是第一页。",
                    )
                    return
                yield event.plain_result("已经是第一页。")
                return
            page -= 1
        else:
            page += 1

        state["page"] = page
        state["limit"] = limit
        self._pager_cache.put(event=event, state=state)

        if kind == "wm":
            item = state.get("item")
            platform_norm = str(state.get("platform") or "pc")
            order_type = str(state.get("order_type") or "sell")
            language = str(state.get("language") or "zh")
            if not item or not getattr(item, "item_id", None):
                yield event.plain_result("分页信息已过期，请重新执行 /wm。")
                return

            orders = await self.market_client.fetch_orders_by_item_id(item.item_id)
            if not orders:
                yield event.plain_result("未获取到订单（可能是网络限制或接口不可达）。")
                return

            filtered = [
                o
                for o in orders
                if o.visible
                and o.order_type == order_type
                and (o.platform or "").lower() == platform_norm
            ]
            filtered.sort(
                key=lambda o: (
                    presence_rank(o.status),
                    o.platinum,
                    (o.ingame_name or ""),
                )
            )
            offset = (page - 1) * limit
            top = filtered[offset : offset + limit]
            if not top:
                if self._qq_pager.enabled_for(event):
                    await self._qq_pager.send_markdown_notice(
                        event,
                        title="翻页",
                        content="没有更多结果了。",
                    )
                    return
                yield event.plain_result("没有更多结果了。")
                return

            action_cn = "收购" if order_type == "buy" else "出售"
            self._wm_pick_cache.put(
                event=event,
                state={
                    "item_name_en": getattr(item, "name", "") or "",
                    "order_type": order_type,
                    "platform": platform_norm,
                    "rows": [
                        {
                            "name": (o.ingame_name or "").strip(),
                            "platinum": int(o.platinum),
                        }
                        for o in top
                    ],
                },
            )

            rendered = await render_wm_orders_image_to_file(
                item=item,
                orders=top,
                platform=platform_norm,
                action_cn=action_cn,
                language=language,
                limit=limit,
            )
            if rendered:
                if self._qq_pager.enabled_for(event):
                    await event.send(event.image_result(rendered.path))
                    await self._qq_pager.send_pager_keyboard(
                        event,
                        kind="/wm",
                        page=page,
                    )
                    return
                yield event.image_result(rendered.path)
                return

            lines = [
                f"{item.get_localized_name(language)}（{platform_norm}）{action_cn} 第{page}页："
            ]
            for idx, o in enumerate(top, start=1):
                status = o.status or "unknown"
                name = o.ingame_name or "unknown"
                lines.append(f"{idx}. {o.platinum}p  {status}  {name}")
            yield event.plain_result("\n".join(lines))
            if self._qq_pager.enabled_for(event):
                await self._qq_pager.send_pager_keyboard(
                    event,
                    kind="/wm",
                    page=page,
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

            auctions = await self.market_client.fetch_riven_auctions(
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

            filtered = [
                a
                for a in auctions
                if a.visible
                and (not a.closed)
                and a.is_direct_sell
                and (a.platform or "").lower() == platform_norm
            ]
            if negative_required and not negative_stats:
                filtered = [
                    a for a in filtered if any((not x.positive) for x in a.attributes)
                ]

            req_pos = set(uniq_lower(positive_stats))
            req_neg = set(uniq_lower(negative_stats))

            def stat_fit_score(a) -> int:
                a_pos = {x.url_name for x in a.attributes if x.positive}
                a_neg = {x.url_name for x in a.attributes if not x.positive}

                score = 0
                score += 10 * len(req_pos & a_pos)
                score += 10 * len(req_neg & a_neg)
                score -= 50 * len(req_pos - a_pos)
                score -= 50 * len(req_neg - a_neg)

                if negative_required and not a_neg:
                    score -= 20

                if req_pos:
                    score -= len(a_pos - req_pos)
                if req_neg:
                    score -= len(a_neg - req_neg)

                if polarity:
                    if (a.polarity or "").strip().lower() == polarity.strip().lower():
                        score += 5
                    else:
                        score -= 5

                if mastery_rank_min is not None and a.mastery_level is not None:
                    score -= abs(int(a.mastery_level) - int(mastery_rank_min))

                return score

            scored: list[tuple[int, object]] = [
                (stat_fit_score(a), a) for a in filtered
            ]
            scored.sort(
                key=lambda x: (
                    -int(x[0]),
                    presence_rank(getattr(x[1], "owner_status", None)),
                    int(getattr(x[1], "buyout_price", 0) or 0),
                    (getattr(x[1], "auction_id", "") or ""),
                )
            )
            ranked = [a for _, a in scored]
            offset = (page - 1) * limit
            picked = ranked[offset : offset + limit]
            if not picked:
                if self._qq_pager.enabled_for(event):
                    await self._qq_pager.send_markdown_notice(
                        event,
                        title="翻页",
                        content="没有更多结果了。",
                    )
                    return
                yield event.plain_result("没有更多结果了。")
                return

            picked.sort(
                key=lambda a: (
                    presence_rank(getattr(a, "owner_status", None)),
                    int(getattr(a, "buyout_price", 0) or 0),
                    (getattr(a, "owner_name", "") or ""),
                )
            )
            top = cast(list, picked)

            def fmt_stats(stats: list[str]) -> str:
                return "+".join([RIVEN_STAT_CN.get(s, s) for s in stats])

            parts: list[str] = []
            if positive_stats:
                parts.append("正:" + fmt_stats(positive_stats))
            if negative_stats:
                parts.append("负:" + fmt_stats(negative_stats))
            elif negative_required:
                parts.append("负:任意")
            if mastery_rank_min is not None:
                parts.append(f"MR≥{mastery_rank_min}")
            if polarity:
                parts.append("极性" + RIVEN_POLARITY_CN.get(polarity, polarity))
            summary = " ".join(parts) if parts else "(无筛选)"

            rendered = await render_wmr_auctions_image_to_file(
                weapon=weapon,
                weapon_display_name=("" if language.startswith("en") else weapon_query),
                auctions=top,
                platform=platform_norm,
                summary=summary,
                limit=len(top),
            )
            if rendered:
                if self._qq_pager.enabled_for(event):
                    await event.send(event.image_result(rendered.path))
                    await self._qq_pager.send_pager_keyboard(
                        event,
                        kind="/wmr",
                        page=page,
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
            if self._qq_pager.enabled_for(event):
                await self._qq_pager.send_pager_keyboard(
                    event,
                    kind="/wmr",
                    page=page,
                )
            return

        yield event.plain_result("当前记录不支持翻页，请重新执行 /wm 或 /wmr。")

    @filter.regex(r"^(上一页|下一页|prev|previous|next)$")
    async def qq_official_webhook_button_page(self, event: AstrMessageEvent):
        """Handle QQ official webhook template buttons.

        Some QQ keyboard templates are configured to send plain text like “上一页/下一页”.
        This handler converts them into /wfp prev|next.
        """

        if not self._qq_pager.enabled_for(event):
            return

        try:
            event.should_call_llm(False)
        except Exception:
            pass

        text = (event.get_message_str() or "").strip().lower()
        direction = "prev" if text in {"上一页", "prev", "previous"} else "next"

        async for res in self.wf_page(event, args=direction):
            yield res

    @filter.regex(r"^\d+$")
    async def wm_pick_number(self, event: AstrMessageEvent):
        """当用户回复 /wm 结果图并只发送数字时，返回对应玩家的 /w 话术。"""

        # 避免进入默认 LLM 链路
        try:
            event.should_call_llm(False)
        except Exception:
            pass

        # 必须是“回复某条消息”的场景，才触发
        comps = event.get_messages() or []
        if not any(isinstance(c, Reply) for c in comps):
            return

        rec = self._wm_pick_cache.get(event)
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

        # 在 sell 列表里，你是向对方“买”；在 buy 列表里，你是向对方“卖”
        verb = "buy" if order_type == "sell" else "sell"
        whisper = (
            f'/w {name} Hi! I want to {verb}: "{item_name_en}" '
            f"for {platinum} platinum. (warframe.market)"
        )
        yield event.plain_result(whisper)

    @filter.command("突击", alias={"sortie"})
    async def wf_sortie(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询今日突击（Sortie）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_sortie(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("警报", alias={"alerts"})
    async def wf_alerts(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询当前警报。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_alerts(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("裂缝", alias={"fissure"})
    async def wf_fissures(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询虚空裂缝：支持 普通/钢铁/九重天（九重天=风暴裂缝）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_fissures(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("九重天裂缝", alias={"风暴裂缝"})
    async def wf_fissures_storm(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/九重天裂缝 = /裂缝 九重天"""
        event.should_call_llm(False)
        result = await worldstate_commands.cmd_fissures_kind(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            fissure_kind="九重天",
        )
        yield result

    @filter.command("钢铁裂缝")
    async def wf_fissures_hard(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/钢铁裂缝 = /裂缝 钢铁"""
        event.should_call_llm(False)
        result = await worldstate_commands.cmd_fissures_kind(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            fissure_kind="钢铁",
        )
        yield result

    @filter.command("普通裂缝")
    async def wf_fissures_normal(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/普通裂缝 = /裂缝 普通"""
        event.should_call_llm(False)
        result = await worldstate_commands.cmd_fissures_kind(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            fissure_kind="普通",
        )
        yield result

    @filter.command("奸商", alias={"虚空商人", "baro"})
    async def wf_void_trader(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询奸商（Baro Ki'Teer / Void Trader）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_void_trader(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("仲裁", alias={"arbitration"})
    async def wf_arbitration(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询仲裁（Arbitration）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_arbitration(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("电波", alias={"夜波", "nightwave"})
    async def wf_nightwave(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询电波（Nightwave）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_nightwave(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("平原")
    async def wf_plains(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询各开放世界平原的当前状态。

        - 无参数：列出所有平原状态（图片输出）
        - 带参数：查询指定平原，例如：/平原 希图斯
        """

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_plains(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("夜灵平原", alias={"希图斯", "cetus", "poe"})
    async def wf_cetus_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询夜灵平原昼夜循环（Cetus Cycle）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="cetus",
        )
        yield result

    @filter.command("魔胎之境", alias={"魔胎", "cambion"})
    async def wf_cambion_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询魔胎之境轮换（Cambion Cycle）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="cambion",
        )
        yield result

    @filter.command("地球昼夜", alias={"地球循环", "地球", "earth"})
    async def wf_earth_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询地球昼夜循环（Earth Cycle）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="earth",
        )
        yield result

    @filter.command(
        "奥布山谷",
        alias={"金星平原", "福尔图娜", "vallis", "orb", "orbvallis", "fortuna"},
    )
    async def wf_vallis_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询奥布山谷温/寒循环（Orb Vallis Cycle）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="vallis",
        )
        yield result

    @filter.command("双衍王境", alias={"双衍", "双衍循环", "双衍王镜", "duviri"})
    async def wf_duviri_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询双衍王境情绪轮换（Duviri Cycle）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="duviri",
        )
        yield result

    @filter.command("轮回奖励", alias={"双衍轮回", "双衍轮回奖励", "circuit"})
    async def wf_duviri_circuit_rewards(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询双衍王境「轮回」奖励轮换（普通/钢铁）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_duviri_circuit_rewards(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("武器", alias={"weapon", "wfweapon"})
    async def wf_weapon(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 PublicExport 查询武器（中文优先，也支持英文/uniqueName 匹配）。用法：/武器 绝路"""

        event.should_call_llm(False)
        result = await public_export_commands.cmd_weapon(
            event=event,
            query=str(args),
            public_export_client=self.public_export_client,
        )
        yield result

    @filter.command("战甲", alias={"warframe", "frame", "wfwarframe"})
    async def wf_warframe(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 PublicExport 查询战甲条目（基础面板信息，字段尽量容错）。用法：/战甲 牛甲"""

        event.should_call_llm(False)
        result = await public_export_commands.cmd_warframe(
            event=event,
            query=str(args),
            public_export_client=self.public_export_client,
        )
        yield result

    @filter.command("MOD", alias={"mod", "模组", "mods"})
    async def wf_mod(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 PublicExport 查询 MOD/升级条目（名称模糊匹配）。用法：/MOD 过载"""

        event.should_call_llm(False)
        result = await public_export_commands.cmd_mod(
            event=event,
            query=str(args),
            public_export_client=self.public_export_client,
        )
        yield result

    @filter.command("掉落", alias={"drop", "drops"})
    async def wf_drops(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 WFCD/warframe-drop-data 查询物品掉落地点。用法：/掉落 <物品> [数量<=30]"""

        event.should_call_llm(False)
        result = await drop_data_commands.cmd_drops(
            event=event,
            raw_args=str(args),
            drop_data_client=self.drop_data_client,
            public_export_client=self.public_export_client,
        )
        yield result

    @filter.command("遗物", alias={"relic", "relics"})
    async def wf_relic(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 WFCD/warframe-drop-data 查询遗物奖池。用法：/遗物 <纪元> <遗物名> 或 /遗物 <遗物名>"""

        event.should_call_llm(False)
        result = await drop_data_commands.cmd_relic(
            event=event,
            raw_args=str(args),
            drop_data_client=self.drop_data_client,
        )
        yield result

    @filter.command("入侵", alias={"invasions"})
    async def wf_invasions(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询当前入侵（Invasions）。用法：/入侵 [平台] [数量<=20]"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_invasions(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("集团", alias={"syndicate", "syndicates"})
    async def wf_syndicates(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询集团任务（Syndicate Missions）。

        用法：
        - /集团
        - /集团 pc
        - /集团 新世间
        - /集团 新世间 pc
        """

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_syndicates(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result
