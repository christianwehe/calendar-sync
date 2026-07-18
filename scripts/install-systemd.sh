#!/usr/bin/env bash
# Install calendar-sync as a systemd timer (a periodic, cron-like job).
#
# Defaults to a per-user unit under ~/.config/systemd/user/ -- no root
# needed, runs as you, only active while your systemd user instance is
# running (see the linger note this script prints at the end). Pass
# --system to install a system-wide unit under /etc/systemd/system/
# instead (requires sudo; runs as whichever user you specify with
# --run-as, defaulting to the user running this script).
#
# Usage:
#   scripts/install-systemd.sh [--on-calendar SPEC] [--system] [--run-as USER]
#
# Examples:
#   scripts/install-systemd.sh                        # every 15 min, user unit
#   scripts/install-systemd.sh --on-calendar hourly
#   scripts/install-systemd.sh --on-calendar '*-*-* 06,18:00:00'
#   scripts/install-systemd.sh --system --run-as christian
#
# Re-running this script is safe (installs/enables are idempotent) and
# is how you pick up a changed --on-calendar or an edited template.

set -euo pipefail

ON_CALENDAR="*:0/15"
SYSTEM_MODE=0
RUN_AS="$(id -un)"

while [ $# -gt 0 ]; do
    case "$1" in
        --on-calendar)
            ON_CALENDAR="$2"
            shift 2
            ;;
        --system)
            SYSTEM_MODE=1
            shift
            ;;
        --run-as)
            RUN_AS="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,21p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "error: $PROJECT_DIR/.env not found." >&2
    echo "Copy .env.example to .env and fill it in before installing the service." >&2
    exit 1
fi

if [ -x "$PROJECT_DIR/.venv/bin/calendar-sync" ]; then
    CALENDAR_SYNC_BIN="$PROJECT_DIR/.venv/bin/calendar-sync"
elif command -v calendar-sync >/dev/null 2>&1; then
    CALENDAR_SYNC_BIN="$(command -v calendar-sync)"
else
    echo "error: no calendar-sync executable found." >&2
    echo "Run 'python3 -m venv .venv && .venv/bin/pip install -e .' in $PROJECT_DIR first." >&2
    exit 1
fi

if command -v systemd-analyze >/dev/null 2>&1; then
    if ! systemd-analyze calendar "$ON_CALENDAR" >/dev/null 2>&1; then
        echo "error: '$ON_CALENDAR' is not a valid systemd calendar expression." >&2
        echo "See: man systemd.time -- or run: systemd-analyze calendar '$ON_CALENDAR'" >&2
        exit 1
    fi
fi

render_unit() {
    # $1 = template path, $2 = destination path
    sed \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__CALENDAR_SYNC_BIN__|$CALENDAR_SYNC_BIN|g" \
        -e "s|__ON_CALENDAR__|$ON_CALENDAR|g" \
        "$1" >"$2"
}

if [ "$SYSTEM_MODE" -eq 1 ]; then
    DEST_DIR="/etc/systemd/system"
    SYSTEMCTL=(sudo systemctl)
    echo "Installing system-wide unit to $DEST_DIR, running as user '$RUN_AS' (needs sudo)..."

    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    render_unit "$SCRIPT_DIR/../systemd/calendar-sync.service" "$TMP_DIR/calendar-sync.service"
    render_unit "$SCRIPT_DIR/../systemd/calendar-sync.timer" "$TMP_DIR/calendar-sync.timer"
    # System-wide units need an explicit User= to avoid running as root.
    sed -i "/^\[Service\]/a User=$RUN_AS" "$TMP_DIR/calendar-sync.service"

    sudo install -m 644 "$TMP_DIR/calendar-sync.service" "$DEST_DIR/calendar-sync.service"
    sudo install -m 644 "$TMP_DIR/calendar-sync.timer" "$DEST_DIR/calendar-sync.timer"
else
    DEST_DIR="$HOME/.config/systemd/user"
    SYSTEMCTL=(systemctl --user)
    echo "Installing user unit to $DEST_DIR..."

    mkdir -p "$DEST_DIR"
    render_unit "$SCRIPT_DIR/../systemd/calendar-sync.service" "$DEST_DIR/calendar-sync.service"
    render_unit "$SCRIPT_DIR/../systemd/calendar-sync.timer" "$DEST_DIR/calendar-sync.timer"
fi

"${SYSTEMCTL[@]}" daemon-reload
"${SYSTEMCTL[@]}" enable --now calendar-sync.timer

if [ "$SYSTEM_MODE" -eq 1 ]; then
    JOURNALCTL_SCOPE="--system"
    UNINSTALL_ARGS="--system"
else
    JOURNALCTL_SCOPE="--user"
    UNINSTALL_ARGS=""
fi

echo
echo "Installed and started calendar-sync.timer (schedule: $ON_CALENDAR)."
echo "Next run:   ${SYSTEMCTL[*]} list-timers calendar-sync.timer"
echo "Run now:    ${SYSTEMCTL[*]} start calendar-sync.service"
echo "Logs:       ${SYSTEMCTL[*]} status calendar-sync.service   /   journalctl $JOURNALCTL_SCOPE -u calendar-sync.service"
echo "Uninstall:  scripts/uninstall-systemd.sh $UNINSTALL_ARGS"

if [ "$SYSTEM_MODE" -eq 0 ]; then
    echo
    echo "Note: user services normally only run while you're logged in. To have"
    echo "this run on schedule even when logged out (e.g. a headless box), enable"
    echo "lingering once (requires sudo):"
    echo "  sudo loginctl enable-linger $RUN_AS"
fi
