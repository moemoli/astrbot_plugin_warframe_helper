from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any

APP_KEY_V2 = "2020#$wegame!act$#v2"


def _serialize_get_params(params: Mapping[str, Any] | None) -> str:
    if not params:
        return ""

    parts: list[str] = []
    for key, value in params.items():
        if isinstance(value, Mapping):
            for sub_key, sub_value in value.items():
                parts.append(f"{key}[{sub_key}]={sub_value}")
        else:
            parts.append(f"{key}={value}")
    return "&".join(parts)


def _decode_uri_like_js(value: str) -> str:
    reserved = ";/?:@&=+$,#"

    def replacer(match: re.Match[str]) -> str:
        hex_text = match.group(1)
        char = bytes.fromhex(hex_text).decode("latin-1")
        if char in reserved:
            return f"%{hex_text.upper()}"
        return char

    return re.sub(r"%([0-9A-Fa-f]{2})", replacer, value)


def build_signed_wegame_url(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    method: str = "GET",
    appid: str = "10003",
    version: str = "2",
    server_time: int | None = None,
    no_sign: bool = False,
) -> str:
    if not url:
        raise ValueError("url is required")

    if no_sign:
        return url

    method_lower = method.lower()
    final_url = url

    if method_lower == "get":
        query = _serialize_get_params(params)
        if query:
            final_url = (
                f"{final_url}&{query}" if "?" in final_url else f"{final_url}?{query}"
            )

    timestamp = str(server_time if server_time is not None else round(time.time()))
    common_query = f"uuid={uuid.uuid4()}&version={version}&appid={appid}&t={timestamp}"
    final_url = (
        f"{final_url}&{common_query}"
        if "?" in final_url
        else f"{final_url}?{common_query}"
    )

    split_token = "index.php/"
    if split_token not in final_url:
        raise ValueError("url must contain 'index.php/' for sign generation")

    sign_target = final_url.split(split_token, 1)[1]
    path_part = sign_target.split("?", 1)[0]

    if path_part.endswith("/"):
        path_part = path_part[:-1]
        query_part = sign_target.split("?", 1)[1] if "?" in sign_target else ""
        sign_target = f"{path_part}?{query_part}" if query_part else path_part

    plain = f"/{_decode_uri_like_js(sign_target)}&appkey={APP_KEY_V2}"
    sign = hashlib.md5(plain.encode("utf-8")).hexdigest()
    return f"{final_url}&sign={sign}"
