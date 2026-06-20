#!/bin/zsh
# Saturday-morning portfolio checkup, run by launchd
# (~/Library/LaunchAgents/com.portfoliointel.weeklysync.plist).
#
# Two stages, so a failure in the AI stage never loses the data stage:
#   1. weekly_sync.py  — deterministic metrics sync + Discord status post
#   2. claude -p "/portfolio-checkup" — full advisory checkup; publishes to
#      the dashboard insights page and Discord via publish_checkup()
#
# Unattended Robinhood login REQUIRES RH_MFA_SECRET in .env (TOTP). Without
# it the login waits on a device-approval prompt nobody is there to tap.

set -uo pipefail
# Resolve the repo root from this script's location (portfolio-intel/scripts/..)
# so the job is portable and carries no hardcoded user path.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_TAG="[weekly-checkup $(date '+%Y-%m-%d %H:%M:%S')]"

cd "$REPO" || { echo "$LOG_TAG repo path missing: $REPO"; exit 1; }

echo "$LOG_TAG stage 1: weekly_sync"
/opt/homebrew/bin/python3 scripts/weekly_sync.py
echo "$LOG_TAG stage 1 exit: $?"

# Stage 2 runs Claude headless under the project's scoped permission
# allowlist (.claude/settings.json) — only the portfolio CLI, localhost curl,
# and web search are pre-approved; anything else is denied, not bypassed.
CLAUDE_BIN="$HOME/.local/bin/claude"
if [[ -x "$CLAUDE_BIN" ]]; then
    echo "$LOG_TAG stage 2: claude /portfolio-checkup"
    cd "$REPO/.." && "$CLAUDE_BIN" -p "/portfolio-checkup" \
        --permission-mode acceptEdits \
        --max-turns 80
    echo "$LOG_TAG stage 2 exit: $?"
else
    echo "$LOG_TAG stage 2 skipped: claude binary not found at $CLAUDE_BIN"
fi

echo "$LOG_TAG done"
