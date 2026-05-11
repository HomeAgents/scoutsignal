from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable, List


from email.utils import formataddr, parseaddr

from scoutsignal.config_loader import EmailConfig, smtp_mailbox_for_from_addr


def _from_header_and_mailbox(cfg: EmailConfig) -> tuple[str, str]:
    """
    Build RFC 5322 From header (display name + mailbox) and the SMTP login mailbox.
    Replies go to the mailbox address.
    """
    raw = (cfg.from_addr or "").strip()
    parsed_name, _parsed_addr = parseaddr(raw)
    mailbox = smtp_mailbox_for_from_addr(raw)
    if not mailbox:
        raise RuntimeError("email.from_addr must include a mailbox address (e.g. you@gmail.com).")

    display = (parsed_name or "").strip()
    if not display and (cfg.from_display_name or "").strip():
        display = cfg.from_display_name.strip()

    if display:
        return formataddr((display, mailbox)), mailbox
    return mailbox, mailbox


@dataclass
class ChatScanSummary:
    """One row for the post-scan email report."""

    chat_title: str
    scraped_messages: int
    new_job_matches: int
    keyword_hits: dict[str, int]
    error: str = ""


def send_digest_email(
    cfg: EmailConfig,
    subject: str,
    body_text: str,
) -> None:
    if not cfg.enabled:
        return
    password = os.getenv(cfg.password_env, "")
    if not password:
        raise RuntimeError(f"Missing env {cfg.password_env} for SMTP password")

    from_header, mailbox = _from_header_and_mailbox(cfg)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{cfg.subject_prefix}{subject}"
    msg["From"] = from_header
    msg["Reply-To"] = mailbox
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as server:
        if cfg.use_tls:
            server.starttls()
        server.login(mailbox, password)
        server.sendmail(mailbox, cfg.to_addrs, msg.as_string())


def format_hit_lines(
    items: Iterable[tuple[str, str, str]],
) -> str:
    """(chat_title, message_preview, link_or_empty)"""
    lines: list[str] = []
    for chat, preview, link in items:
        lines.append(f"Chat: {chat}")
        lines.append(preview.strip())
        if link:
            lines.append(f"Link: {link}")
        lines.append("---")
    return "\n".join(lines).strip()


def format_scan_report(
    default_include_keywords: List[str],
    chat_rows: List[ChatScanSummary],
    job_hits: List[tuple[str, str, str]],
) -> str:
    """
    Full email body: default keywords, per-chat scraped counts + per-keyword substring hits,
    then job-match previews (if any).
    """
    lines: list[str] = []
    lines.append("ScoutSignal — scan summary")
    lines.append("")
    lines.append("Default include keywords (config.yaml `defaults.include_keywords`):")
    if default_include_keywords:
        for kw in default_include_keywords:
            lines.append(f"  - {kw}")
    else:
        lines.append("  (empty — any message passing filters counts as a job match)")
    lines.append("")
    lines.append("Per chat")
    lines.append("--------")
    for ch in chat_rows:
        lines.append(f"Chat: {ch.chat_title}")
        if ch.keyword_hits:
            lines.append("  Keywords (substring hits among qualifying scraped lines):")
            for kw, c in sorted(ch.keyword_hits.items(), key=lambda x: (-x[1], x[0])):
                lines.append(f"    - {kw}: {c}")
        else:
            lines.append("  Keywords: (no include keywords for this chat)")

        if ch.error:
            lines.append(
                f"  Result: {ch.scraped_messages} messages scraped · "
                f"{ch.new_job_matches} new job match(es) · {ch.error}"
            )
        else:
            lines.append(
                f"  Result: {ch.scraped_messages} messages scraped · "
                f"{ch.new_job_matches} new job match(es) · OK"
            )
        lines.append("")
    if job_hits:
        lines.append("New job matches (detail)")
        lines.append("-------------------------")
        lines.append(format_hit_lines(job_hits))
    else:
        lines.append("New job matches (detail): none this run.")
    return "\n".join(lines).strip()
