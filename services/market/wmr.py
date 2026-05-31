from __future__ import annotations

import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ...clients.market_client import WarframeMarketClient
from ...components.event_ttl_cache import EventScopedTTLCache
from ...components.qq_official_webhook import QQOfficialWebhookPager
from ...constants import MARKET_PLATFORM_ALIASES
from ...constants import market_status_to_cn
from ...helpers import split_tokens, uniq_lower
from ...mappers.riven_mapping import RivenWeapon, WarframeRivenWeaponMapper
from ...mappers.riven_stats_mapping import WarframeRivenStatMapper
from .pager_common import (
    rank_wmr_auctions,
    render_wmr_page_image,
    resort_wmr_auctions_by_presence,
)


_WMR_USAGE_TEXT = (
    "用法：/wmr|/wr|/wk <武器与词条可混写> [条件...]\n"
    "示例：/wmr 基伤 双暴 伯斯顿\n"
    "示例：/wmr 基伤 逐枭 负暴率\n"
    "说明：负任意=必须有任意负词条；无负=不能有负词条。"
)


def _normalize_key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip().lower())


def _is_wmr_control_token(token_norm: str) -> bool:
    t = (token_norm or "").strip().lower()
    if not t:
        return True

    if t in MARKET_PLATFORM_ALIASES or t in MARKET_PLATFORM_ALIASES.values():
        return True
    if t.isdigit():
        return True

    if re.fullmatch(r"mr?(\d{1,2})", t):
        return True
    if re.fullmatch(r"(\d{1,2})段", t):
        return True

    if re.fullmatch(r"([vd\-r])槽", t):
        return True
    if re.fullmatch(r"([vd\-r])极性", t):
        return True
    if re.fullmatch(r"极性([vd\-r])", t):
        return True
    if t in {"madurai", "vazarin", "naramon", "zenurik"}:
        return True

    if t in {"负任意", "任意负", "有负", "要负", "无负", "不要负", "不带负"}:
        return True
    if "双暴" in t or "双爆" in t:
        return True
    # Zero-roll keywords.
    if t in {"零洗", "0洗", "零roll", "0roll", "零循环", "0循环"}:
        return True

    return False


async def _extract_weapon_and_rest_tokens(
    *,
    tokens: list[str],
    riven_weapon_mapper: WarframeRivenWeaponMapper,
) -> tuple[str, RivenWeapon, list[str]] | None:
    if not tokens:
        return None

    await riven_weapon_mapper.initialize()

    local_cache: dict[str, RivenWeapon | None] = {}
    best: tuple[tuple[int, int, int, int], int, int, str, RivenWeapon] | None = None

    total = len(tokens)
    for start in range(total):
        for end in range(start + 1, total + 1):
            span_tokens = tokens[start:end]
            span_norm = [_normalize_key(x) for x in span_tokens]
            if not all(span_norm):
                continue

            # Skip spans containing obvious non-weapon control tokens.
            if any(_is_wmr_control_token(x) for x in span_norm):
                continue

            query = " ".join(span_tokens).strip()
            if not query:
                continue

            if query not in local_cache:
                local_cache[query] = await riven_weapon_mapper.resolve_weapon_local(
                    query=query
                )
            weapon = local_cache[query]
            if weapon is None:
                continue

            # Prefer longer spans, then longer normalized content, then later position.
            score = (
                end - start,
                sum(len(x) for x in span_norm),
                end,
                -start,
            )
            if best is None or score > best[0]:
                best = (score, start, end, query, weapon)

    if best is None:
        return None

    _, start, end, query, weapon = best
    rest = tokens[:start] + tokens[end:]
    return query, weapon, rest


async def cmd_wmr(
    *,
    context: object,
    event: AstrMessageEvent,
    raw_args: str,
    config: dict | None,
    market_client: WarframeMarketClient,
    riven_weapon_mapper: WarframeRivenWeaponMapper,
    riven_stat_mapper: WarframeRivenStatMapper,
    pager_cache: EventScopedTTLCache,
    qq_pager: QQOfficialWebhookPager,
):
    try:
        event.should_call_llm(True)
    except Exception as exc:
        logger.debug(f"Failed to disable LLM for /wmr: {exc!s}")

    arg_text = str(raw_args or "").strip()
    if not arg_text:
        yield event.plain_result(_WMR_USAGE_TEXT)
        return

    tokens = split_tokens(arg_text)
    if not tokens:
        yield event.plain_result(_WMR_USAGE_TEXT)
        return

    weapon_query = tokens[0]
    weapon: RivenWeapon | None = None
    rest = tokens[1:]

    extracted_weapon = await _extract_weapon_and_rest_tokens(
        tokens=tokens,
        riven_weapon_mapper=riven_weapon_mapper,
    )
    if extracted_weapon is not None:
        weapon_query, weapon, rest = extracted_weapon

    platform_norm = "pc"
    limit = 10
    language = "zh"

    positive_stats: list[str] = []
    negative_stats: list[str] = []
    negative_required = False
    negative_forbidden = False
    mastery_rank_min: int | None = None
    polarity: str | None = None
    re_rolls: int | None = None  # None=不限, 0=零洗, N=N洗

    pending_stats: list[tuple[str, bool]] = []
    unknown_tokens: list[str] = []
    unresolved_stat_tokens: list[str] = []

    await riven_stat_mapper.initialize()

    for t in rest:
        t_norm = _normalize_key(t)
        if not t_norm:
            continue

        if t_norm in MARKET_PLATFORM_ALIASES:
            platform_norm = MARKET_PLATFORM_ALIASES[t_norm]
            continue
        if t_norm in MARKET_PLATFORM_ALIASES.values():
            platform_norm = t_norm
            continue

        if t_norm.isdigit():
            limit = int(t_norm)
            continue

        if re.fullmatch(r"[a-z]{2}([\-_][a-z]{2,8})?", t_norm):
            language = t_norm.replace("_", "-")
            continue

        m = re.fullmatch(r"mr?(\d{1,2})", t_norm)
        if m:
            mastery_rank_min = int(m.group(1))
            continue
        m = re.fullmatch(r"(\d{1,2})段", t_norm)
        if m:
            mastery_rank_min = int(m.group(1))
            continue

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

        if "双暴" in t_norm or "双爆" in t_norm:
            positive_stats.extend(["critical_chance", "critical_damage"])
            rest_tok = t_norm.replace("双暴", "").replace("双爆", "")
            if "毒" in rest_tok:
                positive_stats.append("toxin_damage")
            if "火" in rest_tok:
                positive_stats.append("heat_damage")
            if "冰" in rest_tok:
                positive_stats.append("cold_damage")
            if "电" in rest_tok:
                positive_stats.append("electric_damage")
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
            if "射速" in rest_tok or "攻速" in rest_tok:
                positive_stats.append("fire_rate_/_attack_speed")
            if "范围" in rest_tok:
                positive_stats.append("range")
            if "触发" in rest_tok:
                positive_stats.append("status_chance")
            if "穿透" in rest_tok:
                positive_stats.append("punch_through")
            if "处决" in rest_tok:
                positive_stats.append("finisher_damage")
            if "连击" in rest_tok and "时间" in rest_tok:
                positive_stats.append("combo_duration")
            if "初始" in rest_tok:
                positive_stats.append("channeling_damage")
            continue

        # Zero-roll filter: "零洗", "0洗", etc.
        if t_norm in {"零洗", "0洗", "零roll", "0roll", "零循环", "0循环"}:
            re_rolls = 0
            continue
        # Numeric roll filter: "N洗" e.g. "5洗" -> re_rolls=5
        m_roll = re.fullmatch(r"(\d{1,3})\s*洗", t_norm)
        if m_roll:
            re_rolls = int(m_roll.group(1))
            continue
        m_roll = re.fullmatch(r"(\d{1,3})\s*roll", t_norm)
        if m_roll:
            re_rolls = int(m_roll.group(1))
            continue

        if t_norm in {"负任意", "任意负", "有负", "要负"} or "负任意" in t_norm:
            negative_required = True
            negative_forbidden = False
            continue
        if t_norm in {"无负", "不要负", "不带负"}:
            negative_required = False
            negative_forbidden = True
            negative_stats = []
            continue
        if t_norm.startswith("负") and len(t_norm) > 1:
            negative_required = True
            negative_forbidden = False
            key = t_norm[1:]
            url_name = riven_stat_mapper.resolve_token(key)
            if url_name:
                if url_name == "damage_vs_sentient":
                    pending_stats.append((url_name, True))
                else:
                    negative_stats.append(url_name)
                continue

        direct_url_name = riven_stat_mapper.resolve_token(t_norm)
        if direct_url_name:
            url_name = direct_url_name
            if url_name == "damage_vs_sentient":
                pending_stats.append((url_name, False))
            else:
                positive_stats.append(url_name)
            continue

        if "暴击率" in t_norm or "爆击" in t_norm or "爆率" in t_norm:
            positive_stats.append("critical_chance")
            continue
        if "暴击伤害" in t_norm or "暴伤" in t_norm or "爆伤" in t_norm:
            positive_stats.append("critical_damage")
            continue
        if "投射物速度" in t_norm or "投射速度" in t_norm or "投射" in t_norm or "弹速" in t_norm:
            positive_stats.append("projectile_speed")
            continue
        if "穿透" in t_norm:
            positive_stats.append("punch_through")
            continue
        if "触发时间" in t_norm or "异常时间" in t_norm:
            positive_stats.append("status_duration")
            continue
        if "后坐力" in t_norm or "后座" in t_norm:
            positive_stats.append("recoil")
            continue
        if "初始连击" in t_norm:
            positive_stats.append("channeling_damage")
            continue
        if "重击效率" in t_norm or "导引效率" in t_norm:
            positive_stats.append("channeling_efficiency")
            continue
        if "处决伤害" in t_norm or "处决" in t_norm:
            positive_stats.append("finisher_damage")
            continue
        if "射速" in t_norm or "攻速" in t_norm:
            positive_stats.append("fire_rate_/_attack_speed")
            continue
        if "触发几率" in t_norm or "触发率" in t_norm:
            positive_stats.append("status_chance")
            continue
        if "连击持续时间" in t_norm or "连击时间" in t_norm:
            positive_stats.append("combo_duration")
            continue
        if "滑砍暴击" in t_norm or "滑暴" in t_norm or "滑行攻击暴击率" in t_norm:
            positive_stats.append("critical_chance_on_slide_attack")
            continue
        if "额外连击" in t_norm:
            positive_stats.append("chance_to_gain_extra_combo_count")
            continue
        if "连击几率" in t_norm or "连击率" in t_norm:
            positive_stats.append("chance_to_gain_combo_count")
            continue

        unknown_tokens.append(str(t).strip())

    for url_name, is_negative in pending_stats:
        if riven_stat_mapper.is_valid_url_name(url_name):
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

    for tok in unknown_tokens:
        tok2 = (tok or "").strip()
        if not tok2:
            continue

        is_negative = False
        query_tok = tok2
        if tok2.startswith("负") and len(tok2) > 1:
            is_negative = True
            negative_required = True
            negative_forbidden = False
            query_tok = tok2[1:]

        resolved = riven_stat_mapper.resolve_token(query_tok)

        if not resolved:
            parts = riven_stat_mapper.split_compound_token(query_tok)
            matched_part = False
            for part in parts:
                part_resolved = riven_stat_mapper.resolve_token(part)
                if not part_resolved:
                    continue
                matched_part = True
                if is_negative:
                    negative_stats.append(part_resolved)
                else:
                    positive_stats.append(part_resolved)
            if not matched_part:
                unresolved_stat_tokens.append(tok2)
            continue

        if is_negative:
            negative_stats.append(resolved)
        else:
            positive_stats.append(resolved)

    if unresolved_stat_tokens:
        unresolved_text = "、".join(unresolved_stat_tokens)
        yield event.plain_result(f"没有找到相关紫卡词条：{unresolved_text}")
        return

    positive_stats = uniq_lower(positive_stats)
    negative_stats = uniq_lower(negative_stats)
    attr_units = riven_stat_mapper.get_unit_map()

    if negative_forbidden:
        negative_required = False
        negative_stats = []

    if weapon is None:
        weapon = await riven_weapon_mapper.resolve_weapon(
            context=context,
            event=event,
            query=weapon_query,
        )
    if not weapon:
        yield event.plain_result(
            f"未识别武器：{weapon_query}（可尝试输入英文名，如 soma）"
        )
        return

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
        yield event.plain_result("未获取到紫卡拍卖数据（接口请求失败或不可达）。")
        return
    if not auctions:
        yield event.plain_result("没有符合条件的一口价紫卡拍卖。")
        return

    ranked = rank_wmr_auctions(
        auctions,
        platform=platform_norm,
        positive_stats=positive_stats,
        negative_stats=negative_stats,
        negative_required=bool(negative_required),
        negative_forbidden=bool(negative_forbidden),
        mastery_rank_min=mastery_rank_min,
        polarity=polarity,
        re_rolls=re_rolls,
    )
    ranked = resort_wmr_auctions_by_presence(ranked)

    limit = max(1, min(int(limit), 20))
    page = 1

    if not ranked:
        yield event.plain_result("没有符合条件的一口价紫卡拍卖。")
        return

    pager_cache.put(
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
            "negative_forbidden": bool(negative_forbidden),
            "mastery_rank_min": mastery_rank_min,
            "polarity": polarity,
            "re_rolls": re_rolls,
            "riven_attr_units": dict(attr_units),
            "reply_msg_id": str(
                getattr(getattr(event, "message_obj", None), "message_id", None) or ""
            ),
        },
    )

    rendered, top, summary = await render_wmr_page_image(
        weapon=weapon,
        weapon_query=weapon_query,
        auctions_ranked=ranked,
        platform=platform_norm,
        language=language,
        positive_stats=list(positive_stats),
        negative_stats=list(negative_stats),
        negative_required=bool(negative_required),
        negative_forbidden=bool(negative_forbidden),
        mastery_rank_min=mastery_rank_min,
        polarity=polarity,
        page=page,
        limit=limit,
        attr_units=attr_units,
        re_rolls=re_rolls,
    )

    if not top:
        yield event.plain_result("没有符合条件的一口价紫卡拍卖。")
        return

    if rendered:
        if qq_pager.enabled_for(event):
            ok = await qq_pager.send_result_markdown_with_keyboard(
                event,
                kind="/wmr",
                page=page,
                image_path=rendered.path,
                title="紫卡拍卖",
            )
            if ok:
                return

            try:
                await event.send(event.image_result(rendered.path))
                await qq_pager.send_pager_keyboard(event, kind="/wmr", page=page)
                return
            except Exception:
                yield event.image_result(rendered.path)
                return

        yield event.image_result(rendered.path)
        return

    fallback_name = weapon.item_name if language.startswith("en") else weapon_query
    lines = [f"紫卡 {fallback_name}（{platform_norm}）{summary} 前{len(top)}："]
    for idx, a in enumerate(top, start=1):
        name = a.owner_name or "unknown"
        status = market_status_to_cn(a.owner_status)
        pol = a.polarity or "?"
        mr = a.mastery_level if a.mastery_level is not None else "?"
        rr = a.re_rolls if a.re_rolls is not None else "?"
        lines.append(
            f"{idx}. {a.buyout_price}p  {status}  {name}  MR{mr}  {pol}  洗练{rr}"
        )
    yield event.plain_result("\n".join(lines))

    if qq_pager.enabled_for(event):
        try:
            await qq_pager.send_pager_keyboard(event, kind="/wmr", page=page)
        except Exception:
            pass
