from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

import yaml

from scoutsignal import __version__
from scoutsignal.config_loader import load_app_config, validate_config
from scoutsignal.engine import run_probe, run_scan


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.directory).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    root = _repo_root()
    for name in ("config.example.yaml", "chats.example.yaml"):
        src = root / name
        if not src.exists():
            print(f"Missing template {src}", file=sys.stderr)
            return 1
    cfg_dst = dest / "config.yaml"
    chats_dst = dest / "chats.yaml"
    if cfg_dst.exists() and not args.force:
        print(f"Already exists: {cfg_dst} (use --force)", file=sys.stderr)
        return 1
    if chats_dst.exists() and not args.force:
        print(f"Already exists: {chats_dst} (use --force)", file=sys.stderr)
        return 1
    shutil.copy(root / "config.example.yaml", cfg_dst)
    shutil.copy(root / "chats.example.yaml", chats_dst)
    profile = (dest / ".scoutsignal" / "browser-profile").resolve()
    profile.mkdir(parents=True, exist_ok=True)
    db = (dest / ".scoutsignal" / "state.db").resolve()
    with cfg_dst.open(encoding="utf-8") as f:
        cfg_data = yaml.safe_load(f) or {}
    cfg_data.setdefault("browser", {})["user_data_dir"] = str(profile)
    cfg_data.setdefault("state", {})["sqlite_path"] = str(db)
    with cfg_dst.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg_data, f, sort_keys=False, allow_unicode=True)
    print(f"Created {cfg_dst} and {chats_dst}")
    print("browser.user_data_dir and state.sqlite_path were set to this folder’s .scoutsignal paths.")
    print("Edit chats.yaml — set each chat `title` to a unique substring of the WhatsApp name.")
    return 0


def cmd_config_check(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    chats_path = Path(args.chats).resolve()
    try:
        cfg = load_app_config(config_path, chats_path)
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    _setup_logging(cfg.logging.level)
    errs = validate_config(cfg, check_email_password=not args.skip_email_check, require_chats=True)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 1
    print("Configuration OK.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    chats_path = Path(args.chats).resolve()
    try:
        cfg = load_app_config(config_path, chats_path)
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    _setup_logging(cfg.logging.level)

    errs = validate_config(
        cfg,
        check_email_password=not args.dry_run and not args.skip_email_check,
        require_chats=True,
    )
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 1

    if args.loop:
        print("Loop mode — Ctrl+C to stop.")
        try:
            while True:
                run_scan(cfg, dry_run=args.dry_run)
                time.sleep(max(30, cfg.run.poll_interval_seconds))
        except KeyboardInterrupt:
            print("Stopped.")
            return 0

    run_scan(cfg, dry_run=args.dry_run)
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    chats_path = Path(args.chats).resolve()
    try:
        cfg = load_app_config(config_path, chats_path)
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    _setup_logging(cfg.logging.level)
    errs = validate_config(cfg, check_email_password=False, require_chats=False)
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 1
    try:
        return run_probe(cfg)
    except Exception as exc:
        print(f"Probe error: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="scoutsignal", description="ScoutSignal — WhatsApp Web job alerts")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create config.yaml and chats.yaml from templates")
    p_init.add_argument("directory", nargs="?", default=".", help="Directory to write files (default: .)")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing files")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("config-check", help="Validate configuration files")
    p_check.add_argument("--config", default="config.yaml")
    p_check.add_argument("--chats", default="chats.yaml")
    p_check.add_argument("--skip-email-check", action="store_true", help="Do not require SMTP password env")
    p_check.set_defaults(func=cmd_config_check)

    p_run = sub.add_parser("run", help="Open WhatsApp Web and scan enabled chats")
    p_run.add_argument("--config", default="config.yaml")
    p_run.add_argument("--chats", default="chats.yaml")
    p_run.add_argument("--dry-run", action="store_true", help="Do not send email")
    p_run.add_argument("--skip-email-check", action="store_true", help="Allow missing SMTP env when not dry-run")
    p_run.add_argument("--loop", action="store_true", help="Re-run after poll_interval_seconds")
    p_run.set_defaults(func=cmd_run)

    p_probe = sub.add_parser(
        "probe",
        help="Print the open chat title from WhatsApp Web (open a chat first; copy into chats.yaml)",
    )
    p_probe.add_argument("--config", default="config.yaml")
    p_probe.add_argument("--chats", default="chats.yaml")
    p_probe.set_defaults(func=cmd_probe)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
