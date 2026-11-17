#!/usr/bin/env bash
# Controls the Feedxcavator systemd unit (ctl): start, restart or stop it.
#
#   ./ctl start|restart|stop
#
# The unit is the user unit installed from deploy/feedxcavator.service, so systemctl runs with --user.
# FEEDXCAVATOR_SERVICE names the unit; a FEEDXCAVATOR_SERVICE line in .env next to this script overrides it.

set -euo pipefail

FEEDXCAVATOR_SERVICE="${FEEDXCAVATOR_SERVICE:-feedxcavator}"

env_file="$(dirname "$0")/.env"
if [[ -f "$env_file" ]]; then
    line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?FEEDXCAVATOR_SERVICE=' "$env_file" | tail -n 1 || true)"
    if [[ -n "$line" ]]; then
        value="${line#*=}"
        value="${value%$'\r'}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        [[ -n "$value" ]] && FEEDXCAVATOR_SERVICE="$value"
    fi
fi

case "${1:-}" in
    start | restart | stop)
        systemctl --user "$1" "$FEEDXCAVATOR_SERVICE"
        systemctl --user --no-pager --lines=0 status "$FEEDXCAVATOR_SERVICE" || true
        ;;
    *)
        echo "usage: $(basename "$0") start|restart|stop" >&2
        exit 2
        ;;
esac
