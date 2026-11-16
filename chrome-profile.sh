#!/usr/bin/env bash
# Opens Chrome with a profile of the worker's browser pool (data/browser/<profile>), for example to sign in
# to a site or solve a captcha by hand. Close it before the worker uses the same profile.
#
#   ./chrome-profile.sh [profile] [url]
#
# The home is FEEDXCAVATOR_HOME when set, otherwise the current directory. CHROME overrides the binary.

set -euo pipefail

profile="${1:-default}"
url="${2:-}"
home="${FEEDXCAVATOR_HOME:-$PWD}"
profile_dir="$home/data/browser/$profile"

find_chrome() {
    if [[ -n "${CHROME:-}" ]]; then
        echo "$CHROME"
        return
    fi
    local candidates=(
        "/c/Program Files/Google/Chrome/Application/chrome.exe"
        "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"
        "${LOCALAPPDATA:-}/Google/Chrome/Application/chrome.exe"
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    local c
    for c in "${candidates[@]}"; do
        [[ -x "$c" ]] && { echo "$c"; return; }
    done
    for c in google-chrome google-chrome-stable chromium chromium-browser; do
        command -v "$c" >/dev/null 2>&1 && { command -v "$c"; return; }
    done
    echo "Chrome not found; set CHROME to its path" >&2
    exit 1
}

chrome="$(find_chrome)"
mkdir -p "$profile_dir"

# Chrome on Windows needs a native path
if command -v cygpath >/dev/null 2>&1; then
    profile_dir="$(cygpath -w "$profile_dir")"
fi

args=(--user-data-dir="$profile_dir" --no-first-run --no-default-browser-check)
[[ -n "$url" ]] && args+=("$url")

"$chrome" "${args[@]}" >/dev/null 2>&1 &
disown
echo "Chrome started with $profile_dir"
