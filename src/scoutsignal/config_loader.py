from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import yaml
from dotenv import load_dotenv


@dataclass
class BrowserConfig:
    user_data_dir: Path
    headless: bool = False
    channel: Optional[str] = None
    # BCP-47 locale (e.g. he-IL for Hebrew WhatsApp UI, en-US default).
    locale: str = "en-US"
    # Extra Chromium CLI flags (e.g. --no-first-run) for quieter unattended launches.
    extra_chromium_args: List[str] = field(default_factory=list)


@dataclass
class RunConfig:
    poll_interval_seconds: int = 300
    max_messages_per_chat: int = 120
    seed_on_first_scan: bool = True
    whatsapp_url: str = "https://web.whatsapp.com"
    # Max wall-clock time to open one chat from search (avoids hanging on modals / empty results).
    open_chat_timeout_seconds: int = 300


@dataclass
class DefaultsConfig:
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    require_url: bool = False
    min_text_length: int = 15


@dataclass
class EmailConfig:
    enabled: bool = True
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    use_tls: bool = True
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)
    subject_prefix: str = "[ScoutSignal] "
    password_env: str = "SCOUTSIGNAL_SMTP_PASSWORD"
    # If true, send an email after every successful scan (even 0 job hits), with keyword scan stats.
    always_send_summary: bool = False


@dataclass
class StateConfig:
    sqlite_path: Path


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class DiagnosticsConfig:
    error_screenshots: bool = True
    screenshots_dir: Optional[Path] = None


@dataclass
class ChatEntry:
    title: str
    enabled: bool = True
    include_keywords: Optional[List[str]] = None
    exclude_keywords: Optional[List[str]] = None
    require_url: Optional[bool] = None


@dataclass
class AppConfig:
    browser: BrowserConfig
    run: RunConfig
    defaults: DefaultsConfig
    email: EmailConfig
    state: StateConfig
    logging: LoggingConfig
    diagnostics: DiagnosticsConfig
    chats: list[ChatEntry]
    project_root: Path


def _expand(path_str: str) -> Path:
    return Path(path_str).expanduser().resolve()


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data


def load_app_config(config_path: Path, chats_path: Path) -> AppConfig:
    # Optional secrets next to config (e.g. SCOUTSIGNAL_SMTP_PASSWORD); does not override existing env.
    load_dotenv(config_path.parent / ".env")
    raw = load_yaml(config_path)
    chats_raw = load_yaml(chats_path)

    browser = raw.get("browser") or {}
    run = raw.get("run") or {}
    defaults = raw.get("defaults") or {}
    email = raw.get("email") or {}
    state = raw.get("state") or {}
    logging_cfg = raw.get("logging") or {}
    diag_raw = raw.get("diagnostics") or {}

    chats_list = chats_raw.get("chats")
    if not isinstance(chats_list, list):
        raise ValueError("chats.yaml: 'chats' must be a list")

    chat_entries: list[ChatEntry] = []
    for i, c in enumerate(chats_list):
        if not isinstance(c, dict):
            raise ValueError(f"chats.yaml: chats[{i}] must be a mapping")
        title = c.get("title")
        if not title or not str(title).strip():
            raise ValueError(f"chats.yaml: chats[{i}] needs non-empty 'title'")
        chat_entries.append(
            ChatEntry(
                title=str(title).strip(),
                enabled=bool(c.get("enabled", True)),
                include_keywords=c.get("include_keywords"),
                exclude_keywords=c.get("exclude_keywords"),
                require_url=c.get("require_url"),
            )
        )

    ud = browser.get("user_data_dir") or "~/.scoutsignal/browser-profile"
    sp = state.get("sqlite_path") or "~/.scoutsignal/state.db"
    extra_raw = browser.get("extra_chromium_args") or browser.get("chromium_args") or []
    if not isinstance(extra_raw, list):
        extra_raw = []
    extra_chromium_args = [str(x) for x in extra_raw if str(x).strip()]

    return AppConfig(
        browser=BrowserConfig(
            user_data_dir=_expand(str(ud)),
            headless=bool(browser.get("headless", False)),
            channel=browser.get("channel"),
            locale=str(browser.get("locale") or "en-US"),
            extra_chromium_args=extra_chromium_args,
        ),
        run=RunConfig(
            poll_interval_seconds=int(run.get("poll_interval_seconds", 300)),
            max_messages_per_chat=int(run.get("max_messages_per_chat", 120)),
            seed_on_first_scan=bool(run.get("seed_on_first_scan", True)),
            whatsapp_url=str(run.get("whatsapp_url", "https://web.whatsapp.com")),
            open_chat_timeout_seconds=max(
                15,
                min(3600, int(run.get("open_chat_timeout_seconds", 300))),
            ),
        ),
        defaults=DefaultsConfig(
            include_keywords=list(defaults.get("include_keywords") or []),
            exclude_keywords=list(defaults.get("exclude_keywords") or []),
            require_url=bool(defaults.get("require_url", False)),
            min_text_length=int(defaults.get("min_text_length", 15)),
        ),
        email=EmailConfig(
            enabled=bool(email.get("enabled", True)),
            smtp_host=str(email.get("smtp_host", "smtp.gmail.com")),
            smtp_port=int(email.get("smtp_port", 587)),
            use_tls=bool(email.get("use_tls", True)),
            from_addr=str(email.get("from_addr", "")),
            to_addrs=list(email.get("to_addrs") or []),
            subject_prefix=str(email.get("subject_prefix", "[ScoutSignal] ")),
            password_env=str(email.get("password_env", "SCOUTSIGNAL_SMTP_PASSWORD")),
            always_send_summary=bool(email.get("always_send_summary", False)),
        ),
        state=StateConfig(sqlite_path=_expand(str(sp))),
        logging=LoggingConfig(level=str(logging_cfg.get("level", "INFO"))),
        diagnostics=DiagnosticsConfig(
            error_screenshots=bool(diag_raw.get("error_screenshots", True)),
            screenshots_dir=_expand(str(diag_raw["screenshots_dir"]))
            if diag_raw.get("screenshots_dir")
            else None,
        ),
        chats=chat_entries,
        project_root=config_path.parent.resolve(),
    )


def screenshots_dir_for(cfg: AppConfig) -> Optional[Path]:
    if not cfg.diagnostics.error_screenshots:
        return None
    if cfg.diagnostics.screenshots_dir is not None:
        return cfg.diagnostics.screenshots_dir
    return cfg.state.sqlite_path.parent / "screenshots"


def validate_config(
    cfg: AppConfig,
    check_email_password: bool,
    *,
    require_chats: bool = True,
) -> list[str]:
    errors: list[str] = []
    if cfg.run.poll_interval_seconds < 30:
        errors.append("run.poll_interval_seconds should be >= 30 to avoid hammering WhatsApp Web.")
    if cfg.run.max_messages_per_chat < 1:
        errors.append("run.max_messages_per_chat must be >= 1.")

    if cfg.email.enabled:
        if not cfg.email.from_addr:
            errors.append("email.from_addr is required when email.enabled is true.")
        if not cfg.email.to_addrs:
            errors.append("email.to_addrs must be non-empty when email.enabled is true.")
        import os

        if check_email_password and not os.getenv(cfg.email.password_env):
            errors.append(
                f"Environment variable {cfg.email.password_env} is not set (required for SMTP)."
            )

    if require_chats:
        enabled = [c for c in cfg.chats if c.enabled]
        if not enabled:
            errors.append("No enabled chats in chats.yaml.")

        titles = [unicodedata.normalize("NFC", c.title.strip().lower()) for c in enabled]
        if len(titles) != len(set(titles)):
            errors.append("Duplicate chat titles among enabled chats — use unique substrings.")

    return errors
