from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable

from scoutsignal.config_loader import EmailConfig


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

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{cfg.subject_prefix}{subject}"
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as server:
        if cfg.use_tls:
            server.starttls()
        server.login(cfg.from_addr, password)
        server.sendmail(cfg.from_addr, cfg.to_addrs, msg.as_string())


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
