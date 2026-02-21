from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from ...clients.market_client import MarketOrder, RivenAuction
from ...constants import RIVEN_POLARITY_CN, RIVEN_STAT_CN
from ...helpers import presence_rank, uniq_lower
from ...mappers.riven_mapping import RivenWeapon
from ...mappers.term_mapping import MarketItem
from ...renderers.wm_render import RenderedImage as WMRenderedImage
from ...renderers.wm_render import render_wm_orders_image_to_file
from ...renderers.wmr_render import RenderedImage as WMRRenderedImage
from ...renderers.wmr_render import render_wmr_auctions_image_to_file

T = TypeVar("T")


def pick_page(items: list[T], *, page: int, limit: int) -> list[T]:
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 10), 20))
    offset = (page - 1) * limit
    return items[offset : offset + limit]


def filter_sort_wm_orders(
    orders: Iterable[MarketOrder], *, platform: str, order_type: str
) -> list[MarketOrder]:
    platform_norm = (platform or "pc").strip().lower()
    order_type_norm = (order_type or "sell").strip().lower()

    filtered = [
        o
        for o in orders
        if o.visible
        and o.order_type == order_type_norm
        and (o.platform or "").strip().lower() == platform_norm
    ]

    filtered.sort(
        key=lambda o: (
            presence_rank(o.status),
            int(o.platinum),
            (o.ingame_name or ""),
        ),
    )
    return filtered


async def render_wm_page_image(
    *,
    item: MarketItem,
    orders: list[MarketOrder],
    platform: str,
    order_type: str,
    language: str,
    page: int,
    limit: int,
) -> tuple[WMRenderedImage | None, list[MarketOrder]]:
    top = pick_page(orders, page=page, limit=limit)
    if not top:
        return None, []

    action_cn = "收购" if (order_type or "sell") == "buy" else "出售"
    rendered = await render_wm_orders_image_to_file(
        item=item,
        orders=top,
        platform=(platform or "pc"),
        action_cn=action_cn,
        language=(language or "zh"),
        limit=max(1, min(int(limit or 10), 20)),
    )
    return rendered, top


def _wmr_fit_score(
    a: RivenAuction,
    *,
    req_pos: set[str],
    req_neg: set[str],
    negative_required: bool,
    mastery_rank_min: int | None,
    polarity: str | None,
) -> int:
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
        if (a.polarity or "").strip().lower() == str(polarity).strip().lower():
            score += 5
        else:
            score -= 5

    if mastery_rank_min is not None and a.mastery_level is not None:
        score -= abs(int(a.mastery_level) - int(mastery_rank_min))

    return score


def rank_wmr_auctions(
    auctions: Iterable[RivenAuction],
    *,
    platform: str,
    positive_stats: list[str],
    negative_stats: list[str],
    negative_required: bool,
    mastery_rank_min: int | None,
    polarity: str | None,
) -> list[RivenAuction]:
    platform_norm = (platform or "pc").strip().lower()

    filtered = [
        a
        for a in auctions
        if a.visible
        and (not a.closed)
        and a.is_direct_sell
        and (a.platform or "").strip().lower() == platform_norm
    ]

    if negative_required and not negative_stats:
        filtered = [a for a in filtered if any((not x.positive) for x in a.attributes)]

    req_pos = set(uniq_lower(positive_stats))
    req_neg = set(uniq_lower(negative_stats))

    scored: list[tuple[int, RivenAuction]] = [
        (
            _wmr_fit_score(
                a,
                req_pos=req_pos,
                req_neg=req_neg,
                negative_required=bool(negative_required),
                mastery_rank_min=mastery_rank_min,
                polarity=polarity,
            ),
            a,
        )
        for a in filtered
    ]

    scored.sort(
        key=lambda x: (
            -int(x[0]),
            presence_rank(x[1].owner_status),
            int(x[1].buyout_price or 0),
            (x[1].auction_id or ""),
        ),
    )

    return [a for _, a in scored]


def build_wmr_summary(
    *,
    positive_stats: list[str],
    negative_stats: list[str],
    negative_required: bool,
    mastery_rank_min: int | None,
    polarity: str | None,
) -> str:
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
        parts.append("极性" + RIVEN_POLARITY_CN.get(str(polarity), str(polarity)))

    return " ".join(parts) if parts else "(无筛选)"


async def render_wmr_page_image(
    *,
    weapon: RivenWeapon,
    weapon_query: str,
    auctions_ranked: list[RivenAuction],
    platform: str,
    language: str,
    positive_stats: list[str],
    negative_stats: list[str],
    negative_required: bool,
    mastery_rank_min: int | None,
    polarity: str | None,
    page: int,
    limit: int,
) -> tuple[WMRRenderedImage | None, list[RivenAuction], str]:
    picked = pick_page(auctions_ranked, page=page, limit=limit)
    if not picked:
        return None, [], ""

    picked.sort(
        key=lambda a: (
            presence_rank(a.owner_status),
            int(a.buyout_price or 0),
            (a.owner_name or ""),
        ),
    )

    summary = build_wmr_summary(
        positive_stats=positive_stats,
        negative_stats=negative_stats,
        negative_required=negative_required,
        mastery_rank_min=mastery_rank_min,
        polarity=polarity,
    )

    rendered = await render_wmr_auctions_image_to_file(
        weapon=weapon,
        weapon_display_name=(
            "" if (language or "zh").startswith("en") else weapon_query
        ),
        auctions=list(picked),
        platform=(platform or "pc"),
        summary=summary,
        limit=len(picked),
    )

    return rendered, list(picked), summary
