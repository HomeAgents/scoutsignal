#!/usr/bin/env bash
# Stage and commit ScoutSignal project files (respects each repo's .gitignore).
# Commits ~/scoutsignal by default; if SCOUTSIGNAL_CONFIG_DIR is a separate git
# repo (~/.config often uses ~/scoutsignal-config), commits that second.
#
# Usage: extras/scoutsignal-git-checkin.sh
#   SCOUTSIGNAL_ROOT=/path/to/scoutsignal
#   SCOUTSIGNAL_CONFIG_DIR=/path/to/scoutsignal-config
#   SCOUTSIGNAL_GIT_MSG="custom message"            (optional — skips prompt on main repo)
#   SCOUTSIGNAL_CONFIG_GIT_MSG="config message"     (optional — second repo when separate)

set -euo pipefail

unset SCOUTSIGNAL_GIT_MSG_SUFFIX 2>/dev/null || true

NEVER_COMMIT=(
  .env
  .env.local
  .env.production
  .env.development
)

die() {
  echo "Error: $*" >&2
  exit 1
}

commit_repo() {
  local ROOT="$1"
  local msg_prefix="$2"

  [[ -d "$ROOT" ]] || die "missing directory: $ROOT"
  cd "$ROOT"
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git repo: $ROOT"

  local tracked_secret
  for tracked_secret in "${NEVER_COMMIT[@]}"; do
    if git ls-files --error-unmatch "$tracked_secret" &>/dev/null; then
      die "in $(basename "$ROOT"): $tracked_secret is tracked — remove from index before check-in"
    fi
  done

  echo "Repository: $ROOT"
  git status -sb
  echo ""

  if git diff --quiet && git diff --cached --quiet; then
    local untracked
    untracked="$(git ls-files --others --exclude-standard)"
    if [[ -z "$untracked" ]]; then
      echo "(clean — nothing to commit)"
      return 0
    fi
  fi

  git add -A

  local f
  for f in "${NEVER_COMMIT[@]}"; do
    if [[ -f "$f" ]]; then
      git reset -q HEAD -- "$f" 2>/dev/null || true
    fi
  done

  if git diff --cached --quiet; then
    echo "Nothing to commit after excluding secrets (.env, etc.)."
    return 0
  fi

  echo "Staged for commit:"
  git diff --cached --name-status
  echo ""

  local default_msg="${msg_prefix}: $(date '+%Y-%m-%d %H:%M')"
  local msg
  if [[ -n "${SCOUTSIGNAL_GIT_MSG:-}" ]]; then
    msg="$SCOUTSIGNAL_GIT_MSG${SCOUTSIGNAL_GIT_MSG_SUFFIX:-}"
  else
    read -r -p "Commit message [$default_msg]: " reply
    if [[ -n "${reply// }" ]]; then
      msg="$reply"
    else
      msg="$default_msg"
    fi
  fi

  git commit -m "$msg"
  echo "Committed ($(basename "$ROOT")): $(git log -1 --oneline)"
  echo ""
}

MAIN_ROOT="${SCOUTSIGNAL_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONFIG_DIR="${SCOUTSIGNAL_CONFIG_DIR:-}"

echo "======== ScoutSignal git check-in ========"
echo ""

commit_repo "$MAIN_ROOT" "scoutsignal"

if [[ -n "$CONFIG_DIR" && -d "$CONFIG_DIR/.git" ]]; then
  top_main="$(git -C "$MAIN_ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
  top_cfg="$(git -C "$CONFIG_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "$top_main" && -n "$top_cfg" && "$top_main" != "$top_cfg" ]]; then
    echo "======== Config repo (separate) ========"
    _saved_was_set=0
    _saved_git_msg=""
    if [[ "${SCOUTSIGNAL_GIT_MSG+x}" = x ]]; then
      _saved_was_set=1
      _saved_git_msg="$SCOUTSIGNAL_GIT_MSG"
    fi
    unset SCOUTSIGNAL_GIT_MSG_SUFFIX 2>/dev/null || true
    if [[ -n "${SCOUTSIGNAL_CONFIG_GIT_MSG:-}" ]]; then
      SCOUTSIGNAL_GIT_MSG="$SCOUTSIGNAL_CONFIG_GIT_MSG"
    elif [[ ${_saved_was_set} -eq 1 ]]; then
      SCOUTSIGNAL_GIT_MSG="$_saved_git_msg"
      SCOUTSIGNAL_GIT_MSG_SUFFIX=" (config)"
    else
      unset SCOUTSIGNAL_GIT_MSG
    fi
    commit_repo "$CONFIG_DIR" "scoutsignal-config"
    if [[ ${_saved_was_set} -eq 1 ]]; then
      SCOUTSIGNAL_GIT_MSG="$_saved_git_msg"
    else
      unset SCOUTSIGNAL_GIT_MSG 2>/dev/null || true
    fi
    unset SCOUTSIGNAL_GIT_MSG_SUFFIX 2>/dev/null || true
  fi
fi

echo "Push when ready:"
echo "  cd \"$MAIN_ROOT\" && git push"
if [[ -n "$CONFIG_DIR" && -d "$CONFIG_DIR/.git" ]]; then
  top_main="$(git -C "$MAIN_ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
  top_cfg="$(git -C "$CONFIG_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "$top_main" && -n "$top_cfg" && "$top_main" != "$top_cfg" ]]; then
    echo "  cd \"$CONFIG_DIR\" && git push"
  fi
fi
echo ""
echo "Not committed (typical gitignore): .env, .venv/, browser profile, *.db, .scoutsignal/"
