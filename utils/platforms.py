from __future__ import annotations

from typing import cast

from ..constants import MARKET_PLATFORM_ALIASES, WORLDSTATE_PLATFORM_ALIASES
from ..helpers import eta_key_zh, parse_platform
from ..clients.worldstate_client import Platform


def worldstate_platform_from_tokens(tokens: list[str]) -> Platform:
    p = parse_platform(tokens, WORLDSTATE_PLATFORM_ALIASES, default="pc")
    if p in {"pc", "ps4", "xb1", "swi"}:
        return cast(Platform, p)
    return "pc"


def market_platform_from_tokens(tokens: list[str]) -> str:
    return parse_platform(tokens, MARKET_PLATFORM_ALIASES, default="pc")


def eta_key(text: str) -> int:
    return eta_key_zh(text)
