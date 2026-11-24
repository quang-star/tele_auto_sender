from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "user_config.json"
DEFAULT_SESSION_PATH = BASE_DIR / "my_telegram_account"  # Telethon appends .session

MIN_DELAY_SECONDS = 1.5
MIN_JITTER_SECONDS = 0.0
MIN_MAX_MESSAGES = 0


@dataclass
class JobConfig:
    target_chat: str = ""             # "@username", "+84...", or integer ID as string e.g. "-100123456" or "123456"
    target_label: str = ""            # Display name in UI
    message_text: str = ""            # Content to send repeatedly
    stop_keywords_raw: str = ""       # Comma or newline separated keywords, e.g. "ok, dậy rồi"
    only_incoming: bool = True        # True = only stop when OTHER person sends keyword (event.out == False)
    case_sensitive: bool = False      # False = case-insensitive substring match
    whole_message_match: bool = False # True = entire stripped message must equal keyword; False = keyword in message
    delay_seconds: float = 10.0       # Base delay between messages (clamped >= 1.5s for safety)
    jitter_seconds: float = 2.0       # Random extra delay uniform(0, jitter_seconds), >= 0.0
    max_messages: int = 0             # 0 = unlimited until stop keyword or manual stop

    def parsed_keywords(self) -> list[str]:
        """Split stop_keywords_raw by comma or newline, strip whitespace, discard empty strings."""
        if not self.stop_keywords_raw:
            return []
        tokens = re.split(r"[,\r\n]+", self.stop_keywords_raw)
        seen: set[str] = set()
        result: list[str] = []
        for token in tokens:
            kw = token.strip()
            if kw and kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result


@dataclass
class AppConfig:
    api_id: int = 0
    api_hash: str = ""
    phone: str = ""
    last_job: JobConfig = field(default_factory=JobConfig)
    presets: dict[str, JobConfig] = field(default_factory=dict)


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _job_from_dict(raw: Any) -> JobConfig:
    defaults = JobConfig()
    if not isinstance(raw, dict):
        return defaults

    delay = _as_float(raw.get("delay_seconds", defaults.delay_seconds), defaults.delay_seconds)
    jitter = _as_float(raw.get("jitter_seconds", defaults.jitter_seconds), defaults.jitter_seconds)
    max_msgs = _as_int(raw.get("max_messages", defaults.max_messages), defaults.max_messages)

    return JobConfig(
        target_chat=_as_str(raw.get("target_chat", defaults.target_chat), defaults.target_chat),
        target_label=_as_str(raw.get("target_label", defaults.target_label), defaults.target_label),
        message_text=_as_str(raw.get("message_text", defaults.message_text), defaults.message_text),
        stop_keywords_raw=_as_str(
            raw.get("stop_keywords_raw", defaults.stop_keywords_raw),
            defaults.stop_keywords_raw,
        ),
        only_incoming=_as_bool(raw.get("only_incoming", defaults.only_incoming), defaults.only_incoming),
        case_sensitive=_as_bool(raw.get("case_sensitive", defaults.case_sensitive), defaults.case_sensitive),
        whole_message_match=_as_bool(
            raw.get("whole_message_match", defaults.whole_message_match),
            defaults.whole_message_match,
        ),
        delay_seconds=max(MIN_DELAY_SECONDS, delay),
        jitter_seconds=max(MIN_JITTER_SECONDS, jitter),
        max_messages=max(MIN_MAX_MESSAGES, max_msgs),
    )


def _app_from_dict(raw: Any) -> AppConfig:
    defaults = AppConfig()
    if not isinstance(raw, dict):
        return defaults

    presets_raw = raw.get("presets")
    presets: dict[str, JobConfig] = {}
    if isinstance(presets_raw, dict):
        for key, value in presets_raw.items():
            name = _as_str(key).strip()
            if name and isinstance(value, dict):
                presets[name] = _job_from_dict(value)

    return AppConfig(
        api_id=max(0, _as_int(raw.get("api_id", defaults.api_id), defaults.api_id)),
        api_hash=_as_str(raw.get("api_hash", defaults.api_hash), defaults.api_hash),
        phone=_as_str(raw.get("phone", defaults.phone), defaults.phone),
        last_job=_job_from_dict(raw.get("last_job")),
        presets=presets,
    )


class ConfigStore:
    def __init__(self, path: Path = DEFAULT_CONFIG_PATH):
        self.path = Path(path)

    def _backup_corrupted(self) -> None:
        backup_path = self.path.with_name(f"{self.path.name}.bak")
        try:
            os.replace(self.path, backup_path)
        except OSError:
            pass

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()

        try:
            content = self.path.read_text(encoding="utf-8")
            if not content.strip():
                self._backup_corrupted()
                return AppConfig()
            data = json.loads(content)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._backup_corrupted()
            return AppConfig()

        if not isinstance(data, dict):
            self._backup_corrupted()
            return AppConfig()

        return _app_from_dict(data)

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(config)
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = tmp_file.name
                json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
                tmp_file.write("\n")
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, self.path)
            tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
