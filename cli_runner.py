from __future__ import annotations

import asyncio
import getpass
import re
import sys
from datetime import datetime
from typing import Optional

from config_store import AppConfig, ConfigStore, JobConfig
from tele_engine import (
    DialogInfo,
    TeleAutoEngine,
    TwoFactorRequiredError,
    normalize_phone_number,
)

_TIMESTAMP_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]")


def _configure_console_encoding() -> None:
    """Ensure Vietnamese UTF-8 output/input works cleanly on Windows consoles."""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass


def _log_line(message: str) -> None:
    line = message.rstrip("\r\n")
    if _TIMESTAMP_PREFIX_RE.match(line):
        print(line, flush=True)
    else:
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] {line}", flush=True)


def _prompt_text(prompt: str, default: str = "", required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default:
            return default
        if not required:
            return ""
        print("Giá trị không được để trống. Vui lòng nhập lại.")


def _prompt_int(prompt: str, default: int, min_value: int = 0) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return max(min_value, default)
        try:
            value = int(raw)
        except ValueError:
            print("Vui lòng nhập một số nguyên hợp lệ.")
            continue
        if value < min_value:
            print(f"Giá trị phải >= {min_value}.")
            continue
        return value


def _prompt_float(prompt: str, default: float, min_value: float = 0.0) -> float:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return max(min_value, default)
        try:
            value = float(raw)
        except ValueError:
            print("Vui lòng nhập một số hợp lệ.")
            continue
        if value < min_value:
            print(f"Giá trị phải >= {min_value}.")
            continue
        return value


def _prompt_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} ({hint}): ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "c", "co", "có"}:
            return True
        if raw in {"n", "no", "k", "khong", "không"}:
            return False
        print("Vui lòng nhập 'y' (Có) hoặc 'n' (Không).")


def _ensure_api_credentials(config: AppConfig, store: ConfigStore) -> AppConfig:
    if config.api_id > 0 and config.api_hash.strip():
        return config

    print("\n--- Thiết lập Telegram API (chỉ cần 1 lần) ---")
    print("Lấy API ID và API Hash tại: https://my.telegram.org -> API development tools")

    while True:
        raw_id = input("Nhập API ID (số nguyên): ").strip()
        try:
            api_id = int(raw_id)
            if api_id > 0:
                break
        except ValueError:
            pass
        print("API ID phải là số nguyên dương.")

    api_hash = _prompt_text("Nhập API Hash", required=True)
    config.api_id = api_id
    config.api_hash = api_hash
    store.save(config)
    print("Đã lưu API ID & API Hash vào cấu hình.")
    return config


async def _ensure_authorized(
    engine: TeleAutoEngine,
    config: AppConfig,
    store: ConfigStore,
) -> None:
    print("\nĐang kết nối tới Telegram...")
    authorized = await engine.connect()
    if authorized:
        return

    print("Phiên chưa đăng nhập. Vui lòng đăng nhập tài khoản Telegram của bạn.")
    raw_phone = _prompt_text(
        "Số điện thoại (VD: 0901234567 hoặc +84901234567)",
        default=config.phone,
        required=True,
    )
    phone = normalize_phone_number(raw_phone)
    await engine.send_login_code(phone)
    print("Telegram đã gửi mã OTP tới ứng dụng Telegram (hoặc SMS) của bạn.")
    code = _prompt_text("Nhập mã OTP", required=True)

    try:
        await engine.complete_login(phone=phone, code=code)
    except TwoFactorRequiredError:
        password = getpass.getpass("Tài khoản có bật mật khẩu 2FA. Nhập mật khẩu: ")
        await engine.complete_login(phone=phone, code=code, password=password)

    config.phone = phone
    store.save(config)
    print("Đăng nhập thành công và đã lưu phiên!")


def _dialog_target_value(dialog: DialogInfo) -> str:
    if dialog.kind == "Saved":
        return "me"
    if dialog.username:
        return f"@{dialog.username}"
    return str(dialog.id)


def _select_target_chat(
    dialogs: list[DialogInfo],
    last_job: JobConfig,
) -> tuple[str, str]:
    print("\n--- Danh sách chat gần đây ---")
    if not dialogs:
        print("(Không tải được danh sách chat gần đây, bạn có thể nhập trực tiếp.)")
    else:
        for idx, dialog in enumerate(dialogs, start=1):
            print(f"  [{idx}] {dialog.display_label()}")

    default_chat = last_job.target_chat.strip()
    default_label = last_job.target_label.strip() or default_chat

    print("\nChọn chat đích:")
    print("- Nhập số thứ tự [1..N] trong danh sách ở trên")
    print("- Hoặc nhập trực tiếp @username, 'me', số điện thoại, hoặc ID chat")
    if default_chat:
        print(f"- Hoặc nhấn Enter để dùng lại: {default_label} ({default_chat})")

    while True:
        raw = input("Chat đích: ").strip()
        if not raw:
            if default_chat:
                return default_chat, default_label
            print("Chưa có chat đích mặc định. Vui lòng chọn số thứ tự hoặc nhập @username/ID.")
            continue

        if raw.isdigit() and dialogs:
            index = int(raw)
            if 1 <= index <= len(dialogs):
                chosen = dialogs[index - 1]
                return _dialog_target_value(chosen), chosen.display_label()
            # Only treat out-of-range small positive integers carefully; allow large numeric IDs.
            if len(raw) <= 3:
                print(f"Số thứ tự phải nằm trong khoảng 1..{len(dialogs)}.")
                continue

        return raw, raw


def _prompt_job_config(last_job: JobConfig, target_chat: str, target_label: str) -> JobConfig:
    print("\n--- Cấu hình gửi tự động ---")
    message_text = _prompt_text(
        "Nội dung tin nhắn gửi lặp lại",
        default=last_job.message_text,
        required=True,
    )
    stop_keywords_raw = _prompt_text(
        "Từ khóa dừng (cách nhau dấu phẩy, VD: ok, dậy rồi, stop)",
        default=last_job.stop_keywords_raw,
        required=False,
    )
    only_incoming = _prompt_yes_no(
        "Chỉ dừng khi người khác nhắn từ khóa (bỏ qua tin nhắn của chính mình)?",
        default=last_job.only_incoming,
    )
    delay_seconds = _prompt_float(
        "Khoảng nghỉ giữa mỗi tin (giây, tối thiểu 1.5)",
        default=max(1.5, last_job.delay_seconds),
        min_value=1.5,
    )
    jitter_seconds = _prompt_float(
        "Cộng thêm ngẫu nhiên 0..X giây",
        default=max(0.0, last_job.jitter_seconds),
        min_value=0.0,
    )
    max_messages = _prompt_int(
        "Giới hạn số tin tối đa (0 = gửi đến khi gặp từ khóa hoặc Ctrl+C)",
        default=max(0, last_job.max_messages),
        min_value=0,
    )

    return JobConfig(
        target_chat=target_chat,
        target_label=target_label,
        message_text=message_text,
        stop_keywords_raw=stop_keywords_raw,
        only_incoming=only_incoming,
        case_sensitive=last_job.case_sensitive,
        whole_message_match=last_job.whole_message_match,
        delay_seconds=delay_seconds,
        jitter_seconds=jitter_seconds,
        max_messages=max_messages,
    )


async def _async_cli() -> None:
    store = ConfigStore()
    config = store.load()
    config = _ensure_api_credentials(config, store)

    engine = TeleAutoEngine(config.api_id, config.api_hash)
    try:
        await _ensure_authorized(engine, config, store)
        me_summary = await engine.get_me_summary()
        print(f"\nĐã đăng nhập: {me_summary}")

        print("Đang tải 40 cuộc hội thoại gần đây...")
        dialogs = await engine.list_recent_dialogs(limit=40)

        target_chat, target_label = _select_target_chat(dialogs, config.last_job)
        job = _prompt_job_config(config.last_job, target_chat, target_label)

        config.last_job = job
        store.save(config)

        keywords = job.parsed_keywords()
        kw_summary = ", ".join(keywords) if keywords else "(Không có — chỉ dừng khi đạt giới hạn hoặc Ctrl+C)"
        print("\n=== BẮT ĐẦU CHẠY ===")
        print(f"Đích         : {job.target_label or job.target_chat} ({job.target_chat})")
        print(f"Từ khóa dừng : {kw_summary}")
        print(f"Khoảng nghỉ  : {job.delay_seconds:.1f}s + ngẫu nhiên 0..{job.jitter_seconds:.1f}s")
        print(f"Giới hạn tin : {job.max_messages if job.max_messages > 0 else 'Không giới hạn'}")
        print("Nhấn Ctrl+C bất cứ lúc nào để dừng an toàn.\n")

        sent_total = 0

        def on_log(msg: str) -> None:
            _log_line(msg)

        def on_status(status: str) -> None:
            _log_line(f"[Trạng thái] {status}")

        def on_count(count: int) -> None:
            nonlocal sent_total
            sent_total = count

        try:
            stop_reason = await engine.run_auto_job(
                job=job,
                on_log=on_log,
                on_status=on_status,
                on_count=on_count,
            )
            print(f"\nKết thúc: {stop_reason} (Tổng đã gửi: {sent_total} tin)")
        except asyncio.CancelledError:
            engine.request_stop()
            _log_line("Đã nhận Ctrl+C, đang dừng công việc an toàn...")
    finally:
        await engine.disconnect()


def launch_cli() -> None:
    """Entry point for the interactive Vietnamese Telegram Auto Sender CLI."""
    _configure_console_encoding()
    print("=== TELE AUTO SENDER (CLI) ===")
    try:
        asyncio.run(_async_cli())
    except KeyboardInterrupt:
        print("\nĐã dừng chương trình theo yêu cầu người dùng (Ctrl+C).")


if __name__ == "__main__":
    launch_cli()
