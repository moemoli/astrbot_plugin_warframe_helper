from __future__ import annotations

import random

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class QQOfficialWebhookPager:
    def __init__(self, *, keyboard_template_id: str) -> None:
        self._keyboard_template_id = (keyboard_template_id or "").strip()

    def enabled_for(self, event: AstrMessageEvent) -> bool:
        if not self._keyboard_template_id:
            return False
        try:
            return event.get_platform_name() == "qq_official_webhook"
        except Exception:
            return False

    async def send_pager_keyboard(
        self,
        event: AstrMessageEvent,
        *,
        kind: str,
        page: int,
    ) -> None:
        """Send a markdown message with a keyboard template (buttons) on QQ official webhook.

        This bypasses AstrBot's generic send path, so it does not affect other platforms.
        """

        if not self.enabled_for(event):
            return

        bot = getattr(event, "bot", None)
        if not bot or not getattr(bot, "api", None):
            return

        try:
            from botpy.http import Route
            from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message
        except Exception:
            return

        source = getattr(event.message_obj, "raw_message", None)

        markdown_text = (
            f"翻页：{kind} 第{max(1, int(page))}页\n\n使用下方按钮上一页/下一页"
        )
        payload: dict = {
            "msg_type": 2,
            "markdown": {"content": markdown_text},
            "keyboard": {"id": self._keyboard_template_id},
            "msg_id": getattr(event.message_obj, "message_id", None),
        }

        payload["msg_seq"] = random.randint(1, 10000)

        route = None
        try:
            if isinstance(source, GroupMessage):
                group_openid = getattr(source, "group_openid", None)
                if not group_openid:
                    return
                route = Route(
                    "POST",
                    "/v2/groups/{group_openid}/messages",
                    group_openid=group_openid,
                )
            elif isinstance(source, C2CMessage):
                openid = getattr(getattr(source, "author", None), "user_openid", None)
                if not openid:
                    return
                route = Route("POST", "/v2/users/{openid}/messages", openid=openid)
            elif isinstance(source, Message):
                channel_id = getattr(source, "channel_id", None)
                if not channel_id:
                    return
                route = Route(
                    "POST",
                    "/channels/{channel_id}/messages",
                    channel_id=channel_id,
                )
            elif isinstance(source, DirectMessage):
                guild_id = getattr(source, "guild_id", None)
                if not guild_id:
                    return
                route = Route("POST", "/dms/{guild_id}/messages", guild_id=guild_id)
            else:
                return

            await bot.api._http.request(route, json=payload)
        except Exception as exc:
            logger.warning(f"QQ pager keyboard send failed: {exc!s}")
            return
