# Extras — maximum automation (macOS)

## 1. One-time WhatsApp login (human)

1. Install ScoutSignal and Chromium: `pip install -e .` and `playwright install chromium`.
2. Run **once** in Terminal with **`headless: false`** in `config.yaml` until **WhatsApp Web shows the chat list** (QR / link device if needed).
3. Keep the same **`browser.user_data_dir`** forever for that profile.

After this, background jobs reuse the saved session until WhatsApp forces re-auth.

## 2. `scoutsignal-run.sh` (launchd-friendly)

- **Default paths:** `$HOME/scoutsignal-config` for YAML + `.env`, `$HOME/scoutsignal/.venv/bin/scoutsignal` for the CLI.
- **Secrets:** put **`SCOUTSIGNAL_SMTP_PASSWORD`** in **`$SCOUTSIGNAL_CONFIG_DIR/.env`** — ScoutSignal loads it when reading `config.yaml` (do not put passwords in the plist).
- **Overrides (optional env):** `SCOUTSIGNAL_CONFIG_DIR`, `SCOUTSIGNAL_VENV_BIN`, `SCOUTSIGNAL_EXTRA_ARGS` (e.g. `SCOUTSIGNAL_EXTRA_ARGS=--dry-run` for tests).

```bash
chmod +x /path/to/scoutsignal/extras/scoutsignal-run.sh
/path/to/scoutsignal/extras/scoutsignal-run.sh
```

## 3. Daily LaunchAgent (`com.scoutsignal.daily.plist`)

1. Edit **`StartCalendarInterval`** (`Hour` / `Minute`) for the **local** time you want a daily scan.
2. Replace **`/REPLACE/WITH/...`** strings with **absolute paths** to `scoutsignal-run.sh`, `scoutsignal-config`, and `.venv/bin/scoutsignal`.
3. Install:

```bash
cp com.scoutsignal.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.scoutsignal.daily.plist
```

4. Logs: **`/tmp/scoutsignal-daily.out.log`** and **`.err.log`** (edit plist paths if you prefer e.g. `~/scoutsignal-config/.scoutsignal/logs/`).

Unload:

```bash
launchctl unload ~/Library/LaunchAgents/com.scoutsignal.daily.plist
```

## 4. Interval agent (`com.scoutsignal.example.plist`)

Use **`StartInterval`** (seconds) if you want scans **every N minutes** while the Mac is awake — set **N ≥ `run.poll_interval_seconds`** in `config.yaml`. Same path / `.env` rules as above.

## 5. Headless + Chromium flags (automation)

- In **`config.yaml`**, set **`browser.headless: true`** only **after** you confirm scans work headless with your logged-in profile (WhatsApp sometimes behaves differently headless).
- **`browser.extra_chromium_args`** (see `config.example.yaml`) reduces first-run popups; ScoutSignal passes them to Playwright’s persistent context.

## 6. Reality check

- **Sleeping Mac:** `launchd` often **does not** fire on a closed laptop at the scheduled time. Use a **desktop**, leave the Mac **awake**, or schedule when it is on.
- **WhatsApp** may occasionally require **re-linking** — plan for rare manual intervention.
