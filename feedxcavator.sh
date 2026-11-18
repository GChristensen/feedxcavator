#!/bin/sh
# Starts Feedxcavator from this directory and starts it again when it exits with code 3.
cd "$(dirname "$0")" || exit 1
PATH="$(pwd):$PATH"
export PATH
export FEEDXCAVATOR_SUPERVISED=1
PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3

while :; do
    "$PYTHON" -m feedxcavator run "$@"
    code=$?
    [ "$code" -eq 3 ] || exit "$code"
done
