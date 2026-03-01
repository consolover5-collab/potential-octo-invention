"""Telethon userbot: monitors chats in real-time, sends DMs to sellers."""

import asyncio
import logging

from telethon import TelegramClient, events
from telethon.tl.types import Message

from bot.models import Config, ForwardMode
from bot.keywords import KeywordMatcher
from bot.price import extract_price
from bot.dedup import DedupChecker
from bot.ratelimit import RateLimiter
from bot.vision import analyse_image, parse_vision_response
from bot.processor import MessageProcessor
from db.database import Database

logger = logging.getLogger(__name__)


class Userbot:
    def __init__(
        self,
        config: Config,
        dedup: DedupChecker,
        dm_limiter: RateLimiter,
        vision_limiter: RateLimiter,
        db: Database,
        notify_callback=None,
    ):
        self.config = config
        self.dedup = dedup
        self.dm_limiter = dm_limiter
        self.vision_limiter = vision_limiter
        self.db = db
        self.notify = notify_callback  # async fn(text: str)
        self.matcher = KeywordMatcher(config.monitoring.keywords)
        self.paused = False
        self.processor = MessageProcessor(config, db)

        self.client = TelegramClient(
            config.telegram.session_name,
            config.telegram.api_id,
            config.telegram.api_hash,
        )

        # album debounce: group_id -> list[Message]
        self._album_buf: dict[int, list[Message]] = {}
        self._album_tasks: dict[int, asyncio.Task] = {}

    async def start(self):
        await self.client.start(phone=self.config.telegram.phone)
        logger.info("Userbot authorised as %s", (await self.client.get_me()).first_name)

        chats = self.config.monitoring.chats
        if not chats:
            logger.warning("No chats to monitor")
            return

        @self.client.on(events.NewMessage(chats=chats))
        async def on_message(event: events.NewMessage.Event):
            if self.paused:
                return
            msg: Message = event.message

            # Album grouping
            if msg.grouped_id:
                await self._handle_album(msg)
            else:
                await self._process_message(msg)

        logger.info("Monitoring %d chats: %s", len(chats), chats)

    async def stop(self):
        # Cancel pending album tasks
        for task in self._album_tasks.values():
            task.cancel()
        self._album_tasks.clear()
        self._album_buf.clear()
        await self.client.disconnect()
        logger.info("Userbot disconnected")

    # ── Album debounce ─────────────────────────────────────────────

    async def _handle_album(self, msg: Message):
        gid = msg.grouped_id
        if gid not in self._album_buf:
            self._album_buf[gid] = []
        self._album_buf[gid].append(msg)

        # Reset debounce timer
        if gid in self._album_tasks:
            self._album_tasks[gid].cancel()
        self._album_tasks[gid] = asyncio.create_task(self._flush_album(gid))

    async def _flush_album(self, gid: int):
        await asyncio.sleep(1.5)  # wait for all parts
        messages = self._album_buf.pop(gid, [])
        self._album_tasks.pop(gid, None)
        if messages:
            # Use first message with text, process photos from first message
            primary = messages[0]
            for m in messages:
                if m.text:
                    primary = m
                    break
            await self._process_message(primary)

    # ── Main pipeline ──────────────────────────────────────────────

    async def _process_message(self, msg: Message):
        seller_id = msg.sender_id
        if not seller_id:
            return

        chat = await self._chat_title(msg)
        chat_external = str(msg.chat_id)
        match_type = None
        matched_value = None
        price = None

        # Step 1: keyword match on text
        if msg.text:
            kw = self.matcher.match(msg.text)
            if kw:
                match_type = "keyword"
                # Check if keyword_map provides a type
                if kw in self.config.rules.keyword_map:
                    matched_value = self.config.rules.keyword_map[kw]
                else:
                    matched_value = kw
                price = extract_price(msg.text)

        # Step 2: vision (if no keyword match and photo present)
        if not match_type and msg.photo and self.config.rules.vision_enabled:
            vision_result = await self._try_vision(msg)
            if vision_result:
                match_type = "vision"
                matched_value = vision_result.get("type", "")
                price = vision_result.get("price")

        if not match_type:
            return  # no match

        # Step 3: price filter
        max_price = self.config.monitoring.max_price
        if max_price and price and price > max_price:
            logger.debug("Price %d > max %d, skipping", price, max_price)
            return

        # Prepare metadata
        link = f"https://t.me/c/{abs(msg.chat_id)}/{msg.id}"
        meta = {
            "type": matched_value,
            "price": price,
            "link": link,
            "author": str(seller_id),
            "chat_title": chat,
            "source_chat": chat_external,
            "message_snippet": (msg.text or "")[:200],
            "match_type": match_type,
            "matched_value": matched_value,
        }

        # Store message in new schema
        msg_uuid = await self.processor.store_message(
            chat_external=chat_external,
            chat_title=chat,
            message_id=msg.id,
            author_id=seller_id,
            text=msg.text or "",
            meta=meta
        )

        # Step 4: dedup check
        is_dup = await self.dedup.is_seen(seller_id)

        if is_dup:
            await self.dedup.record_match(
                seller_id, msg.chat_id, msg.id,
                match_type, matched_value, price, is_duplicate=True,
            )
            await self._notify_duplicate(chat, seller_id)
            # Log duplicate - no actions taken
            await self.db.log_action(msg_uuid, "duplicate", "skipped", {"reason": "seller already seen"})
        else:
            # New seller
            await self.dedup.register(
                seller_id, chat, msg.id, match_type, matched_value, price,
            )
            await self.dedup.record_match(
                seller_id, msg.chat_id, msg.id,
                match_type, matched_value, price, is_duplicate=False,
            )

            # Decide actions using processor
            actions = self.processor.decide_actions(chat_external, seller_id, meta)

            dm_sent = False
            forward_sent = False

            # Execute forward action
            if actions["should_forward"]:
                forward_sent = await self._forward_message(msg, meta)
                await self.db.log_action(
                    msg_uuid, "forward",
                    "success" if forward_sent else "failed",
                    {"mode": self.config.actions.forward_mode.value, "dry_run": self.config.actions.dry_run}
                )

            # Execute DM action
            if actions["should_dm"]:
                dm_sent = await self._send_dm_with_template(seller_id, actions["dm_text"])
                await self.db.log_action(
                    msg_uuid, "dm",
                    "success" if dm_sent else "failed",
                    {"template_used": True, "dry_run": self.config.actions.dry_run}
                )

            # Notify
            await self._notify_new(chat, match_type, matched_value, price, dm_sent, msg, forward_sent)

    # ── Vision ─────────────────────────────────────────────────────

    async def _try_vision(self, msg: Message) -> dict | None:
        if not self.config.vision.api_key:
            return None
        if not self.vision_limiter.consume():
            logger.debug("Vision rate limit reached, skipping")
            return None

        try:
            photo_bytes = await self.client.download_media(msg, bytes)
            if not photo_bytes:
                return None
            reply = await analyse_image(
                photo_bytes,
                self.config.monitoring.vision_prompt,
                self.config.vision,
            )
            return parse_vision_response(reply) if reply else None
        except Exception as e:
            logger.error("Vision processing error: %s", e)
            return None

    # ── DM ─────────────────────────────────────────────────────────

    async def _send_dm(self, seller_id: int) -> bool:
        """Legacy DM method (uses dm_message)."""
        if not self.dm_limiter.consume():
            logger.warning("DM rate limit reached for seller %d", seller_id)
            return False

        if self.config.actions.dry_run:
            logger.info("[DRY RUN] Would send DM to seller %d: %s", seller_id, self.config.actions.dm_message)
            return True

        try:
            await self.client.send_message(seller_id, self.config.actions.dm_message)
            await self.dedup.mark_dm_sent(seller_id)
            logger.info("DM sent to seller %d", seller_id)
            return True
        except Exception as e:
            logger.error("Failed to send DM to %d: %s", seller_id, e)
            return False

    async def _send_dm_with_template(self, seller_id: int, text: str) -> bool:
        """Send DM with rendered template."""
        if not self.dm_limiter.consume():
            logger.warning("DM rate limit reached for seller %d", seller_id)
            return False

        if self.config.actions.dry_run:
            logger.info("[DRY RUN] Would send DM to seller %d: %s", seller_id, text[:100])
            return True

        try:
            await self.client.send_message(seller_id, text)
            await self.dedup.mark_dm_sent(seller_id)
            logger.info("DM sent to seller %d", seller_id)
            return True
        except Exception as e:
            logger.error("Failed to send DM to %d: %s", seller_id, e)
            return False

    async def _forward_message(self, msg: Message, meta: dict) -> bool:
        """Forward message to main bot or send notification."""
        notify_chat_id = self.config.actions.notify_chat_id

        if self.config.actions.dry_run:
            logger.info("[DRY RUN] Would forward message to %s (mode=%s)", notify_chat_id, self.config.actions.forward_mode)
            return True

        try:
            if self.config.actions.forward_mode == ForwardMode.FORWARD_RAW:
                # Forward the actual message
                await self.client.forward_messages(notify_chat_id, msg)
                logger.info("Message forwarded to %s", notify_chat_id)
                return True
            else:
                # Send notification with metadata
                notification_text = self.processor.format_notification(meta, self.config.actions.forward_mode)
                if notification_text and self.notify:
                    await self.notify(notification_text)
                    logger.info("Notification sent")
                    return True
                return False
        except Exception as e:
            logger.error("Failed to forward message: %s", e)
            return False

    # ── Notifications ──────────────────────────────────────────────

    async def _notify_new(self, chat, match_type, matched_value, price, dm_sent, msg, forward_sent=False):
        price_str = f"{price:,} ₽".replace(",", " ") if price else "—"
        dm_str = "✉️ DM отправлен продавцу" if dm_sent else "⚠️ DM не отправлен (лимит или выключен)"
        forward_str = "📤 Переслано" if forward_sent else ""
        link = f"https://t.me/c/{abs(msg.chat_id)}/{msg.id}"

        parts = [
            f"🔔 Новое совпадение!",
            f"📍 Чат: {chat}",
            f"🏷 Тип: {match_type} ({matched_value})",
            f"💰 Цена: {price_str}",
        ]

        if dm_sent:
            parts.append(dm_str)
        if forward_sent:
            parts.append(forward_str)

        parts.append(f"🔗 {link}")

        text = "\n".join(parts)

        if self.notify:
            await self.notify(text)

    async def _notify_duplicate(self, chat, seller_id):
        text = (
            f"🔄 Повтор от того же продавца\n"
            f"📍 Чат: {chat}\n"
            f"ℹ️ DM уже отправлялся ранее"
        )
        if self.notify:
            await self.notify(text)

    # ── Helpers ─────────────────────────────────────────────────────

    async def _chat_title(self, msg: Message) -> str:
        try:
            chat = await self.client.get_entity(msg.chat_id)
            return getattr(chat, "title", None) or getattr(chat, "username", str(msg.chat_id))
        except Exception:
            return str(msg.chat_id)
