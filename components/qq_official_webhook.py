from __future__ import annotations

import random
from collections.abc import Awaitable, Callable

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class QQOfficialWebhookPager:
    def __init__(
        self,
        *,
        keyboard_template_id: str,
        markdown_template_id: str = "",
        enable_markdown_reply: bool = False,
    ) -> None:
        self._keyboard_template_id = (keyboard_template_id or "").strip()
        self._markdown_template_id = (markdown_template_id or "").strip()
        self._enable_markdown_reply = bool(enable_markdown_reply)
        self._interaction_handler: (
            Callable[[object, object], Awaitable[None]] | None
        ) = None
        self._hooked_client_ids: set[int] = set()

    @property
    def enable_markdown_reply(self) -> bool:
        return self._enable_markdown_reply

    def set_interaction_handler(
        self,
        handler: Callable[[object, object], Awaitable[None]] | None,
    ) -> None:
        self._interaction_handler = handler

    def _maybe_hook_interactions(self, event: AstrMessageEvent) -> None:
        if not self._interaction_handler:
            return

        bot = getattr(event, "bot", None)
        if not bot:
            return

        bot_id = id(bot)
        if bot_id in self._hooked_client_ids:
            return

        prev = getattr(bot, "on_interaction_create", None)

        async def on_interaction_create(interaction):
            if callable(prev) and prev is not on_interaction_create:
                try:
                    await prev(interaction)
                except Exception as exc:
                    logger.warning(f"QQ interaction prev handler failed: {exc!s}")

            try:
                await self._interaction_handler(bot, interaction)
            except Exception as exc:
                logger.warning(f"QQ interaction handler failed: {exc!s}")

        try:
            # botpy dispatch uses getattr(self, 'on_' + event_name)
            # and schedules it as a coroutine. Setting an attribute is enough.
            setattr(bot, "on_interaction_create", on_interaction_create)
            self._hooked_client_ids.add(bot_id)
        except Exception as exc:
            logger.warning(f"QQ interaction hook install failed: {exc!s}")
            return

    def enabled_for(self, event: AstrMessageEvent) -> bool:
        if not self._enable_markdown_reply:
            return False
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

        self._maybe_hook_interactions(event)

        bot = getattr(event, "bot", None)
        if not bot or not getattr(bot, "api", None):
            return

        try:
            from botpy.http import Route
            from botpy.interaction import Interaction
            from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message
        except Exception:
            return

        source = getattr(event.message_obj, "raw_message", None)

        page_norm = max(1, int(page))

        markdown: dict
        if self._markdown_template_id:
            # Template variables are defined in the QQ bot console.
            # Keep params <= 9.
            markdown = {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": ["Warframe 助手"]},
                    {"key": "kind", "values": [str(kind)]},
                    {"key": "page", "values": [str(page_norm)]},
                    {
                        "key": "hint",
                        "values": ["使用下方按钮：上一页 / 下一页"],
                    },
                ],
            }
        else:
            markdown_text = f"翻页：{kind} 第{page_norm}页\n\n使用下方按钮上一页/下一页"
            markdown = {"content": markdown_text}

        payload: dict = {
            "msg_type": 2,
            "markdown": markdown,
            "keyboard": {"id": self._keyboard_template_id},
            "msg_id": getattr(event.message_obj, "message_id", None),
        }

        payload["msg_seq"] = random.randint(1, 10000)

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
            elif isinstance(source, Interaction):
                await self._send_pager_keyboard_for_interaction(
                    bot,
                    source,
                    kind=kind,
                    page=page_norm,
                )
                return
            else:
                return

            await bot.api._http.request(route, json=payload)
        except Exception as exc:
            logger.warning(f"QQ pager keyboard send failed: {exc!s}")
            return

    async def _send_pager_keyboard_for_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        kind: str,
        page: int,
    ) -> None:
        try:
            from botpy.http import Route
        except Exception:
            return

        page_norm = max(1, int(page))

        markdown: dict
        if self._markdown_template_id:
            markdown = {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": ["Warframe 助手"]},
                    {"key": "kind", "values": [str(kind)]},
                    {"key": "page", "values": [str(page_norm)]},
                    {"key": "hint", "values": ["使用下方按钮：上一页 / 下一页"]},
                ],
            }
        else:
            markdown_text = f"翻页：{kind} 第{page_norm}页\n\n使用下方按钮上一页/下一页"
            markdown = {"content": markdown_text}

        resolved = getattr(getattr(interaction, "data", None), "resolved", None)
        msg_id = getattr(resolved, "message_id", None)

        payload: dict = {
            "msg_type": 2,
            "markdown": markdown,
            "keyboard": {"id": self._keyboard_template_id},
            "msg_id": msg_id,
            "msg_seq": random.randint(1, 10000),
        }

        route = None
        group_openid = getattr(interaction, "group_openid", None)
        user_openid = getattr(interaction, "user_openid", None)
        channel_id = getattr(interaction, "channel_id", None)
        guild_id = getattr(interaction, "guild_id", None)

        if group_openid:
            route = Route(
                "POST",
                "/v2/groups/{group_openid}/messages",
                group_openid=group_openid,
            )
        elif user_openid:
            route = Route("POST", "/v2/users/{openid}/messages", openid=user_openid)
        elif channel_id:
            route = Route(
                "POST",
                "/channels/{channel_id}/messages",
                channel_id=channel_id,
            )
        elif guild_id:
            route = Route("POST", "/dms/{guild_id}/messages", guild_id=guild_id)
        else:
            return

        try:
            await bot.api._http.request(route, json=payload)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning(f"QQ pager keyboard send failed (interaction): {exc!s}")
            return

    async def send_markdown_notice(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        content: str,
    ) -> None:
        """Send a markdown message (template preferred) without keyboard.

        This is used to make QQ official replies look consistent.
        """

        if not self.enabled_for(event):
            return

        self._maybe_hook_interactions(event)

        bot = getattr(event, "bot", None)
        if not bot or not getattr(bot, "api", None):
            return

        try:
            from botpy.http import Route
            from botpy.interaction import Interaction
            from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message
        except Exception:
            return

        source = getattr(event.message_obj, "raw_message", None)

        markdown: dict
        if self._markdown_template_id:
            markdown = {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": [str(title).strip() or "提示"]},
                    {"key": "kind", "values": ["-"]},
                    {"key": "page", "values": ["-"]},
                    {"key": "hint", "values": [str(content).strip() or ""]},
                ],
            }
        else:
            markdown = {
                "content": f"# {str(title).strip() or '提示'}\n\n{str(content).strip()}"
            }

        payload: dict = {
            "msg_type": 2,
            "markdown": markdown,
            "msg_id": getattr(event.message_obj, "message_id", None),
            "msg_seq": random.randint(1, 10000),
        }

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
            elif isinstance(source, Interaction):
                await self._send_markdown_notice_for_interaction(
                    bot,
                    source,
                    title=title,
                    content=content,
                )
                return
            else:
                return

            await bot.api._http.request(route, json=payload)
        except Exception as exc:
            logger.warning(f"QQ markdown notice send failed: {exc!s}")
            return

    async def send_markdown_notice_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        title: str,
        content: str,
    ) -> None:
        if not self._enable_markdown_reply:
            return
        if not self._keyboard_template_id:
            return
        await self._send_markdown_notice_for_interaction(
            bot,
            interaction,
            title=title,
            content=content,
        )

    async def send_pager_keyboard_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        kind: str,
        page: int,
    ) -> None:
        if not self._enable_markdown_reply:
            return
        if not self._keyboard_template_id:
            return
        await self._send_pager_keyboard_for_interaction(
            bot,
            interaction,
            kind=kind,
            page=page,
        )

    async def _send_markdown_notice_for_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        title: str,
        content: str,
    ) -> None:
        try:
            from botpy.http import Route
        except Exception:
            return

        markdown: dict
        if self._markdown_template_id:
            markdown = {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": [str(title).strip() or "提示"]},
                    {"key": "kind", "values": ["-"]},
                    {"key": "page", "values": ["-"]},
                    {"key": "hint", "values": [str(content).strip() or ""]},
                ],
            }
        else:
            markdown = {
                "content": f"# {str(title).strip() or '提示'}\n\n{str(content).strip()}"
            }

        resolved = getattr(getattr(interaction, "data", None), "resolved", None)
        msg_id = getattr(resolved, "message_id", None)

        payload: dict = {
            "msg_type": 2,
            "markdown": markdown,
            "msg_id": msg_id,
            "msg_seq": random.randint(1, 10000),
        }

        route = None
        group_openid = getattr(interaction, "group_openid", None)
        user_openid = getattr(interaction, "user_openid", None)
        channel_id = getattr(interaction, "channel_id", None)
        guild_id = getattr(interaction, "guild_id", None)

        if group_openid:
            route = Route(
                "POST",
                "/v2/groups/{group_openid}/messages",
                group_openid=group_openid,
            )
        elif user_openid:
            route = Route("POST", "/v2/users/{openid}/messages", openid=user_openid)
        elif channel_id:
            route = Route(
                "POST",
                "/channels/{channel_id}/messages",
                channel_id=channel_id,
            )
        elif guild_id:
            route = Route("POST", "/dms/{guild_id}/messages", guild_id=guild_id)
        else:
            return

        try:
            await bot.api._http.request(route, json=payload)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning(f"QQ markdown notice send failed (interaction): {exc!s}")
            return
