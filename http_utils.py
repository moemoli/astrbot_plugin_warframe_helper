from __future__ import annotations

import json
from typing import Any

import aiohttp

from astrbot.api import logger


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": "AstrBot/warframe_helper (+https://github.com/Soulter/AstrBot)",
        "Accept": "*/*",
    }


async def fetch_bytes(
    urls: str | list[str],
    *,
    timeout_sec: float = 20.0,
    headers: dict[str, str] | None = None,
) -> bytes | None:
    url_list = [urls] if isinstance(urls, str) else [u for u in urls if u]
    if not url_list:
        return None

    req_headers = {**_default_headers(), **(headers or {})}
    timeout = aiohttp.ClientTimeout(total=float(timeout_sec))

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        last_err: str | None = None
        for url in url_list:
            try:
                async with session.get(url, headers=req_headers) as resp:
                    if resp.status != 200:
                        last_err = f"{resp.status} {url}"
                        continue
                    return await resp.read()
            except Exception as exc:
                last_err = f"{exc!s} ({url})"
                continue

        if last_err:
            logger.warning(f"http fetch_bytes failed: {last_err}")
        return None


async def fetch_json(
    urls: str | list[str],
    *,
    timeout_sec: float = 20.0,
    headers: dict[str, str] | None = None,
) -> Any | None:
    raw = await fetch_bytes(urls, timeout_sec=timeout_sec, headers=headers)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        logger.warning(f"http fetch_json decode failed: {exc!s}")
        return None
