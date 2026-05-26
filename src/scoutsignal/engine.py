from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scoutsignal.config_loader import AppConfig, screenshots_dir_for
from scoutsignal.matcher import (
    count_keyword_hits_in_messages,
    effective_include_keywords,
    first_http_url,
    match_message,
)
from scoutsignal.reporter import (
    ChatScanSummary,
    format_hit_lines,
    format_hit_lines_html,
    format_scan_report,
    format_scan_report_html,
    send_digest_email,
)
from scoutsignal.state import StateStore
from scoutsignal.whatsapp import (
    WhatsAppSession,
    is_qr_code_visible,
    open_chat_by_title,
    read_open_chat_title,
    save_error_screenshot,
    scrape_recent_messages,
    wait_for_whatsapp_ready,
)

log = logging.getLogger(__name__)


def _scan_exc_email_prefix(scan_exc: Optional[BaseException]) -> str:
    if scan_exc is None:
        return ""
    if isinstance(scan_exc, PlaywrightTimeoutError):
        return (
            "Important: WhatsApp Web hit a browser timeout (slow connection, heavy UI, or a dialog "
            "blocking the page). The report below may be incomplete. Close any pop-up on WhatsApp Web "
            "and run ScoutSignal again.\n\n"
        )
    return (
        f"Important: The scan stopped early ({type(scan_exc).__name__}). "
        "Partial results follow; check logs or re-run after fixing the issue.\n\n"
    )


def _hit_subject(n: int) -> str:
    if n == 0:
        return "ScoutSignal report — no new listings"
    if n == 1:
        return "ScoutSignal report — 1 new listing"
    return f"ScoutSignal report — {n} new listings"


def _emit_scan_email(
    cfg: AppConfig,
    *,
    dry_run: bool,
    hits: list[Hit],
    summaries: list[ChatScanSummary],
    scan_exc: Optional[BaseException],
) -> None:
    """Send or log the post-scan email (also used from `finally` on partial failure)."""
    job_tuples = [(h.chat_title, h.preview, h.link) for h in hits]
    if not cfg.email.enabled:
        if not hits:
            log.info("No new matches.")
        else:
            log.info("Email disabled; %s hits not mailed.", len(hits))
        return

    prefix = _scan_exc_email_prefix(scan_exc)

    if dry_run:
        if cfg.email.always_send_summary:
            log.info(
                "Dry-run: would send summary email:\n%s%s",
                prefix,
                format_scan_report(
                    list(cfg.defaults.include_keywords),
                    summaries,
                    job_tuples,
                    keyword_watch=cfg.defaults.keyword_watch or None,
                ),
            )
        elif hits:
            log.info(
                "Dry-run: would send %s hits:\n%s",
                len(hits),
                format_hit_lines(job_tuples),
            )
        else:
            log.info("No new matches.")
        return

    if cfg.email.always_send_summary:
        body = prefix + format_scan_report(
            list(cfg.defaults.include_keywords),
            summaries,
            job_tuples,
            keyword_watch=cfg.defaults.keyword_watch or None,
        )
        body_html = format_scan_report_html(
            list(cfg.defaults.include_keywords),
            summaries,
            job_tuples,
            notice_plain=prefix,
            keyword_watch=cfg.defaults.keyword_watch or None,
        )
        send_digest_email(
            cfg.email,
            subject=_hit_subject(len(hits)),
            body_text=body,
            body_html=body_html,
        )
        log.info("Sent summary email (%s job hits).", len(hits))
    elif hits:
        body = prefix + format_hit_lines(job_tuples)
        body_html = format_hit_lines_html(job_tuples, notice_plain=prefix)
        send_digest_email(
            cfg.email,
            subject=_hit_subject(len(hits)),
            body_text=body,
            body_html=body_html,
        )
        log.info("Sent email with %s hits.", len(hits))
    else:
        log.info("No new matches (email.always_send_summary is false; skipping email).")


def _chat_key(title: str) -> str:
    t = unicodedata.normalize("NFC", title.strip())
    s = re.sub(r"\s+", " ", t.lower())
    s = re.sub(r"[^\w\- ]+", "", s)
    return s[:120] if s else "unknown"


@dataclass
class Hit:
    chat_title: str
    preview: str
    link: str


def run_scan(cfg: AppConfig, *, dry_run: bool) -> list[Hit]:
    store = StateStore(cfg.state.sqlite_path)
    store.prune_old()
    hits: list[Hit] = []
    summaries: list[ChatScanSummary] = []
    shot_dir = screenshots_dir_for(cfg)
    page_ref: list = [None]
    scan_exc: Optional[BaseException] = None

    try:
        with WhatsAppSession(cfg.browser) as session:
            page = session.get_or_open_page()
            page_ref[0] = page
            page.goto(cfg.run.whatsapp_url, wait_until="domcontentloaded")
            time.sleep(3)

            if is_qr_code_visible(page):
                log.error("WhatsApp session expired — QR code login screen detected.")
                if cfg.email.enabled and not dry_run:
                    send_digest_email(
                        cfg.email,
                        subject="WhatsApp session expired — action required",
                        body_text=(
                            "ScoutSignal detected that the WhatsApp Web session has expired.\n\n"
                            "Please open the browser profile and re-scan the QR code to restore the session.\n"
                            "The current scan has been aborted."
                        ),
                    )
                return hits

            wait_for_whatsapp_ready(page)
            time.sleep(2)

            try:
                for chat in cfg.chats:
                    if not chat.enabled:
                        continue
                    ck = _chat_key(chat.title)
                    seeded = store.is_seeded(ck)
                    zero_kw = {k: 0 for k in effective_include_keywords(cfg.defaults, chat)}

                    try:
                        open_deadline = time.monotonic() + float(cfg.run.open_chat_timeout_seconds)
                        if not open_chat_by_title(page, chat.title, open_deadline=open_deadline, screenshots_dir=shot_dir):
                            log.warning("Skipping chat (could not open): %s", chat.title)
                            summaries.append(
                                ChatScanSummary(
                                    chat.title,
                                    0,
                                    0,
                                    dict(zero_kw),
                                    "Could not open chat (search / WhatsApp UI).",
                                )
                            )
                            continue

                        messages = scrape_recent_messages(page, cfg.run.max_messages_per_chat)
                        texts = [m.text for m in messages]
                        log.info("Chat %r: scraped %s messages", chat.title, len(messages))
                        kcounts = count_keyword_hits_in_messages(texts, chat, cfg.defaults)

                        if cfg.run.seed_on_first_scan and not seeded:
                            for m in messages:
                                mr = match_message(ck, m.text, cfg.defaults, chat)
                                store.add_fingerprint(ck, mr.fingerprint)
                            store.mark_seeded(ck)
                            log.info("Seeded chat %r — no alerts on first scan.", chat.title)
                            summaries.append(ChatScanSummary(chat.title, len(messages), 0, kcounts, ""))
                            continue

                        chat_new_job_hits = 0
                        for m in messages:
                            mr = match_message(ck, m.text, cfg.defaults, chat)
                            if store.has_fingerprint(ck, mr.fingerprint):
                                continue
                            store.add_fingerprint(ck, mr.fingerprint)
                            if mr.matched:
                                link = first_http_url(m.text) or ""
                                preview = m.text.strip()
                                if len(preview) > 1200:
                                    preview = preview[:1200] + "…"
                                hits.append(Hit(chat_title=chat.title, preview=preview, link=link))
                                chat_new_job_hits += 1
                                log.info("Hit in %s", chat.title)

                        summaries.append(
                            ChatScanSummary(
                                chat.title,
                                len(messages),
                                chat_new_job_hits,
                                kcounts,
                                "",
                            )
                        )
                    except PlaywrightTimeoutError:
                        log.warning("Playwright timeout in chat %r — skipping.", chat.title)
                        summaries.append(
                            ChatScanSummary(
                                chat.title,
                                0,
                                0,
                                dict(zero_kw),
                                "WhatsApp UI timed out (slow network or a popup blocking clicks).",
                            )
                        )
            except Exception:
                if shot_dir:
                    save_error_screenshot(page_ref[0], shot_dir, prefix="scan-error")
                raise
    except BaseException as exc:
        scan_exc = exc
        log.exception("Scan failed.")
    finally:
        _emit_scan_email(cfg, dry_run=dry_run, hits=hits, summaries=summaries, scan_exc=scan_exc)

    if scan_exc is not None:
        raise scan_exc

    return hits


def run_probe(cfg: AppConfig) -> int:
    """
    Open WhatsApp Web and print the title of the currently open chat (copy into chats.yaml).
    """
    page_ref: list = [None]
    shot_dir = screenshots_dir_for(cfg)
    try:
        with WhatsAppSession(cfg.browser) as session:
            page = session.get_or_open_page()
            page_ref[0] = page
            page.goto(cfg.run.whatsapp_url, wait_until="domcontentloaded")
            wait_for_whatsapp_ready(page)
            title = read_open_chat_title(page)
            if title:
                print(title)
                return 0
            log.warning("Could not read chat title from header — open a conversation, then retry.")
            return 1
    except Exception:
        log.exception("Probe failed.")
        if shot_dir:
            save_error_screenshot(page_ref[0], shot_dir, prefix="probe-error")
        raise
