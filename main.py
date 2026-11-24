"""Entrypoint chính cho công cụ Telegram Auto Sender (GUI & CLI)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Đảm bảo import được các module cùng thư mục dù chạy từ bất kỳ working directory nào
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tele_auto_sender",
        description=(
            "Công cụ cá nhân tự động gửi tin nhắn Telegram bằng tài khoản đang đăng nhập "
            "và tự dừng ngay khi phát hiện từ khóa trong cuộc trò chuyện."
        ),
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Chạy ở chế độ dòng lệnh (Terminal) thay vì mở cửa sổ giao diện (GUI).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cli:
        from cli_runner import launch_cli

        launch_cli()
        return 0

    try:
        from gui_app import launch_gui
    except ImportError as exc:
        print(
            f"[!] Không mở được giao diện Tkinter ({exc}). Đang chuyển sang chế độ dòng lệnh (--cli)...",
            file=sys.stderr,
        )
        from cli_runner import launch_cli

        launch_cli()
        return 0

    launch_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
