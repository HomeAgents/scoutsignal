from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PWTimeoutError,
    sync_playwright,
)

from scoutsignal.config_loader import BrowserConfig

log = logging.getLogger(__name__)


@dataclass
class ScrapedMessage:
    text: str


def _launch_context(p: Playwright, cfg: BrowserConfig) -> BrowserContext:
    cfg.user_data_dir.mkdir(parents=True, exist_ok=True)
    args = ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]
    if cfg.extra_chromium_args:
        args.extend(cfg.extra_chromium_args)
    kwargs: dict = {
        "user_data_dir": str(cfg.user_data_dir),
        "headless": cfg.headless,
        "viewport": {"width": 1280, "height": 900},
        "locale": cfg.locale,
        "args": args,
    }
    if cfg.channel:
        kwargs["channel"] = cfg.channel
    if cfg.user_agent:
        kwargs["user_agent"] = cfg.user_agent
    return p.chromium.launch_persistent_context(**kwargs)


def wait_for_whatsapp_ready(page: Page, timeout_ms: int = 180_000) -> None:
    """Wait until chat list / main UI is usable (after QR if needed)."""
    ready_selectors = (
        "#pane-side",
        '[data-testid="chat-list"]',
        'input[aria-label*="Search"]',
        'input[aria-label*="חיפוש"]',
    )
    per_selector_ms = max(5_000, timeout_ms // len(ready_selectors))
    for sel in ready_selectors:
        try:
            page.wait_for_selector(sel, timeout=per_selector_ms)
            return
        except Exception:
            continue
    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 60_000))


def _visible_search_editable(page: Page, timeout_ms: int = 500) -> Optional[Locator]:
    """
    Find the chat-list / new-chat search box. WhatsApp changes DOM often; try several selectors.
    """
    candidates: list[Locator] = [
        page.get_by_role("textbox", name=re.compile(r"search|חיפוש", re.I)),
        page.locator('input[aria-label*="Search"]'),
        page.locator('input[aria-label*="חיפוש"]'),
        page.locator('div[contenteditable="true"][aria-label="Search name or number"]'),
        page.locator('[data-testid="chat-list-search"] [contenteditable="true"]'),
        page.locator('[data-testid="chat-list-search"]').locator('[contenteditable="true"]'),
        page.locator("#pane-side").locator('[contenteditable="true"][role="textbox"]'),
        page.locator("#pane-side div[contenteditable=\"true\"]").first,
        page.locator('div[contenteditable="true"][data-tab="3"]'),
        page.get_by_role("combobox", name=re.compile(r"search|חיפוש", re.I)),
    ]
    for loc in candidates:
        try:
            first = loc.first
            if first.is_visible(timeout=timeout_ms):
                return first
        except Exception:
            continue
    return None


def _dismiss_blocking_layers(page: Page) -> None:
    """Close modals / side panels that intercept clicks on search results."""
    page.keyboard.press("Escape")
    time.sleep(0.1)
    _click_obvious_dialog_buttons(page)
    for _ in range(3):
        dlg = page.locator('[role="dialog"][aria-modal="true"]')
        if dlg.count() == 0:
            break
        page.keyboard.press("Escape")
        time.sleep(0.15)
    # WhatsApp often leaves a full-height overlay on the chat list after search.
    try:
        page.locator("#pane-side").first.click(timeout=1_500)
    except Exception:
        pass
    time.sleep(0.1)


def _open_search_result_row(page: Page, cell: Locator, title: str, click_budget: int) -> bool:
    """
    Open a search hit via title span, row click, or keyboard (ArrowDown + Enter).
  """
    _dismiss_blocking_layers(page)

    title_span = cell.locator('[data-testid="cell-frame-title"]')
    if title_span.count() > 0:
        try:
            title_span.first.click(timeout=min(8_000, click_budget), force=True)
            return True
        except Exception:
            pass

    try:
        cell.click(timeout=click_budget, force=True)
        return True
    except Exception as exc:
        log.debug("Row click failed for %r: %s — trying keyboard", title, exc)

    _dismiss_blocking_layers(page)
    try:
        cell.focus(timeout=2_000)
    except Exception:
        pass
    page.keyboard.press("ArrowDown")
    time.sleep(0.2)
    page.keyboard.press("Enter")
    time.sleep(0.35)
    # Open chat shows #main; search-only UI does not.
    if page.locator("#main").count() > 0:
        return True
    return False


def _click_obvious_dialog_buttons(page: Page) -> None:
    """Dismiss cookie / update / consent sheets that block sidebar clicks."""
    patterns = (
        r"^\s*OK\s*$",
        r"Continue",
        r"Got it",
        r"Accept(\s+all)?",
        r"I agree",
        r"Allow",
        r"מסכים",
        r"אישור",
        r"הבנתי",
        r"סגור",
        r"Close",
    )
    for pat in patterns:
        try:
            btn = page.get_by_role("button", name=re.compile(pat, re.I))
            if btn.count() and btn.first.is_visible(timeout=200):
                btn.first.click(timeout=2_000)
                time.sleep(0.2)
        except Exception:
            continue


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
        "חיפוש או התחלת צ'אט חדש",
        "התחלת צ'אט חדש",
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

    # Short Hebrew / English substrings (accessibility name may be longer).
    for pattern in (re.compile(r"חיפוש"), re.compile(r"Search", re.I)):
        try:
            btn = page.get_by_role("button", name=pattern)
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


def open_chat_by_title(
    page: Page,
    title: str,
    *,
    open_deadline: Optional[float] = None,
    settle_ms: int = 800,
) -> bool:
    """
    Use the sidebar search to open a chat. `title` should be a unique substring
    of the chat name as shown in WhatsApp.

    If `open_deadline` is set (monotonic seconds), abort and return False when time runs out
    so a stuck search / modal cannot block the scan indefinitely.
    """

    def remaining_ms(fallback_cap: int = 90_000) -> int:
        if open_deadline is None:
            return fallback_cap
        return max(0, int((open_deadline - time.monotonic()) * 1000))

    def bail(reason: str) -> bool:
        log.warning("open_chat_by_title: %s (chat %r)", reason, title)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    if open_deadline is not None and time.monotonic() >= open_deadline:
        return bail("deadline already expired at start")

    _open_sidebar_search(page)

    if open_deadline is not None and time.monotonic() >= open_deadline:
        return bail("timed out while opening sidebar search")

    editable = _visible_search_editable(
        page,
        timeout_ms=min(1_500, max(200, remaining_ms(1_500))),
    )
    if editable is None:
        log.error("Could not find WhatsApp search input — UI may have changed.")
        return False

    _click_obvious_dialog_buttons(page)
    try:
        editable.click(timeout=min(5_000, max(250, remaining_ms(5_000))))
    except PWTimeoutError:
        log.warning("Search field click timed out for chat %r", title)
        return False
    time.sleep(0.05)
    if open_deadline is not None and time.monotonic() >= open_deadline:
        return bail("timed out before typing search query")

    if sys.platform == "darwin":
        page.keyboard.press("Meta+a")
    else:
        page.keyboard.press("Control+a")
    page.keyboard.press("Backspace")
    # insert_text handles Hebrew, emoji, and mixed scripts reliably vs key-by-key type().
    page.keyboard.insert_text(title)

    # Prefer a result row that actually contains the chat title (`.first` alone often hits the wrong row).
    wait_results_ms = min(12_000, max(400, remaining_ms(12_000)))
    st = title.strip()
    snippet = st[: min(48, len(st))] if st else ""
    cell: Optional[Locator] = None
    if snippet:
        match_cells = page.locator('[data-testid="cell-frame-container"]').filter(
            has_text=re.compile(re.escape(snippet), re.I)
        )
        try:
            match_cells.first.wait_for(state="visible", timeout=wait_results_ms)
        except Exception:
            pass
        if match_cells.count() > 0:
            cell = match_cells.first

    if cell is None:
        all_cells = page.locator('[data-testid="cell-frame-container"]')
        if all_cells.count() > 0:
            try:
                all_cells.first.wait_for(state="visible", timeout=min(6_000, wait_results_ms))
            except Exception:
                pass
            cell = all_cells.first

    if open_deadline is not None and time.monotonic() >= open_deadline:
        return bail("timed out waiting for search results after typing title")

    if cell is None or cell.count() == 0:
        cell = page.locator('[role="listitem"]').filter(
            has_text=re.compile(re.escape(title[: min(20, len(title))]), re.I)
        ).first

    if cell.count() == 0:
        log.warning("No search result for chat title substring: %s", title)
        page.keyboard.press("Escape")
        return False

    click_budget = min(20_000, max(300, remaining_ms(20_000)))
    if open_deadline is not None and click_budget < 400:
        return bail("insufficient time left to click search result")

    if not _open_search_result_row(page, cell, title, click_budget):
        log.warning("Could not open search result for chat %r", title)
        page.keyboard.press("Escape")
        return False

    settle_s = min(settle_ms / 1000.0, max(0.0, remaining_ms(60_000) / 1000.0))
    if settle_s > 0:
        time.sleep(settle_s)
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
