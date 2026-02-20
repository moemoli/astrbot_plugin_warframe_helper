from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import aiohttp

from astrbot.api import logger

WARFRAME_STAT_BASE_URL = "https://api.warframestat.us"

Platform = Literal["pc", "ps4", "xb1", "swi"]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # warframestat.us commonly returns ISO8601 with trailing 'Z'
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def format_eta(expiry_iso: str | None) -> str:
    dt = _parse_iso_datetime(expiry_iso)
    if not dt:
        return "未知"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - _now_utc()
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


@dataclass(frozen=True, slots=True)
class CetusCycleInfo:
    state: str | None
    is_day: bool | None
    time_left: str | None
    eta: str


@dataclass(frozen=True, slots=True)
class CambionCycleInfo:
    state: str | None
    active: str | None
    time_left: str | None
    eta: str


@dataclass(frozen=True, slots=True)
class VallisCycleInfo:
    state: str | None
    is_warm: bool | None
    time_left: str | None
    eta: str


@dataclass(frozen=True, slots=True)
class DuviriCycleInfo:
    state: str | None
    time_left: str | None
    eta: str


class WarframeWorldstateClient:
    def __init__(
        self, *, http_timeout_sec: float = 10.0, cache_ttl_sec: float = 30.0
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=http_timeout_sec)
        self._cache_ttl_sec = float(cache_ttl_sec)
        self._cache: dict[str, tuple[float, Any]] = {}

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

    async def _get_json(
        self, path: str, *, platform: Platform, language: str
    ) -> Any | None:
        platform_norm: Platform = platform
        lang = (language or "zh").strip().lower() or "zh"
        url = f"{WARFRAME_STAT_BASE_URL}/{platform_norm}/{path.lstrip('/')}?language={aiohttp.helpers.quote(lang, safe='')}"

        cache_key = f"{platform_norm}:{path}:{lang}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        headers = {
            "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
            "Accept": "application/json",
        }

        try:
            async with aiohttp.ClientSession(
                timeout=self._timeout, trust_env=True
            ) as s:
                async with s.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"warframestat request failed: {resp.status} {url}"
                        )
                        return None
                    data = await resp.json()
        except Exception as exc:
            logger.warning(f"warframestat request error: {exc!s}")
            return None

        self._cache_put(cache_key, data)
        return data

    async def fetch_alerts(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> list[AlertInfo] | None:
        data = await self._get_json("alerts", platform=platform, language=language)
        if data is None:
            return None
        if not isinstance(data, list):
            return []

        out: list[AlertInfo] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            mission = row.get("mission")
            if not isinstance(mission, dict):
                continue

            node = mission.get("node") if isinstance(mission.get("node"), str) else ""
            mission_type = (
                mission.get("type") if isinstance(mission.get("type"), str) else ""
            )
            faction = (
                mission.get("faction")
                if isinstance(mission.get("faction"), str)
                else None
            )

            reward = None
            rew = mission.get("reward")
            if isinstance(rew, dict):
                reward = (
                    rew.get("asString")
                    if isinstance(rew.get("asString"), str)
                    else None
                )

            min_level = (
                mission.get("minEnemyLevel")
                if isinstance(mission.get("minEnemyLevel"), int)
                else None
            )
            max_level = (
                mission.get("maxEnemyLevel")
                if isinstance(mission.get("maxEnemyLevel"), int)
                else None
            )

            expiry = row.get("expiry") if isinstance(row.get("expiry"), str) else None
            out.append(
                AlertInfo(
                    node=node or "?",
                    mission_type=mission_type or "?",
                    faction=faction,
                    reward=reward,
                    min_level=min_level,
                    max_level=max_level,
                    eta=format_eta(expiry),
                )
            )
        return out

    async def fetch_fissures(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> list[FissureInfo] | None:
        data = await self._get_json("fissures", platform=platform, language=language)
        if data is None:
            return None
        if not isinstance(data, list):
            return []

        out: list[FissureInfo] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            node = row.get("node") if isinstance(row.get("node"), str) else ""
            mission_type = (
                row.get("missionType")
                if isinstance(row.get("missionType"), str)
                else ""
            )
            tier = row.get("tier") if isinstance(row.get("tier"), str) else ""
            enemy = row.get("enemy") if isinstance(row.get("enemy"), str) else None
            is_storm = bool(row.get("isStorm"))
            is_hard = bool(row.get("isHard"))
            expiry = row.get("expiry") if isinstance(row.get("expiry"), str) else None

            out.append(
                FissureInfo(
                    node=node or "?",
                    mission_type=mission_type or "?",
                    tier=tier or "?",
                    enemy=enemy,
                    is_storm=is_storm,
                    is_hard=is_hard,
                    eta=format_eta(expiry),
                )
            )
        return out

    async def fetch_sortie(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> SortieInfo | None:
        data = await self._get_json("sortie", platform=platform, language=language)
        if not isinstance(data, dict):
            return None

        boss = data.get("boss") if isinstance(data.get("boss"), str) else None
        faction = data.get("faction") if isinstance(data.get("faction"), str) else None
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None

        variants = data.get("variants")
        stages: list[SortieStage] = []
        if isinstance(variants, list):
            for v in variants:
                if not isinstance(v, dict):
                    continue
                node_raw = v.get("node")
                node = node_raw if isinstance(node_raw, str) else "?"
                mission_type_raw = v.get("missionType")
                mission_type = (
                    mission_type_raw if isinstance(mission_type_raw, str) else "?"
                )
                modifier_raw = v.get("modifier")
                modifier = modifier_raw if isinstance(modifier_raw, str) else None
                stages.append(
                    SortieStage(node=node, mission_type=mission_type, modifier=modifier)
                )

        return SortieInfo(
            boss=boss,
            faction=faction,
            eta=format_eta(expiry),
            stages=tuple(stages),
        )

    async def fetch_syndicates(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> list[SyndicateInfo] | None:
        data = await self._get_json(
            "syndicateMissions", platform=platform, language=language
        )
        if data is None:
            return None
        if not isinstance(data, list):
            return []

        out: list[SyndicateInfo] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            name = row.get("syndicate") if isinstance(row.get("syndicate"), str) else ""
            expiry = row.get("expiry") if isinstance(row.get("expiry"), str) else None
            jobs_raw = row.get("jobs")
            jobs: list[SyndicateJob] = []
            if isinstance(jobs_raw, list):
                for j in jobs_raw:
                    if not isinstance(j, dict):
                        continue
                    node = j.get("node") if isinstance(j.get("node"), str) else None
                    mission_type = (
                        j.get("type") if isinstance(j.get("type"), str) else None
                    )
                    jobs.append(
                        SyndicateJob(
                            node=node, mission_type=mission_type, eta=format_eta(expiry)
                        )
                    )
            out.append(
                SyndicateInfo(
                    name=name or "?", eta=format_eta(expiry), jobs=tuple(jobs)
                )
            )
        return out

    async def fetch_void_trader(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> VoidTraderInfo | None:
        data = await self._get_json("voidTrader", platform=platform, language=language)
        if not isinstance(data, dict):
            return None

        active = bool(data.get("active"))
        location = (
            data.get("location") if isinstance(data.get("location"), str) else None
        )
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None

        inv_raw = data.get("inventory")
        inv: list[VoidTraderItem] = []
        if isinstance(inv_raw, list):
            for it in inv_raw:
                if not isinstance(it, dict):
                    continue
                name = it.get("item") if isinstance(it.get("item"), str) else None
                if not name:
                    continue
                ducats = it.get("ducats") if isinstance(it.get("ducats"), int) else None
                credits = (
                    it.get("credits") if isinstance(it.get("credits"), int) else None
                )
                inv.append(VoidTraderItem(item=name, ducats=ducats, credits=credits))

        return VoidTraderInfo(
            active=active,
            location=location,
            eta=format_eta(expiry),
            inventory=tuple(inv),
        )

    async def fetch_arbitration(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> ArbitrationInfo | None:
        data = await self._get_json("arbitration", platform=platform, language=language)
        if not isinstance(data, dict):
            return None

        node = data.get("node") if isinstance(data.get("node"), str) else None
        mission_type = data.get("type") if isinstance(data.get("type"), str) else None
        enemy = data.get("enemy") if isinstance(data.get("enemy"), str) else None
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None

        if not node or not mission_type:
            return None

        return ArbitrationInfo(
            node=node, mission_type=mission_type, enemy=enemy, eta=format_eta(expiry)
        )

    async def fetch_nightwave(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> NightwaveInfo | None:
        data = await self._get_json("nightwave", platform=platform, language=language)
        if not isinstance(data, dict):
            return None

        season = data.get("season") if isinstance(data.get("season"), int) else None
        phase = data.get("phase") if isinstance(data.get("phase"), int) else None
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None

        active_raw = data.get("activeChallenges")
        challenges: list[NightwaveChallenge] = []
        if isinstance(active_raw, list):
            for c in active_raw:
                if not isinstance(c, dict):
                    continue
                title = c.get("title") if isinstance(c.get("title"), str) else None
                if not title:
                    continue
                is_daily = bool(c.get("isDaily"))
                rep = (
                    c.get("reputation")
                    if isinstance(c.get("reputation"), int)
                    else None
                )
                c_expiry = (
                    c.get("expiry") if isinstance(c.get("expiry"), str) else expiry
                )
                challenges.append(
                    NightwaveChallenge(
                        title=title,
                        is_daily=is_daily,
                        reputation=rep,
                        eta=format_eta(c_expiry),
                    )
                )

        return NightwaveInfo(
            season=season,
            phase=phase,
            eta=format_eta(expiry),
            active_challenges=tuple(challenges),
        )

    async def fetch_invasions(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> list[InvasionInfo] | None:
        data = await self._get_json("invasions", platform=platform, language=language)
        if data is None:
            return None
        if not isinstance(data, list):
            return []

        out: list[InvasionInfo] = []
        for row in data:
            if not isinstance(row, dict):
                continue

            node = row.get("node") if isinstance(row.get("node"), str) else None
            if not node:
                continue
            attacker = (
                row.get("attackingFaction")
                if isinstance(row.get("attackingFaction"), str)
                else None
            )
            defender = (
                row.get("defendingFaction")
                if isinstance(row.get("defendingFaction"), str)
                else None
            )
            completion = (
                row.get("completion")
                if isinstance(row.get("completion"), (int, float))
                else None
            )
            expiry = row.get("expiry") if isinstance(row.get("expiry"), str) else None

            # reward: attackerReward/defenderReward has asString
            reward = None
            ar = row.get("attackerReward")
            dr = row.get("defenderReward")
            ar_s = (
                ar.get("asString")
                if isinstance(ar, dict) and isinstance(ar.get("asString"), str)
                else None
            )
            dr_s = (
                dr.get("asString")
                if isinstance(dr, dict) and isinstance(dr.get("asString"), str)
                else None
            )
            if ar_s and dr_s:
                reward = f"攻:{ar_s} / 守:{dr_s}"
            elif ar_s:
                reward = f"攻:{ar_s}"
            elif dr_s:
                reward = f"守:{dr_s}"

            out.append(
                InvasionInfo(
                    node=node,
                    attacker=attacker,
                    defender=defender,
                    completion=float(completion) if completion is not None else None,
                    eta=format_eta(expiry),
                    reward=reward,
                )
            )

        return out

    async def fetch_earth_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> EarthCycleInfo | None:
        data = await self._get_json("earthCycle", platform=platform, language=language)
        if not isinstance(data, dict):
            return None

        state = data.get("state") if isinstance(data.get("state"), str) else None
        is_day = data.get("isDay") if isinstance(data.get("isDay"), bool) else None
        time_left = (
            data.get("timeLeft") if isinstance(data.get("timeLeft"), str) else None
        )
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None
        return EarthCycleInfo(
            state=state, is_day=is_day, time_left=time_left, eta=format_eta(expiry)
        )

    async def fetch_cetus_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> CetusCycleInfo | None:
        data = await self._get_json("cetusCycle", platform=platform, language=language)
        if not isinstance(data, dict):
            return None

        state = data.get("state") if isinstance(data.get("state"), str) else None
        is_day = data.get("isDay") if isinstance(data.get("isDay"), bool) else None
        time_left = (
            data.get("timeLeft") if isinstance(data.get("timeLeft"), str) else None
        )
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None
        return CetusCycleInfo(
            state=state, is_day=is_day, time_left=time_left, eta=format_eta(expiry)
        )

    async def fetch_cambion_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> CambionCycleInfo | None:
        data = await self._get_json(
            "cambionCycle", platform=platform, language=language
        )
        if not isinstance(data, dict):
            return None

        state = data.get("state") if isinstance(data.get("state"), str) else None
        active = data.get("active") if isinstance(data.get("active"), str) else None
        time_left = (
            data.get("timeLeft") if isinstance(data.get("timeLeft"), str) else None
        )
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None
        return CambionCycleInfo(
            state=state, active=active, time_left=time_left, eta=format_eta(expiry)
        )

    async def fetch_vallis_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> VallisCycleInfo | None:
        data = await self._get_json("vallisCycle", platform=platform, language=language)
        if not isinstance(data, dict):
            return None

        state = data.get("state") if isinstance(data.get("state"), str) else None
        is_warm = data.get("isWarm") if isinstance(data.get("isWarm"), bool) else None
        time_left = (
            data.get("timeLeft") if isinstance(data.get("timeLeft"), str) else None
        )
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None
        return VallisCycleInfo(
            state=state, is_warm=is_warm, time_left=time_left, eta=format_eta(expiry)
        )

    async def fetch_duviri_cycle(
        self, *, platform: Platform = "pc", language: str = "zh"
    ) -> DuviriCycleInfo | None:
        data = await self._get_json("duviriCycle", platform=platform, language=language)
        if not isinstance(data, dict):
            return None

        state = data.get("state") if isinstance(data.get("state"), str) else None
        time_left = (
            data.get("timeLeft") if isinstance(data.get("timeLeft"), str) else None
        )
        expiry = data.get("expiry") if isinstance(data.get("expiry"), str) else None
        return DuviriCycleInfo(state=state, time_left=time_left, eta=format_eta(expiry))
