from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
import tempfile
import time
import unittest

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from config_store import AppConfig, ConfigStore, JobConfig
from tele_engine import (
    AsyncLoopThread,
    FloodWaitError,
    TeleAutoEngine,
    match_stop_keyword,
    parse_target_entity,
)


@dataclass
class FakeUser:
    id: int = 999999
    first_name: str = "Minh"
    last_name: str = "Nguyen"
    username: str = "minhnguyen"


@dataclass
class FakeSentMessage:
    id: int
    raw_text: str


@dataclass
class FakeEvent:
    raw_text: str
    out: bool = False
    id: int = 1


class FakeTelegramClient:
    def __init__(self, me_id: int = 999999) -> None:
        self.me = FakeUser(id=me_id)
        self.sent_messages: list[tuple[object, str]] = []
        self.handlers: list[tuple[object, object]] = []
        self._msg_seq = 100
        self.flood_once = False

    def is_connected(self) -> bool:
        return True

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> FakeUser:
        return self.me

    async def get_input_entity(self, target: object) -> object:
        if target in ("me", "self"):
            return FakeUser(id=self.me.id)
        return target

    def add_event_handler(self, callback: object, event_filter: object) -> None:
        self.handlers.append((callback, event_filter))

    def remove_event_handler(self, callback: object) -> None:
        self.handlers = [(cb, ef) for cb, ef in self.handlers if cb is not callback]

    async def emit_event(self, event: FakeEvent) -> None:
        for cb, _ in list(self.handlers):
            await cb(event)  # type: ignore[misc]

    async def send_message(self, entity: object, text: str) -> FakeSentMessage:
        if self.flood_once:
            self.flood_once = False
            err = FloodWaitError.__new__(FloodWaitError)
            err.seconds = 1
            raise err
        self._msg_seq += 1
        msg = FakeSentMessage(id=self._msg_seq, raw_text=text)
        self.sent_messages.append((entity, text))
        # Simulate Telethon echoing outgoing message into NewMessage watcher
        await self.emit_event(FakeEvent(raw_text=text, out=True, id=msg.id))
        return msg


class TestTeleAutoSender(unittest.TestCase):
    def test_config_roundtrip_and_clamping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "user_config.json"
            store = ConfigStore(cfg_path)
            cfg = AppConfig(
                api_id=123456,
                api_hash="abcdef",
                phone="+84901234567",
                last_job=JobConfig(
                    target_chat="@friend",
                    message_text="Dậy đi!",
                    stop_keywords_raw="ok, dậy rồi\ndừng, ok",
                    delay_seconds=0.2,  # Should clamp to >= 1.5 on reload
                ),
            )
            self.assertEqual(cfg.last_job.parsed_keywords(), ["ok", "dậy rồi", "dừng"])
            store.save(cfg)

            loaded = store.load()
            self.assertEqual(loaded.api_id, 123456)
            self.assertEqual(loaded.last_job.target_chat, "@friend")
            self.assertGreaterEqual(loaded.last_job.delay_seconds, 1.5)

    def test_parse_target_and_keyword_matching(self) -> None:
        self.assertEqual(parse_target_entity(" -100123456789 "), -100123456789)
        self.assertEqual(parse_target_entity(" @myfriend "), "@myfriend")
        self.assertEqual(parse_target_entity("me"), "me")

        # Case-insensitive Vietnamese substring match
        self.assertEqual(
            match_stop_keyword("Ừ mình DẬY RỒI nha!", ["ok", "dậy rồi"], case_sensitive=False),
            "dậy rồi",
        )
        # Case-sensitive check
        self.assertIsNone(
            match_stop_keyword("Ừ mình DẬY RỒI nha!", ["dậy rồi"], case_sensitive=True)
        )
        # Whole message match
        self.assertIsNone(
            match_stop_keyword("ok luôn", ["ok"], whole_message_match=True)
        )
        self.assertEqual(
            match_stop_keyword("  OK  ", ["ok"], whole_message_match=True),
            "ok",
        )

    def test_auto_job_stops_instantly_on_incoming_keyword_and_ignores_outgoing(self) -> None:
        async def scenario() -> None:
            engine = TeleAutoEngine(api_id=111, api_hash="hash")
            fake_client = FakeTelegramClient()
            engine.client = fake_client

            job = JobConfig(
                target_chat="@ban_than",
                message_text="Alo dậy đi ok chưa?",  # Contains 'ok' — must NOT self-stop!
                stop_keywords_raw="ok, dậy rồi",
                only_incoming=True,
                delay_seconds=10.0,  # Long delay to prove instant wake-up on keyword
                jitter_seconds=0.0,
            )

            logs: list[str] = []
            statuses: list[str] = []
            counts: list[int] = []

            async def incoming_reply() -> None:
                await asyncio.sleep(0.12)
                # Outgoing message from user on another device without Saved Messages -> ignored when only_incoming=True
                await fake_client.emit_event(FakeEvent(raw_text="ok tự nhắn", out=True, id=501))
                await asyncio.sleep(0.05)
                self.assertTrue(engine._is_running)
                # Incoming message from the friend containing keyword -> stops immediately!
                t0 = time.perf_counter()
                await fake_client.emit_event(
                    FakeEvent(raw_text="Thôi dậy rồi đừng réo nữa!", out=False, id=502)
                )
                return None

            t_start = time.perf_counter()
            reply_task = asyncio.create_task(incoming_reply())
            reason = await engine.run_auto_job(
                job=job,
                on_log=logs.append,
                on_status=statuses.append,
                on_count=counts.append,
            )
            await reply_task
            elapsed = time.perf_counter() - t_start

            self.assertIn("dậy rồi", reason)
            self.assertEqual(len(fake_client.sent_messages), 1)
            self.assertLess(elapsed, 1.5, "Must stop immediately without waiting full 10s delay!")
            self.assertEqual(len(fake_client.handlers), 0, "Event handler must be cleaned up!")

        asyncio.run(scenario())

    def test_saved_messages_allows_manual_stop_without_self_triggering(self) -> None:
        async def scenario() -> None:
            engine = TeleAutoEngine(api_id=111, api_hash="hash")
            fake_client = FakeTelegramClient(me_id=777)
            engine.client = fake_client

            job = JobConfig(
                target_chat="me",
                message_text="Tin nhắn test có chữ stop bên trong",
                stop_keywords_raw="stop",
                only_incoming=True,
                delay_seconds=10.0,
                jitter_seconds=0.0,
            )

            async def user_types_stop_in_saved_messages() -> None:
                await asyncio.sleep(0.1)
                # Must still be running despite auto-sent message containing 'stop'
                self.assertTrue(engine._is_running)
                # Now user manually types 'stop' in Saved Messages (out=True, different text/id)
                await fake_client.emit_event(FakeEvent(raw_text="stop", out=True, id=999))

            stopper = asyncio.create_task(user_types_stop_in_saved_messages())
            reason = await engine.run_auto_job(
                job=job,
                on_log=lambda _m: None,
                on_status=lambda _s: None,
                on_count=lambda _c: None,
            )
            await stopper
            self.assertIn("stop", reason)
            self.assertEqual(len(fake_client.handlers), 0)

        asyncio.run(scenario())

    def test_flood_wait_and_max_messages(self) -> None:
        async def scenario() -> None:
            engine = TeleAutoEngine(api_id=111, api_hash="hash")
            fake_client = FakeTelegramClient()
            fake_client.flood_once = True
            engine.client = fake_client

            job = JobConfig(
                target_chat="@friend",
                message_text="Ping",
                stop_keywords_raw="ok",
                delay_seconds=1.5,
                jitter_seconds=0.0,
                max_messages=1,
            )

            reason = await engine.run_auto_job(
                job=job,
                on_log=lambda _m: None,
                on_status=lambda _s: None,
                on_count=lambda _c: None,
            )
            self.assertIn("giới hạn 1", reason)
            self.assertEqual(len(fake_client.sent_messages), 1)

        asyncio.run(scenario())

    def test_async_loop_thread(self) -> None:
        loop_thread = AsyncLoopThread()
        try:
            fut = loop_thread.run_coro(asyncio.sleep(0.02, result=42))
            self.assertEqual(fut.result(timeout=2.0), 42)
        finally:
            loop_thread.stop_loop()


if __name__ == "__main__":
    unittest.main()
