"""Tkinter desktop GUI for Telegram Auto Sender."""

from __future__ import annotations

import concurrent.futures
import re
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, simpledialog, ttk
from typing import Optional
import webbrowser

from config_store import AppConfig, ConfigStore, JobConfig
from tele_engine import (
    AsyncLoopThread,
    DialogInfo,
    TeleAutoEngine,
    TwoFactorRequiredError,
    normalize_phone_number,
)

TELEGRAM_API_URL = "https://my.telegram.org"
TIMESTAMP_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]")


def _is_two_factor_error(exc: BaseException) -> bool:
    """Return True if exc indicates a Telegram 2FA password is required."""
    return isinstance(exc, TwoFactorRequiredError) or (
        type(exc).__name__ == "SessionPasswordNeededError"
    )


class TeleAutoSenderApp:
    """Main desktop GUI controller for Telegram Auto Sender."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Telegram Auto Sender — Gửi tin tự động & Dừng theo từ khóa")
        self.root.geometry("920x800")
        self.root.minsize(780, 680)

        self.config_store = ConfigStore()
        self.config: AppConfig = self.config_store.load()

        self.loop_thread = AsyncLoopThread()
        self.engine: Optional[TeleAutoEngine] = None
        self._engine_creds: Optional[tuple[int, str]] = None

        self._dialogs: list[DialogInfo] = []
        self._filtered_dialogs: list[DialogInfo] = []
        self._is_authorized: bool = False
        self._is_connecting: bool = False
        self._job_running: bool = False
        self._closing: bool = False
        self._active_job_future: Optional[concurrent.futures.Future] = None

        self._init_variables()
        self._build_ui()
        self._load_config_into_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.append_log("Ứng dụng đã sẵn sàng.")

        # Silently check existing session on startup if credentials were previously saved.
        if self.config.api_id > 0 and self.config.api_hash.strip():
            self.root.after(150, self._auto_check_session_on_startup)

    # ------------------------------------------------------------------
    # Initialization & UI layout
    # ------------------------------------------------------------------
    def _init_variables(self) -> None:
        self.api_id_var = tk.StringVar()
        self.api_hash_var = tk.StringVar()
        self.show_hash_var = tk.BooleanVar(value=False)
        self.phone_var = tk.StringVar()
        self.auth_status_var = tk.StringVar(value="⚪ Chưa kết nối")

        self.dialog_search_var = tk.StringVar()
        self.dialog_choice_var = tk.StringVar()
        self.target_chat_var = tk.StringVar()
        self.target_label_var = tk.StringVar()

        self.preset_var = tk.StringVar()

        self.stop_keywords_var = tk.StringVar()
        self.only_incoming_var = tk.BooleanVar(value=True)
        self.case_sensitive_var = tk.BooleanVar(value=False)
        self.whole_message_var = tk.BooleanVar(value=False)

        self.delay_var = tk.StringVar(value="10.0")
        self.jitter_var = tk.StringVar(value="2.0")
        self.max_messages_var = tk.StringVar(value="0")

        self.sent_count_var = tk.StringVar(value="Đã gửi: 0")
        self.job_status_var = tk.StringVar(value="Trạng thái: Sẵn sàng")

        self.dialog_search_var.trace_add("write", lambda *_: self._refresh_dialog_combobox())

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_auth_section(main)
        self._build_job_section(main)
        self._build_control_log_section(main)

    def _build_auth_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=" 1. Tài khoản Telegram ", padding=(10, 8))
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=2)

        ttk.Label(frame, text="API ID:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.api_id_entry = ttk.Entry(frame, textvariable=self.api_id_var, width=16)
        self.api_id_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=3)

        ttk.Label(frame, text="API Hash:").grid(row=0, column=2, sticky="w", padx=(0, 6), pady=3)
        hash_box = ttk.Frame(frame)
        hash_box.grid(row=0, column=3, sticky="ew", padx=(0, 10), pady=3)
        hash_box.columnconfigure(0, weight=1)

        self.api_hash_entry = ttk.Entry(hash_box, textvariable=self.api_hash_var, show="*")
        self.api_hash_entry.grid(row=0, column=0, sticky="ew")
        self.show_hash_chk = ttk.Checkbutton(
            hash_box,
            text="Hiện",
            variable=self.show_hash_var,
            command=self._toggle_api_hash_visibility,
        )
        self.show_hash_chk.grid(row=0, column=1, padx=(6, 0))

        self.help_api_btn = ttk.Button(
            frame,
            text="❓ Cách lấy API ID",
            command=self._show_api_help,
        )
        self.help_api_btn.grid(row=0, column=4, sticky="e", pady=3)

        ttk.Label(frame, text="Số điện thoại (+84...):").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=3
        )
        self.phone_entry = ttk.Entry(frame, textvariable=self.phone_var, width=20)
        self.phone_entry.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=3)

        self.auth_status_label = tk.Label(
            frame,
            textvariable=self.auth_status_var,
            anchor="w",
            fg="#4b5563",
            font=("Segoe UI", 9, "bold"),
        )
        self.auth_status_label.grid(row=1, column=2, columnspan=2, sticky="ew", padx=(0, 10), pady=3)

        btn_auth_box = ttk.Frame(frame)
        btn_auth_box.grid(row=1, column=4, sticky="ew", pady=3)

        self.connect_btn = ttk.Button(
            btn_auth_box,
            text="🔌 Kết nối / OTP",
            command=self._on_connect_clicked,
        )
        self.connect_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.qr_login_btn = ttk.Button(
            btn_auth_box,
            text="📷 Quét QR",
            command=self._on_qr_login_clicked,
        )
        self.qr_login_btn.pack(side=tk.LEFT)

    def _build_job_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=" 2. Chọn Chat & Cấu hình gửi ", padding=(10, 8))
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        # Row 0: Load dialogs + search filter
        dialog_top = ttk.Frame(frame)
        dialog_top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        dialog_top.columnconfigure(2, weight=1)

        self.load_dialogs_btn = ttk.Button(
            dialog_top,
            text="🔄 Tải danh sách chat",
            command=self._on_load_dialogs_clicked,
        )
        self.load_dialogs_btn.grid(row=0, column=0, sticky="w", padx=(0, 10))

        ttk.Label(dialog_top, text="🔍 Lọc nhanh:").grid(row=0, column=1, sticky="w", padx=(0, 6))
        self.dialog_search_entry = ttk.Entry(dialog_top, textvariable=self.dialog_search_var)
        self.dialog_search_entry.grid(row=0, column=2, sticky="ew")

        # Row 1: Dialog Combobox
        ttk.Label(frame, text="Chọn từ danh sách:").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.dialog_combo = ttk.Combobox(
            frame,
            textvariable=self.dialog_choice_var,
            state="readonly",
        )
        self.dialog_combo.grid(row=1, column=1, sticky="ew", pady=3)
        self.dialog_combo.bind("<<ComboboxSelected>>", self._on_dialog_selected)

        # Row 2: Manual target chat + Preset controls
        target_row = ttk.Frame(frame)
        target_row.grid(row=2, column=1, sticky="ew", pady=3)
        target_row.columnconfigure(0, weight=2)
        target_row.columnconfigure(3, weight=1)

        ttk.Label(frame, text="Target Chat (@/ID/me):").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=3
        )
        self.target_chat_entry = ttk.Entry(target_row, textvariable=self.target_chat_var, width=26)
        self.target_chat_entry.grid(row=0, column=0, sticky="ew")
        self.target_chat_entry.bind("<KeyRelease>", lambda _e: self.target_label_var.set(""))

        self.target_label_lbl = ttk.Label(
            target_row,
            textvariable=self.target_label_var,
            foreground="#4b5563",
        )
        self.target_label_lbl.grid(row=0, column=1, sticky="w", padx=(8, 12))

        ttk.Label(target_row, text="Preset:").grid(row=0, column=2, sticky="e", padx=(4, 4))
        self.preset_combo = ttk.Combobox(
            target_row,
            textvariable=self.preset_var,
            state="readonly",
            width=16,
        )
        self.preset_combo.grid(row=0, column=3, sticky="ew", padx=(0, 4))

        self.load_preset_btn = ttk.Button(
            target_row, text="Nạp", width=5, command=self._on_load_preset
        )
        self.load_preset_btn.grid(row=0, column=4, padx=2)
        self.save_preset_btn = ttk.Button(
            target_row, text="Lưu mẫu", width=8, command=self._on_save_preset
        )
        self.save_preset_btn.grid(row=0, column=5, padx=2)
        self.del_preset_btn = ttk.Button(
            target_row, text="Xóa", width=5, command=self._on_delete_preset
        )
        self.del_preset_btn.grid(row=0, column=6, padx=(2, 0))

        # Row 3: Multiline message text
        ttk.Label(frame, text="Nội dung tin nhắn:").grid(
            row=3, column=0, sticky="nw", padx=(0, 8), pady=(6, 3)
        )
        msg_box = ttk.Frame(frame)
        msg_box.grid(row=3, column=1, sticky="ew", pady=(6, 3))
        msg_box.columnconfigure(0, weight=1)

        self.message_text = tk.Text(msg_box, height=4, wrap="word", font=("Segoe UI", 10))
        self.message_text.grid(row=0, column=0, sticky="ew")
        msg_scroll = ttk.Scrollbar(msg_box, orient="vertical", command=self.message_text.yview)
        msg_scroll.grid(row=0, column=1, sticky="ns")
        self.message_text.configure(yscrollcommand=msg_scroll.set)

        # Row 4: Stop keywords
        ttk.Label(frame, text="Từ khóa dừng:").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=3
        )
        kw_frame = ttk.Frame(frame)
        kw_frame.grid(row=4, column=1, sticky="ew", pady=3)
        kw_frame.columnconfigure(0, weight=1)

        self.keywords_entry = ttk.Entry(kw_frame, textvariable=self.stop_keywords_var)
        self.keywords_entry.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            kw_frame,
            text="(cách nhau dấu phẩy, VD: ok, dậy rồi, dừng lại)",
            foreground="#6b7280",
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))

        # Row 5: Keyword flags
        flags_frame = ttk.Frame(frame)
        flags_frame.grid(row=5, column=1, sticky="ew", pady=3)

        ttk.Checkbutton(
            flags_frame,
            text="Chỉ dừng khi người khác nhắn (bỏ qua tin của chính mình)",
            variable=self.only_incoming_var,
        ).pack(side=tk.LEFT, padx=(0, 14))
        ttk.Checkbutton(
            flags_frame,
            text="Phân biệt HOA/thường",
            variable=self.case_sensitive_var,
        ).pack(side=tk.LEFT, padx=(0, 14))
        ttk.Checkbutton(
            flags_frame,
            text="Khớp toàn bộ tin nhắn",
            variable=self.whole_message_var,
        ).pack(side=tk.LEFT)

        # Row 6: Timing & limits
        ttk.Label(frame, text="Thời gian & Giới hạn:").grid(
            row=6, column=0, sticky="w", padx=(0, 8), pady=(4, 2)
        )
        timing_frame = ttk.Frame(frame)
        timing_frame.grid(row=6, column=1, sticky="ew", pady=(4, 2))

        ttk.Label(timing_frame, text="Khoảng nghỉ (giây):").pack(side=tk.LEFT)
        self.delay_spin = ttk.Spinbox(
            timing_frame,
            from_=1.5,
            to=86400.0,
            increment=0.5,
            textvariable=self.delay_var,
            width=8,
        )
        self.delay_spin.pack(side=tk.LEFT, padx=(6, 16))

        ttk.Label(timing_frame, text="Random thêm 0..X giây:").pack(side=tk.LEFT)
        self.jitter_spin = ttk.Spinbox(
            timing_frame,
            from_=0.0,
            to=3600.0,
            increment=0.5,
            textvariable=self.jitter_var,
            width=8,
        )
        self.jitter_spin.pack(side=tk.LEFT, padx=(6, 16))

        ttk.Label(timing_frame, text="Giới hạn số tin (0 = vô hạn):").pack(side=tk.LEFT)
        self.max_spin = ttk.Spinbox(
            timing_frame,
            from_=0,
            to=1000000,
            increment=1,
            textvariable=self.max_messages_var,
            width=8,
        )
        self.max_spin.pack(side=tk.LEFT, padx=(6, 0))

    def _build_control_log_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text=" 3. Điều khiển & Nhật ký trực tiếp ", padding=(10, 8))
        frame.grid(row=2, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ctrl = ttk.Frame(frame)
        ctrl.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctrl.columnconfigure(3, weight=1)

        self.start_btn = tk.Button(
            ctrl,
            text="▶ BẮT ĐẦU GỬI",
            command=self._on_start_clicked,
            bg="#16a34a",
            fg="#ffffff",
            activebackground="#15803d",
            activeforeground="#ffffff",
            disabledforeground="#d1d5db",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=6,
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 8))

        self.stop_btn = tk.Button(
            ctrl,
            text="⏹ DỪNG NGAY",
            command=self._on_stop_clicked,
            bg="#dc2626",
            fg="#ffffff",
            activebackground="#b91c1c",
            activeforeground="#ffffff",
            disabledforeground="#e5e7eb",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=6,
            relief=tk.FLAT,
            state=tk.DISABLED,
        )
        self.stop_btn.grid(row=0, column=1, padx=(0, 14))

        self.sent_count_lbl = tk.Label(
            ctrl,
            textvariable=self.sent_count_var,
            font=("Segoe UI", 10, "bold"),
            fg="#1d4ed8",
        )
        self.sent_count_lbl.grid(row=0, column=2, padx=(0, 14))

        self.job_status_lbl = tk.Label(
            ctrl,
            textvariable=self.job_status_var,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
            fg="#374151",
        )
        self.job_status_lbl.grid(row=0, column=3, sticky="ew")

        self.clear_log_btn = ttk.Button(ctrl, text="Xóa log", command=self._clear_log)
        self.clear_log_btn.grid(row=0, column=4, sticky="e")

        self.log_text = scrolledtext.ScrolledText(
            frame,
            height=11,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            bg="#0f172a",
            fg="#e2e8f0",
            insertbackground="#e2e8f0",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")

    # ------------------------------------------------------------------
    # Config & form helpers
    # ------------------------------------------------------------------
    def _load_config_into_ui(self) -> None:
        self.api_id_var.set(str(self.config.api_id) if self.config.api_id > 0 else "")
        self.api_hash_var.set(self.config.api_hash)
        self.phone_var.set(self.config.phone)
        self._apply_job_to_ui(self.config.last_job)
        self._refresh_preset_combobox()

    def _apply_job_to_ui(self, job: JobConfig) -> None:
        self.target_chat_var.set(job.target_chat)
        self.target_label_var.set(job.target_label)
        self.message_text.delete("1.0", tk.END)
        self.message_text.insert("1.0", job.message_text)
        self.stop_keywords_var.set(job.stop_keywords_raw)
        self.only_incoming_var.set(job.only_incoming)
        self.case_sensitive_var.set(job.case_sensitive)
        self.whole_message_var.set(job.whole_message_match)
        self.delay_var.set(str(job.delay_seconds))
        self.jitter_var.set(str(job.jitter_seconds))
        self.max_messages_var.set(str(job.max_messages))

    def _read_job_from_ui(self, strict: bool = True) -> JobConfig:
        target_chat = self.target_chat_var.get().strip()
        target_label = self.target_label_var.get().strip()
        msg = self.message_text.get("1.0", "end-1c")
        keywords_raw = self.stop_keywords_var.get().strip()

        delay_raw = self.delay_var.get().strip()
        jitter_raw = self.jitter_var.get().strip()
        max_raw = self.max_messages_var.get().strip()

        if strict:
            try:
                delay = float(delay_raw)
            except ValueError as exc:
                raise ValueError("Khoảng nghỉ (giây) phải là một số hợp lệ.") from exc
            try:
                jitter = float(jitter_raw)
            except ValueError as exc:
                raise ValueError("Random thêm (giây) phải là một số hợp lệ.") from exc
            try:
                max_msgs = int(max_raw)
            except ValueError as exc:
                raise ValueError("Giới hạn số tin phải là số nguyên.") from exc
        else:
            try:
                delay = float(delay_raw)
            except ValueError:
                delay = self.config.last_job.delay_seconds
            try:
                jitter = float(jitter_raw)
            except ValueError:
                jitter = self.config.last_job.jitter_seconds
            try:
                max_msgs = int(max_raw)
            except ValueError:
                max_msgs = self.config.last_job.max_messages

        delay = max(1.5, delay)
        jitter = max(0.0, jitter)
        max_msgs = max(0, max_msgs)

        return JobConfig(
            target_chat=target_chat,
            target_label=target_label,
            message_text=msg,
            stop_keywords_raw=keywords_raw,
            only_incoming=bool(self.only_incoming_var.get()),
            case_sensitive=bool(self.case_sensitive_var.get()),
            whole_message_match=bool(self.whole_message_var.get()),
            delay_seconds=delay,
            jitter_seconds=jitter,
            max_messages=max_msgs,
        )

    def _save_current_config(self, strict: bool = False) -> None:
        api_id_str = self.api_id_var.get().strip()
        if api_id_str.isdigit():
            self.config.api_id = int(api_id_str)
        elif not api_id_str:
            self.config.api_id = 0

        self.config.api_hash = self.api_hash_var.get().strip()
        self.config.phone = self.phone_var.get().strip()
        self.config.last_job = self._read_job_from_ui(strict=strict)
        self.config_store.save(self.config)

    def _refresh_preset_combobox(self) -> None:
        names = sorted(self.config.presets.keys(), key=str.casefold)
        self.preset_combo["values"] = names
        if self.preset_var.get() not in names:
            self.preset_var.set(names[0] if names else "")

    def _toggle_api_hash_visibility(self) -> None:
        self.api_hash_entry.configure(show="" if self.show_hash_var.get() else "*")

    def _show_api_help(self) -> None:
        text = (
            "Hướng dẫn điền form tạo API trên https://my.telegram.org:\n\n"
            "1. Đăng nhập bằng SĐT Telegram (nhập mã gửi về app Telegram, KHÔNG phải SMS).\n"
            "2. Bấm vào 'API development tools'.\n"
            "3. Điền form 'Create new application' đúng như sau:\n"
            "   • App title: Gõ tên tiếng Anh không dấu 6-15 ký tự (VD: MyPersonalTool)\n"
            "   • Short name: Gõ chữ liền không dấu/ký tự đặc biệt, 6-15 ký tự (VD: mypersonal26)\n"
            "   • URL: ĐỂ TRỐNG\n"
            "   • Platform: Chọn 'Desktop'\n"
            "   • Description: ĐỂ TRỐNG\n"
            "4. Bấm 'Create application' -> Copy 'App api_id' và 'App api_hash'.\n\n"
            "Mẹo nếu bị báo chữ 'ERROR': Tắt VPN / 1.1.1.1 (WARP), đổi Short name dài hơn (chỉ chữ và số, không chứa chữ 'telegram').\n\n"
            "Bạn có muốn mở https://my.telegram.org trong trình duyệt ngay bây giờ không?"
        )
        if messagebox.askyesno("Hướng dẫn lấy API ID / API Hash", text, parent=self.root):
            webbrowser.open(TELEGRAM_API_URL)

    # ------------------------------------------------------------------
    # Thread-safe UI callbacks & logging
    # ------------------------------------------------------------------
    def append_log(self, text: str) -> None:
        """Append a timestamped log line safely from any thread."""
        if self._closing:
            return
        line = text.rstrip("\r\n")
        if not TIMESTAMP_PREFIX_RE.match(line):
            line = f"[{datetime.now().strftime('%H:%M:%S')}] {line}"

        def _write() -> None:
            if self._closing:
                return
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")

        self.root.after(0, _write)

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _set_auth_status(self, text: str, color: str = "#4b5563") -> None:
        self.root.after(0, lambda: self._apply_auth_status(text, color))

    def _apply_auth_status(self, text: str, color: str) -> None:
        self.auth_status_var.set(text)
        self.auth_status_label.configure(fg=color)

    def _set_job_status(self, text: str) -> None:
        self.root.after(0, lambda: self.job_status_var.set(f"Trạng thái: {text}"))

    def _set_sent_count(self, count: int) -> None:
        self.root.after(0, lambda: self.sent_count_var.set(f"Đã gửi: {count}"))

    def _update_controls_state(self) -> None:
        busy = self._job_running or self._is_connecting
        self.connect_btn.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.qr_login_btn.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.load_dialogs_btn.configure(state=tk.DISABLED if busy else tk.NORMAL)

        if self._job_running:
            self.start_btn.configure(state=tk.DISABLED, bg="#86efac")
            self.stop_btn.configure(state=tk.NORMAL, bg="#dc2626", cursor="hand2")
            self.load_preset_btn.configure(state=tk.DISABLED)
            self.del_preset_btn.configure(state=tk.DISABLED)
        else:
            self.start_btn.configure(
                state=tk.DISABLED if self._is_connecting else tk.NORMAL,
                bg="#16a34a",
            )
            self.stop_btn.configure(state=tk.DISABLED, bg="#fca5a5", cursor="arrow")
            self.load_preset_btn.configure(state=tk.NORMAL)
            self.del_preset_btn.configure(state=tk.NORMAL)

    # ------------------------------------------------------------------
    # Engine initialization & authentication flow
    # ------------------------------------------------------------------
    def _read_api_credentials(self) -> tuple[int, str]:
        api_id_raw = self.api_id_var.get().strip()
        api_hash = self.api_hash_var.get().strip()
        if not api_id_raw.isdigit() or int(api_id_raw) <= 0:
            raise ValueError("API ID phải là số nguyên dương.")
        if not api_hash:
            raise ValueError("Vui lòng nhập API Hash.")
        return int(api_id_raw), api_hash

    def _connect_and_check_coro(self, api_id: int, api_hash: str):
        async def _connect_and_check() -> Optional[str]:
            if self.engine is not None and self._engine_creds != (api_id, api_hash):
                await self.engine.disconnect()
                self.engine = None
                self._engine_creds = None
            if self.engine is None:
                self.engine = TeleAutoEngine(api_id=api_id, api_hash=api_hash)
                self._engine_creds = (api_id, api_hash)
            authorized = await self.engine.connect()
            if authorized:
                return await self.engine.get_me_summary()
            return None

        return _connect_and_check()

    def _auto_check_session_on_startup(self) -> None:
        try:
            api_id, api_hash = self._read_api_credentials()
        except ValueError:
            return

        self._is_connecting = True
        self._update_controls_state()
        self._set_auth_status("🟡 Đang kiểm tra phiên...", "#d97706")

        fut = self.loop_thread.run_coro(self._connect_and_check_coro(api_id, api_hash))
        fut.add_done_callback(lambda f: self.root.after(0, self._on_startup_check_done, f))

    def _on_startup_check_done(self, fut: concurrent.futures.Future) -> None:
        self._is_connecting = False
        self._update_controls_state()
        if self._closing:
            return
        try:
            summary = fut.result()
        except Exception as exc:
            self._is_authorized = False
            self._set_auth_status("⚪ Chưa kết nối", "#4b5563")
            self.append_log(f"⚠ Không thể tự kiểm tra phiên cũ: {exc}")
            return

        if summary:
            self._is_authorized = True
            self._set_auth_status(f"🟢 Đã đăng nhập: {summary}", "#15803d")
            self.append_log(f"Khôi phục phiên đăng nhập: {summary}")
        else:
            self._is_authorized = False
            self._set_auth_status("⚪ Chưa đăng nhập (bấm Kết nối)", "#4b5563")

    def _on_connect_clicked(self) -> None:
        if self._job_running or self._is_connecting:
            return
        try:
            api_id, api_hash = self._read_api_credentials()
        except ValueError as exc:
            messagebox.showwarning("Thiếu thông tin API", str(exc), parent=self.root)
            return

        phone = normalize_phone_number(self.phone_var.get())
        if phone and phone != self.phone_var.get().strip():
            self.phone_var.set(phone)
        self._is_connecting = True
        self._update_controls_state()
        self._set_auth_status("🟡 Đang kết nối Telegram...", "#d97706")
        self.append_log("Đang kết nối tới Telegram...")

        fut = self.loop_thread.run_coro(self._connect_and_check_coro(api_id, api_hash))
        fut.add_done_callback(
            lambda f: self.root.after(0, self._on_initial_connect_done, f, phone)
        )

    def _on_initial_connect_done(
        self, fut: concurrent.futures.Future, phone: str
    ) -> None:
        if self._closing:
            return
        try:
            summary = fut.result()
        except Exception as exc:
            self._is_connecting = False
            self._is_authorized = False
            self._update_controls_state()
            self._set_auth_status("❌ Lỗi kết nối", "#dc2626")
            self.append_log(f"❌ Lỗi kết nối Telegram: {exc}")
            messagebox.showerror("Lỗi kết nối", str(exc), parent=self.root)
            return

        if summary:
            self._finish_auth_success(summary)
            return

        if not phone:
            self._is_connecting = False
            self._is_authorized = False
            self._update_controls_state()
            self._set_auth_status("⚪ Chưa đăng nhập", "#4b5563")
            messagebox.showwarning(
                "Thiếu số điện thoại",
                "Phiên chưa đăng nhập. Vui lòng nhập Số điện thoại (VD: +84901234567) để nhận mã OTP.",
                parent=self.root,
            )
            self.phone_entry.focus_set()
            return

        self._set_auth_status("🟡 Đang gửi mã OTP...", "#d97706")
        self.append_log(f"Đang gửi yêu cầu mã OTP tới {phone}...")

        assert self.engine is not None
        send_fut = self.loop_thread.run_coro(self.engine.send_login_code(phone))
        send_fut.add_done_callback(
            lambda f: self.root.after(0, self._on_send_code_done, f, phone)
        )

    def _on_send_code_done(self, fut: concurrent.futures.Future, phone: str) -> None:
        if self._closing:
            return
        try:
            delivery_hint = fut.result() or "Kiểm tra mã xác nhận trong app Telegram."
        except Exception as exc:
            self._is_connecting = False
            self._is_authorized = False
            self._update_controls_state()
            self._set_auth_status("❌ Gửi OTP thất bại", "#dc2626")
            self.append_log(f"❌ Không thể gửi mã OTP: {exc}")
            messagebox.showerror("Lỗi gửi OTP", str(exc), parent=self.root)
            return

        self.append_log(f"📩 {delivery_hint}")
        otp_code = simpledialog.askstring(
            "Nhập mã OTP Telegram",
            f"Số ĐT: {phone}\n\n{delivery_hint}\n\nNhập mã OTP (5 chữ số):",
            parent=self.root,
        )
        if not otp_code or not otp_code.strip():
            self._is_connecting = False
            self._is_authorized = False
            self._update_controls_state()
            self._set_auth_status("⚪ Đã hủy đăng nhập", "#4b5563")
            self.append_log("Người dùng đã hủy nhập mã OTP.")
            return

        self._complete_login_attempt(phone, otp_code.strip(), password=None)

    def _on_qr_login_clicked(self) -> None:
        if self._job_running or self._is_connecting:
            return
        try:
            api_id, api_hash = self._read_api_credentials()
        except ValueError as exc:
            messagebox.showwarning("Thiếu thông tin API", str(exc), parent=self.root)
            return

        try:
            import qrcode  # type: ignore[import-untyped]
        except ImportError:
            messagebox.showerror(
                "Thiếu thư viện qrcode",
                "Vui lòng chạy: pip install qrcode",
                parent=self.root,
            )
            return

        self._save_current_config(strict=False)
        self._is_connecting = True
        self._update_controls_state()
        self._set_auth_status("🟡 Đang tạo mã QR đăng nhập...", "#d97706")
        self.append_log("Đang tạo mã QR đăng nhập Telegram...")

        import asyncio as _asyncio

        qr_win = tk.Toplevel(self.root)
        qr_win.title("Đăng nhập nhanh bằng Mã QR Telegram")
        qr_win.resizable(False, False)
        qr_win.transient(self.root)
        qr_win.grab_set()

        info_lbl = ttk.Label(
            qr_win,
            text=(
                "Mở Telegram trên điện thoại:\n"
                "Settings (Cài đặt) -> Devices (Thiết bị) -> Link Desktop Device\n"
                "rồi đưa camera quét mã QR bên dưới (không cần chờ mã OTP!):"
            ),
            justify="center",
            padding=(16, 12, 16, 6),
        )
        info_lbl.pack()

        canvas_size = 290
        qr_canvas = tk.Canvas(
            qr_win,
            width=canvas_size,
            height=canvas_size,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#d1d5db",
        )
        qr_canvas.pack(padx=20, pady=8)
        qr_canvas.create_text(
            canvas_size // 2,
            canvas_size // 2,
            text="Đang tải mã QR...",
            fill="#374151",
            font=("Segoe UI", 10, "bold"),
        )

        cancel_holder: list[_asyncio.Event] = []

        def _cancel_qr() -> None:
            if cancel_holder:
                self.loop_thread.loop.call_soon_threadsafe(cancel_holder[0].set)
            if qr_win.winfo_exists():
                qr_win.destroy()

        ttk.Button(qr_win, text="Hủy", command=_cancel_qr).pack(pady=(4, 14))
        qr_win.protocol("WM_DELETE_WINDOW", _cancel_qr)

        def _render_qr_on_ui(url: str) -> None:
            if self._closing or not qr_win.winfo_exists():
                return
            qr_obj = qrcode.QRCode(border=2, box_size=8)
            qr_obj.add_data(url)
            qr_obj.make(fit=True)
            matrix = qr_obj.get_matrix()
            n = len(matrix)
            qr_canvas.delete("all")
            cell = canvas_size / max(1, n)
            for r, row in enumerate(matrix):
                y0 = r * cell
                y1 = (r + 1) * cell
                for c, val in enumerate(row):
                    if val:
                        qr_canvas.create_rectangle(
                            c * cell, y0, (c + 1) * cell, y1, fill="#000000", outline="#000000"
                        )

        async def _qr_flow() -> str:
            if self.engine is not None and self._engine_creds != (api_id, api_hash):
                await self.engine.disconnect()
                self.engine = None
                self._engine_creds = None
            if self.engine is None:
                self.engine = TeleAutoEngine(api_id=api_id, api_hash=api_hash)
                self._engine_creds = (api_id, api_hash)

            cancel_ev = _asyncio.Event()
            cancel_holder.append(cancel_ev)
            return await self.engine.login_with_qr(
                on_qr_url=lambda u: self.root.after(0, _render_qr_on_ui, u),
                cancel_event=cancel_ev,
            )

        fut = self.loop_thread.run_coro(_qr_flow())
        fut.add_done_callback(lambda f: self.root.after(0, self._on_qr_login_done, f, qr_win))

    def _on_qr_login_done(self, fut: concurrent.futures.Future, qr_win: tk.Toplevel) -> None:
        if qr_win.winfo_exists():
            qr_win.destroy()
        if self._closing:
            return
        try:
            summary = fut.result()
        except Exception as exc:
            if _is_two_factor_error(exc):
                self.append_log("Đã quét QR! Tài khoản bật 2FA -> yêu cầu nhập mật khẩu.")
                password = simpledialog.askstring(
                    "Mật khẩu 2FA Telegram",
                    "Quét QR thành công! Tài khoản có mật khẩu 2 bước (2FA).\nNhập mật khẩu 2FA:",
                    show="*",
                    parent=self.root,
                )
                if not password:
                    self._is_connecting = False
                    self._update_controls_state()
                    self._set_auth_status("⚪ Đã hủy nhập 2FA", "#4b5563")
                    return
                assert self.engine is not None
                pw_fut = self.loop_thread.run_coro(self.engine.complete_2fa_password(password))
                pw_fut.add_done_callback(
                    lambda f: self.root.after(0, self._on_qr_2fa_done, f)
                )
                return

            self._is_connecting = False
            self._update_controls_state()
            msg = str(exc)
            if "hủy" in msg.lower():
                self._set_auth_status("⚪ Đã hủy quét QR", "#4b5563")
                self.append_log("Người dùng đã đóng cửa sổ quét QR.")
            else:
                self._set_auth_status("❌ Lỗi đăng nhập QR", "#dc2626")
                self.append_log(f"❌ Lỗi đăng nhập QR: {exc}")
                messagebox.showerror("Lỗi đăng nhập QR", msg, parent=self.root)
            return

        self._finish_auth_success(summary)

    def _on_qr_2fa_done(self, fut: concurrent.futures.Future) -> None:
        if self._closing:
            return
        try:
            summary = fut.result()
        except Exception as exc:
            self._is_connecting = False
            self._update_controls_state()
            self._set_auth_status("❌ Sai mật khẩu 2FA", "#dc2626")
            self.append_log(f"❌ Lỗi 2FA: {exc}")
            messagebox.showerror("Lỗi 2FA", str(exc), parent=self.root)
            return
        self._finish_auth_success(summary)

    def _complete_login_attempt(
        self, phone: str, code: str, password: Optional[str]
    ) -> None:
        assert self.engine is not None
        self._set_auth_status("🟡 Đang xác thực...", "#d97706")
        fut = self.loop_thread.run_coro(
            self.engine.complete_login(phone=phone, code=code, password=password)
        )
        fut.add_done_callback(
            lambda f: self.root.after(0, self._on_complete_login_done, f, phone, code)
        )

    def _on_complete_login_done(
        self, fut: concurrent.futures.Future, phone: str, code: str
    ) -> None:
        if self._closing:
            return
        try:
            summary = fut.result()
        except Exception as exc:
            if _is_two_factor_error(exc):
                self.append_log("Tài khoản bật xác thực 2 bước (2FA). Yêu cầu nhập mật khẩu.")
                password = simpledialog.askstring(
                    "Mật khẩu 2FA Telegram",
                    "Tài khoản đang bật xác thực 2 bước (Cloud Password).\nVui lòng nhập mật khẩu 2FA:",
                    show="*",
                    parent=self.root,
                )
                if password is None or not password:
                    self._is_connecting = False
                    self._is_authorized = False
                    self._update_controls_state()
                    self._set_auth_status("⚪ Đã hủy nhập 2FA", "#4b5563")
                    self.append_log("Người dùng đã hủy nhập mật khẩu 2FA.")
                    return
                self._complete_login_attempt(phone, code, password=password)
                return

            self._is_connecting = False
            self._is_authorized = False
            self._update_controls_state()
            self._set_auth_status("❌ Đăng nhập thất bại", "#dc2626")
            self.append_log(f"❌ Đăng nhập thất bại: {exc}")
            messagebox.showerror("Đăng nhập thất bại", str(exc), parent=self.root)
            return

        self._finish_auth_success(summary)

    def _finish_auth_success(self, summary: str) -> None:
        self._is_connecting = False
        self._is_authorized = True
        self._update_controls_state()
        self._set_auth_status(f"🟢 Đã đăng nhập: {summary}", "#15803d")
        self.append_log(f"✅ Đã đăng nhập thành công: {summary}")
        self._save_current_config(strict=False)

    # ------------------------------------------------------------------
    # Dialog loading & selection
    # ------------------------------------------------------------------
    def _on_load_dialogs_clicked(self) -> None:
        if self._job_running or self._is_connecting:
            return
        if not self._is_authorized or self.engine is None:
            messagebox.showwarning(
                "Chưa đăng nhập",
                "Vui lòng bấm '🔌 Kết nối / Đăng nhập' trước khi tải danh sách chat.",
                parent=self.root,
            )
            return

        self.load_dialogs_btn.configure(state=tk.DISABLED)
        self.append_log("Đang tải danh sách chat gần đây...")
        fut = self.loop_thread.run_coro(self.engine.list_recent_dialogs(limit=80))
        fut.add_done_callback(lambda f: self.root.after(0, self._on_dialogs_loaded, f))

    def _on_dialogs_loaded(self, fut: concurrent.futures.Future) -> None:
        if self._closing:
            return
        self._update_controls_state()
        try:
            dialogs: list[DialogInfo] = fut.result()
        except Exception as exc:
            self.append_log(f"❌ Lỗi tải danh sách chat: {exc}")
            messagebox.showerror("Lỗi tải danh sách chat", str(exc), parent=self.root)
            return

        self._dialogs = dialogs
        self._refresh_dialog_combobox()
        self.append_log(f"📋 Đã tải {len(dialogs)} cuộc trò chuyện.")

    def _refresh_dialog_combobox(self) -> None:
        query = self.dialog_search_var.get().strip().casefold()
        if not query:
            filtered = list(self._dialogs)
        else:
            filtered = [
                d
                for d in self._dialogs
                if query in d.display_label().casefold()
                or query in d.name.casefold()
                or (d.username and query in d.username.casefold())
                or query in str(d.id)
            ]

        self._filtered_dialogs = filtered
        labels = [d.display_label() for d in filtered]
        self.dialog_combo["values"] = labels
        if labels:
            self.dialog_choice_var.set(labels[0])
        else:
            self.dialog_choice_var.set("")

    def _on_dialog_selected(self, _event=None) -> None:
        idx = self.dialog_combo.current()
        if idx < 0 or idx >= len(self._filtered_dialogs):
            return
        dialog = self._filtered_dialogs[idx]
        if dialog.kind == "Saved":
            target = "me"
        elif dialog.username:
            uname = dialog.username.lstrip("@")
            target = f"@{uname}"
        else:
            target = str(dialog.id)

        self.target_chat_var.set(target)
        self.target_label_var.set(dialog.display_label())
        self.append_log(f"Đã chọn chat: {dialog.display_label()} -> {target}")

    # ------------------------------------------------------------------
    # Preset management
    # ------------------------------------------------------------------
    def _on_load_preset(self) -> None:
        if self._job_running:
            return
        name = self.preset_var.get().strip()
        if not name or name not in self.config.presets:
            messagebox.showinfo("Thông báo", "Chưa có preset nào được chọn.", parent=self.root)
            return
        self._apply_job_to_ui(self.config.presets[name])
        self.append_log(f"Đã nạp cấu hình mẫu: '{name}'.")

    def _on_save_preset(self) -> None:
        try:
            job = self._read_job_from_ui(strict=True)
        except ValueError as exc:
            messagebox.showwarning("Cấu hình chưa hợp lệ", str(exc), parent=self.root)
            return

        name = simpledialog.askstring(
            "Lưu cấu hình mẫu (Preset)",
            "Nhập tên gợi nhớ cho preset này:",
            initialvalue=self.preset_var.get().strip(),
            parent=self.root,
        )
        if not name or not name.strip():
            return
        name = name.strip()
        self.config.presets[name] = job
        self.config_store.save(self.config)
        self._refresh_preset_combobox()
        self.preset_var.set(name)
        self.append_log(f"💾 Đã lưu preset '{name}'.")

    def _on_delete_preset(self) -> None:
        if self._job_running:
            return
        name = self.preset_var.get().strip()
        if not name or name not in self.config.presets:
            return
        if not messagebox.askyesno(
            "Xóa preset", f"Bạn có chắc muốn xóa preset '{name}'?", parent=self.root
        ):
            return
        del self.config.presets[name]
        self.config_store.save(self.config)
        self._refresh_preset_combobox()
        self.append_log(f"🗑️ Đã xóa preset '{name}'.")

    # ------------------------------------------------------------------
    # Auto-send job control
    # ------------------------------------------------------------------
    def _on_start_clicked(self) -> None:
        if self._job_running or self._is_connecting:
            return
        if not self._is_authorized or self.engine is None:
            messagebox.showwarning(
                "Chưa đăng nhập",
                "Vui lòng bấm '🔌 Kết nối / Đăng nhập' trước khi bắt đầu gửi.",
                parent=self.root,
            )
            return

        try:
            job = self._read_job_from_ui(strict=True)
        except ValueError as exc:
            messagebox.showwarning("Cấu hình không hợp lệ", str(exc), parent=self.root)
            return

        if not job.target_chat:
            messagebox.showwarning(
                "Thiếu người nhận",
                "Vui lòng chọn chat từ danh sách hoặc nhập @username / ID / 'me'.",
                parent=self.root,
            )
            self.target_chat_entry.focus_set()
            return

        if not job.message_text.strip():
            messagebox.showwarning(
                "Thiếu nội dung",
                "Vui lòng nhập nội dung tin nhắn cần gửi.",
                parent=self.root,
            )
            self.message_text.focus_set()
            return

        # Reflect clamped values back into Spinboxes for clarity
        self.delay_var.set(str(job.delay_seconds))
        self.jitter_var.set(str(job.jitter_seconds))
        self.max_messages_var.set(str(job.max_messages))

        self._save_current_config(strict=True)

        self._job_running = True
        self._update_controls_state()
        self._set_sent_count(0)
        self._set_job_status("Đang chạy...")

        keywords = job.parsed_keywords()
        kw_info = ", ".join(keywords) if keywords else "(Không có — chỉ dừng thủ công hoặc theo giới hạn)"
        self.append_log(
            f"▶ Bắt đầu gửi tới '{job.target_chat}' | Nghỉ: {job.delay_seconds}s (+0..{job.jitter_seconds}s) | Từ khóa dừng: {kw_info}"
        )

        fut = self.loop_thread.run_coro(
            self.engine.run_auto_job(
                job=job,
                on_log=self.append_log,
                on_status=self._set_job_status,
                on_count=self._set_sent_count,
            )
        )
        self._active_job_future = fut
        fut.add_done_callback(lambda f: self.root.after(0, self._on_job_finished, f))

    def _on_stop_clicked(self) -> None:
        if not self._job_running or self.engine is None:
            return
        self.stop_btn.configure(state=tk.DISABLED)
        self._set_job_status("Đang dừng...")
        self.append_log("⏹ Đã gửi yêu cầu dừng ngay...")
        self.engine.request_stop()

    def _on_job_finished(self, fut: concurrent.futures.Future) -> None:
        self._job_running = False
        self._active_job_future = None
        if self._closing:
            return

        self._update_controls_state()
        try:
            reason = fut.result()
        except Exception as exc:
            self._set_job_status(f"Lỗi: {exc}")
            self.append_log(f"❌ Tác vụ dừng do lỗi: {exc}")
            messagebox.showerror("Lỗi khi gửi tin", str(exc), parent=self.root)
            return

        self._set_job_status(reason)
        self.append_log(f"🏁 Kết thúc tác vụ: {reason}")

    # ------------------------------------------------------------------
    # Clean shutdown
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True

        try:
            self._save_current_config(strict=False)
        except Exception:
            pass

        if self.engine is not None:
            try:
                self.engine.request_stop()
            except Exception:
                pass
            try:
                fut = self.loop_thread.run_coro(self.engine.disconnect())
                fut.result(timeout=3.0)
            except Exception:
                pass

        try:
            self.loop_thread.stop_loop()
        except Exception:
            pass

        self.root.destroy()


def launch_gui() -> None:
    """Create the Tk root window and run the Telegram Auto Sender GUI."""
    root = tk.Tk()
    TeleAutoSenderApp(root)

    # Ensure window is visible & brought to front on Windows even if spawned with SW_HIDE
    def _bring_to_front() -> None:
        try:
            root.state("normal")
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.update_idletasks()
            import sys as _sys

            if _sys.platform == "win32":
                import ctypes

                hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            root.focus_force()
            root.after(400, lambda: root.attributes("-topmost", False))
        except Exception:
            pass

    root.after(50, _bring_to_front)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
