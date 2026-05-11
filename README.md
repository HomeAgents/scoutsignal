# ScoutSignal

**ScoutSignal** opens **WhatsApp Web** in a **persistent** Chromium profile, reads recent messages from chats you list, applies keyword / URL / length filters, **dedupes** with **SQLite**, and can **email** you when new posts match your rules. Optionally it emails a **short scan summary** after every run (even when there are zero new matches).

## What I need from you (human steps)

ScoutSignal cannot log in to your WhatsApp account by itself the first time. Please provide / do the following:

1. **Paths** — Run `scoutsignal init ~/scoutsignal-config` (or your folder). That creates `config.yaml`, `chats.yaml`, `.scoutsignal/browser-profile`, and `state.db` paths.
2. **WhatsApp** — First `scoutsignal run`: scan the **QR code** in the opened browser. The same `user_data_dir` keeps you logged in later.
3. **Chat titles** — In WhatsApp Web, open a group/DM, then run:
   ```bash
   scoutsignal probe --config config.yaml --chats chats.yaml
   ```
   Copy the printed line into `chats.yaml` as `title:` (unique substring is enough).
4. **SMTP** — Gmail: use an **app password**, not your normal password. Set:
   ```bash
   export SCOUTSIGNAL_SMTP_PASSWORD='....'
   ```
   and set `email.from_addr` / `email.to_addrs` in `config.yaml`.
   - Optional: **`email.from_display_name`** — friendly name shown in the **From** line (default `"(ScoutSignal)"`). The real mailbox for Gmail login and **Reply** is always `email.from_addr`.
   - Optional: **`email.always_send_summary: true`** — send an email after **every** scan (even **0** new job hits). The body lists **default include keywords once**, then **per chat** only `Chat:` + `Result: … messages scraped · … new job match(es) · OK` (or an error line), separators, and **new job match** previews when there are hits. If the scan stops early, a summary can still be sent with a short **NOTE** at the top when this flag is on.
5. **Keywords** — Edit `defaults.include_keywords` / `exclude_keywords` (and per-chat overrides in `chats.yaml`) so matches look like real job posts for you.

**Optional:** In the same folder as `config.yaml`, create a file named `.env` (never commit it) with your app password, for example:

```bash
SCOUTSIGNAL_SMTP_PASSWORD=your-gmail-app-password
```

ScoutSignal loads that file automatically when it reads `config.yaml`. Gmail SMTP is `smtp.gmail.com`, port `587`, TLS on — same pattern as other apps that use `GMAIL_APP_PASSWORD` with a Google **app password** (not your normal account password).

Everything else below is automated once the above is done.

## Scan reliability (WhatsApp Web)

WhatsApp’s DOM changes often. ScoutSignal opens each chat via **sidebar search** using a **substring of the chat title** from `chats.yaml`. Current behavior includes:

- **`run.open_chat_timeout_seconds`** (default **300**) — cap how long opening one chat from search may run, so a stuck modal does not block the whole scan.
- **Title-matched search row** — prefers a result row that contains your title text instead of blindly clicking the first row in the list.
- **Dialogs** — best-effort clicks on common “OK / Continue / …” buttons and **Escape** before clicking the search result; a **force** click retry if the first click fails.
- **Playwright timeouts** — if a single chat hits a browser timeout, that chat is recorded and the scan **continues** with the rest.

Use **`headless: true`** only after a **headed** run with the same profile opens chats reliably. You still need the phone-linked WhatsApp session; occasional re-link is normal.

## Hebrew and UTF-8

- **`chats.yaml`** supports **Hebrew and emoji** in `title:` (use double quotes if the name contains `:`).
- **Keywords** can be Hebrew, English, or both under `defaults` or per-chat `include_keywords` / `exclude_keywords`. Matching uses **Unicode NFC** normalization so composed Hebrew letters match reliably.
- If WhatsApp Web’s UI is in **Hebrew**, set in **`config.yaml`** under **`browser`:** `locale: "he-IL"` so Chromium matches Hebrew search and menu labels.

## Important

- Driving **WhatsApp Web** with **Playwright** may conflict with **WhatsApp / Meta** terms of use. Use at your own risk.
- The UI changes; if search or messages break, update selectors in `src/scoutsignal/whatsapp.py`.
- **Privacy:** error **screenshots** may contain chat content; they go under `screenshots/` next to your `state.db` unless you override `diagnostics.screenshots_dir`.

## Where state lives

| Item | Typical location (after `init`) |
|------|----------------------------------|
| Browser session (stay logged in) | `<config-dir>/.scoutsignal/browser-profile` |
| SQLite dedupe / seed flags | `<config-dir>/.scoutsignal/state.db` |
| Error screenshots | `<config-dir>/.scoutsignal/screenshots/` |

## Setup

```bash
cd scoutsignal
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
playwright install chromium
```

## Commands

```bash
scoutsignal init ~/scoutsignal-config
cd ~/scoutsignal-config
# edit config.yaml + chats.yaml; export SCOUTSIGNAL_SMTP_PASSWORD

scoutsignal config-check --config config.yaml --chats chats.yaml
scoutsignal probe --config config.yaml --chats chats.yaml
scoutsignal run --config config.yaml --chats chats.yaml --dry-run   # logs summary body; no SMTP send
scoutsignal run --config config.yaml --chats chats.yaml
scoutsignal run --config config.yaml --chats chats.yaml --loop
```

- **`seed_on_first_scan`** (in `config.yaml`): first time each chat is scanned, fingerprints are stored **without** email, to avoid a burst of old posts.

## Maximum automation (macOS)

For **daily unattended** runs: use **`extras/scoutsignal-run.sh`** + **`extras/com.scoutsignal.daily.plist`** (see **`extras/README.md`**). Put SMTP secrets in **`~/scoutsignal-config/.env`** only; edit plist **absolute paths** and **calendar time**; `launchctl load` the agent.

Also set **`browser.extra_chromium_args`** in `config.yaml` (see `config.example.yaml`) to reduce Chromium first-run noise. Use **`headless: true`** only after a **logged-in** profile works headless on your machine.

**Interval** scans: **`extras/com.scoutsignal.example.plist`** with **`StartInterval`**.

## Files

| File | Purpose |
|------|---------|
| `config.yaml` | Browser profile, run limits, keywords, SMTP, diagnostics |
| `chats.yaml` | Which chats to watch; optional per-chat keyword overrides |

Templates: `config.example.yaml`, `chats.example.yaml`.

## Changelog (high level)

- **0.1.3** — Summary email layout: keywords once, per chat `Result` + separators; optional `from_display_name`; `open_chat_timeout_seconds`; more resilient WhatsApp search/click and per-chat timeout handling; summary email on partial failure when `always_send_summary` is on; macOS extras (`extras/`, `browser.extra_chromium_args`).
- **0.1.2** — Earlier stable baseline (job alerts, seeding, probe, SQLite).
