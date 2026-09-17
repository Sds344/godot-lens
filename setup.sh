#!/usr/bin/env bash
# setup.sh — one-time preparation for godot-lens.
#
#   ./setup.sh              prepare and report what was found
#   ./setup.sh --project DIR   point at a specific Godot project
#
# Does three things:
#   1. verifies the Godot binary is reachable
#   2. checks that a writable state directory exists (Godot aborts with a bare
#      signal 11 without one, which looks like an engine bug)
#   3. generates the engine API reference (~11 MB, queried on demand, never
#      committed)
#
# Safe to re-run.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GODOT="${GODOT:-godot}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) export GODOT_PROJECT="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

echo "godot-lens setup"
echo "================"

# --- 1. Godot ---------------------------------------------------------------
if ! command -v "$GODOT" >/dev/null 2>&1; then
  echo "FAIL  Godot not found on PATH."
  echo "      Install Godot 4.x, or set GODOT=/path/to/godot and re-run."
  exit 1
fi
echo "ok    Godot: $("$GODOT" --version 2>/dev/null | head -1)"

# --- 2. Project -------------------------------------------------------------
PROJECT="$(python3 "$ROOT/tools/project_path.py")"
if [[ -f "$PROJECT/project.godot" ]]; then
  echo "ok    project: $PROJECT"
else
  echo "warn  no Godot project found (looked at: $PROJECT)"
  echo "      Run from inside a project, or pass --project DIR,"
  echo "      or set GODOT_PROJECT."
fi

# --- 3. Writable state ------------------------------------------------------
# Mirrors state_dir() in tools/project_path.py.
# `[[ -w ]]` is NOT trustworthy here: on this filesystem it reports writable for
# a directory that rejects writes, so it must be verified by actually writing.
is_writable() {
  mkdir -p "$1" 2>/dev/null || return 1
  local probe="$1/.writable-probe-$$"
  ( : > "$probe" ) 2>/dev/null || return 1
  rm -f "$probe" 2>/dev/null
  return 0
}

STATE="${GODOT_LENS_HOME:-${GODOT_KIT_HOME:-}}"
if [[ -z "$STATE" ]]; then
  if is_writable "$ROOT/.tooling"; then
    STATE="$ROOT/.tooling"
  else
    STATE="${XDG_CACHE_HOME:-$HOME/.cache}/godot-lens"
  fi
fi
if ! is_writable "$STATE"; then
  echo "FAIL  no writable state directory."
  echo "      Godot aborts with a bare signal 11 when it cannot write its"
  echo "      config/data dirs. Tried:"
  echo "        lens dir : $ROOT/.tooling"
  echo "        fallback : $STATE"
  echo "      Fix: export GODOT_LENS_HOME=/some/writable/path"
  exit 1
fi
echo "ok    state:   $STATE"
if [[ "$STATE" != "$ROOT/.tooling" ]]; then
  echo "      (set GODOT_LENS_HOME=$STATE in your shell profile so later runs"
  echo "       resolve to the same place)"
fi

# --- 4. API reference -------------------------------------------------------
# Existence is not enough: a partial or empty dump also fails, and reporting
# "ok" for one is worse than reporting nothing.
dump_is_valid() {
  [[ -s "$1" ]] || return 1
  python3 -c "
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
sys.exit(0 if d.get('classes') else 1)
" "$1" 2>/dev/null
}

DUMP="$STATE/api-dump/extension_api.json"
if dump_is_valid "$DUMP"; then
  echo "ok    api:     already present ($(du -h "$DUMP" | cut -f1))"
else
  echo "..    generating engine API reference (one time)"
  GODOT_LENS_HOME="$STATE" bash "$ROOT/tools/godot_api.sh" dump 2>&1 | tail -3
  if dump_is_valid "$DUMP"; then
    echo "ok    api:     $DUMP"
  else
    echo "FAIL  could not generate the API reference."
    echo "      Run manually to see why: GODOT_LENS_HOME=$STATE bash tools/godot_api.sh dump"
    exit 1
  fi
fi

echo
echo "Ready. Try:"
echo "  python3 tools/godot_context.py --summary"
