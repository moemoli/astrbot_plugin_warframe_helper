from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import cast

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from ..clients.worldstate_client import Platform, WarframeWorldstateClient
from ..constants import WORLDSTATE_PLATFORM_ALIASES
from ..helpers import parse_platform, split_tokens
from ..renderers.worldstate_render import (
    WorldstateRow,
    render_worldstate_rows_image_to_file,
)


class SubscriptionService:
    def __init__(
        self,
        *,
        context,
        worldstate_client: WarframeWorldstateClient,
        config: dict | None,
        plugin_data_dirname: str = "astrbot_plugin_warframe_helper",
    ) -> None:
        self._context = context
        self._worldstate_client = worldstate_client
        self._config = config
        self._plugin_data_dirname = plugin_data_dirname

        self._lock = asyncio.Lock()
        self._subscriptions: list[dict] = self._load()

        self._poll_task: asyncio.Task | None = None
        self._stop = False

    def start(self) -> None:
        if self._poll_task:
            return
        self._stop = False
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._stop = True
        task = self._poll_task
        self._poll_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass

    def _plugin_data_dir(self) -> Path:
        base = Path(get_astrbot_plugin_data_path())
        return base / self._plugin_data_dirname

    def _subs_file_path(self) -> Path:
        return self._plugin_data_dir() / "fissure_subscriptions.json"

    def _load(self) -> list[dict]:
        try:
            fp = self._subs_file_path()
            if not fp.exists():
                return []
            raw = fp.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, list):
                return []
            out: list[dict] = []
            for it in data:
                if not isinstance(it, dict):
                    continue
                if not isinstance(it.get("session"), str):
                    continue
                sid = str(it.get("id") or "").strip()
                if not sid:
                    it = dict(it)
                    it["id"] = uuid.uuid4().hex
                out.append(it)
            return out
        except Exception:
            return []

    def _save(self) -> None:
        try:
            d = self._plugin_data_dir()
            d.mkdir(parents=True, exist_ok=True)
            fp = self._subs_file_path()
            fp.write_text(
                json.dumps(self._subscriptions, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"save subscriptions failed: {exc!s}")

    def _worldstate_platform_from_tokens(self, tokens: list[str]) -> Platform:
        p = parse_platform(tokens, WORLDSTATE_PLATFORM_ALIASES, default="pc")
        if p in {"pc", "ps4", "xb1", "swi"}:
            return cast(Platform, p)
        return "pc"

    def _parse_fissure_subscribe_query(self, *, raw_args: str, tokens: list[str]) -> dict | None:
        raw = (raw_args or "").strip()
        if not raw:
            return None

        platform_norm = self._worldstate_platform_from_tokens(tokens)

        compact = re.sub(r"\s+", "", raw)
        compact = compact.replace("节点", "")

        fissure_kind = "普通"  # 普通/钢铁/九重天
        if any(k in compact for k in ["九重天", "九重", "风暴", "storm"]):
            fissure_kind = "九重天"
        if any(k in compact.lower() for k in ["钢铁", "钢", "sp", "steel"]):
            fissure_kind = "钢铁"
        if any(k in compact for k in ["普通", "正常", "normal"]):
            fissure_kind = "普通"

        tier = ""  # 古纪/前纪/中纪/后纪/安魂/全能 (optional)
        compact_l = compact.lower()
        if any(k in compact for k in ["全能"]) or any(k in compact_l for k in ["omnia", "t6", "voidt6"]):
            tier = "全能"
        elif any(k in compact for k in ["安魂"]) or any(k in compact_l for k in ["requiem", "t5", "voidt5"]):
            tier = "安魂"
        elif any(k in compact for k in ["后纪"]) or any(k in compact_l for k in ["t4", "voidt4"]):
            tier = "后纪"
        elif any(k in compact for k in ["中纪"]) or any(k in compact_l for k in ["t3", "voidt3"]):
            tier = "中纪"
        elif any(k in compact for k in ["前纪"]) or any(k in compact_l for k in ["t2", "voidt2"]):
            tier = "前纪"
        elif any(k in compact for k in ["古纪"]) or any(k in compact_l for k in ["t1", "voidt1"]):
            tier = "古纪"

        code = compact
        for w in [
            "九重天",
            "九重",
            "风暴",
            "钢铁",
            "钢",
            "普通",
            "正常",
            "全能",
            "安魂",
            "后纪",
            "中纪",
            "前纪",
            "古纪",
            "storm",
            "steel",
            "sp",
            "normal",
            "omnia",
            "voidt6",
            "voidt5",
            "voidt4",
            "voidt3",
            "voidt2",
            "voidt1",
            "t6",
            "t5",
            "t4",
            "t3",
            "t2",
            "t1",
        ]:
            code = code.replace(w, "")

        planet_alias = {
            "地": "地球",
            "地球": "地球",
            "水": "水星",
            "水星": "水星",
            "金": "金星",
            "金星": "金星",
            "火": "火星",
            "火星": "火星",
            "谷": "谷神星",
            "谷神星": "谷神星",
            "木": "木星",
            "木星": "木星",
            "土": "土星",
            "土星": "土星",
            "天": "天王星",
            "天王星": "天王星",
            "海": "海王星",
            "海王星": "海王星",
            "冥": "冥王星",
            "冥王星": "冥王星",
            "欧": "欧罗巴",
            "欧罗巴": "欧罗巴",
            "德": "德莫斯",
            "德莫斯": "德莫斯",
            "月": "月球",
            "月球": "月球",
            "赛": "赛德娜",
            "赛德娜": "赛德娜",
        }
        mission_alias = {
            "生": "生存",
            "生存": "生存",
            "歼": "歼灭",
            "歼灭": "歼灭",
            "防": "防御",
            "防御": "防御",
            "移": "移动防御",
            "移动防御": "移动防御",
            "救": "救援",
            "救援": "救援",
            "破": "破坏",
            "破坏": "破坏",
            "捕": "捕获",
            "捕获": "捕获",
            "拦": "拦截",
            "拦截": "拦截",
            "劫": "劫持",
            "劫持": "劫持",
            "刺": "刺杀",
            "刺杀": "刺杀",
            "间": "间谍",
            "间谍": "间谍",
            "挖": "挖掘",
            "挖掘": "挖掘",
            "中": "中断",
            "中断": "中断",
            "炼": "炼金",
            "炼金": "炼金",
            "瀑": "虚空瀑流",
            "虚空瀑流": "虚空瀑流",
            "腐": "腐化",
            "腐化": "腐化",
        }

        planet = None
        mission_type = None

        full_planets = [
            "赛德娜",
            "地球",
            "水星",
            "金星",
            "火星",
            "谷神星",
            "木星",
            "土星",
            "天王星",
            "海王星",
            "冥王星",
            "欧罗巴",
            "德莫斯",
            "月球",
        ]
        for fp in full_planets:
            if planet is None and fp in code:
                planet = fp
                code = code.replace(fp, "")

        full_missions = [
            "移动防御",
            "虚空瀑流",
            "歼灭",
            "生存",
            "防御",
            "救援",
            "破坏",
            "捕获",
            "拦截",
            "劫持",
            "刺杀",
            "间谍",
            "挖掘",
            "中断",
            "炼金",
            "腐化",
        ]
        for fm in full_missions:
            if mission_type is None and fm in code:
                mission_type = fm
                code = code.replace(fm, "")

        code = code.strip()
        if (planet is None or mission_type is None) and len(code) >= 2:
            p = code[0]
            m = code[1]
            if planet is None and p in planet_alias:
                planet = planet_alias[p]
            if mission_type is None and m in mission_alias:
                mission_type = mission_alias[m]

        if fissure_kind == "钢铁" and (planet is None or mission_type is None):
            c = code.strip()
            if c in {"月", "月球"}:
                planet = planet or "月球"
                mission_type = mission_type or "生存"
                tier = tier or "全能"

        if not planet or not mission_type:
            return None

        return {
            "id": uuid.uuid4().hex,
            "session": "",
            "platform": platform_norm,
            "kind": fissure_kind,
            "planet": planet,
            "tier": tier,
            "mission_type": mission_type,
            "created_ts": time.time(),
            "last_sigs": [],
            "type": "fissure",
            "remaining": None,
        }

    def _parse_cycle_subscribe_query(self, *, raw_args: str, tokens: list[str]) -> dict | None:
        raw = (raw_args or "").strip()
        if not raw:
            return None

        platform_norm = self._worldstate_platform_from_tokens(tokens)

        def _n(text: str) -> str:
            return re.sub(r"\s+", "", str(text).strip().lower())

        platform_tokens = {_n(k) for k in WORLDSTATE_PLATFORM_ALIASES.keys() if k} | {
            _n(v) for v in WORLDSTATE_PLATFORM_ALIASES.values() if v
        }
        tnorms = [_n(t) for t in tokens if _n(t) and _n(t) not in platform_tokens]
        raw_n = _n(raw)

        is_cetus = any(t in {"夜灵平原", "希图斯", "cetus", "poe"} for t in tnorms) or (
            "夜灵平原" in raw_n or "希图斯" in raw_n
        )
        if not is_cetus:
            return None

        desired: str | None = None
        if any(t in {"黑夜", "夜晚", "晚上", "night"} for t in tnorms) or any(
            k in raw_n for k in ["黑夜", "夜晚", "night"]
        ):
            desired = "夜晚"
        elif any(t in {"白天", "白昼", "day"} for t in tnorms) or any(
            k in raw_n for k in ["白天", "白昼", "day"]
        ):
            desired = "白天"

        if not desired:
            return None

        return {
            "id": uuid.uuid4().hex,
            "type": "cycle",
            "session": "",
            "platform": platform_norm,
            "cycle": "cetus",
            "plain": "夜灵平原",
            "state": desired,
            "created_ts": time.time(),
            "last_state": "",
            "remaining": None,
        }

    def parse_subscribe_times(self, raw_args: str) -> tuple[str, int | None]:
        raw = (raw_args or "").strip()
        if not raw:
            return "", None
        tokens = split_tokens(raw)
        if not tokens:
            return raw, None

        last = str(tokens[-1]).strip()
        if last == "永久":
            query = " ".join([str(t) for t in tokens[:-1]]).strip()
            return query, None

        if last.isdigit():
            n = int(last)
            if n <= 0:
                n = 1
            query = " ".join([str(t) for t in tokens[:-1]]).strip()
            return query, n

        if raw.endswith("永久") and len(raw) > 2:
            return raw[: -len("永久")].strip(), None
        m = re.search(r"(\d+)$", raw)
        if m:
            n = int(m.group(1))
            if n <= 0:
                n = 1
            return raw[: -len(m.group(1))].strip(), n

        return raw, None

    async def guess_fissure_subscribe_query_via_llm(
        self, *, event: AstrMessageEvent, query: str, platform_norm: Platform
    ) -> dict | None:
        provider_id = self._config.get("unknown_abbrev_provider_id") if self._config else ""
        if not provider_id:
            return None

        q = (query or "").strip()
        if not q:
            return None

        system_prompt = (
            "You convert a Warframe fissure subscription shorthand into structured fields. "
            "Return JSON only."
        )
        prompt = (
            "Convert the user's subscription shorthand into JSON for a Warframe Void Fissure subscription.\n"
            "The user may use Chinese abbreviations like: '钢铁赛中' meaning 'Steel Path + Sedna + Disruption'.\n"
            "Rules:\n"
            '- Output MUST be valid JSON only: {"kind":"普通|钢铁|九重天","planet":"...","mission_type":"..."}.\n'
            "- kind must be one of: 普通, 钢铁, 九重天.\n"
            "- planet is the planet name in Chinese (e.g. 赛德娜).\n"
            "- mission_type is the mission type in Chinese (e.g. 中断/歼灭/生存/防御/拦截/救援/破坏/捕获/移动防御/间谍/挖掘/劫持/刺杀).\n"
            "- If kind is not specified by the user, choose 普通.\n"
            "- Do NOT include extra keys.\n"
            f"Platform: {platform_norm}\n"
            f"User: {q}\n"
            "JSON:"
        )

        try:
            llm_resp = await self._context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0,
                timeout=15,
            )
        except TypeError:
            try:
                llm_resp = await self._context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=0,
                )
            except Exception:
                return None
        except Exception:
            return None

        text = (getattr(llm_resp, "completion_text", "") or "").strip()
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

        if not isinstance(obj, dict):
            return None

        kind = str(obj.get("kind") or "").strip()
        planet = str(obj.get("planet") or "").strip()
        mission_type = str(obj.get("mission_type") or "").strip()
        if kind not in {"普通", "钢铁", "九重天"}:
            kind = "普通"
        if not planet or not mission_type:
            return None

        return {
            "id": uuid.uuid4().hex,
            "session": "",
            "platform": platform_norm,
            "kind": kind,
            "planet": planet,
            "tier": "",
            "mission_type": mission_type,
            "created_ts": time.time(),
            "last_sigs": [],
            "type": "fissure",
            "remaining": None,
        }

    async def render_list(self, *, event: AstrMessageEvent) -> MessageChain:
        session = event.unified_msg_origin
        async with self._lock:
            subs = [
                dict(s)
                for s in self._subscriptions
                if isinstance(s, dict) and s.get("session") == session
            ]

        if not subs:
            return MessageChain().message("当前没有订阅。")

        def remaining_text(v) -> str:
            if v is None:
                return "永久"
            if isinstance(v, int):
                return str(v)
            return "永久"

        def type_order(s: dict) -> int:
            t = str(s.get("type") or "fissure")
            return 0 if t == "cycle" else 1

        subs.sort(
            key=lambda s: (
                type_order(s),
                str(s.get("platform") or ""),
                str(s.get("plain") or ""),
                str(s.get("state") or ""),
                str(s.get("kind") or ""),
                str(s.get("planet") or ""),
                str(s.get("tier") or ""),
                str(s.get("mission_type") or ""),
            )
        )

        rows: list[WorldstateRow] = []
        for s in subs[:50]:
            stype = str(s.get("type") or "fissure")
            if stype == "cycle":
                title = f"{s.get('plain', '?')} {s.get('state', '?')}"
                subtitle = f"平台：{s.get('platform', 'pc')}"
                right = f"次数：{remaining_text(s.get('remaining'))}"
                rows.append(WorldstateRow(title=title, subtitle=subtitle, right=right))
            else:
                tier = str(s.get("tier") or "").strip()
                tier_s = tier if tier else ""
                title = f"{s.get('kind', '?')} {s.get('planet', '?')}{tier_s}{s.get('mission_type', '?')}"
                subtitle = f"平台：{s.get('platform', 'pc')}"
                right = f"次数：{remaining_text(s.get('remaining'))}"
                rows.append(WorldstateRow(title=title, subtitle=subtitle, right=right))

        rendered = await render_worldstate_rows_image_to_file(
            title="订阅列表",
            header_lines=[f"共{len(subs)}条（展示前{min(50, len(subs))}条）"],
            rows=rows,
            accent=(16, 185, 129, 255),
        )
        if rendered:
            return MessageChain().file_image(rendered.path)

        lines = [f"订阅列表：共{len(subs)}条"]
        for s in subs:
            stype = str(s.get("type") or "fissure")
            if stype == "cycle":
                lines.append(
                    f"- {s.get('plain', '?')} {s.get('state', '?')}"
                    f"（{s.get('platform', 'pc')}） 次数：{remaining_text(s.get('remaining'))}"
                )
            else:
                tier = str(s.get("tier") or "").strip()
                tier_s = tier if tier else ""
                lines.append(
                    f"- {s.get('kind', '?')} {s.get('planet', '?')}{tier_s}{s.get('mission_type', '?')}"
                    f"（{s.get('platform', 'pc')}） 次数：{remaining_text(s.get('remaining'))}"
                )
        return MessageChain().message("\n".join(lines))

    async def subscribe(self, *, event: AstrMessageEvent, raw_args: str) -> tuple[str | None, MessageChain | None]:
        """Handle subscription creation.

        Returns:
        - (None, MessageChain) for list rendering
        - (message, None) for plain-text responses
        """

        if str(raw_args).strip() in {"列表", "查看", "list"}:
            chain = await self.render_list(event=event)
            return None, chain

        query_str, remaining = self.parse_subscribe_times(raw_args)
        tokens = split_tokens(query_str)
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        rec = self._parse_cycle_subscribe_query(raw_args=query_str, tokens=tokens)
        if not rec:
            rec = self._parse_fissure_subscribe_query(raw_args=query_str, tokens=tokens)
        if not rec:
            rec = await self.guess_fissure_subscribe_query_via_llm(
                event=event, query=query_str, platform_norm=platform_norm
            )
        if not rec:
            return (
                "用法：\n"
                "- /订阅 钢铁赛中 [次数|永久]\n"
                "- /订阅 夜灵平原 黑夜 [次数|永久]\n"
                "示例：/订阅 钢铁赛中 永久\n"
                "示例：/订阅 夜灵平原 黑夜 3"
            ), None

        rec["session"] = event.unified_msg_origin
        rec["remaining"] = remaining

        async with self._lock:
            for s in self._subscriptions:
                if not isinstance(s, dict):
                    continue

                if str(rec.get("type") or "fissure") == "cycle":
                    if (
                        str(s.get("type") or "fissure") == "cycle"
                        and s.get("session") == rec.get("session")
                        and s.get("platform") == rec.get("platform")
                        and s.get("cycle") == rec.get("cycle")
                        and s.get("state") == rec.get("state")
                    ):
                        if isinstance(remaining, int):
                            s["remaining"] = remaining
                            self._save()
                        return (
                            f"已订阅：{rec.get('plain', '夜灵平原')} {rec.get('state', '?')}（{rec.get('platform', 'pc')}）"
                        ), None
                else:
                    if (
                        str(s.get("type") or "fissure") != "cycle"
                        and s.get("session") == rec.get("session")
                        and s.get("platform") == rec.get("platform")
                        and s.get("kind") == rec.get("kind")
                        and s.get("planet") == rec.get("planet")
                        and str(s.get("tier") or "") == str(rec.get("tier") or "")
                        and s.get("mission_type") == rec.get("mission_type")
                    ):
                        if isinstance(remaining, int):
                            s["remaining"] = remaining
                            self._save()
                        return (
                            f"已订阅：{rec['kind']} {rec['planet']}{(rec.get('tier') or '')}{rec['mission_type']}（{rec['platform']}）"
                        ), None

            self._subscriptions.append(rec)
            self._save()

        times_desc = "永久" if remaining is None else str(remaining)
        if str(rec.get("type") or "fissure") == "cycle":
            return (
                f"订阅成功：{rec.get('plain', '夜灵平原')} {rec.get('state', '?')}（{rec.get('platform', 'pc')}）\n"
                f"提醒次数：{times_desc}\n"
                "当平原状态切换并进入目标状态时将主动推送提醒。"
            ), None

        return (
            f"订阅成功：{rec['kind']} {rec['planet']}{(rec.get('tier') or '')}{rec['mission_type']}（{rec['platform']}）\n"
            f"提醒次数：{times_desc}\n"
            "当匹配裂缝出现/变化时将主动推送提醒。"
        ), None

    async def unsubscribe(self, *, event: AstrMessageEvent, raw_args: str) -> str:
        if str(raw_args).strip() in {"全部", "all", "所有"}:
            session = event.unified_msg_origin
            async with self._lock:
                before = len(self._subscriptions)
                self._subscriptions = [
                    s
                    for s in self._subscriptions
                    if not (isinstance(s, dict) and s.get("session") == session)
                ]
                after = len(self._subscriptions)
                self._save()
            return f"已退订全部提醒：{before - after}条"

        query_str, _ = self.parse_subscribe_times(raw_args)
        tokens = split_tokens(query_str)
        platform_norm = self._worldstate_platform_from_tokens(tokens)

        rec = self._parse_cycle_subscribe_query(raw_args=query_str, tokens=tokens)
        if not rec:
            rec = self._parse_fissure_subscribe_query(raw_args=query_str, tokens=tokens)
            if not rec:
                rec = await self.guess_fissure_subscribe_query_via_llm(
                    event=event, query=query_str, platform_norm=platform_norm
                )
        if not rec:
            return "用法：/退订 钢铁赛中 或 /退订 夜灵平原 黑夜"

        session = event.unified_msg_origin
        removed = 0
        async with self._lock:
            new_list: list[dict] = []
            for s in self._subscriptions:
                if not isinstance(s, dict):
                    continue

                if str(rec.get("type") or "fissure") == "cycle":
                    if (
                        str(s.get("type") or "fissure") == "cycle"
                        and s.get("session") == session
                        and s.get("platform") == rec.get("platform")
                        and s.get("cycle") == rec.get("cycle")
                        and s.get("state") == rec.get("state")
                    ):
                        removed += 1
                        continue
                else:
                    if (
                        str(s.get("type") or "fissure") != "cycle"
                        and s.get("session") == session
                        and s.get("platform") == rec.get("platform")
                        and s.get("kind") == rec.get("kind")
                        and s.get("planet") == rec.get("planet")
                        and str(s.get("tier") or "") == str(rec.get("tier") or "")
                        and s.get("mission_type") == rec.get("mission_type")
                    ):
                        removed += 1
                        continue
                new_list.append(s)
            self._subscriptions = new_list
            self._save()

        if removed:
            if str(rec.get("type") or "fissure") == "cycle":
                return (
                    f"已退订：{rec.get('plain', '夜灵平原')} {rec.get('state', '?')}（{rec.get('platform', 'pc')}）"
                )
            return (
                f"已退订：{rec['kind']} {rec['planet']}{(rec.get('tier') or '')}{rec['mission_type']}（{rec['platform']}）"
            )

        return "未找到对应订阅记录。"

    def _fissure_sig(self, f) -> str:
        try:
            return (
                f"{int(bool(getattr(f, 'is_hard', False)))}|"
                f"{int(bool(getattr(f, 'is_storm', False)))}|"
                f"{getattr(f, 'tier', '')}|{getattr(f, 'mission_type', '')}|{getattr(f, 'node', '')}"
            )
        except Exception:
            return ""

    def _match_fissure(self, *, sub: dict, f) -> bool:
        kind = str(sub.get("kind") or "普通")
        planet = str(sub.get("planet") or "").strip()
        tier = str(sub.get("tier") or "").strip()
        mission_type = str(sub.get("mission_type") or "").strip()
        if not planet or not mission_type:
            return False

        if kind == "九重天" and not getattr(f, "is_storm", False):
            return False
        if kind == "钢铁" and not getattr(f, "is_hard", False):
            return False
        if kind == "普通" and (getattr(f, "is_hard", False) or getattr(f, "is_storm", False)):
            return False

        node = str(getattr(f, "node", "") or "")
        mt = str(getattr(f, "mission_type", "") or "")
        ft = str(getattr(f, "tier", "") or "")
        if planet not in node:
            return False
        if tier and tier != ft:
            return False
        if mission_type != mt:
            return False
        return True

    async def _poll_loop(self) -> None:
        while not self._stop:
            try:
                async with self._lock:
                    subs = [dict(s) for s in self._subscriptions if isinstance(s, dict)]

                if not subs:
                    await asyncio.sleep(10)
                    continue

                by_platform: dict[str, list[dict]] = {}
                for s in subs:
                    by_platform.setdefault(str(s.get("platform") or "pc"), []).append(s)

                updated: dict[str, dict] = {}
                to_remove_ids: set[str] = set()

                for platform_norm, s_list in by_platform.items():
                    if platform_norm not in {"pc", "ps4", "xb1", "swi"}:
                        platform_norm = "pc"

                    fissure_subs: list[dict] = []
                    cycle_subs: list[dict] = []
                    for s in s_list:
                        if str(s.get("type") or "fissure") == "cycle":
                            cycle_subs.append(s)
                        else:
                            fissure_subs.append(s)

                    fissures: list = []
                    if fissure_subs:
                        fetched = await self._worldstate_client.fetch_fissures(
                            platform=cast(Platform, platform_norm), language="zh"
                        )
                        if isinstance(fetched, list):
                            fissures = fetched
                        else:
                            fissure_subs = []

                    for s in fissure_subs:
                        session = s.get("session")
                        if not isinstance(session, str) or not session:
                            continue

                        remaining = s.get("remaining")
                        if isinstance(remaining, int) and remaining <= 0:
                            sid = str(s.get("id") or "").strip()
                            if sid:
                                to_remove_ids.add(sid)
                            continue

                        matches = [f for f in fissures if self._match_fissure(sub=s, f=f)]
                        sigs = [self._fissure_sig(f) for f in matches]
                        sigs = [x for x in sigs if x]
                        sigs.sort()

                        last_sigs = s.get("last_sigs")
                        if not isinstance(last_sigs, list):
                            last_sigs = []

                        if sigs and sigs != last_sigs:
                            planet = str(s.get("planet") or "")
                            mt = str(s.get("mission_type") or "")
                            kind = str(s.get("kind") or "普通")

                            lines = [
                                f"【裂缝订阅】已匹配到：{kind} {planet}{mt}（{platform_norm}）"
                            ]
                            for f in matches[:5]:
                                tag = "钢铁" if f.is_hard else ("九重天" if f.is_storm else "普通")
                                lines.append(
                                    f"- {tag} {f.tier} {f.mission_type} {f.node} | 剩余{f.eta}"
                                )
                            msg = MessageChain().message("\n".join(lines))
                            await self._context.send_message(session, msg)

                            if isinstance(remaining, int):
                                remaining = int(remaining) - 1
                                s["remaining"] = remaining
                                if remaining <= 0:
                                    sid = str(s.get("id") or "").strip()
                                    if sid:
                                        to_remove_ids.add(sid)

                        s["last_sigs"] = sigs
                        sid = str(s.get("id") or "").strip()
                        if sid:
                            updated[sid] = s

                    if cycle_subs:
                        cetus = await self._worldstate_client.fetch_cetus_cycle(
                            platform=cast(Platform, platform_norm), language="zh"
                        )
                        if cetus is None:
                            continue

                        current_state = cetus.state or (
                            "白天" if cetus.is_day else ("夜晚" if cetus.is_day is False else "未知")
                        )
                        left = cetus.time_left or cetus.eta

                        for s in cycle_subs:
                            session = s.get("session")
                            if not isinstance(session, str) or not session:
                                continue

                            remaining = s.get("remaining")
                            if isinstance(remaining, int) and remaining <= 0:
                                sid = str(s.get("id") or "").strip()
                                if sid:
                                    to_remove_ids.add(sid)
                                continue

                            desired = str(s.get("state") or "").strip()
                            if desired not in {"白天", "夜晚"}:
                                sid = str(s.get("id") or "").strip()
                                if sid:
                                    updated[sid] = s
                                continue

                            last_state = s.get("last_state")
                            if not isinstance(last_state, str):
                                last_state = ""

                            if current_state != last_state and current_state == desired:
                                lines = [
                                    f"【平原订阅】夜灵平原已进入：{current_state}（{platform_norm}）",
                                    f"剩余{left}",
                                ]
                                msg = MessageChain().message("\n".join(lines))
                                await self._context.send_message(session, msg)

                                if isinstance(remaining, int):
                                    remaining = int(remaining) - 1
                                    s["remaining"] = remaining
                                    if remaining <= 0:
                                        sid = str(s.get("id") or "").strip()
                                        if sid:
                                            to_remove_ids.add(sid)

                            s["last_state"] = current_state
                            sid = str(s.get("id") or "").strip()
                            if sid:
                                updated[sid] = s

                if updated:
                    async with self._lock:
                        new_list: list[dict] = []
                        for old in self._subscriptions:
                            if not isinstance(old, dict):
                                continue
                            sid = str(old.get("id") or "").strip()
                            if sid and sid in to_remove_ids:
                                continue
                            if sid and sid in updated:
                                new_list.append(updated[sid])
                            else:
                                new_list.append(old)
                        self._subscriptions = new_list
                        self._save()

                if to_remove_ids and not updated:
                    async with self._lock:
                        self._subscriptions = [
                            s
                            for s in self._subscriptions
                            if isinstance(s, dict) and str(s.get("id") or "").strip() not in to_remove_ids
                        ]
                        self._save()

                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(f"subscription loop error: {exc!s}")
                await asyncio.sleep(30)
