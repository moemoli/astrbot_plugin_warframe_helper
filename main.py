import re
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Reply
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from .clients.market_client import WarframeMarketClient
from .clients.worldstate_client import WarframeWorldstateClient
from .constants import (
    MARKET_PLATFORM_ALIASES,
    RIVEN_POLARITY_CN,
    RIVEN_STAT_ALIASES,
    RIVEN_STAT_CN,
    WM_BUY_ALIASES,
    WM_SELL_ALIASES,
    WORLDSTATE_PLATFORM_ALIASES,
)
from .helpers import (
    eta_key_zh,
    parse_platform,
    presence_rank,
    split_tokens,
    uniq_lower,
)
from .mappers.riven_mapping import WarframeRivenWeaponMapper
from .mappers.riven_stats_mapping import WarframeRivenStatMapper
from .mappers.term_mapping import WarframeTermMapper
from .renderers.wm_render import render_wm_orders_image_to_file
from .renderers.wmr_render import render_wmr_auctions_image_to_file
from .renderers.worldstate_render import (
    WorldstateRow,
    render_worldstate_rows_image_to_file,
)


@register("warframe_helper", "moemoli", "Warframe 助手", "v0.0.1")
class MyPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self.config = config
        self.term_mapper = WarframeTermMapper()
        self.riven_weapon_mapper = WarframeRivenWeaponMapper()
        self.riven_stat_mapper = WarframeRivenStatMapper()
        self.market_client = WarframeMarketClient()
        self.worldstate_client = WarframeWorldstateClient()

        # 最近一次 /wm 的 TopN 结果缓存（用于“回复图片发数字”快速生成 /w 话术）
        # key = unified_origin + sender_id
        self._wm_pick_cache: dict[str, dict] = {}
        self._wm_pick_cache_ttl_sec: float = 8 * 60

    def _wm_cache_key(self, event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}|{event.get_sender_id()}"

    def _wm_put_pick_cache(
        self,
        *,
        event: AstrMessageEvent,
        item_name_en: str,
        order_type: str,
        platform: str,
        rows,
    ) -> None:
        try:
            self._wm_pick_cache[self._wm_cache_key(event)] = {
                "ts": time.time(),
                "item_name_en": item_name_en,
                "order_type": order_type,
                "platform": platform,
                # rows: list[{"name": str, "platinum": int}]
                "rows": rows,
            }
        except Exception:
            return

    def _wm_get_pick_cache(self, event: AstrMessageEvent) -> dict | None:
        rec = self._wm_pick_cache.get(self._wm_cache_key(event))
        if not isinstance(rec, dict):
            return None
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            return None
        if (time.time() - float(ts)) > self._wm_pick_cache_ttl_sec:
            return None
        return rec

    def _worldstate_platform_from_tokens(self, tokens: list[str]) -> str:
        return parse_platform(tokens, WORLDSTATE_PLATFORM_ALIASES, default="pc")

    def _market_platform_from_tokens(self, tokens: list[str]) -> str:
        return parse_platform(tokens, MARKET_PLATFORM_ALIASES, default="pc")

    def _eta_key(self, s: str) -> int:
        return eta_key_zh(s)

    async def _render_worldstate_single_row(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        platform_norm: str,
        row_title: str,
        row_right: str | None,
        accent: tuple[int, int, int, int],
        plain_text: str,
    ):
        rendered = await render_worldstate_rows_image_to_file(
            title=title,
            header_lines=[f"平台：{platform_norm}"],
            rows=[WorldstateRow(title=row_title, right=row_right)],
            accent=accent,
        )
        if rendered:
            return event.image_result(rendered.path)
        return event.plain_result(plain_text)

    async def _render_fissures_text(
        self, *, platform_norm: str, fissure_kind: str
    ) -> str:
        fissures = await self.worldstate_client.fetch_fissures(
            platform=platform_norm, language="zh"
        )
        if fissures is None:
            return "未获取到裂缝信息（可能是网络限制或接口不可达）。"
        if not fissures:
            return f"当前无裂缝（{platform_norm}）。"

        def pick(f):
            if fissure_kind == "九重天":
                return f.is_storm
            if fissure_kind == "钢铁":
                return f.is_hard
            return (not f.is_storm) and (not f.is_hard)

        picked = [f for f in fissures if pick(f)]
        if not picked:
            return f"当前无{fissure_kind}裂缝（{platform_norm}）。"

        picked.sort(key=lambda x: self._eta_key(x.eta))

        lines: list[str] = [
            f"裂缝（{platform_norm}）{fissure_kind} 共{len(picked)}条："
        ]
        for f in picked:
            enemy = f" | {f.enemy}" if f.enemy else ""
            lines.append(f"- {f.tier} {f.mission_type} - {f.node} | 剩余{f.eta}{enemy}")
        return "\n".join(lines)

    async def _render_fissures_image(self, *, platform_norm: str, fissure_kind: str):
        fissures = await self.worldstate_client.fetch_fissures(
            platform=platform_norm, language="zh"
        )
        if fissures is None:
            return None
        if not fissures:
            return None

        def pick(f):
            if fissure_kind == "九重天":
                return f.is_storm
            if fissure_kind == "钢铁":
                return f.is_hard
            return (not f.is_storm) and (not f.is_hard)

        picked = [f for f in fissures if pick(f)]
        if not picked:
            return None

        picked.sort(key=lambda x: self._eta_key(x.eta))

        def row_accent(f):
            if f.is_hard:
                return (100, 116, 139, 255)
            if f.is_storm:
                return (14, 165, 233, 255)
            return (139, 92, 246, 255)

        rows: list[WorldstateRow] = []
        for f in picked[:18]:
            enemy = f" | {f.enemy}" if f.enemy else ""
            tag = "钢铁" if f.is_hard else ("九重天" if f.is_storm else "普通")
            rows.append(
                WorldstateRow(
                    title=f"{f.tier} {f.mission_type}",
                    subtitle=f"{f.node}{enemy}",
                    right=f"剩余{f.eta}",
                    tag=tag,
                    accent=row_accent(f),
                )
            )

        return await render_worldstate_rows_image_to_file(
            title="裂缝",
            header_lines=[
                f"平台：{platform_norm}",
                f"筛选：{fissure_kind}",
                f"共{len(picked)}条（展示前{min(18, len(picked))}条）",
            ],
            rows=rows,
            accent=(139, 92, 246, 255),
        )

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        await self.term_mapper.initialize()
        await self.riven_weapon_mapper.initialize()
        await self.riven_stat_mapper.initialize()

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
        top = filtered[:limit]

        action_cn = "收购" if order_type == "buy" else "出售"
        if not top:
            yield event.plain_result(
                f"{item.name}（{platform_norm}）暂无可用{action_cn}订单。",
            )
            return

        # 缓存本次 TopN（用于后续“回复图片发数字”）
        self._wm_put_pick_cache(
            event=event,
            item_name_en=item.name,
            order_type=order_type,
            platform=platform_norm,
            rows=[
                {
                    "name": (o.ingame_name or "").strip(),
                    "platinum": int(o.platinum),
                }
                for o in top
            ],
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

        positive_stats: list[str] = []
        negative_stats: list[str] = []
        negative_required = False
        mastery_rank_min: int | None = None
        polarity: str | None = None

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

            # mastery: 12段 / MR12
            m = re.fullmatch(r"mr?(\d{1,2})", t_norm)
            if m:
                mastery_rank_min = int(m.group(1))
                continue
            m = re.fullmatch(r"(\d{1,2})段", t_norm)
            if m:
                mastery_rank_min = int(m.group(1))
                continue

            # polarity: v槽/d槽/-槽/r槽
            m = re.fullmatch(r"([vd\-r])槽", t_norm)
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

            # shorthand: 双暴
            if "双暴" in t_norm or "双爆" in t_norm:
                positive_stats.extend(["critical_chance", "critical_damage"])
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
                    negative_stats.append(RIVEN_STAT_ALIASES[key])
                continue

            # explicit positive stat tokens
            if t_norm in RIVEN_STAT_ALIASES:
                positive_stats.append(RIVEN_STAT_ALIASES[t_norm])
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

        filtered.sort(
            key=lambda a: (
                presence_rank(a.owner_status),
                a.buyout_price,
                (a.owner_name or ""),
            )
        )

        limit = max(1, min(int(limit), 20))
        top = filtered[:limit]
        if not top:
            yield event.plain_result("没有符合条件的一口价紫卡拍卖。")
            return

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
            auctions=top,
            platform=platform_norm,
            summary=summary,
            limit=len(top),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        # fallback text
        lines = [f"紫卡 {weapon.item_name}（{platform_norm}）{summary} 前{len(top)}："]
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

        rec = self._wm_get_pick_cache(event)
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

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""

    @filter.command("突击", alias={"sortie"})
    async def wf_sortie(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询今日突击（Sortie）。"""

        event.should_call_llm(False)

        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_sortie(
            platform=platform_norm, language="zh"
        )
        if not info:
            yield event.plain_result("未获取到突击信息（可能是网络限制或接口不可达）。")
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
            title="突击",
            header_lines=header_lines,
            rows=rows,
            accent=(59, 130, 246, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        head_parts: list[str] = [f"突击（{platform_norm}）"]
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

    @filter.command("警报", alias={"alerts"})
    async def wf_alerts(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询当前警报。"""

        event.should_call_llm(False)

        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        alerts = await self.worldstate_client.fetch_alerts(
            platform=platform_norm, language="zh"
        )
        if alerts is None:
            yield event.plain_result("未获取到警报信息（可能是网络限制或接口不可达）。")
            return
        if not alerts:
            yield event.plain_result(f"当前无警报（{platform_norm}）。")
            return

        rows: list[WorldstateRow] = []
        for a in alerts[:20]:
            lvl = ""
            if a.min_level is not None and a.max_level is not None:
                lvl = f" Lv{a.min_level}-{a.max_level}"
            sub_parts = []
            if a.faction:
                sub_parts.append(str(a.faction))
            if a.reward:
                sub_parts.append(str(a.reward))
            subtitle = " | ".join(sub_parts) if sub_parts else None
            rows.append(
                WorldstateRow(
                    title=f"{a.mission_type} - {a.node}{lvl}",
                    subtitle=subtitle,
                    right=f"剩余{a.eta}",
                )
            )

        rendered = await render_worldstate_rows_image_to_file(
            title="警报",
            header_lines=[
                f"平台：{platform_norm}",
                f"共{len(alerts)}条（展示前{min(20, len(alerts))}条）",
            ],
            rows=rows,
            accent=(245, 158, 11, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        lines: list[str] = [f"警报（{platform_norm}）共{len(alerts)}条："]
        for a in alerts:
            lvl = ""
            if a.min_level is not None and a.max_level is not None:
                lvl = f" Lv{a.min_level}-{a.max_level}"
            rew = f" | {a.reward}" if a.reward else ""
            fac = f" | {a.faction}" if a.faction else ""
            lines.append(f"- {a.mission_type} {a.node}{lvl} | 剩余{a.eta}{fac}{rew}")

        yield event.plain_result("\n".join(lines))

    @filter.command("裂缝", alias={"fissure"})
    async def wf_fissures(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询虚空裂缝：支持 普通/钢铁/九重天（九重天=风暴裂缝）。"""

        event.should_call_llm(False)

        tokens = split_tokens(str(args))
        fissure_kind = "普通"  # 普通/钢铁/九重天
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        for t in tokens:
            t2 = str(t).strip().lower()
            if t2 in {"九重天", "九重", "风暴", "storm"}:
                fissure_kind = "九重天"
                continue
            if t2 in {"钢铁", "钢", "sp", "steel"}:
                fissure_kind = "钢铁"
                continue
            if t2 in {"普通", "正常", "normal"}:
                fissure_kind = "普通"
                continue

        rendered = await self._render_fissures_image(
            platform_norm=platform_norm, fissure_kind=fissure_kind
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        text = await self._render_fissures_text(
            platform_norm=platform_norm, fissure_kind=fissure_kind
        )
        yield event.plain_result(text)

    @filter.command("九重天裂缝", alias={"风暴裂缝"})
    async def wf_fissures_storm(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/九重天裂缝 = /裂缝 九重天"""
        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)
        rendered = await self._render_fissures_image(
            platform_norm=platform_norm, fissure_kind="九重天"
        )
        if rendered:
            yield event.image_result(rendered.path)
            return
        text = await self._render_fissures_text(
            platform_norm=platform_norm, fissure_kind="九重天"
        )
        yield event.plain_result(text)

    @filter.command("钢铁裂缝")
    async def wf_fissures_hard(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/钢铁裂缝 = /裂缝 钢铁"""
        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)
        rendered = await self._render_fissures_image(
            platform_norm=platform_norm, fissure_kind="钢铁"
        )
        if rendered:
            yield event.image_result(rendered.path)
            return
        text = await self._render_fissures_text(
            platform_norm=platform_norm, fissure_kind="钢铁"
        )
        yield event.plain_result(text)

    @filter.command("普通裂缝")
    async def wf_fissures_normal(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/普通裂缝 = /裂缝 普通"""
        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)
        rendered = await self._render_fissures_image(
            platform_norm=platform_norm, fissure_kind="普通"
        )
        if rendered:
            yield event.image_result(rendered.path)
            return
        text = await self._render_fissures_text(
            platform_norm=platform_norm, fissure_kind="普通"
        )
        yield event.plain_result(text)

    @filter.command("奸商", alias={"虚空商人", "baro"})
    async def wf_void_trader(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询奸商（Baro Ki'Teer / Void Trader）。"""

        event.should_call_llm(False)

        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_void_trader(
            platform=platform_norm, language="zh"
        )
        if info is None:
            yield event.plain_result("未获取到奸商信息（可能是网络限制或接口不可达）。")
            return

        if not info.active:
            yield event.plain_result(f"奸商未到访（{platform_norm}），预计{info.eta}。")
            return

        rows: list[WorldstateRow] = []
        if info.inventory:
            for it in info.inventory[:30]:
                price = []
                if it.ducats is not None:
                    price.append(f"{it.ducats}D")
                if it.credits is not None:
                    price.append(f"{it.credits}CR")
                rows.append(
                    WorldstateRow(
                        title=it.item, right=" / ".join(price) if price else None
                    )
                )
        else:
            rows.append(WorldstateRow(title="(未返回商品清单)"))

        rendered = await render_worldstate_rows_image_to_file(
            title="奸商",
            header_lines=[
                f"平台：{platform_norm}",
                f"地点：{info.location or '未知'}",
                f"剩余：{info.eta}",
            ],
            rows=rows,
            accent=(14, 165, 233, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        lines: list[str] = [
            f"奸商（{platform_norm}）已到访：",
            f"- 地点：{info.location or '未知'}",
            f"- 剩余：{info.eta}",
        ]
        if not info.inventory:
            lines.append("- (未返回商品清单)")
            yield event.plain_result("\n".join(lines))
            return

        for it in info.inventory[:30]:
            price = []
            if it.ducats is not None:
                price.append(f"{it.ducats}D")
            if it.credits is not None:
                price.append(f"{it.credits}CR")
            p = " / ".join(price)
            lines.append(f"- {it.item}{(' | ' + p) if p else ''}")

        yield event.plain_result("\n".join(lines))

    @filter.command("仲裁", alias={"arbitration"})
    async def wf_arbitration(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询仲裁（Arbitration）。"""

        event.should_call_llm(False)

        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_arbitration(
            platform=platform_norm, language="zh"
        )
        if info is None:
            yield event.plain_result("未获取到仲裁信息（可能是网络限制或接口不可达）。")
            return

        rendered = await render_worldstate_rows_image_to_file(
            title="仲裁",
            header_lines=[f"平台：{platform_norm}"],
            rows=[
                WorldstateRow(
                    title=f"{info.mission_type} - {info.node}",
                    subtitle=info.enemy or None,
                    right=f"剩余{info.eta}",
                )
            ],
            accent=(100, 116, 139, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        enemy = f" | {info.enemy}" if info.enemy else ""
        yield event.plain_result(
            f"仲裁（{platform_norm}）\n- {info.mission_type} - {info.node}{enemy}\n- 剩余{info.eta}",
        )

    @filter.command("电波", alias={"夜波", "nightwave"})
    async def wf_nightwave(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询电波（Nightwave）。"""

        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_nightwave(
            platform=platform_norm, language="zh"
        )
        if info is None:
            yield event.plain_result("未获取到电波信息（可能是网络限制或接口不可达）。")
            return

        title = f"电波（{platform_norm}）"
        if info.season is not None:
            title += f" S{info.season}"
        if info.phase is not None:
            title += f" P{info.phase}"

        lines: list[str] = [f"{title}\n- 剩余{info.eta}"]
        if not info.active_challenges:
            lines.append("- (未返回挑战列表)")
            rendered = await render_worldstate_rows_image_to_file(
                title="电波",
                header_lines=[f"平台：{platform_norm}", f"剩余：{info.eta}"],
                rows=[WorldstateRow(title="(未返回挑战列表)")],
                accent=(124, 58, 237, 255),
            )
            if rendered:
                yield event.image_result(rendered.path)
                return
            yield event.plain_result("\n".join(lines))
            return

        rows: list[WorldstateRow] = []
        for c in info.active_challenges[:12]:
            kind = "日常" if c.is_daily else "周常"
            rep = f"+{c.reputation}" if c.reputation is not None else ""
            rows.append(
                WorldstateRow(
                    title=c.title,
                    subtitle=rep or None,
                    right=f"剩余{c.eta}",
                    tag=kind,
                )
            )

        rendered = await render_worldstate_rows_image_to_file(
            title="电波",
            header_lines=[f"平台：{platform_norm}", f"剩余：{info.eta}"],
            rows=rows,
            accent=(124, 58, 237, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        for c in info.active_challenges[:12]:
            kind = "日常" if c.is_daily else "周常"
            rep = f" +{c.reputation}" if c.reputation is not None else ""
            lines.append(f"- [{kind}] {c.title}{rep} | 剩余{c.eta}")

        yield event.plain_result("\n".join(lines))

    @filter.command("夜灵平原", alias={"平原", "希图斯", "cetus", "poe"})
    async def wf_cetus_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询夜灵平原昼夜循环（Cetus Cycle）。"""

        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_cetus_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            yield event.plain_result(
                "未获取到夜灵平原信息（可能是网络限制或接口不可达）。"
            )
            return

        if info.is_day is True:
            state_cn = "白天"
        elif info.is_day is False:
            state_cn = "夜晚"
        else:
            state_cn = info.state or "未知"

        left = info.time_left or info.eta
        yield await self._render_worldstate_single_row(
            event,
            title="夜灵平原",
            platform_norm=platform_norm,
            row_title=f"当前：{state_cn}",
            row_right=f"剩余{left}",
            accent=(20, 184, 166, 255),
            plain_text=f"夜灵平原（{platform_norm}）当前：{state_cn} | 剩余{left}",
        )

    @filter.command("魔胎之境", alias={"魔胎", "cambion"})
    async def wf_cambion_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询魔胎之境轮换（Cambion Cycle）。"""

        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_cambion_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            yield event.plain_result(
                "未获取到魔胎之境信息（可能是网络限制或接口不可达）。"
            )
            return

        active = (info.active or "").strip().lower()
        if active == "fass":
            state_cn = "法斯"
        elif active == "vome":
            state_cn = "沃姆"
        else:
            state_cn = info.active or info.state or "未知"

        left = info.time_left or info.eta
        yield await self._render_worldstate_single_row(
            event,
            title="魔胎之境",
            platform_norm=platform_norm,
            row_title=f"当前：{state_cn}",
            row_right=f"剩余{left}",
            accent=(20, 184, 166, 255),
            plain_text=f"魔胎之境（{platform_norm}）当前：{state_cn} | 剩余{left}",
        )

    @filter.command("地球昼夜", alias={"地球循环", "地球", "earth"})
    async def wf_earth_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询地球昼夜循环（Earth Cycle）。"""

        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_earth_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            yield event.plain_result(
                "未获取到地球循环信息（可能是网络限制或接口不可达）。"
            )
            return

        if info.is_day is True:
            state_cn = "白天"
        elif info.is_day is False:
            state_cn = "夜晚"
        else:
            state_cn = info.state or "未知"

        left = info.time_left or info.eta
        yield await self._render_worldstate_single_row(
            event,
            title="地球昼夜",
            platform_norm=platform_norm,
            row_title=f"当前：{state_cn}",
            row_right=f"剩余{left}",
            accent=(20, 184, 166, 255),
            plain_text=f"地球（{platform_norm}）当前：{state_cn} | 剩余{left}",
        )

    @filter.command(
        "奥布山谷",
        alias={"金星平原", "福尔图娜", "vallis", "orb", "orbvallis", "fortuna"},
    )
    async def wf_vallis_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询奥布山谷温/寒循环（Orb Vallis Cycle）。"""

        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_vallis_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            yield event.plain_result(
                "未获取到奥布山谷信息（可能是网络限制或接口不可达）。"
            )
            return

        if info.is_warm is True:
            state_cn = "温暖"
        elif info.is_warm is False:
            state_cn = "寒冷"
        else:
            raw = (info.state or "").strip().lower()
            if raw == "warm":
                state_cn = "温暖"
            elif raw == "cold":
                state_cn = "寒冷"
            else:
                state_cn = info.state or "未知"

        left = info.time_left or info.eta
        yield await self._render_worldstate_single_row(
            event,
            title="奥布山谷",
            platform_norm=platform_norm,
            row_title=f"当前：{state_cn}",
            row_right=f"剩余{left}",
            accent=(20, 184, 166, 255),
            plain_text=f"奥布山谷（{platform_norm}）当前：{state_cn} | 剩余{left}",
        )

    @filter.command("双衍王境", alias={"双衍", "双衍循环", "duviri"})
    async def wf_duviri_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询双衍王境情绪轮换（Duviri Cycle）。"""

        event.should_call_llm(False)
        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        info = await self.worldstate_client.fetch_duviri_cycle(
            platform=platform_norm, language="zh"
        )
        if info is None:
            yield event.plain_result(
                "未获取到双衍王境信息（可能是网络限制或接口不可达）。"
            )
            return

        state = (info.state or "未知").strip()
        left = info.time_left or info.eta
        yield await self._render_worldstate_single_row(
            event,
            title="双衍王境",
            platform_norm=platform_norm,
            row_title=f"当前：{state}",
            row_right=f"剩余{left}",
            accent=(20, 184, 166, 255),
            plain_text=f"双衍王境（{platform_norm}）当前：{state} | 剩余{left}",
        )

    @filter.command("入侵", alias={"invasions"})
    async def wf_invasions(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询当前入侵（Invasions）。用法：/入侵 [平台] [数量<=20]"""

        event.should_call_llm(False)

        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        limit = 10
        for t in tokens:
            if str(t).isdigit():
                limit = int(str(t))
                break
        limit = max(1, min(limit, 20))

        inv = await self.worldstate_client.fetch_invasions(
            platform=platform_norm, language="zh"
        )
        if inv is None:
            yield event.plain_result("未获取到入侵信息（可能是网络限制或接口不可达）。")
            return
        if not inv:
            yield event.plain_result(f"当前无入侵（{platform_norm}）。")
            return

        inv.sort(key=lambda x: (self._eta_key(x.eta), -(x.completion or 0.0)))

        rows: list[WorldstateRow] = []
        for i in inv[:limit]:
            sides = (
                " vs ".join([x for x in [i.attacker, i.defender] if x]) or "未知阵营"
            )
            comp = f"进度{i.completion:.0f}%" if i.completion is not None else ""
            subtitle_parts = [p for p in [comp, i.reward] if p]
            rows.append(
                WorldstateRow(
                    title=f"{i.node} | {sides}",
                    subtitle=" | ".join(subtitle_parts) if subtitle_parts else None,
                    right=f"剩余{i.eta}",
                )
            )

        rendered = await render_worldstate_rows_image_to_file(
            title="入侵",
            header_lines=[f"平台：{platform_norm}", f"展示前{min(limit, len(inv))}条"],
            rows=rows,
            accent=(249, 115, 22, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        lines: list[str] = [f"入侵（{platform_norm}）前{min(limit, len(inv))}条："]
        for i in inv[:limit]:
            sides = (
                " vs ".join([x for x in [i.attacker, i.defender] if x]) or "未知阵营"
            )
            comp = f" | 进度{i.completion:.0f}%" if i.completion is not None else ""
            rew = f" | {i.reward}" if i.reward else ""
            lines.append(f"- {i.node} | {sides} | 剩余{i.eta}{comp}{rew}")

        yield event.plain_result("\n".join(lines))

    @filter.command("集团", alias={"syndicate", "syndicates"})
    async def wf_syndicates(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询集团任务（Syndicate Missions）。"""

        event.should_call_llm(False)

        tokens = split_tokens(str(args))
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        syndicates = await self.worldstate_client.fetch_syndicates(
            platform=platform_norm, language="zh"
        )
        if syndicates is None:
            yield event.plain_result(
                "未获取到集团任务信息（可能是网络限制或接口不可达）。"
            )
            return
        if not syndicates:
            yield event.plain_result(f"当前无集团任务（{platform_norm}）。")
            return

        rows: list[WorldstateRow] = []
        for s in syndicates[:10]:
            jobs = []
            for j in s.jobs[:3]:
                node = j.node or "?"
                mtype = j.mission_type or "?"
                jobs.append(f"{mtype}-{node}")
            subtitle = " | ".join(jobs) if jobs else None
            rows.append(
                WorldstateRow(title=s.name, subtitle=subtitle, right=f"剩余{s.eta}")
            )

        rendered = await render_worldstate_rows_image_to_file(
            title="集团任务",
            header_lines=[
                f"平台：{platform_norm}",
                f"共{len(syndicates)}组（展示前{min(10, len(syndicates))}组）",
            ],
            rows=rows,
            accent=(16, 185, 129, 255),
        )
        if rendered:
            yield event.image_result(rendered.path)
            return

        lines: list[str] = [f"集团任务（{platform_norm}）共{len(syndicates)}组："]
        for s in syndicates:
            lines.append(f"- {s.name} | 剩余{s.eta}")
            if not s.jobs:
                continue
            for j in s.jobs[:3]:
                node = j.node or "?"
                mtype = j.mission_type or "?"
                lines.append(f"  - {mtype} - {node}")

        yield event.plain_result("\n".join(lines))
