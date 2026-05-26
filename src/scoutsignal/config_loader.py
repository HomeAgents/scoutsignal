from __future__ import annotations

import copy
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

import yaml
from dotenv import load_dotenv


@dataclass
class KeywordWatchRow:
    """One email row: position (role label), language code, phrases to match."""

    position: str
    language: str
    combinations: list[str]


def _keyword_phrase_key(phrase: str) -> str:
    """Same normalization as matcher substring checks (NFC + lower + collapse spaces)."""
    s = " ".join((phrase or "").split()).strip().lower()
    return unicodedata.normalize("NFC", s)


def parse_keyword_watch(raw: Any) -> Tuple[list[KeywordWatchRow], list[str]]:
    """
    Parse defaults.keyword_watch from YAML into rows + flattened unique phrases (match order).
    Each item: { position, language, combinations: [str, ...] }

    Multiple YAML entries with the same position (case-insensitive) and language are merged
    into one row (combinations concatenated, de-duplicated within the row).
    Phrases that differ only by case/spacing are de-duplicated (first spelling kept).
    """
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        raise ValueError("defaults.keyword_watch must be a list (or omit it)")

    buckets: "OrderedDict[tuple[str, str], dict[str, Any]]" = OrderedDict()
    flat: list[str] = []
    seen_norm_global: set[str] = set()

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"defaults.keyword_watch[{i}] must be a mapping")
        pos = str(item.get("position") or "").strip()
        lang = str(item.get("language") or "").strip().upper()
        combos = item.get("combinations")
        if combos is None:
            combos = item.get("phrases")
        if not isinstance(combos, list):
            raise ValueError(f"defaults.keyword_watch[{i}]: 'combinations' must be a list")
        phrases = [str(p).strip() for p in combos if str(p).strip()]
        if not pos:
            raise ValueError(f"defaults.keyword_watch[{i}]: non-empty 'position' is required")
        if not lang:
            raise ValueError(f"defaults.keyword_watch[{i}]: non-empty 'language' is required (e.g. EN or HE)")
        key = (pos.lower(), lang)
        if key not in buckets:
            buckets[key] = {"position": pos, "phrases": [], "seen_norm": set()}
        bucket = buckets[key]
        bucket_phrases: list[str] = bucket["phrases"]
        seen_in_bucket: set[str] = bucket["seen_norm"]
        for p in phrases:
            pk = _keyword_phrase_key(p)
            if pk in seen_in_bucket:
                continue
            seen_in_bucket.add(pk)
            bucket_phrases.append(p)
            if pk not in seen_norm_global:
                seen_norm_global.add(pk)
                flat.append(p)

    rows = [
        KeywordWatchRow(position=str(v["position"]), language=str(k[1]), combinations=list(v["phrases"]))
        for k, v in buckets.items()
    ]
    return rows, flat


def merge_include_keywords(watch_flat: list[str], yaml_list: list[str]) -> list[str]:
    """Phrases from keyword_watch first, then any extra include_keywords, de-duplicated by normalized text."""
    out: list[str] = []
    seen_norm: set[str] = set()
    for p in watch_flat + yaml_list:
        p = str(p).strip()
        if not p:
            continue
        pk = _keyword_phrase_key(p)
        if pk in seen_norm:
            continue
        seen_norm.add(pk)
        out.append(p)
    return out


# WhatsApp Web rejects Playwright's default headless UA with an "update Chrome" gate page.
DEFAULT_HEADLESS_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class BrowserConfig:
    user_data_dir: Path
    headless: bool = False
    channel: Optional[str] = None
    # BCP-47 locale (e.g. he-IL for Hebrew WhatsApp UI, en-US default).
    locale: str = "en-US"
    # Override Chromium user agent (required for headless WhatsApp Web on Linux).
    user_agent: Optional[str] = None
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
    # Optional matrix for email + maintenance; flattened into include_keywords at load time.
    keyword_watch: list[KeywordWatchRow] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    require_url: bool = False
    min_text_length: int = 15


@dataclass
class EmailConfig:
    enabled: bool = True
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    use_tls: bool = True
    # Mailbox used for SMTP auth. May be `addr@domain` or `Display Name <addr@domain>`.
    from_addr: str = ""
    # Shown in clients as "From: … <addr>"; SMTP auth uses from_addr. Replies use reply_to_addr when no_reply.
    from_display_name: str = "(Do Not Reply) ScoutSignal"
    # When true (default), Reply-To is a non-monitored address so replies are not delivered.
    no_reply: bool = True
    # Optional override for Reply-To when no_reply is true (default: no-reply@do-not-reply.invalid).
    reply_to_addr: Optional[str] = None
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


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*.

    - Dict values are merged recursively.
    - All other types (lists, scalars) in *override* replace the base value.
    """
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


class _IncludeLoader(yaml.SafeLoader):
    """SafeLoader subclass that supports ``!include path/to/file.yaml``."""


def _include_constructor(loader: _IncludeLoader, node: yaml.Node) -> Any:
    """Load another YAML file relative to the including file."""
    include_path = Path(loader.name).parent / loader.construct_scalar(node)
    resolved = include_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"!include target not found: {resolved}")
    with resolved.open(encoding="utf-8") as f:
        return yaml.load(f, _IncludeLoader)  # noqa: S506 – uses our safe subclass


_IncludeLoader.add_constructor("!include", _include_constructor)


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, resolving ``!include`` tags and ``extends`` base files.

    When the top-level mapping contains an ``extends`` key, the referenced file
    is loaded first and the current file's values are deep-merged on top. This
    allows derivative configs (e.g. ``config.vm.yaml``) to share rules with a
    base file while overriding only what differs.
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.load(f, _IncludeLoader)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")

    extends_ref = data.pop("extends", None)
    if extends_ref is not None:
        base_path = (path.parent / str(extends_ref)).resolve()
        base_data = load_yaml(base_path)
        data = _deep_merge(base_data, data)

    return data


def _load_from_display_name(email: dict) -> str:
    if "from_display_name" not in email:
        return "(Do Not Reply) ScoutSignal"
    v = email["from_display_name"]
    if v is None:
        return "(Do Not Reply) ScoutSignal"
    return str(v).strip()


NO_REPLY_ADDRESS = "no-reply@do-not-reply.invalid"


def smtp_mailbox_for_from_addr(from_addr: str) -> str:
    """Mailbox for SMTP login / envelope, from bare email or `Name <email>`."""
    from email.utils import parseaddr

    _, addr = parseaddr((from_addr or "").strip())
    addr = addr.strip()
    if addr and "@" in addr:
        return addr
    raw = (from_addr or "").strip()
    return raw if "@" in raw else ""


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

    watch_rows, watch_flat = parse_keyword_watch(defaults.get("keyword_watch"))
    inc_yaml = [str(x).strip() for x in (defaults.get("include_keywords") or []) if str(x).strip()]
    merged_keywords = merge_include_keywords(watch_flat, inc_yaml)

    headless = bool(browser.get("headless", False))
    ua_raw = browser.get("user_agent")
    user_agent = str(ua_raw).strip() if ua_raw else None
    if headless and not user_agent:
        user_agent = DEFAULT_HEADLESS_USER_AGENT

    return AppConfig(
        browser=BrowserConfig(
            user_data_dir=_expand(str(ud)),
            headless=headless,
            channel=browser.get("channel"),
            locale=str(browser.get("locale") or "en-US"),
            user_agent=user_agent,
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
            include_keywords=merged_keywords,
            keyword_watch=watch_rows,
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
            from_display_name=_load_from_display_name(email),
            no_reply=bool(email.get("no_reply", True)),
            reply_to_addr=(
                str(email["reply_to_addr"]).strip()
                if email.get("reply_to_addr")
                else None
            ),
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
        mbox = smtp_mailbox_for_from_addr(cfg.email.from_addr)
        if not mbox or "@" not in mbox:
            errors.append(
                "email.from_addr must contain a mailbox address "
                "(e.g. you@gmail.com or ScoutSignal <you@gmail.com>)."
            )
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
