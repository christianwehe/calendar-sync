#!/usr/bin/env bash
# Remove the calendar-sync systemd timer/service installed by
# scripts/install-systemd.sh. Safe to run even if it was never
# installed, or was already removed.
#
# Usage:
#   scripts/uninstall-systemd.sh [--system]
#
# Pass --system if you installed with `install-systemd.sh --system`;
# otherwise this removes the per-user unit (the default install mode).
# This does NOT touch .env, jobs.toml, or any cached Google/SpielerPlus
# credentials -- only the systemd unit files themselves.

set -euo pipefail

SYSTEM_MODE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --system)
            SYSTEM_MODE=1
            shift
            ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if [ "$SYSTEM_MODE" -eq 1 ]; then
    DEST_DIR="/etc/systemd/system"
    SYSTEMCTL=(sudo systemctl)
    RM=(sudo rm -f)
else
    DEST_DIR="$HOME/.config/systemd/user"
    SYSTEMCTL=(systemctl --user)
    RM=(rm -f)
fi

"${SYSTEMCTL[@]}" disable --now calendar-sync.timer 2>/dev/null || true
"${SYSTEMCTL[@]}" stop calendar-sync.service 2>/dev/null || true

"${RM[@]}" "$DEST_DIR/calendar-sync.service" "$DEST_DIR/calendar-sync.timer"

"${SYSTEMCTL[@]}" daemon-reload
"${SYSTEMCTL[@]}" reset-failed calendar-sync.service calendar-sync.timer 2>/dev/null || true

echo "Removed calendar-sync.service and calendar-sync.timer ($DEST_DIR)."
