#!/usr/bin/env bash
# Installs Chromium for the browser tests so that a flaky Ubuntu mirror cannot stall or fail the whole job.
#   1. the system libraries (apt) and the browser download (a CDN) are separate steps, each with a short timeout and a few tries;
#   2. if apt stays unreachable, the run goes on without the extra packages (the runner image already has the libraries) and the result is
#      judged by one real thing: Chromium must start. If it does not, this script fails with that reason, not with an apt error.
# The pages are rendered with the fonts that ship in static/fonts/ (Vazirmatn, Noto Naskh, Inter), never with fonts from apt.
# Usage: ci_install_browser.sh [path-to-playwright]   (default: playwright)
# Tunables (seconds / counts): RAV_TRY_TIMEOUT (240), RAV_TRIES (3), RAV_PAUSE (10), RAV_SUDO (sudo; empty when already root)
set -uo pipefail

PW="${1:-playwright}"
TRY_TIMEOUT="${RAV_TRY_TIMEOUT:-240}"
TRIES="${RAV_TRIES:-3}"
PAUSE="${RAV_PAUSE:-10}"
SUDO="${RAV_SUDO-sudo}"
SUDO_CMD=()
[ -n "$SUDO" ] && SUDO_CMD=("$SUDO")

say() { printf '%s\n' "$*"; }

# retry LABEL COMMAND... : up to $TRIES tries, each killed after $TRY_TIMEOUT seconds
retry() {
  local label="$1" n=1
  shift
  while [ "$n" -le "$TRIES" ]; do
    say "== $label: try $n of $TRIES (limit ${TRY_TIMEOUT}s)"
    if timeout --kill-after=10 "$TRY_TIMEOUT" "$@"; then
      return 0
    fi
    say "== $label: try $n failed"
    n=$((n + 1))
    [ "$n" -le "$TRIES" ] && sleep "$PAUSE"
  done
  return 1
}

# apt must not wait for a dead mirror: short network timeouts and its own retries
if [ -n "$SUDO" ] && [ -d /etc/apt/apt.conf.d ]; then
  printf 'Acquire::Retries "3";\nAcquire::http::Timeout "20";\nAcquire::https::Timeout "20";\n' | "${SUDO_CMD[@]}" tee /etc/apt/apt.conf.d/80-ravix-ci >/dev/null || true
fi

DEPS="ok"
if ! retry "system libraries" "${SUDO_CMD[@]}" "$PW" install-deps chromium; then
  DEPS="skipped"
  say "WARNING: the system libraries could not be installed (apt unreachable); continuing with what the runner image already has"
fi

if ! retry "browser download" "$PW" install chromium; then
  say "ERROR: the Chromium download failed $TRIES times" >&2
  exit 1
fi

# the only verdict that matters: can it start?
CHECK='import sys
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page()
    page.set_content("<p>ok</p>")
    sys.exit(0 if page.inner_text("p") == "ok" else 1)'
PYBIN="$(dirname "$PW")/python"
[ -x "$PYBIN" ] || PYBIN="python3"
if ! "$PYBIN" -c "$CHECK"; then
  say "ERROR: Chromium does not start (system libraries: $DEPS)" >&2
  exit 1
fi
say "Chromium is ready (system libraries: $DEPS)"
