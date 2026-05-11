from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from scoutsignal.config_loader import AppConfig, screenshots_dir_for
from scoutsignal.matcher import first_http_url, match_message
from scoutsignal.reporter import format_hit_lines, send_digest_email
from scoutsignal.state import StateStore
from scoutsignal.whatsapp import (
    WhatsAppSession,
    open_chat_by_title,
    read_open_chat_title,
    save_error_screenshot,
    scrape_recent_messages,
    wait_for_whatsapp_ready,
)

log = logging.getLogger(__name__)


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
    hits: list[Hit] = []
    shot_dir = screenshots_dir_for(cfg)
    page_ref: list = [None]

    try:
        with WhatsAppSession(cfg.browser) as session:
            page = session.get_or_open_page()
            page_ref[0] = page
            page.goto(cfg.run.whatsapp_url, wait_until="domcontentloaded")
            wait_for_whatsapp_ready(page)

            for chat in cfg.chats:
                if not chat.enabled:
                    continue
                ck = _chat_key(chat.title)
                seeded = store.is_seeded(ck)

                if not open_chat_by_title(page, chat.title):
                    log.warning("Skipping chat (could not open): %s", chat.title)
                    continue

                messages = scrape_recent_messages(page, cfg.run.max_messages_per_chat)
                log.info("Chat %r: scraped %s messages", chat.title, len(messages))

                if cfg.run.seed_on_first_scan and not seeded:
                    for m in messages:
                        mr = match_message(ck, m.text, cfg.defaults, chat)
                        store.add_fingerprint(ck, mr.fingerprint)
                    store.mark_seeded(ck)
                    log.info("Seeded chat %r — no alerts on first scan.", chat.title)
                    continue

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
                        log.info("Hit in %s", chat.title)
    except Exception:
        log.exception("Scan failed.")
        if shot_dir:
            save_error_screenshot(page_ref[0], shot_dir, prefix="scan-error")
        raise

    if hits and cfg.email.enabled and not dry_run:
        body = format_hit_lines((h.chat_title, h.preview, h.link) for h in hits)
        send_digest_email(cfg.email, subject=f"{len(hits)} new match(es)", body_text=body)
        log.info("Sent email with %s hits.", len(hits))
    elif hits and dry_run:
        log.info("Dry-run: would send %s hits:\n%s", len(hits), format_hit_lines((h.chat_title, h.preview, h.link) for h in hits))
    elif not hits:
        log.info("No new matches.")

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
