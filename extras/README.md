# Extras

## macOS `launchd` (periodic scan)

1. Copy `com.scoutsignal.example.plist` to `~/Library/LaunchAgents/com.scoutsignal.plist`.
2. Edit **absolute paths**: `ProgramArguments` (venv `scoutsignal` binary, `config.yaml`, `chats.yaml`).
3. Set **`StartInterval`** (seconds) to match or exceed `poll_interval_seconds` in `config.yaml`.
4. Put your Gmail app password in **`SCOUTSIGNAL_SMTP_PASSWORD`** inside the plist’s `EnvironmentVariables`, or remove that key and use a wrapper script that `source`s a file (avoid committing secrets).
5. Load:

```bash
launchctl load ~/Library/LaunchAgents/com.scoutsignal.plist
```

Unload:

```bash
launchctl unload ~/Library/LaunchAgents/com.scoutsignal.plist
```

**Note:** `launchd` jobs run headless by default; WhatsApp Web usually needs an **interactive first login** (QR) using `scoutsignal run` in Terminal with `headless: false`, then the same `user_data_dir` is reused.
