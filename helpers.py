from __future__ import annotations

import re
from collections.abc import Mapping


def split_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", (text or "").strip()) if t]


def parse_platform(
    tokens: list[str], alias_map: Mapping[str, str], *, default: str = "pc"
) -> str:
    for token in tokens:
        token_norm = str(token).strip().lower()
        if not token_norm:
            continue
        if token_norm in alias_map:
            return alias_map[token_norm]
        if token_norm in alias_map.values():
            return token_norm
    return default


def presence_rank(status: str | None) -> int:
    s = (status or "").strip().lower()
    if s == "ingame":
        return 0
    if s == "online":
        return 1
    if s == "offline":
        return 2
    return 3


def uniq_lower(seq: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in seq:
        norm = str(item).strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def eta_key_zh(eta_text: str) -> int:
    s = (eta_text or "").strip()

    m = re.fullmatch(r"(\d+)天(\d+)小时", s)
    if m:
        return int(m.group(1)) * 86400 + int(m.group(2)) * 3600

    m = re.fullmatch(r"(\d+)小时(\d+)分", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60

    m = re.fullmatch(r"(\d+)分", s)
    if m:
        return int(m.group(1)) * 60

    return 999_999
