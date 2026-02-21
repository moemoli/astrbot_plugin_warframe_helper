from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class QQOfficialWebhookPager:
    def __init__(
        self,
        *,
        keyboard_template_id: str,
        markdown_template_id: str = "",
        enable_markdown_reply: bool = False,
        public_base_url: str = "",
    ) -> None:
        self._keyboard_template_id = (keyboard_template_id or "").strip()
        self._markdown_template_id = (markdown_template_id or "").strip()
        self._enable_markdown_reply = bool(enable_markdown_reply)
        self._public_base_url = (public_base_url or "").strip().rstrip("/")
        self._interaction_handler: (
            Callable[[object, object], Awaitable[None]] | None
        ) = None
        self._hooked_client_ids: set[int] = set()

    def _build_public_file_url(self, file_token: str) -> str | None:
        if not self._public_base_url or not file_token:
            return None
        base = self._public_base_url + "/"
        return urljoin(base, f"api/file/{file_token}")

    async def _register_file_token(
        self, file_path: str, *, timeout_sec: float = 600
    ) -> str | None:
        path = str(file_path or "").strip()
        if not path:
            return None
        try:
            from astrbot.core import file_token_service
        except Exception:
            return None
        try:
            return await file_token_service.register_file(path, timeout=timeout_sec)
        except Exception:
            return None

    async def send_result_markdown_with_keyboard(
        self,
        event: AstrMessageEvent,
        *,
        kind: str,
        page: int,
        image_path: str,
        title: str = "Warframe 助手",
        hint: str = "使用下方按钮：上一页 / 下一页",
        reply_to_msg_id: str | None = None,
    ) -> bool:
        """Send ONE message: markdown (with embedded image) + keyboard.

        Requires `public_base_url` so QQ can fetch the image URL.
        """

        if not self.keyboard_enabled_for(event):
            return False

        if not self._public_base_url:
            logger.warning(
                "QQ markdown-with-image requires `webhook_public_base_url` to be configured."
            )
            return False

        token = await self._register_file_token(image_path)
        if not token:
            return False
        image_url = self._build_public_file_url(token)
        if not image_url:
            return False

        size = self._get_image_size(image_path)
        image_w, image_h = size if size else (1280, 720)
        image_markdown = self._build_markdown_image(image_url, image_path=image_path)

        return await self._send_markdown_keyboard(
            event,
            title=title,
            kind=kind,
            page=page,
            hint=hint,
            image_url=image_url,
            image_width=image_w,
            image_height=image_h,
            image_markdown=image_markdown,
            reply_to_msg_id=reply_to_msg_id,
        )

    async def send_result_markdown_no_keyboard(
        self,
        event: AstrMessageEvent,
        *,
        kind: str,
        image_path: str,
        title: str,
        reply_to_msg_id: str | None = None,
    ) -> bool:
        """Send ONE message: markdown (with embedded image) without keyboard."""

        if not self.enabled_for(event):
            return False

        if not self._public_base_url:
            logger.warning(
                "QQ markdown-with-image requires `webhook_public_base_url` to be configured."
            )
            return False

        token = await self._register_file_token(image_path)
        if not token:
            return False
        image_url = self._build_public_file_url(token)
        if not image_url:
            return False

        size = self._get_image_size(image_path)
        image_w, image_h = size if size else (1280, 720)
        image_markdown = self._build_markdown_image(image_url, image_path=image_path)

        return await self._send_markdown_only_image(
            event,
            title=title,
            kind=kind,
            image_url=image_url,
            image_width=image_w,
            image_height=image_h,
            image_markdown=image_markdown,
            reply_to_msg_id=reply_to_msg_id,
        )

    async def send_markdown_text(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        content: str,
        reply_to_msg_id: str | None = None,
    ) -> bool:
        """Send ONE markdown message using markdown.content (no keyboard)."""

        if not self.enabled_for(event):
            return False

        self._maybe_hook_interactions(event)

        bot = getattr(event, "bot", None)
        if not bot or not getattr(bot, "api", None):
            return False

        try:
            from botpy.http import Route
            from botpy.interaction import Interaction
            from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message
        except Exception:
            return False

        source = getattr(event.message_obj, "raw_message", None)

        md_title = str(title).strip() or "提示"
        body = str(content or "").strip()
        markdown: dict = {
            "content": f"# {md_title}\n\n{body}" if body else f"# {md_title}",
        }

        msg_id = reply_to_msg_id or getattr(event.message_obj, "message_id", None)

        payload: dict = {
            "content": " ",
            "msg_type": 2,
            "markdown": markdown,
        }

        if msg_id:
            payload["msg_id"] = msg_id
            payload["msg_seq"] = random.randint(1, 10000)

        try:
            if isinstance(source, GroupMessage):
                group_openid = getattr(source, "group_openid", None)
                if not group_openid:
                    return False
                route = Route(
                    "POST",
                    "/v2/groups/{group_openid}/messages",
                    group_openid=group_openid,
                )
            elif isinstance(source, C2CMessage):
                openid = getattr(getattr(source, "author", None), "user_openid", None)
                if not openid:
                    return False
                route = Route("POST", "/v2/users/{openid}/messages", openid=openid)
            elif isinstance(source, Message):
                channel_id = getattr(source, "channel_id", None)
                if not channel_id:
                    return False
                route = Route(
                    "POST",
                    "/channels/{channel_id}/messages",
                    channel_id=channel_id,
                )
            elif isinstance(source, DirectMessage):
                guild_id = getattr(source, "guild_id", None)
                if not guild_id:
                    return False
                route = Route("POST", "/dms/{guild_id}/messages", guild_id=guild_id)
            elif isinstance(source, Interaction):
                return False
            else:
                return False

            await bot.api._http.request(route, json=payload)
            return True
        except Exception as exc:
            logger.warning(f"QQ markdown text send failed: {exc!s}")
            return False

    async def send_result_markdown_with_keyboard_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        kind: str,
        page: int,
        image_path: str,
        title: str = "Warframe 助手",
        hint: str = "使用下方按钮：上一页 / 下一页",
        reply_to_msg_id: str | None = None,
    ) -> bool:
        if not self._enable_markdown_reply or not self._keyboard_template_id:
            return False

        if not self._public_base_url:
            logger.warning(
                "QQ markdown-with-image requires `webhook_public_base_url` to be configured."
            )
            return False

        token = await self._register_file_token(image_path)
        if not token:
            return False
        image_url = self._build_public_file_url(token)
        if not image_url:
            return False

        size = self._get_image_size(image_path)
        image_w, image_h = size if size else (1280, 720)
        image_markdown = self._build_markdown_image(image_url, image_path=image_path)

        return await self._send_markdown_keyboard_for_interaction(
            bot,
            interaction,
            title=title,
            kind=kind,
            page=page,
            hint=hint,
            image_url=image_url,
            image_width=image_w,
            image_height=image_h,
            image_markdown=image_markdown,
            reply_to_msg_id=reply_to_msg_id,
        )

    def _get_image_size(self, image_path: str) -> tuple[int, int] | None:
        path = str(image_path or "").strip()
        if not path:
            return None
        try:
            from PIL import Image

            with Image.open(path) as im:
                width, height = im.size
            if width > 0 and height > 0:
                return int(width), int(height)
        except Exception:
            return None
        return None

    def _build_markdown_image(self, image_url: str, *, image_path: str) -> str:
        url = str(image_url or "").strip()
        if not url:
            return ""

        size = self._get_image_size(image_path)
        if not size:
            return f"![result]({url})"

        width, height = size
        # QQ Markdown supports setting image size via alt-text fragments.
        # Use the original image dimensions to avoid aspect-ratio stretching.
        return f"![result #{width}px #{height}px]({url})"

    async def _send_markdown_keyboard(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        kind: str,
        page: int,
        hint: str,
        image_url: str,
        image_width: int,
        image_height: int,
        image_markdown: str,
        reply_to_msg_id: str | None,
    ) -> bool:
        """Low-level send: markdown + keyboard (event path)."""

        self._maybe_hook_interactions(event)

        bot = getattr(event, "bot", None)
        if not bot or not getattr(bot, "api", None):
            return False

        try:
            from botpy.http import Route
            from botpy.interaction import Interaction
            from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message
        except Exception:
            return False

        source = getattr(event.message_obj, "raw_message", None)
        page_norm = max(1, int(page))

        def build_template_markdown() -> dict:
            return {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": [str(title).strip() or "Warframe 助手"]},
                    {"key": "kind", "values": [str(kind)]},
                    {"key": "page", "values": [f"第{page_norm}页"]},
                    {"key": "hint", "values": [str(hint)]},
                    {"key": "image", "values": [str(image_url)]},
                    {"key": "image_w", "values": [str(max(1, int(image_width)))]},
                    {"key": "image_h", "values": [str(max(1, int(image_height)))]},
                ],
            }

        def build_plain_markdown() -> dict:
            image_md = (
                str(image_markdown or "").strip() or f"![result]({str(image_url)})"
            )
            md = (
                f"# {str(title).strip() or 'Warframe 助手'}\n\n"
                f"{image_md}\n\n"
                f"**指令**：{str(kind)}  \n**页数**：第{page_norm}页  \n\n{str(hint)}"
            )
            return {"content": md}

        markdown: dict = (
            build_template_markdown()
            if self._markdown_template_id
            else build_plain_markdown()
        )

        msg_id = reply_to_msg_id or getattr(event.message_obj, "message_id", None)
        payload: dict = {
            "content": " ",
            "msg_type": 2,
            "markdown": markdown,
            "keyboard": {"id": self._keyboard_template_id},
        }
        if msg_id:
            payload["msg_id"] = msg_id
            payload["msg_seq"] = random.randint(1, 10000)

        try:
            if isinstance(source, GroupMessage):
                group_openid = getattr(source, "group_openid", None)
                if not group_openid:
                    return False
                route = Route(
                    "POST",
                    "/v2/groups/{group_openid}/messages",
                    group_openid=group_openid,
                )
            elif isinstance(source, C2CMessage):
                openid = getattr(getattr(source, "author", None), "user_openid", None)
                if not openid:
                    return False
                route = Route("POST", "/v2/users/{openid}/messages", openid=openid)
            elif isinstance(source, Message):
                channel_id = getattr(source, "channel_id", None)
                if not channel_id:
                    return False
                route = Route(
                    "POST",
                    "/channels/{channel_id}/messages",
                    channel_id=channel_id,
                )
            elif isinstance(source, DirectMessage):
                guild_id = getattr(source, "guild_id", None)
                if not guild_id:
                    return False
                route = Route("POST", "/dms/{guild_id}/messages", guild_id=guild_id)
            elif isinstance(source, Interaction):
                return await self._send_markdown_keyboard_for_interaction(
                    bot,
                    source,
                    title=title,
                    kind=kind,
                    page=page_norm,
                    hint=hint,
                    image_url=image_url,
                    image_width=image_width,
                    image_height=image_height,
                    image_markdown=image_markdown,
                )
            else:
                return False

            await bot.api._http.request(route, json=payload)
            return True
        except Exception as exc:
            if self._markdown_template_id:
                try:
                    payload_fallback = dict(payload)
                    payload_fallback["markdown"] = build_plain_markdown()
                    await bot.api._http.request(route, json=payload_fallback)
                    return True
                except Exception as exc2:
                    logger.warning(
                        "QQ markdown+keyboard send failed: %s; fallback failed: %s",
                        str(exc),
                        str(exc2),
                    )
                    return False

            logger.warning(f"QQ markdown+keyboard send failed: {exc!s}")
            return False

    async def _send_markdown_keyboard_for_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        title: str,
        kind: str,
        page: int,
        hint: str,
        image_url: str,
        image_width: int,
        image_height: int,
        image_markdown: str,
        reply_to_msg_id: str | None = None,
    ) -> bool:
        try:
            from botpy.http import Route
        except Exception:
            return False

        page_norm = max(1, int(page))

        def build_template_markdown() -> dict:
            return {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": [str(title).strip() or "Warframe 助手"]},
                    {"key": "kind", "values": [str(kind)]},
                    {"key": "page", "values": [f"第{page_norm}页"]},
                    {"key": "hint", "values": [str(hint)]},
                    {"key": "image", "values": [str(image_url)]},
                    {"key": "image_w", "values": [str(max(1, int(image_width)))]},
                    {"key": "image_h", "values": [str(max(1, int(image_height)))]},
                ],
            }

        def build_plain_markdown() -> dict:
            image_md = (
                str(image_markdown or "").strip() or f"![result]({str(image_url)})"
            )
            md = (
                f"# {str(title).strip() or 'Warframe 助手'}\n\n"
                f"{image_md}\n\n"
                f"**指令**：{str(kind)}  \n**页数**：第{page_norm}页  \n\n{str(hint)}"
            )
            return {"content": md}

        markdown: dict = (
            build_template_markdown()
            if self._markdown_template_id
            else build_plain_markdown()
        )

        msg_id = reply_to_msg_id

        # Do not send proactive messages for paging interactions.
        if not msg_id:
            return False

        payload: dict = {
            "content": " ",
            "msg_type": 2,
            "markdown": markdown,
            "keyboard": {"id": self._keyboard_template_id},
        }
        payload["msg_id"] = msg_id
        payload["msg_seq"] = random.randint(1, 10000)

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
            return False

        try:
            await bot.api._http.request(route, json=payload)  # type: ignore[attr-defined]
            return True
        except Exception as exc:
            if self._markdown_template_id:
                try:
                    payload_fallback = dict(payload)
                    payload_fallback["markdown"] = build_plain_markdown()
                    await bot.api._http.request(route, json=payload_fallback)  # type: ignore[attr-defined]
                    return True
                except Exception as exc2:
                    logger.warning(
                        "QQ markdown+keyboard send failed (interaction): %s; fallback failed: %s",
                        str(exc),
                        str(exc2),
                    )
                    return False

            logger.warning(f"QQ markdown+keyboard send failed (interaction): {exc!s}")
            return False

    async def _send_markdown_only_image(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        kind: str,
        image_url: str,
        image_width: int,
        image_height: int,
        image_markdown: str,
        reply_to_msg_id: str | None,
    ) -> bool:
        """Low-level send: markdown with embedded image, without keyboard."""

        self._maybe_hook_interactions(event)

        bot = getattr(event, "bot", None)
        if not bot or not getattr(bot, "api", None):
            return False

        try:
            from botpy.http import Route
            from botpy.interaction import Interaction
            from botpy.message import C2CMessage, DirectMessage, GroupMessage, Message
        except Exception:
            return False

        source = getattr(event.message_obj, "raw_message", None)

        def build_template_markdown() -> dict:
            return {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": [str(title).strip() or "Warframe 助手"]},
                    {"key": "kind", "values": [str(kind)]},
                    {"key": "page", "values": [" "]},
                    {"key": "hint", "values": [" "]},
                    {"key": "image", "values": [str(image_url)]},
                    {"key": "image_w", "values": [str(max(1, int(image_width)))]},
                    {"key": "image_h", "values": [str(max(1, int(image_height)))]},
                ],
            }

        def build_plain_markdown() -> dict:
            image_md = (
                str(image_markdown or "").strip() or f"![result]({str(image_url)})"
            )
            md = f"# {str(title).strip() or 'Warframe 助手'}\n\n{image_md}"
            return {"content": md}

        markdown: dict = (
            build_template_markdown()
            if self._markdown_template_id
            else build_plain_markdown()
        )

        msg_id = reply_to_msg_id or getattr(event.message_obj, "message_id", None)
        payload: dict = {
            "content": " ",
            "msg_type": 2,
            "markdown": markdown,
        }

        if msg_id:
            payload["msg_id"] = msg_id
            payload["msg_seq"] = random.randint(1, 10000)

        try:
            if isinstance(source, GroupMessage):
                group_openid = getattr(source, "group_openid", None)
                if not group_openid:
                    return False
                route = Route(
                    "POST",
                    "/v2/groups/{group_openid}/messages",
                    group_openid=group_openid,
                )
            elif isinstance(source, C2CMessage):
                openid = getattr(getattr(source, "author", None), "user_openid", None)
                if not openid:
                    return False
                route = Route("POST", "/v2/users/{openid}/messages", openid=openid)
            elif isinstance(source, Message):
                channel_id = getattr(source, "channel_id", None)
                if not channel_id:
                    return False
                route = Route(
                    "POST",
                    "/channels/{channel_id}/messages",
                    channel_id=channel_id,
                )
            elif isinstance(source, DirectMessage):
                guild_id = getattr(source, "guild_id", None)
                if not guild_id:
                    return False
                route = Route("POST", "/dms/{guild_id}/messages", guild_id=guild_id)
            elif isinstance(source, Interaction):
                return False
            else:
                return False

            await bot.api._http.request(route, json=payload)
            return True
        except Exception as exc:
            if self._markdown_template_id:
                try:
                    payload_fallback = dict(payload)
                    payload_fallback["markdown"] = build_plain_markdown()
                    await bot.api._http.request(route, json=payload_fallback)
                    return True
                except Exception as exc2:
                    logger.warning(
                        "QQ markdown image send failed: %s; fallback failed: %s",
                        str(exc),
                        str(exc2),
                    )
                    return False

            logger.warning(f"QQ markdown image send failed: {exc!s}")
            return False

    @property
    def enable_markdown_reply(self) -> bool:
        return self._enable_markdown_reply

    def set_interaction_handler(
        self,
        handler: Callable[[object, object], Awaitable[None]] | None,
    ) -> None:
        self._interaction_handler = handler

    def _maybe_hook_interactions(self, event: AstrMessageEvent) -> None:
        interaction_handler = self._interaction_handler
        if not interaction_handler:
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
                    import inspect

                    maybe_awaitable = prev(interaction)
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                except Exception as exc:
                    logger.warning(f"QQ interaction prev handler failed: {exc!s}")

            try:
                await interaction_handler(bot, interaction)
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
        try:
            return event.get_platform_name() == "qq_official_webhook"
        except Exception:
            return False

    def keyboard_enabled_for(self, event: AstrMessageEvent) -> bool:
        return self.enabled_for(event) and bool(self._keyboard_template_id)

    async def send_pager_keyboard(
        self,
        event: AstrMessageEvent,
        *,
        kind: str,
        page: int,
        reply_to_msg_id: str | None = None,
    ) -> None:
        """Send a markdown message with a keyboard template (buttons) on QQ official webhook.

        This bypasses AstrBot's generic send path, so it does not affect other platforms.
        """

        if not self.keyboard_enabled_for(event):
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

        markdown_text = f"翻页：{kind} 第{page_norm}页\n\n使用下方按钮上一页/下一页"

        def build_template_markdown() -> dict:
            # Template variables are defined in the QQ bot console.
            # If the user's template uses different keys, QQ will reject the payload.
            # We'll fallback to plain markdown content in that case.
            return {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": ["Warframe 助手"]},
                    {"key": "kind", "values": [str(kind)]},
                    {"key": "page", "values": [f"第{page_norm}页"]},
                    {"key": "hint", "values": ["使用下方按钮：上一页 / 下一页"]},
                ],
            }

        def build_plain_markdown() -> dict:
            return {"content": markdown_text}

        markdown: dict = (
            build_template_markdown()
            if self._markdown_template_id
            else build_plain_markdown()
        )

        msg_id = reply_to_msg_id or getattr(event.message_obj, "message_id", None)

        payload: dict = {
            # QQ v2 send message schema may require `content` even for markdown.
            # Use a single space so it does not visibly affect the message.
            "content": " ",
            "msg_type": 2,
            "markdown": markdown,
            "keyboard": {"id": self._keyboard_template_id},
        }

        if msg_id:
            payload["msg_id"] = msg_id

            # msg_seq is used together with msg_id to deduplicate replies.
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
            return
        except Exception as exc:
            # If a custom markdown template is configured, the most common failure
            # is "markdown parameter error" due to mismatched template keys.
            if self._markdown_template_id:
                try:
                    payload_fallback = dict(payload)
                    payload_fallback["markdown"] = build_plain_markdown()
                    await bot.api._http.request(route, json=payload_fallback)
                    return
                except Exception as exc2:
                    logger.warning(
                        "QQ pager keyboard send failed: %s; fallback failed: %s",
                        str(exc),
                        str(exc2),
                    )
                    return

            logger.warning(f"QQ pager keyboard send failed: {exc!s}")
            return

    async def _send_pager_keyboard_for_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        kind: str,
        page: int,
        reply_to_msg_id: str | None = None,
    ) -> None:
        try:
            from botpy.http import Route
        except Exception:
            return

        page_norm = max(1, int(page))

        markdown_text = f"翻页：{kind} 第{page_norm}页\n\n使用下方按钮上一页/下一页"

        def build_template_markdown() -> dict:
            return {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": ["Warframe 助手"]},
                    {"key": "kind", "values": [str(kind)]},
                    {"key": "page", "values": [f"第{page_norm}页"]},
                    {"key": "hint", "values": ["使用下方按钮：上一页 / 下一页"]},
                ],
            }

        def build_plain_markdown() -> dict:
            return {"content": markdown_text}

        markdown: dict = (
            build_template_markdown()
            if self._markdown_template_id
            else build_plain_markdown()
        )

        msg_id = reply_to_msg_id

        # Do not send proactive messages for paging interactions.
        if not msg_id:
            return

        payload: dict = {
            "content": " ",
            "msg_type": 2,
            "markdown": markdown,
            "keyboard": {"id": self._keyboard_template_id},
        }

        payload["msg_id"] = msg_id
        payload["msg_seq"] = random.randint(1, 10000)

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
            return
        except Exception as exc:
            if self._markdown_template_id:
                try:
                    payload_fallback = dict(payload)
                    payload_fallback["markdown"] = build_plain_markdown()
                    await bot.api._http.request(route, json=payload_fallback)  # type: ignore[attr-defined]
                    return
                except Exception as exc2:
                    logger.warning(
                        "QQ pager keyboard send failed (interaction): %s; fallback failed: %s",
                        str(exc),
                        str(exc2),
                    )
                    return

            logger.warning(f"QQ pager keyboard send failed (interaction): {exc!s}")
            return

    async def send_markdown_notice(
        self,
        event: AstrMessageEvent,
        *,
        title: str,
        content: str,
        reply_to_msg_id: str | None = None,
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

        def build_template_markdown() -> dict:
            return {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": [str(title).strip() or "提示"]},
                    {"key": "kind", "values": ["-"]},
                    {"key": "page", "values": ["-"]},
                    {"key": "hint", "values": [str(content).strip() or ""]},
                ],
            }

        def build_plain_markdown() -> dict:
            return {
                "content": f"# {str(title).strip() or '提示'}\n\n{str(content).strip()}"
            }

        markdown: dict = (
            build_template_markdown()
            if self._markdown_template_id
            else build_plain_markdown()
        )

        msg_id = reply_to_msg_id or getattr(event.message_obj, "message_id", None)

        payload: dict = {
            "content": " ",
            "msg_type": 2,
            "markdown": markdown,
        }

        if msg_id:
            payload["msg_id"] = msg_id

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
            return
        except Exception as exc:
            if self._markdown_template_id:
                try:
                    payload_fallback = dict(payload)
                    payload_fallback["markdown"] = build_plain_markdown()
                    await bot.api._http.request(route, json=payload_fallback)
                    return
                except Exception as exc2:
                    logger.warning(
                        "QQ markdown notice send failed: %s; fallback failed: %s",
                        str(exc),
                        str(exc2),
                    )
                    return

            logger.warning(f"QQ markdown notice send failed: {exc!s}")
            return

    async def send_markdown_notice_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        title: str,
        content: str,
        reply_to_msg_id: str | None = None,
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
            reply_to_msg_id=reply_to_msg_id,
        )

    async def send_pager_keyboard_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        kind: str,
        page: int,
        reply_to_msg_id: str | None = None,
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
            reply_to_msg_id=reply_to_msg_id,
        )

    async def _send_markdown_notice_for_interaction(
        self,
        bot: object,
        interaction: object,
        *,
        title: str,
        content: str,
        reply_to_msg_id: str | None = None,
    ) -> None:
        try:
            from botpy.http import Route
        except Exception:
            return

        def build_template_markdown() -> dict:
            return {
                "custom_template_id": self._markdown_template_id,
                "params": [
                    {"key": "title", "values": [str(title).strip() or "提示"]},
                    {"key": "kind", "values": ["-"]},
                    {"key": "page", "values": ["-"]},
                    {"key": "hint", "values": [str(content).strip() or ""]},
                ],
            }

        def build_plain_markdown() -> dict:
            return {
                "content": f"# {str(title).strip() or '提示'}\n\n{str(content).strip()}"
            }

        markdown: dict = (
            build_template_markdown()
            if self._markdown_template_id
            else build_plain_markdown()
        )

        msg_id = reply_to_msg_id

        # Do not send proactive messages for paging interactions.
        if not msg_id:
            return

        payload: dict = {
            "content": " ",
            "msg_type": 2,
            "markdown": markdown,
        }

        payload["msg_id"] = msg_id
        payload["msg_seq"] = random.randint(1, 10000)

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
            return
        except Exception as exc:
            if self._markdown_template_id:
                try:
                    payload_fallback = dict(payload)
                    payload_fallback["markdown"] = build_plain_markdown()
                    await bot.api._http.request(route, json=payload_fallback)  # type: ignore[attr-defined]
                    return
                except Exception as exc2:
                    logger.warning(
                        "QQ markdown notice send failed (interaction): %s; fallback failed: %s",
                        str(exc),
                        str(exc2),
                    )
                    return

            logger.warning(f"QQ markdown notice send failed (interaction): {exc!s}")
            return
