from __future__ import annotations

import re


def normalize_compact(text: str) -> str:
    """Normalize to a compact lowercase string without whitespace."""

    return re.sub(r"\s+", "", str(text or "").strip().lower())


def safe_relic_name(text: str) -> str:
    """Keep only ASCII alnum for relic name matching."""

    s = re.sub(r"\s+", "", str(text or "").strip())
    s = re.sub(r"[^A-Za-z0-9]", "", s)
    return s
