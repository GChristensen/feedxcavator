#!/usr/bin/env bash
# Opens Camoufox with a profile of the worker's browser pool (data/browser/<profile>), for example to sign in
# to a site or solve a captcha by hand. Close it before the worker uses the same profile.
#
#   ./firefox-profile.sh [profile] [url]
#
# The browser is started through the camoufox package, the same way the worker starts it with
# browser-engine: camoufox, so the profile is created and used by the same Camoufox build. The script waits
# until the browser window is closed.
#
# The home is FEEDXCAVATOR_HOME when set, otherwise the current directory. PYTHON overrides the interpreter
# (by default the .venv next to this script).

set -euo pipefail

profile="${1:-default}"
url="${2:-}"
home="${FEEDXCAVATOR_HOME:-$PWD}"
profile_dir="$home/data/browser/$profile"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_python() {
    if [[ -n "${PYTHON:-}" ]]; then
        echo "$PYTHON"
        return
    fi
    local c
    for c in "$script_dir/.venv/Scripts/python.exe" "$script_dir/.venv/bin/python"; do
        [[ -x "$c" ]] && { echo "$c"; return; }
    done
    echo "Python virtual environment not found in $script_dir/.venv; set PYTHON to its interpreter" >&2
    exit 1
}

python="$(find_python)"
mkdir -p "$profile_dir"

# A Windows Python needs a native path
if command -v cygpath >/dev/null 2>&1; then
    profile_dir="$(cygpath -w "$profile_dir")"
fi

echo "Camoufox starting with $profile_dir; close its window to finish"

"$python" - "$profile_dir" "$url" "$script_dir" <<'EOF'
import sys

sys.path.insert(0, sys.argv[3])
from feedxcavator import camoufox_identity

try:
    import camoufox  # noqa: F401
except ImportError:
    sys.exit("camoufox is not installed: install it into the virtual environment, "
             "then run: python -m camoufox fetch (see the README)")

profile_dir, url = sys.argv[1], sys.argv[2]

manager, context = camoufox_identity.start(profile_dir, headless=False)
try:
    page = context.pages[0] if context.pages else context.new_page()
    if url:
        page.goto(url)
    context.wait_for_event("close", timeout=0)
finally:
    manager.__exit__(None, None, None)
EOF
