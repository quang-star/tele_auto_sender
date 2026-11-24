from __future__ import annotations

import asyncio
import concurrent.futures
import random
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from config_store import DEFAULT_SESSION_PATH, JobConfig

try:
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError, SessionPasswordNeededError

    _TELETHON_IMPORT_ERROR: Optional[ImportError] = None
except ImportError as exc:  # pragma: no cover - allows pure-helper tests without telethon
    TelegramClient = None  # type: ignore[assignment, misc]

    class _FallbackEvents:
        class NewMessage:
            def __init__(self, chats: Any = None) -> None:
                self.chats = chats

    events = _FallbackEvents()  # type: ignore[assignment]

    class FloodWaitError(Exception):  # type: ignore[no-redef]
        def __init__(self, seconds: int = 1) -> None:
            super().__init__(f"FloodWait {seconds}s")
            self.seconds: int = seconds

    class SessionPasswordNeededError(Exception):  # type: ignore[no-redef]
        pass

    _TELETHON_IMPORT_ERROR = exc


_INT_ENTITY_RE = re.compile(r"^-?[0-9]+$")
_VN_LOCAL_PHONE_RE = re.compile(r"^0([1-9][0-9]{8})$")
_VN_NOPLUS_PHONE_RE = re.compile(r"^84([1-9][0-9]{8})$")


def normalize_phone_number(raw_phone: str) -> str:
    """
    Normalize phone numbers into E.164 international format expected by Telegram.
    Automatically converts Vietnamese local numbers like '0901234567' or '84901234567'
    into '+84901234567', stripping spaces/dashes/dots.
    """
    cleaned = re.sub(r"[\s\-\.\(\)]+", "", (raw_phone or "").strip())
    if not cleaned:
        return ""
    m_local = _VN_LOCAL_PHONE_RE.fullmatch(cleaned)
    if m_local:
        return f"+84{m_local.group(1)}"
    m_noplus = _VN_NOPLUS_PHONE_RE.fullmatch(cleaned)
    if m_noplus:
        return f"+84{m_noplus.group(1)}"
    return cleaned


class TwoFactorRequiredError(Exception):
    """Raised when Telegram account requires a 2FA cloud password to complete sign-in."""


@dataclass
class DialogInfo:
    id: int
    name: str
    username: Optional[str]
    kind: str  # "User" | "Group" | "Channel" | "Bot" | "Saved"

    def display_label(self) -> str:
        """Format a human-readable label for UI dropdowns and CLI selection lists."""
        title = (self.name or "").strip() or str(self.id)
        if self.username:
            uname = self.username.lstrip("@")
            return f"[{self.kind}] {title} (@{uname}) — ID: {self.id}"
        return f"[{self.kind}] {title} — ID: {self.id}"


def parse_target_entity(raw: str) -> int | str:
    """
    Strip whitespace. If raw looks like an integer (optional leading '-', digits only),
    return int(raw) so Telethon resolves numeric dialog IDs properly.
    Otherwise return stripped string (e.g. '@username', 'me', '+849...').
    """
    cleaned = (raw or "").strip()
    if _INT_ENTITY_RE.fullmatch(cleaned):
        return int(cleaned)
    return cleaned


def match_stop_keyword(
    message_text: str,
    keywords: list[str],
    case_sensitive: bool = False,
    whole_message_match: bool = False,
) -> Optional[str]:
    """
    Return the first matched keyword from `keywords` if `message_text` matches, else None.
    - If `message_text` is empty or `keywords` is empty, return None.
    - When `case_sensitive` is False, compare using `.casefold()`.
    - When `whole_message_match` is True, `message_text.strip()` must equal `kw.strip()`.
      Otherwise `kw` only needs to be a substring of `message_text`.
    """
    if not message_text or not keywords:
        return None

    msg_whole = message_text.strip()
    msg_cmp = message_text if case_sensitive else message_text.casefold()
    msg_whole_cmp = msg_whole if case_sensitive else msg_whole.casefold()

    for kw in keywords:
        if not kw or not kw.strip():
            continue
        if whole_message_match:
            kw_clean = kw.strip()
            kw_cmp = kw_clean if case_sensitive else kw_clean.casefold()
            if msg_whole_cmp == kw_cmp:
                return kw
        else:
            kw_cmp = kw if case_sensitive else kw.casefold()
            if kw_cmp in msg_cmp:
                return kw

    return None


class TeleAutoEngine:
    """Async Telegram automation engine built on Telethon."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_path: Path = DEFAULT_SESSION_PATH,
    ) -> None:
        self.api_id: int = int(api_id)
        self.api_hash: str = api_hash.strip()
        self.session_path: Path = Path(session_path)
        self.client: Optional[Any] = None
        self._phone_code_hash: Optional[str] = None
        self._pending_phone: Optional[str] = None
        self._awaiting_2fa: bool = False
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._is_running: bool = False

    def _require_telethon(self) -> None:
        if TelegramClient is None or events is None:
            raise RuntimeError(
                "Thư viện 'telethon' chưa được cài đặt. Hãy chạy: pip install telethon>=1.36.0"
            ) from _TELETHON_IMPORT_ERROR

    async def connect(self) -> bool:
        """Create/connect TelegramClient and return whether the session is authorized."""
        if self.client is None:
            self._require_telethon()
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            self.client = TelegramClient(
                str(self.session_path), self.api_id, self.api_hash
            )

        if not self.client.is_connected():
            await self.client.connect()

        return bool(await self.client.is_user_authorized())

    async def send_login_code(self, phone: str) -> str:
        """Request a Telegram login OTP code for `phone` and return delivery channel hint."""
        if self.client is None or not self.client.is_connected():
            await self.connect()
        assert self.client is not None

        cleaned_phone = normalize_phone_number(phone)
        sent = await self.client.send_code_request(cleaned_phone)
        self._pending_phone = cleaned_phone
        self._phone_code_hash = getattr(sent, "phone_code_hash", None)
        self._awaiting_2fa = False

        sent_type_name = type(getattr(sent, "type", None)).__name__
        if "App" in sent_type_name:
            return (
                "Mã OTP đã gửi VÀO TRONG APP TELEGRAM (tìm cuộc trò chuyện có tích xanh tên 'Telegram' "
                "trên điện thoại/máy tính, KHÔNG phải tin nhắn SMS!)."
            )
        if "Sms" in sent_type_name:
            return (
                "Telegram báo gửi qua SMS (lưu ý: API bên thứ 3 ở VN thường bị chặn SMS — "
                "nếu không nhận được, hãy hủy và bấm nút '📷 Quét QR' để đăng nhập ngay!)."
            )
        return f"Đã yêu cầu gửi mã xác nhận ({sent_type_name}). Kiểm tra trong app Telegram mục 'Telegram'."

    async def login_with_qr(
        self,
        on_qr_url: Callable[[str], None],
        cancel_event: asyncio.Event,
    ) -> str:
        """
        Authenticate via Telegram QR code scan (Settings -> Devices -> Link Desktop Device).
        Automatically refreshes expired QR tokens until scanned or cancelled.
        Raises TwoFactorRequiredError if the account has a 2FA cloud password enabled.
        """
        if self.client is None or not self.client.is_connected():
            await self.connect()
        assert self.client is not None

        if await self.client.is_user_authorized():
            return await self.get_me_summary()

        qr_login = await self.client.qr_login()
        on_qr_url(qr_login.url)

        while not cancel_event.is_set():
            wait_task = asyncio.ensure_future(qr_login.wait(timeout=25.0))
            cancel_task = asyncio.ensure_future(cancel_event.wait())
            done, pending = await asyncio.wait(
                {wait_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            if cancel_task in done and cancel_event.is_set():
                raise RuntimeError("Đã hủy đăng nhập bằng mã QR.")

            try:
                wait_task.result()
                self._awaiting_2fa = False
                return await self.get_me_summary()
            except asyncio.TimeoutError:
                if cancel_event.is_set():
                    raise RuntimeError("Đã hủy đăng nhập bằng mã QR.")
                await qr_login.recreate()
                on_qr_url(qr_login.url)
            except SessionPasswordNeededError:
                self._awaiting_2fa = True
                raise TwoFactorRequiredError(
                    "Tài khoản có mật khẩu xác thực 2 lớp (2FA). Vui lòng nhập mật khẩu."
                )

        raise RuntimeError("Đã hủy đăng nhập bằng mã QR.")

    async def complete_2fa_password(self, password: str) -> str:
        """Complete sign-in when _awaiting_2fa is active (works for both OTP and QR login)."""
        if self.client is None or not self.client.is_connected():
            await self.connect()
        assert self.client is not None
        await self.client.sign_in(password=password)
        self._awaiting_2fa = False
        return await self.get_me_summary()

    async def complete_login(
        self,
        phone: str,
        code: str,
        password: Optional[str] = None,
    ) -> str:
        """
        Complete Telegram sign-in using OTP code and optional 2FA password.
        Raises TwoFactorRequiredError if 2FA is enabled and no password was provided.
        """
        if self.client is None or not self.client.is_connected():
            await self.connect()
        assert self.client is not None

        cleaned_phone = normalize_phone_number(phone) or (self._pending_phone or "")
        cleaned_code = re.sub(r"\s+", "", code.strip())

        if self._awaiting_2fa and password:
            await self.client.sign_in(password=password)
            self._awaiting_2fa = False
            return await self.get_me_summary()

        sign_in_kwargs: dict[str, Any] = {
            "phone": cleaned_phone,
            "code": cleaned_code,
        }
        if self._phone_code_hash:
            sign_in_kwargs["phone_code_hash"] = self._phone_code_hash

        try:
            await self.client.sign_in(**sign_in_kwargs)
            self._awaiting_2fa = False
        except SessionPasswordNeededError:
            self._awaiting_2fa = True
            if password:
                await self.client.sign_in(password=password)
                self._awaiting_2fa = False
            else:
                raise TwoFactorRequiredError(
                    "Tài khoản có mật khẩu xác thực 2 lớp (2FA). Vui lòng nhập mật khẩu."
                )

        return await self.get_me_summary()

    async def get_me_summary(self) -> str:
        """Return formatted summary of the currently authorized Telegram user."""
        if self.client is None or not self.client.is_connected():
            return "Chưa đăng nhập"
        if not await self.client.is_user_authorized():
            return "Chưa đăng nhập"

        me = await self.client.get_me()
        if me is None:
            return "Chưa đăng nhập"

        parts = [getattr(me, "first_name", None), getattr(me, "last_name", None)]
        full_name = " ".join(p for p in parts if p).strip() or "Người dùng"
        username = getattr(me, "username", None)
        if username:
            return f"{full_name} (@{username}) [ID: {me.id}]"
        return f"{full_name} [ID: {me.id}]"

    async def list_recent_dialogs(self, limit: int = 80) -> list[DialogInfo]:
        """List recent dialogs with Saved Messages prepended and kinds classified."""
        if self.client is None or not self.client.is_connected():
            await self.connect()
        assert self.client is not None

        if not await self.client.is_user_authorized():
            raise RuntimeError("Chưa đăng nhập Telegram.")

        me = await self.client.get_me()
        results: list[DialogInfo] = []
        me_id: Optional[int] = getattr(me, "id", None) if me is not None else None

        if me is not None and me_id is not None:
            results.append(
                DialogInfo(
                    id=int(me_id),
                    name="Saved Messages",
                    username=getattr(me, "username", None),
                    kind="Saved",
                )
            )

        dialogs = await self.client.get_dialogs(limit=limit)
        for dialog in dialogs:
            dialog_id = int(dialog.id)
            if me_id is not None and dialog_id == int(me_id):
                continue

            entity = getattr(dialog, "entity", None)
            if getattr(dialog, "is_group", False):
                kind = "Group"
            elif getattr(dialog, "is_channel", False):
                kind = "Channel"
            elif getattr(entity, "bot", False):
                kind = "Bot"
            else:
                kind = "User"

            name = (
                getattr(dialog, "name", None)
                or getattr(entity, "title", None)
                or getattr(entity, "first_name", None)
                or str(dialog_id)
            )
            username = getattr(entity, "username", None)
            results.append(
                DialogInfo(
                    id=dialog_id,
                    name=str(name),
                    username=username,
                    kind=kind,
                )
            )

        return results

    async def _resolve_target_entity(self, raw_target: str) -> Any:
        """Resolve a target chat via get_input_entity with a get_dialogs fallback for numeric IDs."""
        assert self.client is not None
        parsed = parse_target_entity(raw_target)
        if parsed == "":
            raise ValueError("Chưa nhập chat/người nhận mục tiêu.")

        try:
            return await self.client.get_input_entity(parsed)
        except ValueError:
            if isinstance(parsed, int):
                dialogs = await self.client.get_dialogs(limit=200)
                for dialog in dialogs:
                    entity = getattr(dialog, "entity", None)
                    entity_id = getattr(entity, "id", None)
                    if dialog.id == parsed or (entity_id is not None and entity_id == parsed):
                        return getattr(dialog, "input_entity", None) or entity
            raise ValueError(
                f"Không tìm thấy cuộc trò chuyện '{raw_target}'. "
                "Hãy tải danh sách chat trước hoặc nhập @username."
            )

    async def run_auto_job(
        self,
        job: JobConfig,
        on_log: Callable[[str], None],
        on_status: Callable[[str], None],
        on_count: Callable[[int], None],
    ) -> str:
        """
        Repeatedly send `job.message_text` to `job.target_chat` until a stop keyword
        is observed in incoming messages, `max_messages` is reached, or `request_stop()` is called.
        """
        if not job.message_text or not job.message_text.strip():
            raise ValueError("Nội dung tin nhắn không được để trống.")
        if not job.target_chat or not job.target_chat.strip():
            raise ValueError("Vui lòng chọn hoặc nhập chat mục tiêu.")
        if self._is_running:
            raise RuntimeError("Một tác vụ gửi tự động đang chạy.")

        if self.client is None or not self.client.is_connected():
            await self.connect()
        assert self.client is not None

        if not await self.client.is_user_authorized():
            raise RuntimeError("Chưa đăng nhập Telegram.")

        def _log(msg: str) -> None:
            ts = datetime.now().strftime("%H:%M:%S")
            on_log(f"[{ts}] {msg}")

        entity = await self._resolve_target_entity(job.target_chat)
        keywords = job.parsed_keywords()

        me = await self.client.get_me()
        me_id = getattr(me, "id", None) if me is not None else None
        raw_target_clean = job.target_chat.strip().lower()
        entity_peer_id = getattr(entity, "user_id", None) or getattr(entity, "id", None)
        is_saved_messages = (raw_target_clean in {"me", "self"}) or (
            me_id is not None and entity_peer_id is not None and int(entity_peer_id) == int(me_id)
        )

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._is_running = True
        stop_reason = "Đã dừng thủ công"
        auto_sent_ids: set[int] = set()

        async def _watcher(event: Any) -> None:
            nonlocal stop_reason
            is_outgoing = bool(getattr(event, "out", False))
            msg_id = getattr(event, "id", None)
            text = getattr(event, "raw_text", None) or ""

            if is_outgoing:
                # Never let the engine's own automated messages self-trigger stop keywords
                if (msg_id is not None and msg_id in auto_sent_ids) or (
                    text.strip() == job.message_text.strip()
                ):
                    return
                if job.only_incoming and not is_saved_messages:
                    return

            matched = match_stop_keyword(
                text,
                keywords,
                case_sensitive=job.case_sensitive,
                whole_message_match=job.whole_message_match,
            )
            if matched is not None:
                _log(
                    f"🎯 Phát hiện từ khóa dừng '{matched}' trong tin nhắn: "
                    f'"{text[:80]}" -> Dừng ngay!'
                )
                stop_reason = f"Đã dừng tự động (gặp từ khóa: {matched})"
                if self._stop_event is not None:
                    self._stop_event.set()

        self.client.add_event_handler(_watcher, events.NewMessage(chats=entity))
        try:
            on_status("Đang chạy gửi tự động...")
            _log(f"Bắt đầu gửi tới: {job.target_label or job.target_chat}")
            if is_saved_messages and job.only_incoming and keywords:
                _log(
                    "ℹ️ Đang test trong Saved Messages: bạn có thể tự nhắn từ khóa dừng vào đây để test dừng tự động."
                )
            if keywords:
                _log(f"Từ khóa dừng ({len(keywords)}): {', '.join(keywords)}")
            else:
                _log("Không đặt từ khóa dừng — sẽ chạy đến khi dừng thủ công hoặc đạt giới hạn.")

            sent_count = 0
            on_count(0)

            while not self._stop_event.is_set():
                try:
                    sent_msg = await self.client.send_message(entity, job.message_text)
                    sent_id = getattr(sent_msg, "id", None)
                    if isinstance(sent_id, int):
                        auto_sent_ids.add(sent_id)
                    sent_count += 1
                    on_count(sent_count)
                    _log(f"Đã gửi tin #{sent_count}")
                except FloodWaitError as e:
                    wait_sec = max(1, int(getattr(e, "seconds", 5)))
                    _log(f"⚠️ Telegram giới hạn tốc độ (FloodWait): tạm nghỉ {wait_sec}s...")
                    on_status(f"Tạm nghỉ FloodWait {wait_sec}s...")
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=wait_sec + 1
                        )
                        break
                    except asyncio.TimeoutError:
                        on_status("Đang chạy gửi tự động...")
                        continue

                if job.max_messages > 0 and sent_count >= job.max_messages:
                    stop_reason = f"Đã đạt giới hạn {sent_count} tin nhắn"
                    _log(f"✅ {stop_reason}")
                    break

                if self._stop_event.is_set():
                    break

                wait_time = max(1.5, float(job.delay_seconds)) + random.uniform(
                    0.0, max(0.0, float(job.jitter_seconds))
                )
                on_status(f"Đã gửi {sent_count} tin — nghỉ {wait_time:.1f}s...")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait_time)
                    break
                except asyncio.TimeoutError:
                    pass
        except Exception as exc:
            _log(f"❌ Lỗi trong quá trình gửi: {exc}")
            on_status(f"Lỗi: {exc}")
            raise
        finally:
            try:
                self.client.remove_event_handler(_watcher)
            except Exception:
                pass
            self._is_running = False
            self._stop_event = None
            self._loop = None

        on_status(stop_reason)
        _log(f"⏹ Kết thúc: {stop_reason}")
        return stop_reason

    def request_stop(self) -> None:
        """Thread-safe / loop-safe trigger to stop an active auto job immediately."""
        event = self._stop_event
        loop = self._loop
        if event is None:
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if loop is not None and not loop.is_closed():
            if running_loop is loop:
                event.set()
            else:
                loop.call_soon_threadsafe(event.set)
        else:
            event.set()

    async def disconnect(self) -> None:
        """Stop any active job and cleanly disconnect the Telethon client."""
        self.request_stop()
        if self.client is not None:
            if self.client.is_connected():
                await self.client.disconnect()


class AsyncLoopThread:
    """Daemon thread hosting a single persistent asyncio event loop for Telethon."""

    def __init__(self, name: str = "TeleAutoAsyncLoop") -> None:
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        try:
            self.loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(self.loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            except Exception:
                pass
            finally:
                self.loop.close()

    def run_coro(self, coro: Any) -> concurrent.futures.Future:
        """Schedule a coroutine onto the dedicated event loop thread."""
        if self._stopped or self.loop.is_closed():
            raise RuntimeError("AsyncLoopThread đã dừng.")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop_loop(self, timeout: float = 5.0) -> None:
        """Cleanly stop the background event loop and join its thread."""
        if self._stopped:
            return
        self._stopped = True
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=timeout)
