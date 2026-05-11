from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Locator, Page, Playwright, sync_playwright

from scoutsignal.config_loader import BrowserConfig

log = logging.getLogger(__name__)


@dataclass
class ScrapedMessage:
    text: str


def _launch_context(p: Playwright, cfg: BrowserConfig) -> BrowserContext:
    cfg.user_data_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {
        "user_data_dir": str(cfg.user_data_dir),
        "headless": cfg.headless,
        "viewport": {"width": 1280, "height": 900},
        "locale": "en-US",
    }
    if cfg.channel:
        kwargs["channel"] = cfg.channel
    return p.chromium.launch_persistent_context(**kwargs)


def wait_for_whatsapp_ready(page: Page, timeout_ms: int = 180_000) -> None:
    """Wait until chat list / main UI is usable (after QR if needed)."""
    # QR canvas disappears when logged in; chat list appears.
    try:
        page.wait_for_selector("#pane-side", timeout=timeout_ms)
        return
    except Exception:
        pass
    try:
        page.wait_for_selector('[data-testid="chat-list"]', timeout=10_000)
        return
    except Exception:
        pass
    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 60_000))


def _visible_search_editable(page: Page, timeout_ms: int = 500) -> Optional[Locator]:
    """
    Find the chat-list / new-chat search box. WhatsApp changes DOM often; try several selectors.
    """
    candidates: list[Locator] = [
        page.locator('div[contenteditable="true"][aria-label="Search name or number"]'),
        page.locator('[data-testid="chat-list-search"] [contenteditable="true"]'),
        page.locator('[data-testid="chat-list-search"]').locator('[contenteditable="true"]'),
        page.locator("#pane-side").locator('[contenteditable="true"][role="textbox"]'),
        page.locator("#pane-side div[contenteditable=\"true\"]").first,
        page.locator('div[contenteditable="true"][data-tab="3"]'),
        page.get_by_role("combobox", name=re.compile(r"search", re.I)),
        page.get_by_role("textbox", name=re.compile(r"search", re.I)),
    ]
    for loc in candidates:
        try:
            first = loc.first
            if first.is_visible(timeout=timeout_ms):
                return first
        except Exception:
            continue
    return None


def _open_sidebar_search(page: Page) -> None:
    """Dismiss overlays, focus sidebar, try shortcut / buttons so search UI appears."""
    page.keyboard.press("Escape")
    time.sleep(0.15)
    try:
        page.locator("#pane-side").first.click(timeout=3_000)
        time.sleep(0.1)
    except Exception:
        pass

    if sys.platform == "darwin":
        page.keyboard.press("Meta+k")
    else:
        page.keyboard.press("Control+k")
    time.sleep(0.45)

    editable = _visible_search_editable(page, timeout_ms=800)
    if editable is not None:
        return

    # Click header search / new-chat entry points (labels vary by locale).
    for label in (
        "Search",
        "חיפוש",
        "Search or start new chat",
        "New chat",
        "צ'אט חדש",
    ):
        try:
            btn = page.get_by_role("button", name=re.compile(re.escape(label), re.I))
            if btn.count() and btn.first.is_visible(timeout=400):
                btn.first.click()
                time.sleep(0.35)
                if _visible_search_editable(page, timeout_ms=600) is not None:
                    return
        except Exception:
            continue

    # Icon-based (fragile but common on older builds)
    for sel in ('[data-icon="search"]', '[data-testid="chat-list-search"]'):
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                el.click()
                time.sleep(0.35)
                if _visible_search_editable(page, timeout_ms=600) is not None:
                    return
        except Exception:
            continue


def open_chat_by_title(page: Page, title: str, settle_ms: int = 800) -> bool:
    """
    Use the sidebar search to open a chat. `title` should be a unique substring
    of the chat name as shown in WhatsApp.
    """
    _open_sidebar_search(page)
    editable = _visible_search_editable(page, timeout_ms=1_500)
    if editable is None:
        log.error("Could not find WhatsApp search input — UI may have changed.")
        return False

    editable.click()
    time.sleep(0.05)
    if sys.platform == "darwin":
        page.keyboard.press("Meta+a")
    else:
        page.keyboard.press("Control+a")
    page.keyboard.press("Backspace")
    page.keyboard.type(title, delay=20)
    time.sleep(0.4)

    # First search result cell
    cell = page.locator('[data-testid="cell-frame-container"]').first
    if cell.count() == 0:
        cell = page.locator('[role="listitem"]').filter(has_text=re.compile(re.escape(title[: min(20, len(title))]), re.I)).first

    if cell.count() == 0:
        log.warning("No search result for chat title substring: %s", title)
        page.keyboard.press("Escape")
        return False

    cell.click()
    time.sleep(settle_ms / 1000)
    page.keyboard.press("Escape")
    return True


def scrape_recent_messages(page: Page, max_messages: int) -> list[ScrapedMessage]:
    """Read message text from the open conversation (best-effort DOM)."""
    main = page.locator("#main")
    if main.count():
        main.first.evaluate("el => el.scrollTop = el.scrollHeight")
    time.sleep(0.3)

    containers = page.locator('[data-testid="msg-container"]')
    n = containers.count()
    if n == 0:
        # Older / alternate structure
        containers = page.locator("#main .message")
        n = containers.count()

    start = max(0, n - max_messages)
    out: list[ScrapedMessage] = []
    for i in range(start, n):
        el = containers.nth(i)
        try:
            text_bits: list[str] = []
            for sel in (
                '[data-testid="msg-text"]',
                "span.selectable-text",
                ".copyable-text",
            ):
                loc = el.locator(sel)
                if loc.count():
                    for j in range(loc.count()):
                        t = loc.nth(j).inner_text().strip()
                        if t:
                            text_bits.append(t)
            text = "\n".join(text_bits).strip()
            if not text:
                text = el.inner_text().strip()
            if text:
                out.append(ScrapedMessage(text=text))
        except Exception as exc:
            log.debug("Skip message %s: %s", i, exc)
            continue
    return out


def read_open_chat_title(page: Page) -> Optional[str]:
    """
    Best-effort title of the currently open conversation (for `probe` and debugging).
    User should have exactly one chat open in the main panel.
    """
    selectors = (
        '[data-testid="conversation-info-header-chat-title"]',
        'header[data-testid="conversation-header"] span[title]',
        "#main header span[dir=\"auto\"]",
    )
    for sel in selectors:
        loc = page.locator(sel).first
        if loc.count() == 0:
            continue
        try:
            t = loc.get_attribute("title")
            if t and t.strip():
                return t.strip()
            text = loc.inner_text(timeout=3_000)
            if text and text.strip():
                return " ".join(text.split()).strip()
        except Exception:
            continue
    return None


def save_error_screenshot(page: Optional[Page], directory: Path, prefix: str = "error") -> Optional[Path]:
    """Write PNG under directory; return path or None if unavailable."""
    if page is None:
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"{prefix}-{ts}.png"
        page.screenshot(path=str(path), full_page=True)
        log.error("Saved error screenshot: %s", path)
        return path
    except Exception as exc:
        log.warning("Could not save screenshot: %s", exc)
        return None


class WhatsAppSession:
    def __init__(self, browser_cfg: BrowserConfig) -> None:
        self._browser_cfg = browser_cfg
        self._playwright_cm = None
        self._pw: Optional[Playwright] = None
        self._ctx: Optional[BrowserContext] = None

    def __enter__(self) -> WhatsAppSession:
        self._playwright_cm = sync_playwright()
        self._pw = self._playwright_cm.__enter__()
        self._ctx = _launch_context(self._pw, self._browser_cfg)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._ctx:
            self._ctx.close()
            self._ctx = None
        if self._playwright_cm:
            self._playwright_cm.__exit__(exc_type, exc, tb)
            self._playwright_cm = None
            self._pw = None

    @property
    def context(self) -> BrowserContext:
        if not self._ctx:
            raise RuntimeError("WhatsAppSession not started")
        return self._ctx

    def get_or_open_page(self) -> Page:
        pages = self.context.pages
        for p in pages:
            if "web.whatsapp.com" in (p.url or ""):
                return p
        page = self.context.new_page()
        return page
