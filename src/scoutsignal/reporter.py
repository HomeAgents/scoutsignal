from __future__ import annotations

import html
import os
import smtplib
from datetime import datetime
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable, List, Optional


from email.utils import formataddr, parseaddr

from scoutsignal.config_loader import EmailConfig, KeywordWatchRow, NO_REPLY_ADDRESS, smtp_mailbox_for_from_addr


def _reply_to_header(cfg: EmailConfig, mailbox: str) -> str:
    if cfg.no_reply:
        return (cfg.reply_to_addr or NO_REPLY_ADDRESS).strip()
    if cfg.reply_to_addr and cfg.reply_to_addr.strip():
        return cfg.reply_to_addr.strip()
    return mailbox


def _from_header_and_mailbox(cfg: EmailConfig) -> tuple[str, str]:
    """
    Build RFC 5322 From header (display name + mailbox) and the SMTP login mailbox.
    SMTP auth uses the real mailbox; Reply-To is set separately (no-reply by default).
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
    body_html: Optional[str] = None,
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
    msg["Reply-To"] = _reply_to_header(cfg, mailbox)
    msg["Auto-Submitted"] = "auto-generated"
    msg["Precedence"] = "bulk"
    msg["X-Auto-Response-Suppress"] = "All"
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as server:
        if cfg.use_tls:
            server.starttls()
        server.login(mailbox, password)
        server.sendmail(mailbox, cfg.to_addrs, msg.as_string())


def _cell_plain(s: str, max_len: int = 56) -> str:
    """Single cell for Markdown-style pipe tables (plain text)."""
    t = " ".join(s.replace("|", "/").split())
    if len(t) > max_len:
        return t[: max_len - 1] + "…"
    return t


def _markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    line_h = "| " + " | ".join(_cell_plain(h, max_len=120) for h in headers) + " |"
    line_sep = "| " + " | ".join("---" for _ in headers) + " |"
    line_rows = ["| " + " | ".join(_cell_plain(c) for c in r) + " |" for r in rows]
    return "\n".join([line_h, line_sep] + line_rows)


def _html_table(headers: List[str], rows: List[List[str]], *, caption: str = "") -> str:
    cap = ""
    if caption.strip():
        cap = (
            f'<caption style="text-align:left;font-weight:600;padding:0 0 10px 0">'
            f"{html.escape(caption.strip())}</caption>"
        )
    ths = "".join(
        "<th style=\"text-align:left;border:1px solid #d1d5db;padding:8px 10px;"
        "background:#f9fafb;font-size:13px\">"
        f"{html.escape(h)}</th>"
        for h in headers
    )
    body_rows: list[str] = []
    for r in rows:
        tds = "".join(
            "<td style=\"border:1px solid #d1d5db;padding:8px 10px;vertical-align:top;font-size:13px\">"
            f"{html.escape(c)}</td>"
            for c in r
        )
        body_rows.append(f"<tr>{tds}</tr>")
    return (
        f'<table role="presentation" style="border-collapse:collapse;width:100%;max-width:920px;'
        f'margin:0 0 18px 0">{cap}<thead><tr>{ths}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'
    )


def _email_html_shell(inner: str) -> str:
    style = (
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        "line-height:1.5;color:#111827;margin:16px;}"
        "h1{font-size:20px;margin:0 0 12px 0;font-weight:600;}"
        "h2{font-size:15px;margin:22px 0 10px 0;padding-bottom:6px;border-bottom:1px solid #e5e7eb;"
        "font-weight:600;color:#374151;}"
        "p{margin:8px 0;font-size:14px;}"
        ".muted{color:#6b7280;font-size:13px;}"
    )
    return (
        f"<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<style>{style}</style></head><body>{inner}</body></html>"
    )


def _scan_notice_block_html(plain_notice: str) -> str:
    if not plain_notice.strip():
        return ""
    return (
        '<div style="margin:0 0 18px 0;padding:12px 14px;background:#fffbeb;'
        'border-left:4px solid #f59e0b;border-radius:6px;color:#78350f;'
        'font-size:14px;white-space:pre-wrap">'
        f"{html.escape(plain_notice.strip())}"
        "</div>"
    )


def _combinations_plain_cell(phrases: List[str], max_len: int = 360) -> str:
    if not phrases:
        return "—"
    joined = "; ".join(phrases)
    return _cell_plain(joined, max_len=max_len)


def _keyword_watch_markdown_table(rows: List[KeywordWatchRow]) -> str:
    data = [[r.position, r.language, _combinations_plain_cell(r.combinations)] for r in rows]
    return _markdown_table(["Position", "Language", "Combinations"], data)


def _keyword_watch_html_table(rows: List[KeywordWatchRow], *, caption: str) -> str:
    cap = (
        f'<caption style="text-align:left;font-weight:600;padding:0 0 10px 0">'
        f"{html.escape(caption.strip())}</caption>"
    )
    th_pos = (
        '<th style="text-align:left;border:1px solid #d1d5db;padding:8px 10px;'
        'background:#f9fafb;font-size:13px;width:22%">Position</th>'
    )
    th_lang = (
        '<th style="text-align:left;border:1px solid #d1d5db;padding:8px 10px;'
        'background:#f9fafb;font-size:13px;width:8%">Language</th>'
    )
    th_combo = (
        '<th style="text-align:left;border:1px solid #d1d5db;padding:8px 10px;'
        'background:#f9fafb;font-size:13px">Combinations</th>'
    )
    body_rows: list[str] = []
    for r in rows:
        if r.combinations:
            lis = "".join(f"<li>{html.escape(p)}</li>" for p in r.combinations)
            combo_cell = f'<ul style="margin:0;padding-left:18px;font-size:13px">{lis}</ul>'
        else:
            combo_cell = "—"
        body_rows.append(
            "<tr>"
            f'<td style="border:1px solid #d1d5db;padding:8px 10px;vertical-align:top;font-size:13px">'
            f"{html.escape(r.position)}</td>"
            f'<td style="border:1px solid #d1d5db;padding:8px 10px;vertical-align:top;font-size:13px">'
            f"{html.escape(r.language)}</td>"
            f'<td style="border:1px solid #d1d5db;padding:8px 10px;vertical-align:top">{combo_cell}</td>'
            "</tr>"
        )
    return (
        f'<table role="presentation" style="border-collapse:collapse;width:100%;max-width:920px;'
        f'margin:0 0 18px 0">{cap}<thead><tr>{th_pos}{th_lang}{th_combo}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table>'
    )


def format_hit_lines(
    items: Iterable[tuple[str, str, str]],
) -> str:
    """(chat_title, message_preview, link_or_empty)"""
    lines: list[str] = []
    for i, (chat, preview, link) in enumerate(items, start=1):
        lines.append(f"Match {i}")
        lines.append("")
        lines.append(f"Source chat: {chat}")
        if link.strip():
            lines.append(f"Link: {link.strip()}")
        lines.append("")
        lines.append("Message preview:")
        lines.append(preview.strip())
        lines.append("")
        lines.append("—" * 48)
    return "\n".join(lines).rstrip()


def _hits_section_html(job_hits: List[tuple[str, str, str]]) -> str:
    blocks: list[str] = []
    for i, (chat, preview, link) in enumerate(job_hits, start=1):
        href = link.strip()
        link_html = ""
        if href:
            safe_href = html.escape(href, quote=True)
            link_html = (
                f'<p style="margin:6px 0"><a href="{safe_href}" style="color:#2563eb">Open link</a> '
                f'<span class="muted">({html.escape(href[:80] + ("…" if len(href) > 80 else ""))})</span></p>'
            )
        blocks.append(
            '<div style="margin:14px 0;padding:14px;border:1px solid #e5e7eb;border-radius:8px;'
            'background:#fafafa">'
            f'<p style="margin:0 0 8px 0;font-weight:600;font-size:15px">Match {i}</p>'
            f'<p style="margin:4px 0;font-size:14px"><strong>Source chat:</strong> {html.escape(chat)}</p>'
            f"{link_html}"
            '<p style="margin:10px 0 4px 0;font-size:13px;font-weight:600">Message preview</p>'
            '<pre style="white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
            'font-size:12px;margin:0;padding:10px;background:#fff;border:1px solid #e5e7eb;'
            f'border-radius:6px">{html.escape(preview.strip())}</pre>'
            "</div>"
        )
    return "".join(blocks)


def format_hit_lines_html(
    items: Iterable[tuple[str, str, str]],
    *,
    notice_plain: str = "",
) -> str:
    """HTML version of hit-only digest (multipart alternative)."""
    parts = _scan_notice_block_html(notice_plain) + _hits_section_html(list(items))
    return _email_html_shell(parts)


def format_scan_report(
    default_include_keywords: List[str],
    chat_rows: List[ChatScanSummary],
    job_hits: List[tuple[str, str, str]],
    *,
    keyword_watch: Optional[List[KeywordWatchRow]] = None,
) -> str:
    """
    Full email body: short overview, watched keywords, per-chat status, then new matches.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_chats = len(chat_rows)
    n_new = len(job_hits)
    total_scraped = sum(ch.scraped_messages for ch in chat_rows)
    n_issues = sum(1 for ch in chat_rows if ch.error)

    lines: list[str] = []
    lines.append("ScoutSignal — job scan report")
    lines.append("")
    lines.append(f"Report generated: {now} (this device's local time)")
    lines.append("")
    lines.append("Overview")
    lines.append("--------")
    lines.append(
        f"Chats checked: {n_chats} · "
        f"Recent messages reviewed (approx.): {total_scraped} · "
        f"New alerts this run: {n_new}"
    )
    if n_issues:
        issue_phrase = "1 chat had a problem" if n_issues == 1 else f"{n_issues} chats had a problem"
        lines.append(
            f"Note: {issue_phrase} (see below). "
            "Fix WhatsApp Web if needed, then run again."
        )
    else:
        lines.append("All configured chats completed without errors.")
    lines.append("")

    lines.append("What we watch for")
    lines.append("-----------------")
    lines.append(
        "A post is alerted when it matches your filters in config.yaml "
        "(for example keyword rules and, if enabled, a link in the message)."
    )
    lines.append("")
    if keyword_watch:
        lines.append("Watched roles (each phrase under Combinations can trigger a match when rules pass).")
        lines.append("")
        lines.append(_keyword_watch_markdown_table(keyword_watch))
    elif default_include_keywords:
        lines.append("Include keywords (any of these phrases can contribute to a match)")
        lines.append("")
        kw_rows = [[str(i), kw] for i, kw in enumerate(default_include_keywords, start=1)]
        lines.append(_markdown_table(["#", "Keyword phrase"], kw_rows))
    else:
        lines.append("Include keywords")
        lines.append("")
        lines.append(_markdown_table(["#", "Keyword phrase"], [["—", "None configured (other filters only)"]]))
    lines.append("")
    lines.append("(In HTML-capable mail clients, use the HTML / rich-text view for best table layout.)")
    lines.append("")

    lines.append("Results by chat")
    lines.append("---------------")
    chat_table_rows: list[list[str]] = []
    for ch in chat_rows:
        status = "Completed" if not ch.error else "Issue"
        notes = ch.error if ch.error else "—"
        chat_table_rows.append(
            [
                _cell_plain(ch.chat_title, max_len=44),
                status,
                str(ch.scraped_messages),
                str(ch.new_job_matches),
                _cell_plain(notes, max_len=64),
            ]
        )
    lines.append(
        _markdown_table(
            ["Chat", "Status", "Reviewed", "New alerts", "Notes"],
            chat_table_rows,
        )
    )

    lines.append("")
    lines.append("New matches")
    lines.append("-----------")
    if job_hits:
        lines.append(
            "Below are the new listings detected in this run. "
            "Open the link (if shown) or search the preview text in the source chat."
        )
        lines.append("")
        lines.append(format_hit_lines(job_hits))
    else:
        lines.append("No new listings matched your rules in this run.")
        lines.append("")
        lines.append(
            "Tip: If you expected something, widen keywords slightly, "
            "or confirm the chat title in chats.yaml matches WhatsApp."
        )

    return "\n".join(lines).strip()


def format_scan_report_html(
    default_include_keywords: List[str],
    chat_rows: List[ChatScanSummary],
    job_hits: List[tuple[str, str, str]],
    *,
    notice_plain: str = "",
    keyword_watch: Optional[List[KeywordWatchRow]] = None,
) -> str:
    """HTML companion body for multipart/alternative summary emails."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_chats = len(chat_rows)
    n_new = len(job_hits)
    total_scraped = sum(ch.scraped_messages for ch in chat_rows)
    n_issues = sum(1 for ch in chat_rows if ch.error)

    parts: list[str] = []
    parts.append(_scan_notice_block_html(notice_plain))
    parts.append("<h1>ScoutSignal — job scan report</h1>")
    parts.append(f'<p class="muted">Report generated: {html.escape(now)} (this device\'s local time)</p>')

    parts.append("<h2>Overview</h2>")
    overview_bits = [
        f"<strong>Chats checked:</strong> {n_chats}",
        f"<strong>Messages reviewed (approx.):</strong> {total_scraped}",
        f"<strong>New alerts this run:</strong> {n_new}",
    ]
    parts.append("<p>" + " · ".join(overview_bits) + "</p>")
    if n_issues:
        issue_phrase = "1 chat had a problem" if n_issues == 1 else f"{n_issues} chats had a problem"
        parts.append(
            f'<p style="color:#92400e"><strong>Note:</strong> {html.escape(issue_phrase)}. '
            "Fix WhatsApp Web if needed, then run again.</p>"
        )
    else:
        parts.append('<p style="color:#065f46"><strong>All chats completed</strong> without errors.</p>')

    parts.append("<h2>What we watch for</h2>")
    parts.append(
        "<p>A post is alerted when it matches your filters in <code>config.yaml</code> "
        "(for example keyword rules and, if enabled, a link in the message).</p>"
    )
    if keyword_watch:
        parts.append(
            _keyword_watch_html_table(
                keyword_watch,
                caption="Watched roles (Position · Language · Combinations)",
            )
        )
    elif default_include_keywords:
        kw_rows = [[str(i), kw] for i, kw in enumerate(default_include_keywords, start=1)]
        parts.append(
            _html_table(
                ["#", "Keyword phrase"],
                kw_rows,
                caption="Include keywords (any phrase can contribute to a match)",
            )
        )
    else:
        parts.append(
            _html_table(
                ["#", "Keyword phrase"],
                [["—", "None configured (other filters only)"]],
                caption="Include keywords",
            )
        )

    chat_html_rows: list[list[str]] = []
    for ch in chat_rows:
        status = "Completed" if not ch.error else "Issue"
        notes = ch.error if ch.error else "—"
        chat_html_rows.append(
            [
                ch.chat_title,
                status,
                str(ch.scraped_messages),
                str(ch.new_job_matches),
                notes,
            ]
        )
    parts.append(
        _html_table(
            ["Chat", "Status", "Reviewed", "New alerts", "Notes"],
            chat_html_rows,
            caption="Results by chat",
        )
    )

    parts.append("<h2>New matches</h2>")
    if job_hits:
        parts.append(
            "<p>Below are the new listings detected in this run. Use <em>Open link</em> or search the preview "
            "in the source chat.</p>"
        )
        parts.append(_hits_section_html(job_hits))
    else:
        parts.append("<p>No new listings matched your rules in this run.</p>")
        parts.append(
            "<p class=\"muted\">Tip: If you expected a hit, widen keywords slightly or confirm each "
            "<code>chats.yaml</code> title matches WhatsApp.</p>"
        )

    return _email_html_shell("".join(parts))
