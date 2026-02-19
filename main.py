import re

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.star.filter.command import GreedyStr

from .market_client import WarframeMarketClient
from .term_mapping import WarframeTermMapper
from .wm_render import render_wm_orders_image_to_file

@register("warframe_helper", "moemoli", "Warframe 助手", "v0.0.1")
class MyPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self.config = config
        self.term_mapper = WarframeTermMapper()
        self.market_client = WarframeMarketClient()

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        await self.term_mapper.initialize()

    # 注册指令的装饰器。指令名为 helloworld。注册成功后，发送 `/helloworld` 就会触发这个指令，并回复 `你好, {user_name}!`
    @filter.command("helloworld")
    async def helloworld(self, event: AstrMessageEvent):
        """这是一个 hello world 指令""" # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        user_name = event.get_sender_name()
        message_str = event.message_str # 用户发的纯文本消息字符串
        message_chain = event.get_messages() # 用户所发的消息的消息链 # from astrbot.api.message_components import *
        logger.info(message_chain)
        yield event.plain_result(f"Hello, {user_name}, 你发了 {message_str}!") # 发送一条纯文本消息

    @filter.command("wfmap", alias={"wf映射"})
    async def wfmap(self, event: AstrMessageEvent, query: str = ""):
        """将常用简写/别名映射为 warframe.market 官方词条（例如：猴p -> Wukong Prime Set）"""
        query = (query or "").strip()
        if not query:
            yield event.plain_result("用法：/wfmap 猴p")
            return

        item = await self.term_mapper.resolve_with_ai(
            context=self.context,
            event=event,
            query=query,
            provider_id=(self.config.get("unknown_abbrev_provider_id") if self.config else ""),
        )
        if not item:
            yield event.plain_result(f"未找到可映射的词条：{query}")
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

        tokens = [t for t in re.split(r"\s+", arg_text) if t]
        if not tokens:
            yield event.plain_result(
                "用法：/wm <物品> [平台] [收/卖] [语言] [数量] 例如：/wm 猴p pc 收 zh 10",
            )
            return

        query = tokens[0]
        rest = tokens[1:]

        platform_map = {
            "pc": "pc",
            "电脑": "pc",
            "ps": "ps4",
            "ps4": "ps4",
            "ps5": "ps4",
            "xbox": "xbox",
            "xb": "xbox",
            "ns": "switch",
            "switch": "switch",
        }
        buy_alias = {"收", "买", "buy", "b"}
        sell_alias = {"出", "卖", "sell", "s"}

        platform_norm = "pc"
        order_type = "sell"
        language = "zh"
        limit = 10

        for t in rest:
            t_norm = str(t).strip().lower()
            if not t_norm:
                continue
            if t_norm in platform_map:
                platform_norm = platform_map[t_norm]
                continue
            if t_norm in platform_map.values():
                platform_norm = t_norm
                continue
            if t_norm in buy_alias:
                order_type = "buy"
                continue
            if t_norm in sell_alias:
                order_type = "sell"
                continue
            if t_norm.isdigit():
                limit = int(t_norm)
                continue
            if re.fullmatch(r"[a-z]{2}([\-_][a-z]{2,8})?", t_norm):
                language = t_norm.replace("_", "-")
                continue

        provider_id = self.config.get("unknown_abbrev_provider_id") if self.config else ""
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
        filtered.sort(key=lambda o: o.platinum)
        limit = max(1, min(int(limit), 20))
        top = filtered[:limit]

        action_cn = "收购" if order_type == "buy" else "出售"
        if not top:
            yield event.plain_result(
                f"{item.name}（{platform_norm}）暂无可用{action_cn}订单。",
            )
            return

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
        lines = [f"{item.get_localized_name(language)}（{platform_norm}）{action_cn} 低->高 前{len(top)}："]
        for idx, o in enumerate(top, start=1):
            status = o.status or "unknown"
            name = o.ingame_name or "unknown"
            lines.append(f"{idx}. {o.platinum}p  {status}  {name}")
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
