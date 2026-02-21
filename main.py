from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from .clients.drop_data_client import DropDataClient
from .clients.market_client import WarframeMarketClient
from .clients.public_export_client import PublicExportClient
from .clients.worldstate_client import WarframeWorldstateClient
from .components.event_ttl_cache import EventScopedTTLCache
from .components.qq_official_webhook import QQOfficialWebhookPager
from .handlers.qq_interaction import handle_qq_interaction_create
from .handlers.wm_pick import handle_wm_pick_number
from .http_utils import set_proxy_url
from .mappers.riven_mapping import WarframeRivenWeaponMapper
from .mappers.riven_stats_mapping import WarframeRivenStatMapper
from .mappers.term_mapping import WarframeTermMapper
from .renderers.worldstate_render import (
    WorldstateRow,
    render_worldstate_rows_image_to_file,
)
from .services import drop_data_commands, public_export_commands, worldstate_commands
from .services.market.pager import cmd_wfp
from .services.market.wm import cmd_wm
from .services.market.wmr import cmd_wmr
from .services.subscriptions import SubscriptionService

QQ_OFFICIAL_WEBHOOK_PAGER_TEMPLATE_ID_DEFAULT = ""


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

        # Current schema stores QQ official webhook settings under `sub_config`.
        sub_config: object = {}
        try:
            sub_config = (self.config or {}).get("qq_official") or {}
        except Exception:
            sub_config = {}

        enable_md = False
        keyboard_tpl = ""
        markdown_tpl = ""
        if isinstance(sub_config, dict):
            enable_md = bool(
                sub_config.get("webhook_enable_markdown_reply")
            )
            keyboard_tpl = str(
                sub_config.get("webhook_pager_keyboard_template_id") or ""
            ).strip()
            markdown_tpl = str(
                sub_config.get("webhook_markdown_template_id") or ""
            ).strip()

        # QQ official webhook keyboard template id (message buttons).
        # Note: QQ button templates do NOT support variables.
        qq_tpl = keyboard_tpl or QQ_OFFICIAL_WEBHOOK_PAGER_TEMPLATE_ID_DEFAULT

        self._qq_pager = QQOfficialWebhookPager(
            keyboard_template_id=qq_tpl,
            markdown_template_id=markdown_tpl,
            enable_markdown_reply=enable_md,
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
        await handle_qq_interaction_create(
            bot=bot,
            interaction=interaction,
            qq_pager=self._qq_pager,
            pager_cache=self._pager_cache,
            market_client=self.market_client,
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
        result = await worldstate_commands.cmd_archon_hunt(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

    @filter.command("钢铁奖励", alias={"steelreward", "sp奖励"})
    async def wf_steel_reward(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询钢铁之路当前奖励轮换（Steel Path）。"""

        event.should_call_llm(False)
        result = await worldstate_commands.cmd_steel_path_reward(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        yield result

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
        async for res in cmd_wm(
            context=self.context,
            event=event,
            raw_args=str(args),
            config=self.config,
            term_mapper=self.term_mapper,
            market_client=self.market_client,
            pager_cache=self._pager_cache,
            wm_pick_cache=self._wm_pick_cache,
            qq_pager=self._qq_pager,
        ):
            yield res

    @filter.command("wmr")
    async def wmr(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询 warframe.market 紫卡（Riven）一口价拍卖。

        示例：/wmr 绝路 双暴 负任意 12段 r槽
        语义：武器=绝路，正面=暴击率+暴击伤害，负面任意（但需要有负面），MR>=12，极性=R(zenurik)
        """
        async for res in cmd_wmr(
            context=self.context,
            event=event,
            raw_args=str(args),
            config=self.config,
            market_client=self.market_client,
            riven_weapon_mapper=self.riven_weapon_mapper,
            riven_stat_mapper=self.riven_stat_mapper,
            pager_cache=self._pager_cache,
            qq_pager=self._qq_pager,
        ):
            yield res

    @filter.command("wfp")
    async def wf_page(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """Pagination helper for /wm and /wmr.

        Usage:
        - /wfp prev
        - /wfp next

        Designed to be used by QQ official webhook "command" buttons.
        """
        async for res in cmd_wfp(
            event=event,
            raw_args=str(args),
            pager_cache=self._pager_cache,
            wm_pick_cache=self._wm_pick_cache,
            market_client=self.market_client,
            qq_pager=self._qq_pager,
        ):
            yield res

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

        async for res in cmd_wfp(
            event=event,
            raw_args=direction,
            pager_cache=self._pager_cache,
            wm_pick_cache=self._wm_pick_cache,
            market_client=self.market_client,
            qq_pager=self._qq_pager,
        ):
            yield res

    @filter.regex(r"^\d+$")
    async def wm_pick_number(self, event: AstrMessageEvent):
        """当用户回复 /wm 结果图并只发送数字时，返回对应玩家的 /w 话术。"""
        async for res in handle_wm_pick_number(
            event=event,
            wm_pick_cache=self._wm_pick_cache,
        ):
            yield res

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
