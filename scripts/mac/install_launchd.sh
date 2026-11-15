#!/usr/bin/env bash
# Make the Mac run the platform by itself: services that start at login and
# come back if they crash, scheduled backups and retrain reports, and
# (optionally) no system sleep while on the charger.
#
#   scripts/mac/install_launchd.sh --dry-run          # print the plan and the plists
#   scripts/mac/install_launchd.sh                    # services + backups + retrain report
#   scripts/mac/install_launchd.sh --awake            # ... and stay awake on AC power
#   scripts/mac/install_launchd.sh --only backups     # one group: services|backups|retrain|awake
#   scripts/mac/install_launchd.sh --uninstall        # remove every agent this installed
#
# Everything is a per-user LaunchAgent in ~/Library/LaunchAgents with a label
# starting com.f1forecast., so nothing needs sudo and --uninstall finds it all.
# Logs go to ~/Library/Logs/f1forecast/.
#
# Before installing the services group, stop the copies you started by hand
# (Ctrl-C in their Terminals): the agents bind the same ports and would
# otherwise crash-loop until those are free.
#
# Schedules (the Mac's local time):
#   backups  Mon and Thu 12:00   scripts/backup_mongo.sh; Monday's runs after
#                                 every Sunday race has been scored
#   retrain  Tue 06:00           scripts/weekly_retrain.sh, report only (never --apply)
# A job missed while the Mac slept runs when it wakes. Failures are pushed to
# ALERT_WEBHOOK_URL by notify_on_failure.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/f1forecast"
PREFIX="com.f1forecast"
DRY=0; UNINSTALL=0; AWAKE=0; ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --awake) AWAKE=1 ;;
    --only) ONLY="$2"; shift ;;
    *) echo "unknown option $1" >&2; exit 64 ;;
  esac
  shift
done

uid="$(id -u)"

unload() {  # label
  launchctl bootout "gui/$uid/$1" 2>/dev/null || true
}

if [ "$UNINSTALL" = 1 ]; then
  for plist in "$AGENTS/$PREFIX".*.plist; do
    [ -e "$plist" ] || continue
    label="$(basename "$plist" .plist)"
    if [ "$DRY" = 1 ]; then echo "would remove $label"; continue; fi
    unload "$label"; rm -f "$plist"; echo "removed $label"
  done
  exit 0
fi

# launchd starts jobs with a bare PATH; carry the one that finds npm and node.
NPM_BIN="$(command -v npm || true)"
JOB_PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
[ -n "$NPM_BIN" ] && JOB_PATH="$(dirname "$NPM_BIN"):$JOB_PATH"

xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }

# plist <label> <keepalive 0|1> <schedule xml or ""> <program args...>
plist() {
  local label="$1" keep="$2" schedule="$3"; shift 3
  local args=""
  for a in "$@"; do args+="    <string>$(xml "$a")</string>"$'\n'; done
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>WorkingDirectory</key><string>$(xml "$ROOT")</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$(xml "$JOB_PATH")</string></dict>
  <key>StandardOutPath</key><string>$(xml "$LOGS/$label.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$LOGS/$label.log")</string>
$(if [ "$keep" = 1 ]; then printf '  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n  <key>ThrottleInterval</key><integer>30</integer>\n'; fi)$schedule
</dict>
</plist>
PLIST
}

at() {  # weekday hour [weekday hour ...] -> StartCalendarInterval
  local out="  <key>StartCalendarInterval</key>"$'\n'"  <array>"$'\n'
  while [ $# -gt 0 ]; do
    out+="    <dict><key>Weekday</key><integer>$1</integer><key>Hour</key><integer>$2</integer><key>Minute</key><integer>0</integer></dict>"$'\n'
    shift 2
  done
  printf '%s  </array>' "$out"
}

install() {  # label plist-text
  local label="$1" text="$2" file="$AGENTS/$1.plist"
  if [ "$DRY" = 1 ]; then
    echo "── $file"; echo "$text"; return
  fi
  mkdir -p "$AGENTS" "$LOGS"
  unload "$label"
  printf '%s\n' "$text" > "$file"
  plutil -lint "$file" >/dev/null
  launchctl bootstrap "gui/$uid" "$file"
  echo "installed $label"
}

want() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }

if want services; then
  # Mongo and Redis are Homebrew services already (brew services start ...).
  for svc in ingestion prediction scoring core-api frontend; do
    install "$PREFIX.$svc" "$(plist "$PREFIX.$svc" 1 "" "$ROOT/scripts/mac/run_service.sh" "$svc")"
  done
fi

if want backups; then
  install "$PREFIX.backup" "$(plist "$PREFIX.backup" 0 "$(at 1 12 4 12)" \
    "$ROOT/scripts/mac/notify_on_failure.sh" "Mongo backup" "$ROOT/scripts/backup_mongo.sh")"
fi

if want retrain; then
  install "$PREFIX.retrain" "$(plist "$PREFIX.retrain" 0 "$(at 2 6)" \
    "$ROOT/scripts/mac/notify_on_failure.sh" "Weekly retrain report" "$ROOT/scripts/weekly_retrain.sh")"
fi

if { [ "$AWAKE" = 1 ] && [ -z "$ONLY" ]; } || [ "$ONLY" = awake ]; then
  # -s holds off system sleep while on AC power only; the display still sleeps
  # and a closed lid on battery still sleeps.
  install "$PREFIX.awake" "$(plist "$PREFIX.awake" 1 "" /usr/bin/caffeinate -s)"
fi

[ "$DRY" = 1 ] || echo "Logs: $LOGS   Status: launchctl list | grep $PREFIX"
