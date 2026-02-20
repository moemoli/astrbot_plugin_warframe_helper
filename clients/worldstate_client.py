from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from astrbot.api import logger

from ..http_utils import fetch_json
from .public_export_client import PublicExportClient

Platform = Literal["pc", "ps4", "xb1", "swi"]


OFFICIAL_WORLDSTATE_URLS: list[str] = [
    # In some environments api.warframe.com may return 403.
    # content.warframe.com is typically accessible and returns the same payload.
    "https://content.warframe.com/dynamic/worldState.php",
    "https://api.warframe.com/cdn/worldState.php",
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ws_date(value: Any) -> datetime | None:
    if value is None:
        return None

    # Official worldState uses {"$date": {"$numberLong": "<ms>"}}
    if isinstance(value, dict):
        d = value.get("$date")
        if isinstance(d, dict):
            n = d.get("$numberLong")
            try:
                if not isinstance(n, (str, int, float)):
                    return None
                ms = int(n)
                return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
            except Exception:
                return None
        return None

    # Sometimes we may get a plain epoch ms/sec
    if isinstance(value, (int, float)):
        n = float(value)
        if n > 10_000_000_000:  # ms
            return datetime.fromtimestamp(n / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(n, tz=timezone.utc)

    return None


def _format_eta_from_dt(expiry: datetime | None) -> str:
    if not expiry:
        return "未知"
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    delta = expiry - _now_utc()
    sec = int(delta.total_seconds())
    if sec <= 0:
        return "已结束"

    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)

    if days > 0:
        return f"{days}天{hours}小时"
    if hours > 0:
        return f"{hours}小时{mins}分"
    return f"{mins}分"


def _format_time_left(sec: int) -> str:
    sec = max(0, int(sec))
    if sec <= 0:
        return "0分"
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}天{hours}小时"
    if hours > 0:
        return f"{hours}小时{mins}分"
    return f"{mins}分"


def _format_dt_local(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _env_epoch(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


@dataclass(frozen=True, slots=True)
class CycleCalcResult:
    state: str
    phase_start_utc: datetime
    phase_end_utc: datetime


def _compute_two_phase_cycle(
    server_time_utc: datetime,
    *,
    epoch_sec: int,
    phase_a_sec: int,
    phase_b_sec: int,
    phase_a_name: str,
    phase_b_name: str,
) -> CycleCalcResult:
    total = int(phase_a_sec) + int(phase_b_sec)
    now_sec = int(server_time_utc.timestamp())
    t = now_sec - int(epoch_sec)
    t_mod = t % total
    if t_mod < phase_a_sec:
        start = now_sec - t_mod
        end = start + int(phase_a_sec)
        return CycleCalcResult(
            state=phase_a_name,
            phase_start_utc=datetime.fromtimestamp(start, tz=timezone.utc),
            phase_end_utc=datetime.fromtimestamp(end, tz=timezone.utc),
        )
    start = now_sec - t_mod + int(phase_a_sec)
    end = start + int(phase_b_sec)
    return CycleCalcResult(
        state=phase_b_name,
        phase_start_utc=datetime.fromtimestamp(start, tz=timezone.utc),
        phase_end_utc=datetime.fromtimestamp(end, tz=timezone.utc),
    )


@dataclass(frozen=True, slots=True)
class AlertInfo:
    node: str
    mission_type: str
    faction: str | None
    reward: str | None
    min_level: int | None
    max_level: int | None
    eta: str


@dataclass(frozen=True, slots=True)
class FissureInfo:
    node: str
    mission_type: str
    tier: str
    enemy: str | None
    is_storm: bool
    is_hard: bool
    eta: str


@dataclass(frozen=True, slots=True)
class SortieStage:
    node: str
    mission_type: str
    modifier: str | None


@dataclass(frozen=True, slots=True)
class SortieInfo:
    boss: str | None
    faction: str | None
    eta: str
    stages: tuple[SortieStage, ...]


@dataclass(frozen=True, slots=True)
class SyndicateJob:
    node: str | None
    mission_type: str | None
    eta: str


@dataclass(frozen=True, slots=True)
class SyndicateInfo:
    name: str
    eta: str
    jobs: tuple[SyndicateJob, ...]


@dataclass(frozen=True, slots=True)
class VoidTraderItem:
    item: str
    ducats: int | None
    credits: int | None


@dataclass(frozen=True, slots=True)
class VoidTraderInfo:
    active: bool
    location: str | None
    eta: str
    inventory: tuple[VoidTraderItem, ...]


@dataclass(frozen=True, slots=True)
class ArbitrationInfo:
    node: str
    mission_type: str
    enemy: str | None
    eta: str


@dataclass(frozen=True, slots=True)
class NightwaveChallenge:
    title: str
    is_daily: bool
    reputation: int | None
    eta: str


@dataclass(frozen=True, slots=True)
class NightwaveInfo:
    season: int | None
    phase: int | None
    eta: str
    active_challenges: tuple[NightwaveChallenge, ...]


@dataclass(frozen=True, slots=True)
class InvasionInfo:
    node: str
    attacker: str | None
    defender: str | None
    completion: float | None
    eta: str
    reward: str | None


@dataclass(frozen=True, slots=True)
class EarthCycleInfo:
    state: str | None
    is_day: bool | None
    time_left: str | None
    eta: str
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True, slots=True)
class CetusCycleInfo:
    state: str | None
    is_day: bool | None
    time_left: str | None
    eta: str
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True, slots=True)
class CambionCycleInfo:
    state: str | None
    active: str | None
    time_left: str | None
    eta: str
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True, slots=True)
class VallisCycleInfo:
    state: str | None
    is_warm: bool | None
    time_left: str | None
    eta: str
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True, slots=True)
class DuviriCycleInfo:
    state: str | None
    time_left: str | None
    eta: str
    start_time: str | None = None
    end_time: str | None = None


class WarframeWorldstateClient:
    def __init__(
        self, *, http_timeout_sec: float = 10.0, cache_ttl_sec: float = 30.0
    ) -> None:
        self._cache_ttl_sec = float(cache_ttl_sec)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._http_timeout_sec = float(http_timeout_sec)
        self._public_export = PublicExportClient(http_timeout_sec=http_timeout_sec)

        # Cycle epochs (seconds). Set via env vars if you need to calibrate.
        self._epoch_cetus = _env_epoch("ASTRBOT_WF_CETUS_EPOCH", 0)
        self._epoch_cambion = _env_epoch("ASTRBOT_WF_CAMBION_EPOCH", 0)
        self._epoch_vallis = _env_epoch("ASTRBOT_WF_VALLIS_EPOCH", 0)
        self._epoch_earth = _env_epoch("ASTRBOT_WF_EARTH_EPOCH", 0)
        self._epoch_duviri = _env_epoch("ASTRBOT_WF_DUVIRI_EPOCH", 0)

    def _cache_get(self, key: str) -> Any | None:
        rec = self._cache.get(key)
        if not rec:
            return None
        ts, payload = rec
        if (time.time() - float(ts)) > self._cache_ttl_sec:
            return None
        return payload

    def _cache_put(self, key: str, payload: Any) -> None:
        self._cache[key] = (time.time(), payload)

    async def _get_worldstate(self, *, platform: Platform, language: str) -> Any | None:
        platform_norm: Platform = platform
        lang = (language or "zh").strip().lower() or "zh"

        cache_key = f"worldstate:{platform_norm}:{lang}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        data = await fetch_json(
            OFFICIAL_WORLDSTATE_URLS, timeout_sec=self._http_timeout_sec
        )
        if data is None:
            return None

        self._cache_put(cache_key, data)
        return data

    async def _node_name(self, node: str | None, *, language: str) -> str:
        node = (node or "").strip()
        if not node:
            return "?"
        translated = await self._public_export.translate_region(node, language=language)
        return translated or node

    async def _item_name(self, unique_name: str, *, language: str) -> str | None:
        return await self._public_export.translate_unique_name(
            unique_name, language=language
        )

    def _mission_type_cn(self, code: str | None) -> str:
        code = (code or "").strip()
        mt = {
            "MT_EXTERMINATION": "歼灭",
            "MT_SURVIVAL": "生存",
            "MT_DEFENSE": "防御",
            "MT_MOBILE_DEFENSE": "移动防御",
            "MT_RESCUE": "救援",
            "MT_SABOTAGE": "破坏",
            "MT_CAPTURE": "捕获",
            "MT_INTERCEPTION": "拦截",
            "MT_HIJACK": "劫持",
            "MT_ASSASSINATION": "刺杀",
            "MT_SPY": "间谍",
            "MT_EXCAVATE": "挖掘",
            "MT_ALCHEMY": "炼金",
            "MT_VOID_CASCADE": "虚空瀑流",
            "MT_CORRUPTION": "腐化",
        }
        return mt.get(code, code.replace("MT_", "") or "?")

    def _fissure_tier_cn(self, modifier: str | None) -> str:
        modifier = (modifier or "").strip()
        tier = {
            "VoidT1": "古纪",
            "VoidT2": "前纪",
            "VoidT3": "中纪",
            "VoidT4": "后纪",
            "VoidT5": "安魂",
            "VoidT6": "全能",
        }
        return tier.get(modifier, modifier or "?")

    async def _format_counted_items(
        self, counted_items: Any, *, language: str
    ) -> list[str]:
        out: list[str] = []
        if not isinstance(counted_items, list):
            return out
        for it in counted_items:
            if not isinstance(it, dict):
                continue
            item_type = it.get("ItemType")
            item_count = it.get("ItemCount")
            if not isinstance(item_type, str):
                continue
            name = await self._item_name(item_type, language=language)
            if isinstance(item_count, int) and item_count > 1:
                out.append(f"{name or item_type} x{item_count}")
            else:
                out.append(name or item_type)
        return out

    async def fetch_alerts(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> list[AlertInfo] | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None
        alerts = ws.get("Alerts")
        if not isinstance(alerts, list):
            return []

        out: list[AlertInfo] = []
        for row in alerts:
            if not isinstance(row, dict):
                continue
            mi = row.get("MissionInfo")
            if not isinstance(mi, dict):
                continue

            node_raw = mi.get("location") or mi.get("Node")
            node_u = node_raw if isinstance(node_raw, str) else ""
            node = await self._node_name(node_u, language=language)

            mt_raw = mi.get("missionType")
            mission_code = mt_raw if isinstance(mt_raw, str) else ""
            mission_type = self._mission_type_cn(mission_code)
            faction = mi.get("faction") if isinstance(mi.get("faction"), str) else None

            min_level = (
                mi.get("minEnemyLevel")
                if isinstance(mi.get("minEnemyLevel"), int)
                else None
            )
            max_level = (
                mi.get("maxEnemyLevel")
                if isinstance(mi.get("maxEnemyLevel"), int)
                else None
            )

            reward_str: str | None = None
            mr = mi.get("missionReward")
            if isinstance(mr, dict):
                parts: list[str] = []
                credits = mr.get("credits")
                if isinstance(credits, int) and credits > 0:
                    parts.append(f"{credits}现金")
                parts.extend(
                    await self._format_counted_items(
                        mr.get("countedItems"), language=language
                    )
                )
                items = mr.get("items")
                if isinstance(items, list):
                    for it in items:
                        if not isinstance(it, str):
                            continue
                        parts.append(
                            (await self._item_name(it, language=language)) or it
                        )
                reward_str = " + ".join([p for p in parts if p]) or None

            expiry = _parse_ws_date(row.get("Expiry"))
            out.append(
                AlertInfo(
                    node=node,
                    mission_type=mission_type,
                    faction=faction,
                    reward=reward_str,
                    min_level=min_level,
                    max_level=max_level,
                    eta=_format_eta_from_dt(expiry),
                )
            )
        return out

    async def fetch_fissures(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> list[FissureInfo] | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None

        out: list[FissureInfo] = []

        active = ws.get("ActiveMissions")
        if isinstance(active, list):
            for row in active:
                if not isinstance(row, dict):
                    continue
                node_u = row.get("Node") if isinstance(row.get("Node"), str) else ""
                node = await self._node_name(node_u, language=language)
                mc_raw = row.get("MissionType")
                mission_code = mc_raw if isinstance(mc_raw, str) else ""
                mission_type = self._mission_type_cn(mission_code)
                mod_raw = row.get("Modifier")
                modifier = mod_raw if isinstance(mod_raw, str) else ""
                tier = self._fissure_tier_cn(modifier)
                is_hard = bool(row.get("Hard"))
                expiry = _parse_ws_date(row.get("Expiry"))
                out.append(
                    FissureInfo(
                        node=node,
                        mission_type=mission_type,
                        tier=tier,
                        enemy=None,
                        is_storm=False,
                        is_hard=is_hard,
                        eta=_format_eta_from_dt(expiry),
                    )
                )

        storms = ws.get("VoidStorms")
        if isinstance(storms, list):
            for row in storms:
                if not isinstance(row, dict):
                    continue
                node_u = row.get("Node") if isinstance(row.get("Node"), str) else ""
                node = await self._node_name(node_u, language=language)
                tier = self._fissure_tier_cn(
                    row.get("ActiveMissionTier")
                    if isinstance(row.get("ActiveMissionTier"), str)
                    else ""
                )
                expiry = _parse_ws_date(row.get("Expiry"))
                out.append(
                    FissureInfo(
                        node=node,
                        mission_type="九重天",
                        tier=tier,
                        enemy=None,
                        is_storm=True,
                        is_hard=False,
                        eta=_format_eta_from_dt(expiry),
                    )
                )

        return out

    async def fetch_sortie(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> SortieInfo | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None
        sorties = ws.get("Sorties")
        if not isinstance(sorties, list) or not sorties:
            return None
        so = sorties[0]
        if not isinstance(so, dict):
            return None

        boss_raw = so.get("Boss") if isinstance(so.get("Boss"), str) else None
        boss = boss_raw.replace("SORTIE_BOSS_", "") if boss_raw else None
        faction = so.get("Faction") if isinstance(so.get("Faction"), str) else None
        expiry = _parse_ws_date(so.get("Expiry"))

        variants = so.get("Variants")
        stages: list[SortieStage] = []
        if isinstance(variants, list):
            for v in variants:
                if not isinstance(v, dict):
                    continue
                node_u = v.get("node") if isinstance(v.get("node"), str) else ""
                node = await self._node_name(node_u, language=language)
                mt = self._mission_type_cn(
                    v.get("missionType")
                    if isinstance(v.get("missionType"), str)
                    else ""
                )
                mod_raw = v.get("modifierType")
                modifier = (
                    mod_raw.replace("SORTIE_MODIFIER_", "")
                    if isinstance(mod_raw, str)
                    else None
                )
                stages.append(
                    SortieStage(node=node, mission_type=mt, modifier=modifier)
                )

        return SortieInfo(
            boss=boss,
            faction=faction,
            eta=_format_eta_from_dt(expiry),
            stages=tuple(stages),
        )

    async def fetch_syndicates(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> list[SyndicateInfo] | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None
        missions = ws.get("SyndicateMissions")
        if not isinstance(missions, list):
            return []

        tag_name = {
            "ArbitersSyndicate": "仲裁者",
            "SteelMeridianSyndicate": "钢铁守望",
            "NewLokaSyndicate": "新世间",
            "PerrinSyndicate": "佩兰数列",
            "CephalonSudaSyndicate": "苏达",
            "RedVeilSyndicate": "赤色面纱",
            "CetusSyndicate": "希图斯",
            "SolarisSyndicate": "福尔图娜",
            "ZarimanSyndicate": "扎里曼",
        }

        out: list[SyndicateInfo] = []
        for row in missions:
            if not isinstance(row, dict):
                continue
            tag = row.get("Tag") if isinstance(row.get("Tag"), str) else None
            name = tag_name.get(tag or "", tag or "?")
            expiry = _parse_ws_date(row.get("Expiry"))
            eta = _format_eta_from_dt(expiry)

            jobs: list[SyndicateJob] = []
            # Open-world syndicates have Jobs
            jobs_raw = row.get("Jobs")
            if isinstance(jobs_raw, list):
                for _ in jobs_raw[:3]:
                    jobs.append(SyndicateJob(node=name, mission_type="赏金", eta=eta))
            # Regular syndicates have Nodes
            nodes = row.get("Nodes")
            if isinstance(nodes, list) and nodes:
                for n in nodes[:3]:
                    if not isinstance(n, str):
                        continue
                    jobs.append(
                        SyndicateJob(
                            node=await self._node_name(n, language=language),
                            mission_type="集团",
                            eta=eta,
                        )
                    )

            out.append(SyndicateInfo(name=name, eta=eta, jobs=tuple(jobs)))

        return out

    async def fetch_void_trader(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> VoidTraderInfo | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None
        vts = ws.get("VoidTraders")
        if not isinstance(vts, list) or not vts:
            return None
        vt = vts[0]
        if not isinstance(vt, dict):
            return None

        activation = _parse_ws_date(vt.get("Activation"))
        expiry = _parse_ws_date(vt.get("Expiry"))
        now = _now_utc()
        active = bool(activation and expiry and (activation <= now <= expiry))

        node = vt.get("Node") if isinstance(vt.get("Node"), str) else None
        hub_map = {
            "MercuryHUB": "水星中继站",
            "VenusHUB": "金星中继站",
            "EarthHUB": "地球中继站",
            "MarsHUB": "火星中继站",
            "SaturnHUB": "土星中继站",
            "PlutoHUB": "冥王星中继站",
        }
        location = hub_map.get(node or "", node)

        inv: list[VoidTraderItem] = []
        manifest = vt.get("Manifest")
        if isinstance(manifest, list):
            for it in manifest:
                if not isinstance(it, dict):
                    continue
                item_type = it.get("ItemType")
                if not isinstance(item_type, str) or not item_type:
                    continue
                item_name = (
                    await self._item_name(item_type, language=language)
                ) or item_type
                ducats = (
                    it.get("PrimePrice")
                    if isinstance(it.get("PrimePrice"), int)
                    else None
                )
                credits = (
                    it.get("RegularPrice")
                    if isinstance(it.get("RegularPrice"), int)
                    else None
                )
                inv.append(
                    VoidTraderItem(item=item_name, ducats=ducats, credits=credits)
                )

        return VoidTraderInfo(
            active=active,
            location=location,
            eta=_format_eta_from_dt(expiry),
            inventory=tuple(inv),
        )

    async def fetch_arbitration(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> ArbitrationInfo | None:
        logger.warning(
            "Official worldState does not expose arbitration data currently; returning None"
        )
        return None

    async def fetch_nightwave(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> NightwaveInfo | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None
        si = ws.get("SeasonInfo")
        if not isinstance(si, dict):
            return None

        season = si.get("Season") if isinstance(si.get("Season"), int) else None
        phase = si.get("Phase") if isinstance(si.get("Phase"), int) else None
        expiry = _parse_ws_date(si.get("Expiry"))

        mapping = await self._public_export.get_nightwave_challenge_map(
            language=language
        )
        challenges: list[NightwaveChallenge] = []
        active_raw = si.get("ActiveChallenges")
        if isinstance(active_raw, list):
            for c in active_raw:
                if not isinstance(c, dict):
                    continue
                uniq = (
                    c.get("Challenge") if isinstance(c.get("Challenge"), str) else None
                )
                if not uniq:
                    continue
                title, standing = mapping.get(uniq, (uniq.split("/")[-1], None))
                is_daily = bool(c.get("Daily"))
                c_expiry = _parse_ws_date(c.get("Expiry")) or expiry
                challenges.append(
                    NightwaveChallenge(
                        title=title,
                        is_daily=is_daily,
                        reputation=standing,
                        eta=_format_eta_from_dt(c_expiry),
                    )
                )

        return NightwaveInfo(
            season=season,
            phase=phase,
            eta=_format_eta_from_dt(expiry),
            active_challenges=tuple(challenges),
        )

    async def fetch_invasions(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> list[InvasionInfo] | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None
        invasions = ws.get("Invasions")
        if not isinstance(invasions, list):
            return []

        out: list[InvasionInfo] = []
        for row in invasions:
            if not isinstance(row, dict):
                continue
            if bool(row.get("Completed")):
                continue

            node_u = row.get("Node") if isinstance(row.get("Node"), str) else None
            if not node_u:
                continue
            node = await self._node_name(node_u, language=language)

            attacker = (
                row.get("Faction") if isinstance(row.get("Faction"), str) else None
            )
            defender = (
                row.get("DefenderFaction")
                if isinstance(row.get("DefenderFaction"), str)
                else None
            )

            goal = row.get("Goal") if isinstance(row.get("Goal"), int) else None
            count = row.get("Count") if isinstance(row.get("Count"), int) else None
            completion = None
            if goal and count is not None and goal > 0:
                completion = (float(count) / float(goal)) * 100.0

            ar = (
                row.get("AttackerReward")
                if isinstance(row.get("AttackerReward"), dict)
                else None
            )
            dr = (
                row.get("DefenderReward")
                if isinstance(row.get("DefenderReward"), dict)
                else None
            )
            ar_ci = (ar or {}).get("countedItems") or (ar or {}).get("CountedItems")
            dr_ci = (dr or {}).get("countedItems") or (dr or {}).get("CountedItems")
            ar_parts = await self._format_counted_items(ar_ci, language=language)
            dr_parts = await self._format_counted_items(dr_ci, language=language)
            reward = None
            if ar_parts and dr_parts:
                reward = f"攻:{' + '.join(ar_parts)} / 守:{' + '.join(dr_parts)}"
            elif ar_parts:
                reward = f"攻:{' + '.join(ar_parts)}"
            elif dr_parts:
                reward = f"守:{' + '.join(dr_parts)}"

            # Official schema does not expose a reliable expiry; keep as unknown.
            out.append(
                InvasionInfo(
                    node=node,
                    attacker=attacker,
                    defender=defender,
                    completion=completion,
                    eta="未知",
                    reward=reward,
                )
            )

        return out

    async def fetch_earth_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> EarthCycleInfo | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None

        server_sec = ws.get("Time")
        if not isinstance(server_sec, int):
            server_sec = int(time.time())
        server_dt = datetime.fromtimestamp(server_sec, tz=timezone.utc)

        # Earth: 4h day + 4h night
        res = _compute_two_phase_cycle(
            server_dt,
            epoch_sec=self._epoch_earth,
            phase_a_sec=4 * 60 * 60,
            phase_b_sec=4 * 60 * 60,
            phase_a_name="白天",
            phase_b_name="夜晚",
        )
        left_sec = int((res.phase_end_utc - server_dt).total_seconds())
        is_day = res.state == "白天"
        return EarthCycleInfo(
            state=res.state,
            is_day=is_day,
            time_left=_format_time_left(left_sec),
            eta=_format_time_left(left_sec),
            start_time=_format_dt_local(res.phase_start_utc),
            end_time=_format_dt_local(res.phase_end_utc),
        )

    async def fetch_cetus_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> CetusCycleInfo | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None

        server_sec = ws.get("Time")
        if not isinstance(server_sec, int):
            server_sec = int(time.time())
        server_dt = datetime.fromtimestamp(server_sec, tz=timezone.utc)

        # Cetus: 100m day + 50m night
        res = _compute_two_phase_cycle(
            server_dt,
            epoch_sec=self._epoch_cetus,
            phase_a_sec=100 * 60,
            phase_b_sec=50 * 60,
            phase_a_name="白天",
            phase_b_name="夜晚",
        )
        left_sec = int((res.phase_end_utc - server_dt).total_seconds())
        is_day = res.state == "白天"
        return CetusCycleInfo(
            state=res.state,
            is_day=is_day,
            time_left=_format_time_left(left_sec),
            eta=_format_time_left(left_sec),
            start_time=_format_dt_local(res.phase_start_utc),
            end_time=_format_dt_local(res.phase_end_utc),
        )

    async def fetch_cambion_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> CambionCycleInfo | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None

        server_sec = ws.get("Time")
        if not isinstance(server_sec, int):
            server_sec = int(time.time())
        server_dt = datetime.fromtimestamp(server_sec, tz=timezone.utc)

        # Cambion: Fass 100m + Vome 50m
        res = _compute_two_phase_cycle(
            server_dt,
            epoch_sec=self._epoch_cambion,
            phase_a_sec=100 * 60,
            phase_b_sec=50 * 60,
            phase_a_name="法斯",
            phase_b_name="沃姆",
        )
        left_sec = int((res.phase_end_utc - server_dt).total_seconds())
        return CambionCycleInfo(
            state=res.state,
            active=res.state,
            time_left=_format_time_left(left_sec),
            eta=_format_time_left(left_sec),
            start_time=_format_dt_local(res.phase_start_utc),
            end_time=_format_dt_local(res.phase_end_utc),
        )

    async def fetch_vallis_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> VallisCycleInfo | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None

        server_sec = ws.get("Time")
        if not isinstance(server_sec, int):
            server_sec = int(time.time())
        server_dt = datetime.fromtimestamp(server_sec, tz=timezone.utc)

        # Vallis: warm 6m40s + cold 20m
        res = _compute_two_phase_cycle(
            server_dt,
            epoch_sec=self._epoch_vallis,
            phase_a_sec=6 * 60 + 40,
            phase_b_sec=20 * 60,
            phase_a_name="温暖",
            phase_b_name="寒冷",
        )
        left_sec = int((res.phase_end_utc - server_dt).total_seconds())
        is_warm = res.state == "温暖"
        return VallisCycleInfo(
            state=res.state,
            is_warm=is_warm,
            time_left=_format_time_left(left_sec),
            eta=_format_time_left(left_sec),
            start_time=_format_dt_local(res.phase_start_utc),
            end_time=_format_dt_local(res.phase_end_utc),
        )

    async def fetch_duviri_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> DuviriCycleInfo | None:
        ws = await self._get_worldstate(platform=platform, language=language)
        if not isinstance(ws, dict):
            return None

        server_sec = ws.get("Time")
        if not isinstance(server_sec, int):
            server_sec = int(time.time())
        server_dt = datetime.fromtimestamp(server_sec, tz=timezone.utc)

        # Duviri spiral: 2h per state, 5-state loop.
        now_sec = int(server_dt.timestamp())
        epoch = int(self._epoch_duviri)
        step = 2 * 60 * 60
        idx = ((now_sec - epoch) // step) % 5
        order = ["欢乐", "愤怒", "嫉妒", "悲伤", "恐惧"]
        state = order[int(idx)]
        phase_start = epoch + ((now_sec - epoch) // step) * step
        phase_end = phase_start + step
        start_dt = datetime.fromtimestamp(phase_start, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(phase_end, tz=timezone.utc)
        left_sec = int((end_dt - server_dt).total_seconds())

        return DuviriCycleInfo(
            state=state,
            time_left=_format_time_left(left_sec),
            eta=_format_time_left(left_sec),
            start_time=_format_dt_local(start_dt),
            end_time=_format_dt_local(end_dt),
        )
