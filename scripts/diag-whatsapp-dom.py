#!/usr/bin/env python3
"""Print WhatsApp Web sidebar selectors (headless) for ScoutSignal debugging."""
from pathlib import Path
import yaml
from playwright.sync_api import sync_playwright

cfg = yaml.safe_load((Path.home() / "scoutsignal-config/config.yaml").read_text(encoding="utf-8"))
ud = str(Path(cfg["browser"]["user_data_dir"]).expanduser())
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=ud,
        headless=True,
        viewport={"width": 1280, "height": 900},
        locale=cfg.get("browser", {}).get("locale", "he-IL"),
        args=["--no-sandbox", *list(cfg.get("browser", {}).get("extra_chromium_args") or [])],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(10_000)
    out = Path.home() / "scoutsignal-config/.scoutsignal/screenshots/diag-wa.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out), full_page=True)
    sels = [
        "#pane-side",
        '[data-testid="chat-list-search"]',
        'motionediv[aria-label="Search input textbox"]',
        'motionediv[aria-label="Search name or number"]',
        'motionediv[aria-label="חיפוש"]',
        'div[contenteditable="true"][data-tab="3"]',
        'motionediv[contenteditable="true"]',
        'motionediv[role="textbox"]',
        'motionediv div[contenteditable="true"]',
        'div[role="textbox"][contenteditable="true"]',
        '[data-icon="search"]',
    ]
    for s in sels:
        loc = page.locator(s)
        c = loc.count()
        vis = False
        if c:
            try:
                vis = loc.first.is_visible(timeout=500)
            except Exception:
                pass
        print(f"{s!r}: count={c} visible={vis}")
    print("--- contenteditable elements ---")
    for i, el in enumerate(page.locator('[contenteditable="true"]').all()[:10]):
        try:
            al = el.get_attribute("aria-label") or ""
            role = el.get_attribute("role") or ""
            tab = el.get_attribute("data-tab") or ""
            print(f"  [{i}] aria-label={al!r} role={role!r} data-tab={tab!r}")
        except Exception as exc:
            print(f"  [{i}] err={exc}")
    print("screenshot:", out)
    ctx.close()
