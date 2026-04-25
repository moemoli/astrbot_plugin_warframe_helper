import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .clients.drop_data_client import DropDataClient
from .clients.market_client import WarframeMarketClient
from .clients.public_export_client import PublicExportClient
from .clients.worldstate_client import WarframeWorldstateClient
from .components.event_ttl_cache import EventScopedTTLCache
from .components.qq_official_webhook import QQOfficialWebhookPager
from .handlers.qq_interaction import handle_qq_interaction_create
from .handlers.wm_pick import handle_wm_pick_number
from .helpers import split_tokens
from .http_utils import set_direct_domains, set_proxy_url
from .mappers.riven_mapping import WarframeRivenWeaponMapper
from .mappers.riven_stats_mapping import WarframeRivenStatMapper
from .mappers.term_mapping import WarframeTermMapper
from .renderers.html_snapshot import (
    configure_image_cache,
    start_playwright_runtime_prepare,
)
from .renderers.template_loader import (
    has_render_template_name,
    list_available_render_template_names,
    set_current_render_command,
    set_current_render_template_name,
    set_render_template_name,
)
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
_DEBUG_LOGGING_ENABLED = False
_RENDER_TEMPLATE_RESOLVER: Callable[[AstrMessageEvent], str | None] | None = None


def set_debug_logging_enabled(enabled: bool) -> None:
    global _DEBUG_LOGGING_ENABLED
    _DEBUG_LOGGING_ENABLED = bool(enabled)


def set_render_template_resolver(
    resolver: Callable[[AstrMessageEvent], str | None] | None,
) -> None:
    global _RENDER_TEMPLATE_RESOLVER
    _RENDER_TEMPLATE_RESOLVER = resolver


def _apply_render_template_for_event(event: AstrMessageEvent) -> None:
    name: str | None = None
    resolver = _RENDER_TEMPLATE_RESOLVER
    if resolver:
        try:
            name = resolver(event)
        except Exception:
            name = None
    set_current_render_template_name(name)


@dataclass(frozen=True, slots=True)
class QQWebhookConfig:
    enable_markdown: bool
    keyboard_template_id: str
    markdown_template_id: str
    public_base_url: str
    debug_logging: bool


def _safe_disable_llm(event: AstrMessageEvent, *, reason: str) -> None:
    _apply_render_template_for_event(event)

    if reason.startswith("/"):
        set_current_render_command(reason)

    if _DEBUG_LOGGING_ENABLED:
        try:
            text = (event.get_message_str() or "").strip()
        except Exception:
            text = ""
        logger.info(
            " | ".join(
                [
                    "[WFHelperDebug] command_dispatch",
                    f"reason={reason}",
                    f"sender={event.get_sender_id()}",
                    f"session={event.session_id}",
                    f"wake={getattr(event, 'is_wake', False)}",
                    f"wake_cmd={getattr(event, 'is_at_or_wake_command', False)}",
                    f"text={text[:180]}",
                ]
            )
        )

    try:
        event.should_call_llm(True)
    except Exception as exc:
        logger.debug(f"Failed to disable LLM for {reason}: {exc!s}")


def _apply_proxy_config(config: dict | None) -> None:
    try:
        proxy_url = (config or {}).get("proxy_url") if config else None
        set_proxy_url(proxy_url)

        direct_domains = None
        if config:
            direct_domains = config.get("direct_domains")
        if isinstance(direct_domains, list):
            set_direct_domains(direct_domains)
        else:
            set_direct_domains(None)
    except Exception as exc:
        logger.warning(f"Proxy config setup failed, fallback to defaults: {exc!s}")
        set_proxy_url(None)
        set_direct_domains(None)


def _parse_qq_webhook_config(config: dict | None) -> QQWebhookConfig:
    sub_config: object = {}
    try:
        sub_config = (config or {}).get("qq_official") or {}
    except Exception as exc:
        logger.debug(f"QQ webhook config parse failed: {exc!s}")
        sub_config = {}

    enable_md = False
    keyboard_tpl = ""
    markdown_tpl = ""
    public_base_url = ""
    debug_logging = False
    if isinstance(sub_config, dict):
        enable_md = bool(sub_config.get("webhook_enable_markdown_reply"))
        keyboard_tpl = str(
            sub_config.get("webhook_pager_keyboard_template_id") or ""
        ).strip()
        markdown_tpl = str(sub_config.get("webhook_markdown_template_id") or "").strip()
        public_base_url = str(sub_config.get("webhook_public_base_url") or "").strip()
        debug_logging = bool(sub_config.get("debug_logging"))

    return QQWebhookConfig(
        enable_markdown=enable_md,
        keyboard_template_id=keyboard_tpl,
        markdown_template_id=markdown_tpl,
        public_base_url=public_base_url,
        debug_logging=debug_logging,
    )


def _parse_warframestat_bases(config: dict | None) -> tuple[list[str], list[str]]:
    def as_url_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            s = str(item or "").strip()
            if not s:
                continue
            if s not in out:
                out.append(s)
        return out

    cfg = config or {}
    api_bases = as_url_list(cfg.get("warframestat_api_bases"))
    proxy_bases = as_url_list(cfg.get("warframestat_proxy_bases"))
    return api_bases, proxy_bases


def _parse_render_template_name(config: dict | None) -> str:
    cfg = config or {}
    name = str(cfg.get("render_template_name") or "").strip()
    return name or "default"


def _parse_enable_no_prefix_commands(config: dict | None) -> bool:
    cfg = config or {}
    return bool(cfg.get("enable_no_prefix_commands"))


def _parse_debug_logging(config: dict | None) -> bool:
    cfg = config or {}
    return bool(cfg.get("debug_logging"))


def _parse_image_cache_config(config: dict | None) -> str:
    cfg = config or {}
    cache_dir = str(cfg.get("image_cache_dir") or "").strip()
    return cache_dir


def _convert_wm_args_to_wmr(raw_args: str) -> str | None:
    tokens = split_tokens(str(raw_args or "").strip())
    if not tokens:
        return None

    converted: list[str] = []
    is_riven_query = False
    for token in tokens:
        t = str(token or "").strip()
        if not t:
            continue

        t_lower = t.lower()
        if t in {"紫卡", "裂罅", "裂罅mod"} or t_lower in {"riven", "rivenmod"}:
            is_riven_query = True
            continue

        t_clean = t.replace("紫卡", "").replace("裂罅", "")
        t_clean = re.sub(r"(?i)riven", "", t_clean)
        if t_clean != t:
            is_riven_query = True
        t_clean = t_clean.strip()
        if not t_clean:
            continue

        converted.append(t_clean)

    if not is_riven_query:
        return None
    return " ".join(converted)


def _clear_plugin_image_cache() -> dict[str, int | str]:
    removed = 0
    failed = 0

    try:
        temp_dir = Path(get_astrbot_temp_path())
    except Exception as exc:
        return {
            "removed": 0,
            "failed": 0,
            "message": f"无法获取临时目录: {exc!s}",
        }

    if not temp_dir.exists() or not temp_dir.is_dir():
        return {"removed": 0, "failed": 0, "message": "临时目录不存在"}

    def should_remove(name: str) -> bool:
        n = (name or "").strip().lower()
        if not n:
            return False
        if n == "wf_helper_blank_1x1.png":
            return True
        if n.startswith("wf_worldstate_") and n.endswith(".png"):
            return True
        if n.startswith("wmr_") and n.endswith(".png"):
            return True
        if n.startswith("wm_") and n.endswith(".png"):
            return True
        return False

    for child in temp_dir.iterdir():
        if not child.is_file():
            continue
        if not should_remove(child.name):
            continue
        try:
            child.unlink(missing_ok=True)
            removed += 1
        except Exception:
            failed += 1

    return {"removed": removed, "failed": failed, "message": "ok"}


class QQResultDispatcher:
    def __init__(self, qq_pager: QQOfficialWebhookPager) -> None:
        self._qq_pager = qq_pager

    def extract_plain_from_result(self, result) -> str:
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return ""
        parts: list[str] = []
        for comp in chain:
            if isinstance(comp, Plain):
                parts.append(str(getattr(comp, "text", "") or ""))
        return "".join(parts).strip()

    def extract_image_path_from_result(self, result) -> str:
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return ""
        return self.extract_image_path_from_chain(chain)

    def extract_image_path_from_chain(self, chain: Sequence[object]) -> str:
        if not chain:
            return ""
        for comp in chain:
            if not isinstance(comp, Image):
                continue
            path = str(getattr(comp, "path", "") or "").strip()
            if path:
                return path
            file_url = str(getattr(comp, "file", "") or "").strip()
            if file_url.startswith("file:///"):
                return file_url[8:]
        return ""

    async def try_send_markdown_for_result(
        self,
        *,
        event: AstrMessageEvent,
        result,
        title: str,
        kind: str,
        prefer_keyboard: bool = False,
        page: int = 1,
        hint: str = "使用下方按钮：上一页 / 下一页",
    ) -> bool:
        if not self._qq_pager.enabled_for(event):
            return False

        image_path = self.extract_image_path_from_result(result)
        if image_path:
            if prefer_keyboard and self._qq_pager.keyboard_enabled_for(event):
                return await self._qq_pager.send_result_markdown_with_keyboard(
                    event,
                    kind=kind,
                    page=page,
                    image_path=image_path,
                    title=title,
                    hint=hint,
                )
            return await self._qq_pager.send_result_markdown_no_keyboard(
                event,
                kind=kind,
                image_path=image_path,
                title=title,
            )

        text = self.extract_plain_from_result(result)
        if text:
            return await self._qq_pager.send_markdown_text(
                event,
                title=title,
                content=text,
            )

        return False

    async def try_send_markdown_text_for_result(
        self,
        *,
        event: AstrMessageEvent,
        result,
        title: str,
    ) -> bool:
        if not self._qq_pager.enabled_for(event):
            return False
        text = self.extract_plain_from_result(result)
        if not text:
            return False
        return await self._qq_pager.send_markdown_text(
            event,
            title=title,
            content=text,
        )


class WarframeHelperPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self.config = config

        self._default_render_template = _parse_render_template_name(self.config)
        set_render_template_name(self._default_render_template)
        self._session_render_templates: dict[str, str] = {}
        set_render_template_resolver(self._resolve_session_template)
        self._enable_no_prefix_commands = _parse_enable_no_prefix_commands(self.config)
        self._no_prefix_head_regex: re.Pattern[str] | None = None

        image_cache_dir = _parse_image_cache_config(self.config)
        configure_image_cache(
            cache_dir=image_cache_dir,
        )

        _apply_proxy_config(self.config)

        self.term_mapper = WarframeTermMapper()
        self.riven_weapon_mapper = WarframeRivenWeaponMapper()
        self.riven_stat_mapper = WarframeRivenStatMapper()
        self.market_client = WarframeMarketClient()
        ws_api_bases, ws_proxy_bases = _parse_warframestat_bases(self.config)
        self.worldstate_client = WarframeWorldstateClient(
            warframestat_api_bases=ws_api_bases,
            warframestat_proxy_bases=ws_proxy_bases,
        )
        self.public_export_client = PublicExportClient()
        self.drop_data_client = DropDataClient()

        # 最近一次 /wm 的 TopN 结果缓存（用于“回复图片发数字”快速生成 /w 话术）
        self._wm_pick_cache = EventScopedTTLCache(ttl_sec=8 * 60)

        # /wm, /wmr pagination cache for QQ official webhook button paging.
        self._pager_cache = EventScopedTTLCache(ttl_sec=10 * 60)

        qq_cfg = _parse_qq_webhook_config(self.config)
        self._debug_logging_enabled = bool(
            _parse_debug_logging(self.config) or qq_cfg.debug_logging
        )
        set_debug_logging_enabled(self._debug_logging_enabled)
        self.term_mapper.set_debug_logging_enabled(self._debug_logging_enabled)
        self.riven_weapon_mapper.set_debug_logging_enabled(self._debug_logging_enabled)
        self.riven_stat_mapper.set_debug_logging_enabled(self._debug_logging_enabled)
        # QQ official webhook keyboard template id (message buttons).
        # Note: QQ button templates do NOT support variables.
        qq_tpl = (
            qq_cfg.keyboard_template_id or QQ_OFFICIAL_WEBHOOK_PAGER_TEMPLATE_ID_DEFAULT
        )

        self._qq_pager = QQOfficialWebhookPager(
            keyboard_template_id=qq_tpl,
            markdown_template_id=qq_cfg.markdown_template_id,
            enable_markdown_reply=qq_cfg.enable_markdown,
            public_base_url=qq_cfg.public_base_url,
        )

        self._qq_dispatcher = QQResultDispatcher(self._qq_pager)

        self._qq_pager.set_interaction_handler(self._on_qq_interaction_create)

        self._debug_log(
            "plugin_initialized",
            enable_no_prefix=self._enable_no_prefix_commands,
            qq_markdown_enabled=qq_cfg.enable_markdown,
            qq_keyboard_template=bool(qq_tpl),
            qq_markdown_template=bool(qq_cfg.markdown_template_id),
            qq_public_base_url=bool(qq_cfg.public_base_url),
        )

        # Fissure subscription (proactive notifications)
        self._subscriptions = SubscriptionService(
            context=self.context,
            worldstate_client=self.worldstate_client,
            config=self.config,
        )

    def _resolve_session_template(self, event: AstrMessageEvent) -> str | None:
        sid = str(getattr(event, "session_id", "") or "").strip()
        if not sid:
            return None
        return self._session_render_templates.get(sid)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        start_playwright_runtime_prepare()
        await self.term_mapper.initialize()
        await self.riven_weapon_mapper.initialize()
        await self.riven_stat_mapper.initialize()
        await self._warmup_public_export(reason="initialize")

        # Start subscription polling loop after the event loop is ready.
        self._subscriptions.start()

    async def _warmup_public_export(self, *, reason: str) -> dict[str, Any]:
        try:
            stats = await self.public_export_client.warmup_common_exports(language="zh")
            if not bool(stats.get("ok", False)):
                logger.warning(
                    f"PublicExport warmup failed ({reason}): index unavailable"
                )
            return stats
        except Exception as exc:
            logger.warning(f"PublicExport warmup failed ({reason}): {exc!s}")
            return {
                "ok": False,
                "language": "zh",
                "index_files": 0,
                "unique_names": 0,
                "regions": 0,
                "mission_types": 0,
                "fissure_tiers": 0,
                "factions": 0,
                "syndicates": 0,
            }

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        await self._subscriptions.stop()

    async def _on_qq_interaction_create(self, bot: object, interaction: object) -> None:
        self._debug_log(
            "qq_interaction_received",
            interaction_type=type(interaction).__name__,
            interaction_id=getattr(interaction, "id", None),
        )
        try:
            await handle_qq_interaction_create(
                bot=bot,
                interaction=interaction,
                qq_pager=self._qq_pager,
                pager_cache=self._pager_cache,
                wm_pick_cache=self._wm_pick_cache,
                market_client=self.market_client,
            )
            self._debug_log("qq_interaction_handled")
        except Exception as exc:
            logger.warning(f"QQ interaction dispatch failed: {exc!s}")
            self._debug_log("qq_interaction_failed", error=str(exc))
        return

    def _debug_log(
        self,
        action: str,
        *,
        event: AstrMessageEvent | None = None,
        **fields: object,
    ) -> None:
        if not getattr(self, "_debug_logging_enabled", False):
            return

        def _clip(value: object, max_len: int = 180) -> str:
            s = str(value)
            if len(s) <= max_len:
                return s
            return s[: max_len - 3] + "..."

        parts = [f"[WFHelperDebug] {action}"]
        if event is not None:
            text = (event.get_message_str() or "").strip()
            raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
            parts.extend(
                [
                    f"sender={getattr(event, 'get_sender_id', lambda: '')()}",
                    f"session={getattr(event, 'session_id', '')}",
                    f"private={event.is_private_chat()}",
                    f"wake={getattr(event, 'is_wake', False)}",
                    f"wake_cmd={getattr(event, 'is_at_or_wake_command', False)}",
                    f"text={_clip(text)}",
                    f"raw_type={type(raw).__name__}",
                ]
            )

        for key, value in fields.items():
            parts.append(f"{key}={_clip(value)}")

        logger.info(" | ".join(parts))

    async def _try_send_qq_markdown_for_result(
        self,
        *,
        event: AstrMessageEvent,
        result,
        title: str,
        kind: str,
        prefer_keyboard: bool = False,
        page: int = 1,
        hint: str = "使用下方按钮：上一页 / 下一页",
    ) -> bool:
        return await self._qq_dispatcher.try_send_markdown_for_result(
            event=event,
            result=result,
            title=title,
            kind=kind,
            prefer_keyboard=prefer_keyboard,
            page=page,
            hint=hint,
        )

    async def _cleanup_result_image_file(self, result) -> None:
        image_path = self._qq_dispatcher.extract_image_path_from_result(result)
        if not image_path:
            return

        try:
            path = Path(image_path).expanduser()
        except Exception:
            return

        filename = path.name.lower()
        is_plugin_generated = (
            filename == "wf_helper_blank_1x1.png"
            or (filename.startswith("wf_worldstate_") and filename.endswith(".png"))
            or (filename.startswith("wm_") and filename.endswith(".png"))
            or (filename.startswith("wmr_") and filename.endswith(".png"))
        )
        if not is_plugin_generated:
            return

        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            self._debug_log(
                "result_image_cleanup_failed",
                image_path=str(path),
                error=str(exc),
            )

    async def _yield_result_and_cleanup_image(self, result):
        try:
            yield result
        finally:
            await self._cleanup_result_image_file(result)

    def _no_prefix_handler_map(self) -> dict[str, Callable[..., Any] | None]:
        return {
            "wf": self.wf_help_cmd,
            "wf帮助": self.wf_help_alias,
            "wfmap": self.wfmap,
            "wf映射": self.wfmap,
            "模板": self.wf_template,
            "wf模板": self.wf_template,
            "渲染模板": self.wf_template,
            "wm": self.wm,
            "wmr": self.wmr,
            "wr": self.wmr,
            "wk": self.wmr,
            "wfp": self.wf_page,
            "订阅": self.wf_subscribe,
            "退订": self.wf_unsubscribe,
            "取消订阅": self.wf_unsubscribe,
            "订阅列表": self.wf_subscribe_list,
            "执行官猎杀": self.wf_archon_hunt,
            "archon": self.wf_archon_hunt,
            "执行官": self.wf_archon_hunt,
            "钢铁奖励": self.wf_steel_reward,
            "steelreward": self.wf_steel_reward,
            "sp奖励": self.wf_steel_reward,
            "突击": self.wf_sortie,
            "sortie": self.wf_sortie,
            "警报": self.wf_alerts,
            "alerts": self.wf_alerts,
            "裂缝": self.wf_fissures,
            "fissure": self.wf_fissures,
            "九重天裂缝": self.wf_fissures_storm,
            "风暴裂缝": self.wf_fissures_storm,
            "钢铁裂缝": self.wf_fissures_hard,
            "普通裂缝": self.wf_fissures_normal,
            "奸商": self.wf_void_trader,
            "虚空商人": self.wf_void_trader,
            "baro": self.wf_void_trader,
            "仲裁": self.wf_arbitration,
            "arbitration": self.wf_arbitration,
            "电波": self.wf_nightwave,
            "夜波": self.wf_nightwave,
            "nightwave": self.wf_nightwave,
            "平原": self.wf_plains,
            "夜灵平原": self.wf_cetus_cycle,
            "希图斯": self.wf_cetus_cycle,
            "cetus": self.wf_cetus_cycle,
            "poe": self.wf_cetus_cycle,
            "魔胎之境": self.wf_cambion_cycle,
            "魔胎": self.wf_cambion_cycle,
            "cambion": self.wf_cambion_cycle,
            "地球昼夜": self.wf_earth_cycle,
            "地球循环": self.wf_earth_cycle,
            "地球": self.wf_earth_cycle,
            "earth": self.wf_earth_cycle,
            "奥布山谷": self.wf_vallis_cycle,
            "金星平原": self.wf_vallis_cycle,
            "福尔图娜": self.wf_vallis_cycle,
            "vallis": self.wf_vallis_cycle,
            "orb": self.wf_vallis_cycle,
            "orbvallis": self.wf_vallis_cycle,
            "fortuna": self.wf_vallis_cycle,
            "双衍王境": self.wf_duviri_cycle,
            "双衍": self.wf_duviri_cycle,
            "双衍循环": self.wf_duviri_cycle,
            "双衍王镜": self.wf_duviri_cycle,
            "duviri": self.wf_duviri_cycle,
            "轮回奖励": self.wf_duviri_circuit_rewards,
            "双衍轮回": self.wf_duviri_circuit_rewards,
            "双衍轮回奖励": self.wf_duviri_circuit_rewards,
            "circuit": self.wf_duviri_circuit_rewards,
            "武器": self.wf_weapon,
            "weapon": self.wf_weapon,
            "wfweapon": self.wf_weapon,
            "战甲": self.wf_warframe,
            "warframe": self.wf_warframe,
            "frame": self.wf_warframe,
            "wfwarframe": self.wf_warframe,
            "mod": self.wf_mod,
            "mods": self.wf_mod,
            "模组": self.wf_mod,
            "掉落": self.wf_drops,
            "drop": self.wf_drops,
            "drops": self.wf_drops,
            "遗物": self.wf_relic,
            "relic": self.wf_relic,
            "relics": self.wf_relic,
            "入侵": self.wf_invasions,
            "invasions": self.wf_invasions,
            "集团": self.wf_syndicates,
            "syndicate": self.wf_syndicates,
            "syndicates": self.wf_syndicates,
        }

    def _get_no_prefix_head_regex(self) -> re.Pattern[str]:
        if self._no_prefix_head_regex is None:
            commands = sorted(
                {cmd.lower() for cmd in self._no_prefix_handler_map()},
                key=len,
                reverse=True,
            )
            # Match only when a command appears at the beginning of the message.
            # Commands are length-sorted to avoid `wf` shadowing `wfmap`.
            alternation = "|".join(re.escape(cmd) for cmd in commands)
            self._no_prefix_head_regex = re.compile(
                rf"^(?P<cmd>{alternation})",
                re.IGNORECASE,
            )
        return self._no_prefix_head_regex

    def _parse_no_prefix_command(self, text: str) -> tuple[str | None, str]:
        """Resolve command and args only when command is at message start."""
        src = (text or "").strip()
        if not src:
            return None, ""

        match = self._get_no_prefix_head_regex().match(src)
        if not match:
            return None, ""

        cmd_text = match.group("cmd")
        rest = src[match.end("cmd") :]

        # Keep legacy behavior: ASCII commands require a boundary to avoid
        # accidental hits like `wfmapping` being interpreted as `wf`.
        if rest and (not rest[0].isspace()) and cmd_text.isascii():
            return None, ""

        cmd_l = cmd_text.lower()
        return cmd_l, rest.strip()

    @filter.regex(r"^\S(?:[\s\S]*)$")
    async def no_prefix_command_router(self, event: AstrMessageEvent):
        if not self._enable_no_prefix_commands:
            self._debug_log("no_prefix_skip", event=event, reason="feature_disabled")
            return

        # Messages that already entered wake/command flow (e.g. "/指令", @bot)
        # are handled by regular command filters and must not be dispatched again.
        if getattr(event, "is_at_or_wake_command", False):
            self._debug_log(
                "no_prefix_skip", event=event, reason="wake_or_command_flow"
            )
            return

        text = (event.get_message_str() or "").strip()
        if not text or text.startswith("/"):
            self._debug_log("no_prefix_skip", event=event, reason="empty_or_slash")
            return

        lowered = text.lower()
        if lowered in {"上一页", "下一页", "prev", "previous", "next"}:
            self._debug_log("no_prefix_skip", event=event, reason="pager_keyword")
            return
        if lowered.isdigit():
            self._debug_log("no_prefix_skip", event=event, reason="numeric_reply")
            return

        command, raw_args = self._parse_no_prefix_command(text)
        if not command:
            self._debug_log("no_prefix_miss", event=event, command="")
            return

        handler = self._no_prefix_handler_map().get(command)
        if handler is None:
            self._debug_log("no_prefix_miss", event=event, command=command)
            return
        handler_fn = cast(Callable[..., Any], handler)

        self._debug_log("no_prefix_hit", event=event, command=command, args=raw_args)

        _safe_disable_llm(event, reason=f"no_prefix:{command}")

        try:
            async for res in handler_fn(event, raw_args):
                yield res
        except TypeError:
            self._debug_log(
                "no_prefix_retry_without_args", event=event, command=command
            )
            async for res in handler_fn(event):
                yield res

    @filter.command("订阅")
    async def wf_subscribe(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """订阅提醒。

        - 裂缝：/订阅 钢铁赛中 [次数|永久]
        - 平原：/订阅 夜灵平原 黑夜 [次数|永久]
        """

        _safe_disable_llm(event, reason="/订阅")

        raw_args = str(args)
        msg, chain = await self._subscriptions.subscribe(event=event, raw_args=raw_args)
        if chain is not None:
            if self._qq_pager.enabled_for(event):
                image_path = self._qq_dispatcher.extract_image_path_from_chain(
                    chain.chain
                )
                if image_path:
                    ok = await self._qq_pager.send_result_markdown_no_keyboard(
                        event,
                        kind="/订阅",
                        image_path=image_path,
                        title="订阅列表",
                    )
                    if ok:
                        yield event.make_result().stop_event()
                        return
                else:
                    plain = chain.get_plain_text()
                    ok = await self._qq_pager.send_markdown_text(
                        event,
                        title="订阅",
                        content=plain,
                    )
                    if ok:
                        yield event.make_result().stop_event()
                        return

            result = event.chain_result(chain.chain)
            async for output in self._yield_result_and_cleanup_image(result):
                yield output
            return
        if msg:
            if self._qq_pager.enabled_for(event):
                ok = await self._qq_pager.send_markdown_text(
                    event,
                    title="订阅",
                    content=msg,
                )
                if ok:
                    yield event.make_result().stop_event()
                    return
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

        _safe_disable_llm(event, reason="/退订")

        msg = await self._subscriptions.unsubscribe(event=event, raw_args=str(args))
        if self._qq_pager.enabled_for(event):
            ok = await self._qq_pager.send_markdown_text(
                event,
                title="退订",
                content=msg,
            )
            if ok:
                yield event.make_result().stop_event()
                return
        yield event.plain_result(msg)

    @filter.command("订阅列表")
    async def wf_subscribe_list(self, event: AstrMessageEvent):
        """查看当前会话的订阅列表。"""

        _safe_disable_llm(event, reason="/订阅列表")
        chain = await self._subscriptions.render_list(event=event)
        if self._qq_pager.enabled_for(event):
            image_path = self._qq_dispatcher.extract_image_path_from_chain(chain.chain)
            if image_path:
                ok = await self._qq_pager.send_result_markdown_no_keyboard(
                    event,
                    kind="/订阅列表",
                    image_path=image_path,
                    title="订阅列表",
                )
                if ok:
                    yield event.make_result().stop_event()
                    return
            else:
                ok = await self._qq_pager.send_markdown_text(
                    event,
                    title="订阅列表",
                    content=chain.get_plain_text(),
                )
                if ok:
                    yield event.make_result().stop_event()
                    return
        result = event.chain_result(chain.chain)
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("执行官猎杀", alias={"archon", "执行官"})
    async def wf_archon_hunt(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询执行官猎杀（Archon Hunt）。"""

        _safe_disable_llm(event, reason="/执行官猎杀")
        result = await worldstate_commands.cmd_archon_hunt(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="执行官猎杀",
            kind="/执行官猎杀",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("钢铁奖励", alias={"steelreward", "sp奖励"})
    async def wf_steel_reward(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询钢铁之路当前奖励轮换（Steel Path）。"""

        _safe_disable_llm(event, reason="/钢铁奖励")
        result = await worldstate_commands.cmd_steel_path_reward(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="钢铁奖励",
            kind="/钢铁奖励",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("wfmap", alias={"wf映射"})
    async def wfmap(self, event: AstrMessageEvent, query: str = ""):
        """将常用简写/别名映射为 warframe.market 官方词条（例如：猴p -> Wukong Prime Set）"""
        _safe_disable_llm(event, reason="/wfmap")
        query = (query or "").strip()
        if not query:
            result = event.plain_result("用法：/wfmap 猴p")
            if await self._try_send_qq_markdown_for_result(
                event=event,
                result=result,
                title="WF 映射",
                kind="/wfmap",
            ):
                yield event.make_result().stop_event()
                return
            async for output in self._yield_result_and_cleanup_image(result):
                yield output
            return

        item, trace = await self.term_mapper.resolve_with_trace(query)
        if not item:
            result = event.plain_result(f"没有找到相关物品：{query}")
            if await self._try_send_qq_markdown_for_result(
                event=event,
                result=result,
                title="WF 映射",
                kind="/wfmap",
            ):
                yield event.make_result().stop_event()
                return
            async for output in self._yield_result_and_cleanup_image(result):
                yield output
            return

        matched_name = item.get_localized_name("zh-hans") or item.name
        header = [
            f"{query} -> {trace.canonical_full_name}",
            f"{trace.canonical_full_name} -> {matched_name}",
            f"{matched_name} -> {item.slug}",
        ]
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
            result = event.image_result(rendered.path)
            if await self._try_send_qq_markdown_for_result(
                event=event,
                result=result,
                title="WF 映射",
                kind="/wfmap",
            ):
                yield event.make_result().stop_event()
                return
            async for output in self._yield_result_and_cleanup_image(result):
                yield output
            return

        extra = f"\nWiki: {item.wiki_link}" if item.wiki_link else ""
        result = event.plain_result(
            f"{query} -> {trace.canonical_full_name} -> {matched_name} -> {item.slug}{extra}"
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="WF 映射",
            kind="/wfmap",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("简称补充")
    async def wf_add_alias(self, event: AstrMessageEvent, alias: str ,full:str):
        """管理员补充简称映射。用法：/简称补充 <简称> <全称>"""

        _safe_disable_llm(event, reason="/简称补充")

        if not event.is_admin():
            yield event.plain_result("/简称补充 仅限 astradmin 使用。")
            return

        if not alias or not full:
            yield event.plain_result("用法：/简称补充 [简称] [全称]")
            return

        try:
            _, _ = self.term_mapper.upsert_alias(alias=alias, full_name=full)
            self.term_mapper.reload_aliases()
            self.riven_weapon_mapper.reload_aliases()
            self.riven_stat_mapper.reload_aliases()
        except Exception as exc:
            yield event.plain_result(f"简称补充失败：{exc!s}")
            return

        yield event.plain_result(
            "简称补充成功："
            f"{alias} -> {full}\n"
            f"简称文件：{self.term_mapper.nickname_file_path}"
        )

    @filter.command_group("wf")
    def wf(self):
        pass

    @wf.command("help", alias={"帮助", "h"})
    async def wf_help_cmd(self, event: AstrMessageEvent):
        _safe_disable_llm(event, reason="/wf help")
        result = await self._handle_wf_help(event)
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @wf.command("refresh", alias={"reset", "刷新", "刷新缓存", "重置缓存"})
    async def wf_refresh_cmd(self, event: AstrMessageEvent):
        _safe_disable_llm(event, reason="/wf refresh")
        result = await self._handle_wf_refresh(event)
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("wf帮助")
    async def wf_help_alias(self, event: AstrMessageEvent):
        _safe_disable_llm(event, reason="/wf 帮助")
        result = await self._handle_wf_help(event)
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    async def _handle_wf_refresh(self, event: AstrMessageEvent):
        if not event.is_admin():
            return event.plain_result("/wf refresh 仅限 astradmin 使用。")

        reset_result = self.worldstate_client.reset_public_export_cache(
            clear_worldstate_cache=True,
            remove_disk=True,
        )
        ws_n = int(reset_result.get("worldstate_entries", 0))
        mem_n = int(reset_result.get("memory_entries", 0))
        disk_ok = bool(reset_result.get("disk_cleared", False))
        disk_msg = "已清理" if disk_ok else "无需清理或清理失败"

        image_result = _clear_plugin_image_cache()
        img_removed = int(image_result.get("removed", 0))
        img_failed = int(image_result.get("failed", 0))

        items_n = await self.term_mapper.refresh_items_cache()
        lich_weapons_n = await self.riven_weapon_mapper.refresh_cache()
        riven_attrs_n = await self.riven_stat_mapper.refresh_cache()

        self.term_mapper.reload_aliases()
        self.riven_weapon_mapper.reload_aliases()
        self.riven_stat_mapper.reload_aliases()
        pe_stats = await self._warmup_public_export(reason="wf_refresh")

        return event.plain_result(
            "/wf refresh 完成："
            f"PublicExport 内存条目 {mem_n}，worldstate 缓存条目 {ws_n}，磁盘缓存 {disk_msg}；"
            f"图片缓存已删除 {img_removed} 个"
            + (f"（失败 {img_failed} 个）；" if img_failed > 0 else "；")
            + "WFM 缓存刷新："
            f"items {items_n}，merged weapons(riven/lich/sister) {lich_weapons_n}，riven attributes {riven_attrs_n}；"
            + "PublicExport 预热："
            + (
                f"index {int(pe_stats.get('index_files', 0))}，"
                f"unique_names {int(pe_stats.get('unique_names', 0))}，"
                f"regions {int(pe_stats.get('regions', 0))}。"
                if bool(pe_stats.get("ok", False))
                else "失败（索引不可用或网络异常）。"
            )
        )

    async def _handle_wm_refresh(self, event: AstrMessageEvent):
        if not event.is_admin():
            return event.plain_result("/wm 刷新缓存 仅限 astradmin 使用。")

        items_n = await self.term_mapper.refresh_items_cache()
        lich_weapons_n = await self.riven_weapon_mapper.refresh_cache()
        riven_attrs_n = await self.riven_stat_mapper.refresh_cache()

        self.term_mapper.reload_aliases()
        self.riven_weapon_mapper.reload_aliases()
        self.riven_stat_mapper.reload_aliases()
        pe_stats = await self._warmup_public_export(reason="wm_refresh")

        if items_n <= 0 and lich_weapons_n <= 0 and riven_attrs_n <= 0:
            return event.plain_result(
                "/wm 刷新缓存失败：未从 warframe.market 拉取到有效数据，请稍后重试。"
            )

        return event.plain_result(
            "/wm 刷新缓存完成："
            f"items {items_n}，merged weapons(riven/lich/sister) {lich_weapons_n}，riven attributes {riven_attrs_n}；"
            + "PublicExport 预热："
            + (
                f"index {int(pe_stats.get('index_files', 0))}，"
                f"unique_names {int(pe_stats.get('unique_names', 0))}，"
                f"regions {int(pe_stats.get('regions', 0))}。\n"
                if bool(pe_stats.get("ok", False))
                else "失败（索引不可用或网络异常）。\n"
            )
            + f"items 缓存：{self.term_mapper.items_cache_path}"
        )

    async def _handle_wf_help(self, event: AstrMessageEvent):

        rows = [
            WorldstateRow(
                title="市场查询",
                subtitle="/wm /wmr（别名：wr、wk） /wfp（翻页 prev|next；QQ 按钮可用 wfp:prev / wfp:next）",
            ),
            WorldstateRow(
                title="订阅",
                subtitle="/订阅 /退订（别名：取消订阅）/订阅列表",
            ),
            WorldstateRow(
                title="世界状态（任务）",
                subtitle="/突击 /警报 /裂缝 /普通裂缝 /钢铁裂缝 /九重天裂缝",
            ),
            WorldstateRow(
                title="世界状态（其它）",
                subtitle="/奸商（别名：虚空商人、baro）/仲裁 /电波（别名：夜波、nightwave）",
            ),
            WorldstateRow(
                title="世界状态（事件）",
                subtitle="/入侵（别名：invasions）/集团（别名：syndicate）",
            ),
            WorldstateRow(
                title="循环",
                subtitle="/平原 /夜灵平原（别名：希图斯、cetus、poe）/魔胎之境 /地球昼夜",
            ),
            WorldstateRow(
                title="循环（其它）",
                subtitle="/奥布山谷（别名：金星平原、福尔图娜、vallis）/双衍王境 /轮回奖励",
            ),
            WorldstateRow(
                title="奖励",
                subtitle="/执行官猎杀（别名：archon、执行官）/钢铁奖励",
            ),
            WorldstateRow(
                title="资料查询",
                subtitle="/武器 /战甲 /MOD /掉落 /遗物",
            ),
            WorldstateRow(
                title="工具",
                subtitle=(
                    "/wfmap（别名：wf映射）/简称补充（仅astradmin）/wm 刷新缓存（仅astradmin）/模板（别名：wf模板、渲染模板）/wf refresh（仅astradmin）/wf（本帮助；别名：wf帮助）"
                ),
            ),
        ]

        rendered = await render_worldstate_rows_image_to_file(
            title="WF 帮助",
            header_lines=["Warframe 助手 - 全部指令一览"],
            rows=rows,
            accent=(79, 70, 229, 255),
        )
        if rendered:
            result = event.image_result(rendered.path)
            if await self._try_send_qq_markdown_for_result(
                event=event,
                result=result,
                title="WF 帮助",
                kind="/wf",
            ):
                return event.make_result().stop_event()
            return result

        # Fallback: in case image rendering fails.
        return event.plain_result("/wf 帮助图片渲染失败，请稍后重试。")

    @filter.command("模板", alias={"wf模板", "渲染模板", "wft"})
    async def wf_template(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """切换当前会话的渲染模板。"""
        _safe_disable_llm(event, reason="/模板")

        sid = str(getattr(event, "session_id", "") or "").strip()
        raw = str(args or "").strip()
        available = list_available_render_template_names()
        available_text = "、".join(available)

        current = (
            self._session_render_templates.get(sid) or self._default_render_template
        )

        if not raw or raw.lower() in {"list", "ls", "列表"}:
            yield event.plain_result(
                "渲染模板设置\n"
                f"- 当前会话模板：{current}\n"
                f"- 可用模板：{available_text}\n"
                "- 用法：/模板 <名称>（恢复默认：/模板 默认）"
            )
            return

        if raw.lower() in {"default", "reset", "clear"} or raw in {"默认", "重置"}:
            if sid:
                self._session_render_templates.pop(sid, None)
            yield event.plain_result(f"已恢复默认模板：{self._default_render_template}")
            return

        if not has_render_template_name(raw):
            yield event.plain_result(f"模板不存在：{raw}\n可用模板：{available_text}")
            return

        if sid:
            self._session_render_templates[sid] = raw
        yield event.plain_result(f"当前会话渲染模板已切换为：{raw}")

    @filter.command("wm")
    async def wm(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询 warframe.market 订单。

        用法：
        - /wm 猴p
        - /wm 猴p pc
        - /wm 猴p pc 收
        - /wm 猴p pc 收 zh 10
        """
        _apply_render_template_for_event(event)

        raw_args = str(args or "")
        raw_args_norm = raw_args.strip().lower()
        if raw_args_norm in {"refresh", "reset", "刷新", "刷新缓存", "重置缓存"}:
            _safe_disable_llm(event, reason="/wm 刷新缓存")
            result = await self._handle_wm_refresh(event)
            async for output in self._yield_result_and_cleanup_image(result):
                yield output
            return

        converted_wmr_args = _convert_wm_args_to_wmr(raw_args)
        if converted_wmr_args is not None:
            set_current_render_command("/wmr")
            async for res in cmd_wmr(
                context=self.context,
                event=event,
                raw_args=converted_wmr_args,
                config=self.config,
                market_client=self.market_client,
                riven_weapon_mapper=self.riven_weapon_mapper,
                riven_stat_mapper=self.riven_stat_mapper,
                pager_cache=self._pager_cache,
                qq_pager=self._qq_pager,
            ):
                if await self._qq_dispatcher.try_send_markdown_text_for_result(
                    event=event,
                    result=res,
                    title="紫卡拍卖",
                ):
                    yield event.make_result().stop_event()
                    return
                async for output in self._yield_result_and_cleanup_image(res):
                    yield output
            return

        set_current_render_command("/wm")
        async for res in cmd_wm(
            context=self.context,
            event=event,
            raw_args=raw_args,
            config=self.config,
            term_mapper=self.term_mapper,
            market_client=self.market_client,
            pager_cache=self._pager_cache,
            wm_pick_cache=self._wm_pick_cache,
            qq_pager=self._qq_pager,
        ):
            if await self._qq_dispatcher.try_send_markdown_text_for_result(
                event=event,
                result=res,
                title="市场订单",
            ):
                yield event.make_result().stop_event()
                return
            async for output in self._yield_result_and_cleanup_image(res):
                yield output

    @filter.command("wmr", alias={"wr", "wk"})
    async def wmr(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询 warframe.market 紫卡（Riven）一口价拍卖。

        示例：/wmr 基伤 双暴 伯斯顿
        示例：/wmr 基伤 逐枭 负暴率
        说明：武器名与词条可混写；负任意=必须有任意负词条；无负=不能有负词条。
        """
        _apply_render_template_for_event(event)

        set_current_render_command("/wmr")
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
            if await self._qq_dispatcher.try_send_markdown_text_for_result(
                event=event,
                result=res,
                title="紫卡拍卖",
            ):
                yield event.make_result().stop_event()
                return
            async for output in self._yield_result_and_cleanup_image(res):
                yield output

    @filter.command("wfp")
    async def wf_page(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """Pagination helper for /wm and /wmr.

        Usage:
        - /wfp prev
        - /wfp next

        Designed to be used by QQ official webhook "command" buttons.
        """
        _apply_render_template_for_event(event)

        async for res in cmd_wfp(
            event=event,
            raw_args=str(args),
            pager_cache=self._pager_cache,
            wm_pick_cache=self._wm_pick_cache,
            market_client=self.market_client,
            qq_pager=self._qq_pager,
        ):
            if await self._qq_dispatcher.try_send_markdown_text_for_result(
                event=event,
                result=res,
                title="wfp",
            ):
                yield event.make_result().stop_event()
                return
            async for output in self._yield_result_and_cleanup_image(res):
                yield output

    @filter.regex(r"^(上一页|下一页|prev|previous|next)$")
    async def qq_official_webhook_button_page(self, event: AstrMessageEvent):
        """Handle QQ official webhook template buttons.

        Some QQ keyboard templates are configured to send plain text like “上一页/下一页”.
        This handler converts them into /wfp prev|next.
        """

        if not self._qq_pager.keyboard_enabled_for(event):
            self._debug_log(
                "qq_button_skip",
                event=event,
                reason="keyboard_not_enabled_for_event",
            )
            return

        _safe_disable_llm(event, reason="qq_official_webhook_button_page")

        text = (event.get_message_str() or "").strip().lower()
        direction = "prev" if text in {"上一页", "prev", "previous"} else "next"
        self._debug_log("qq_button_route", event=event, direction=direction)

        async for res in cmd_wfp(
            event=event,
            raw_args=direction,
            pager_cache=self._pager_cache,
            wm_pick_cache=self._wm_pick_cache,
            market_client=self.market_client,
            qq_pager=self._qq_pager,
        ):
            async for output in self._yield_result_and_cleanup_image(res):
                yield output

    @filter.regex(r"^\d+$")
    async def wm_pick_number(self, event: AstrMessageEvent):
        """当用户回复 /wm 结果图并只发送数字时，返回对应玩家的 /w 话术。"""
        async for res in handle_wm_pick_number(
            event=event,
            wm_pick_cache=self._wm_pick_cache,
        ):
            if await self._qq_dispatcher.try_send_markdown_text_for_result(
                event=event,
                result=res,
                title="wm",
            ):
                yield event.make_result().stop_event()
                return
            async for output in self._yield_result_and_cleanup_image(res):
                yield output

    @filter.command("突击", alias={"sortie"})
    async def wf_sortie(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询今日突击（Sortie）。"""

        _safe_disable_llm(event, reason="/突击")
        result = await worldstate_commands.cmd_sortie(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="突击",
            kind="/突击",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("警报", alias={"alerts"})
    async def wf_alerts(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询当前警报。"""

        _safe_disable_llm(event, reason="/警报")
        result = await worldstate_commands.cmd_alerts(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="警报",
            kind="/警报",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("裂缝", alias={"fissure"})
    async def wf_fissures(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询虚空裂缝：支持 普通/钢铁/九重天（九重天=风暴裂缝）。"""

        _safe_disable_llm(event, reason="/裂缝")
        result = await worldstate_commands.cmd_fissures(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="裂缝",
            kind="/裂缝",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("九重天裂缝", alias={"风暴裂缝"})
    async def wf_fissures_storm(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/九重天裂缝 = /裂缝 九重天"""
        _safe_disable_llm(event, reason="/九重天裂缝")
        result = await worldstate_commands.cmd_fissures_kind(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            fissure_kind="九重天",
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="裂缝",
            kind="/九重天裂缝",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("钢铁裂缝")
    async def wf_fissures_hard(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/钢铁裂缝 = /裂缝 钢铁"""
        _safe_disable_llm(event, reason="/钢铁裂缝")
        result = await worldstate_commands.cmd_fissures_kind(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            fissure_kind="钢铁",
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="裂缝",
            kind="/钢铁裂缝",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("普通裂缝")
    async def wf_fissures_normal(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """别称：/普通裂缝 = /裂缝 普通"""
        _safe_disable_llm(event, reason="/普通裂缝")
        result = await worldstate_commands.cmd_fissures_kind(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            fissure_kind="普通",
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="裂缝",
            kind="/普通裂缝",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("奸商", alias={"虚空商人", "baro"})
    async def wf_void_trader(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询奸商（Baro Ki'Teer / Void Trader）。"""

        _safe_disable_llm(event, reason="/奸商")
        result = await worldstate_commands.cmd_void_trader(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="奸商",
            kind="/奸商",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("仲裁", alias={"arbitration"})
    async def wf_arbitration(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询仲裁（Arbitration）。"""

        _safe_disable_llm(event, reason="/仲裁")
        result = await worldstate_commands.cmd_arbitration(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="仲裁",
            kind="/仲裁",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("电波", alias={"夜波", "nightwave"})
    async def wf_nightwave(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询电波（Nightwave）。"""

        _safe_disable_llm(event, reason="/电波")
        try:
            result = await asyncio.wait_for(
                worldstate_commands.cmd_nightwave(
                    event=event,
                    raw_args=str(args),
                    worldstate_client=self.worldstate_client,
                ),
                timeout=25,
            )
        except TimeoutError:
            self._debug_log("nightwave_timeout", event=event, timeout_sec=25)
            yield event.plain_result(
                "电波查询超时（25s）。请稍后重试，或检查网络/代理配置。"
            )
            return

        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="电波",
            kind="/电波",
        ):
            yield event.make_result().stop_event()
            return

        # QQ official webhook: if markdown image send failed, avoid silent failure.
        if self._qq_pager.enabled_for(event):
            image_path = self._qq_dispatcher.extract_image_path_from_result(result)
            if image_path:
                self._debug_log(
                    "qq_markdown_image_failed_fallback_text",
                    event=event,
                    command="/电波",
                )
                yield event.plain_result(
                    "电波结果已生成，但 QQ 官方 Markdown 图片发送失败。"
                    "请检查 qq_official.webhook_markdown_template_id 与 "
                    "qq_official.webhook_public_base_url 配置。"
                )
                return

        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("平原")
    async def wf_plains(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """查询各开放世界平原的当前状态。

        - 无参数：列出所有平原状态（图片输出）
        - 带参数：查询指定平原，例如：/平原 希图斯
        """

        _safe_disable_llm(event, reason="/平原")
        result = await worldstate_commands.cmd_plains(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="平原状态",
            kind="/平原",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("夜灵平原", alias={"希图斯", "cetus", "poe"})
    async def wf_cetus_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询夜灵平原昼夜循环（Cetus Cycle）。"""

        _safe_disable_llm(event, reason="/夜灵平原")
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="cetus",
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="夜灵平原",
            kind="/夜灵平原",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("魔胎之境", alias={"魔胎", "cambion"})
    async def wf_cambion_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询魔胎之境轮换（Cambion Cycle）。"""

        _safe_disable_llm(event, reason="/魔胎之境")
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="cambion",
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="魔胎之境",
            kind="/魔胎之境",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("地球昼夜", alias={"地球循环", "地球", "earth"})
    async def wf_earth_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询地球昼夜循环（Earth Cycle）。"""

        _safe_disable_llm(event, reason="/地球昼夜")
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="earth",
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="地球昼夜",
            kind="/地球昼夜",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command(
        "奥布山谷",
        alias={"金星平原", "福尔图娜", "vallis", "orb", "orbvallis", "fortuna"},
    )
    async def wf_vallis_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询奥布山谷温/寒循环（Orb Vallis Cycle）。"""

        _safe_disable_llm(event, reason="/奥布山谷")
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="vallis",
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="奥布山谷",
            kind="/奥布山谷",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("双衍王境", alias={"双衍", "双衍循环", "双衍王镜", "duviri"})
    async def wf_duviri_cycle(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询双衍王境情绪轮换（Duviri Cycle）。"""

        _safe_disable_llm(event, reason="/双衍王境")
        result = await worldstate_commands.cmd_cycle(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
            cycle="duviri",
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="双衍王境",
            kind="/双衍王境",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("轮回奖励", alias={"双衍轮回", "双衍轮回奖励", "circuit","本周轮换"})
    async def wf_duviri_circuit_rewards(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询双衍王境「轮回」奖励轮换（普通/钢铁）。"""

        _safe_disable_llm(event, reason="/轮回奖励")
        result = await worldstate_commands.cmd_duviri_circuit_rewards(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="轮回奖励",
            kind="/轮回奖励",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("武器", alias={"weapon", "wfweapon"})
    async def wf_weapon(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 PublicExport 查询武器（中文优先，也支持英文/uniqueName 匹配）。用法：/武器 绝路"""

        _safe_disable_llm(event, reason="/武器")
        result = await public_export_commands.cmd_weapon(
            event=event,
            query=str(args),
            public_export_client=self.public_export_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="武器",
            kind="/武器",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("战甲", alias={"warframe", "frame", "wfwarframe"})
    async def wf_warframe(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 PublicExport 查询战甲条目（基础面板信息，字段尽量容错）。用法：/战甲 牛甲"""

        _safe_disable_llm(event, reason="/战甲")
        result = await public_export_commands.cmd_warframe(
            event=event,
            query=str(args),
            public_export_client=self.public_export_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="战甲",
            kind="/战甲",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("MOD", alias={"mod", "模组", "mods"})
    async def wf_mod(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 PublicExport 查询 MOD/升级条目（名称模糊匹配）。用法：/MOD 过载"""

        _safe_disable_llm(event, reason="/MOD")
        result = await public_export_commands.cmd_mod(
            event=event,
            query=str(args),
            public_export_client=self.public_export_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="MOD",
            kind="/MOD",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("掉落", alias={"drop", "drops"})
    async def wf_drops(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 WFCD/warframe-drop-data 查询物品掉落地点。用法：/掉落 <物品> [数量<=30]"""

        _safe_disable_llm(event, reason="/掉落")
        result = await drop_data_commands.cmd_drops(
            event=event,
            raw_args=str(args),
            drop_data_client=self.drop_data_client,
            public_export_client=self.public_export_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="掉落",
            kind="/掉落",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("遗物", alias={"relic", "relics"})
    async def wf_relic(self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()):
        """根据 WFCD/warframe-drop-data 查询遗物奖池。用法：/遗物 <纪元> <遗物名> 或 /遗物 <遗物名>"""

        _safe_disable_llm(event, reason="/遗物")
        result = await drop_data_commands.cmd_relic(
            event=event,
            raw_args=str(args),
            drop_data_client=self.drop_data_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="遗物",
            kind="/遗物",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

    @filter.command("入侵", alias={"invasions"})
    async def wf_invasions(
        self, event: AstrMessageEvent, args: GreedyStr = GreedyStr()
    ):
        """查询当前入侵（Invasions）。用法：/入侵 [平台] [数量<=20]"""

        _safe_disable_llm(event, reason="/入侵")
        result = await worldstate_commands.cmd_invasions(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="入侵",
            kind="/入侵",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output

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

        _safe_disable_llm(event, reason="/集团")
        result = await worldstate_commands.cmd_syndicates(
            event=event,
            raw_args=str(args),
            worldstate_client=self.worldstate_client,
        )
        if await self._try_send_qq_markdown_for_result(
            event=event,
            result=result,
            title="集团",
            kind="/集团",
        ):
            yield event.make_result().stop_event()
            return
        async for output in self._yield_result_and_cleanup_image(result):
            yield output
