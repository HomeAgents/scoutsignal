#!/usr/bin/env bash
# Unattended ScoutSignal run for launchd/cron.
# ScoutSignal loads ~/.env next to config.yaml via python-dotenv — no need to export secrets here.
#
# Override defaults with env (optional):
#   SCOUTSIGNAL_CONFIG_DIR   (default: $HOME/scoutsignal-config)
#   SCOUTSIGNAL_VENV_BIN     (default: $HOME/scoutsignal/.venv/bin/scoutsignal)
#   SCOUTSIGNAL_EXTRA_ARGS   (e.g. --dry-run for testing)

set -euo pipefail

CONFIG_DIR="${SCOUTSIGNAL_CONFIG_DIR:-${HOME}/scoutsignal-config}"
VENV_BIN="${SCOUTSIGNAL_VENV_BIN:-${HOME}/scoutsignal/.venv/bin/scoutsignal}"
declare -a EXTRA=()
if [[ -n "${SCOUTSIGNAL_EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  EXTRA=(${SCOUTSIGNAL_EXTRA_ARGS})
fi

if [[ ! -x "$VENV_BIN" ]]; then
  echo "scoutsignal-run.sh: missing or non-executable VENV_BIN: $VENV_BIN" >&2
  exit 127
fi
if [[ ! -f "$CONFIG_DIR/config.yaml" ]] || [[ ! -f "$CONFIG_DIR/chats.yaml" ]]; then
  echo "scoutsignal-run.sh: missing config in $CONFIG_DIR" >&2
  exit 2
fi

# With `set -u`, an empty EXTRA array makes "${EXTRA[@]}" error on some bash versions.
if ((${#EXTRA[@]} > 0)); then
  exec "$VENV_BIN" run \
    --config "$CONFIG_DIR/config.yaml" \
    --chats "$CONFIG_DIR/chats.yaml" \
    "${EXTRA[@]}"
else
  exec "$VENV_BIN" run \
    --config "$CONFIG_DIR/config.yaml" \
    --chats "$CONFIG_DIR/chats.yaml"
fi
