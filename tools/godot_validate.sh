#!/usr/bin/env bash
# godot_validate.sh — closed-loop validation harness for a Godot 4 project.
#
# Why this exists: an AI agent editing .gd/.tscn files cannot see the editor's
# red/yellow marks. This script converts "what the editor would complain about"
# into plain text on stdout that an agent can read and act on.
#
# Layers (cheapest first, fail fast):
#   1. static  — `godot --check-only` per script: parse/type/identifier errors
#   2. scene   — load every scene headless: broken ext_resource, unknown node class
#   3. runtime — boot the game N frames: real errors + push_warning + push_error
#
# Exit code: 0 = clean, 1 = findings. Never trust Godot's own exit code
# (it returns 0 even for fatal scene errors) — we grep the output instead.
#
# Usage:
#   tools/godot_validate.sh [--project DIR] [--frames N] [--layer all|static|scene|runtime]
#                           [--json] [--verbose]

set -uo pipefail

KIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Resolve the Godot project via the shared helper so the kit works whether it
# sits inside a project, beside one, or somewhere unrelated (see project_path.py).
PROJECT="$(python3 "$KIT_ROOT/tools/project_path.py")"
FRAMES=180
LAYER="all"
JSON=0
VERBOSE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)  PROJECT="$2"; shift 2 ;;
    --frames)   FRAMES="$2"; shift 2 ;;
    --layer)    LAYER="$2"; shift 2 ;;
    --json)     JSON=1; shift ;;
    --verbose)  VERBOSE=1; shift ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

GODOT="${GODOT:-godot}"
if ! command -v "$GODOT" >/dev/null 2>&1; then
  echo "FATAL: godot binary not found (set GODOT=/path/to/godot)" >&2
  exit 2
fi

# --- Critical: Godot must be able to WRITE its config/data dirs. ---------------
# If ~/.config is read-only (common in sandboxes/WSL setups), Godot segfaults
# with signal 11 before it does anything useful. Redirect both homes somewhere
# writable. The kit directory itself may be read-only (installed system-wide,
# shared, containerised, sandboxed), so do not assume it is usable: override
# with GODOT_LENS_HOME, else use the kit dir when writable, else a cache dir.
# Verify writability by ACTUALLY writing. `[[ -w ]]` reports writable for
# directories that reject writes on some filesystems, which silently produced a
# broken state dir and then a confusing Godot abort.
_is_writable() {
  mkdir -p "$1" 2>/dev/null || return 1
  local _p="$1/.writable-probe-$$"
  ( : > "$_p" ) 2>/dev/null || return 1
  rm -f "$_p" 2>/dev/null
  return 0
}

STATE="${GODOT_LENS_HOME:-${GODOT_KIT_HOME:-}}"
if [[ -z "$STATE" ]]; then
  KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if _is_writable "$KIT/.tooling"; then
    STATE="$KIT/.tooling"
  else
    STATE="${XDG_CACHE_HOME:-$HOME/.cache}/godot-lens"
  fi
fi
if ! mkdir -p "$STATE" 2>/dev/null || [[ ! -w "$STATE" ]]; then
  cat >&2 <<EOF
godot_validate: no writable state directory.

Godot must be able to write its config/data dirs or it aborts with a bare
signal 11 that looks like an engine bug. Tried:
  kit dir : ${KIT:-<kit>}/.tooling
  fallback: $STATE

Point it somewhere writable and retry:
  export GODOT_LENS_HOME=/some/writable/path
EOF
  exit 3
fi
export XDG_CONFIG_HOME="$STATE/godot_home/config"
export XDG_DATA_HOME="$STATE/godot_home/data"
export XDG_CACHE_HOME="$STATE/godot_home/cache"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" 2>/dev/null

cd "$PROJECT" || { echo "FATAL: no project at $PROJECT" >&2; exit 2; }

# --- One-time asset import ----------------------------------------------------
# A freshly generated project has its asset files on disk but nothing imported,
# and in that state `ResourceLoader.exists("res://assets/x.png")` is FALSE for a
# file that is plainly present on disk. A game that loads textures by path then
# reports "missing resource" for assets it actually has, which reads as a content
# bug in the project rather than a missing build step.
#
# Importing here fixes that for every caller at once, and is a no-op on an
# already-imported project.
if [[ ! -d ".godot/imported" ]]; then
  import_log="$(timeout 600 "$GODOT" --headless --import 2>&1)"
  if [[ ! -d ".godot/imported" ]]; then
    printf 'WARNING: asset import produced no .godot/imported directory.\n'
    printf 'Textures loaded by path may report as missing. Last import output:\n'
    printf '%s\n' "$import_log" | tail -5
  fi
fi

# Godot's own noise that is never a project defect.
NOISE='godot2026|dir_access|Failed to open .user://logs|editor_settings|Error saving editor settings'
NOISE="$NOISE"'|Cannot save file .home|^$|^Godot Engine v'

FINDINGS=0
JSONL="$(mktemp)"
trap 'rm -f "$JSONL"' EXIT

strip_noise() { grep -vE "$NOISE"; }

hr() { printf '\n===== %s =====\n' "$1"; }

# Emit one structured record per finding, in addition to the human-readable
# text. Agents and jq-based CI gates can consume these without regex-scraping
# the log; the text stays for humans. Uses python3 for correct JSON escaping
# (no jq dependency).
emit_json() { # layer, file, line, message
  python3 - "$1" "$2" "$3" "$4" >>"$JSONL" <<'PY'
import json, sys
layer, f, line, msg = sys.argv[1:5]
rec = {"layer": layer, "file": f, "message": msg}
if line and line.isdigit():
    rec["line"] = int(line)
print(json.dumps(rec, ensure_ascii=False))
PY
}

# Pull "<file>:<line>" out of a Godot error line when present.
findings_from() { # layer, file, raw_text
  local layer="$1" file="$2" raw="$3"
  local msg line
  # Godot formats vary: "res://x.gd:4" and "res://x.tscn:10 - Parse Error: ..."
  msg="$(grep -E 'SCRIPT ERROR|Parse Error|ERROR:|WARNING:' <<<"$raw" | head -5 | paste -sd' | ' -)"
  [[ -z "$msg" ]] && msg="$(head -1 <<<"$raw")"
  line="$(grep -oE "${file//\//\\/}:[0-9]+" <<<"$raw" | head -1 | grep -oE '[0-9]+$')"
  emit_json "$layer" "$file" "${line:-}" "$msg"
}

add_finding() { # label, detail  (label is like static:res://x.gd)
  local label="$1" detail="$2"
  local layer="${label%%:*}"
  local file="${label#*:}"
  [[ "$file" == "$label" ]] && file=""
  FINDINGS=$((FINDINGS + 1))
  printf '\n### %s\n%s\n' "$label" "$detail"
  findings_from "$layer" "$file" "$detail"
}

# --- Layer 1: static parse/type check of every script -------------------------
run_static() {
  hr "LAYER 1/3  static (godot --check-only)"
  local found=0
  while IFS= read -r f; do
    local rel="res://${f#./}"
    local out rc
    out="$(timeout 60 "$GODOT" --headless --check-only --script "$rel" 2>&1)"
    rc=$?
    # A non-zero rc OR any SCRIPT ERROR both count. Belt and braces: Godot's rc
    # is reliable here but we match text too so we never silently pass.
    if [[ $rc -ne 0 ]] || grep -q 'SCRIPT ERROR' <<<"$out"; then
      local body
      body="$(strip_noise <<<"$out" | grep -E 'SCRIPT ERROR|Parse Error|at:' | head -40)"
      [[ -z "$body" ]] && body="(no parseable output; rc=$rc)"
      add_finding "static:$rel" "$body"
      found=$((found + 1))
    fi
  done < <(find . -name '*.gd' -not -path './.godot/*' -not -path './addons/*' | sort)
  [[ $found -eq 0 ]] && printf 'OK — all scripts parse clean.\n'
}

# --- Layer 2: headless load of every scene -----------------------------------
# Catches what --check-only cannot: ext_resource pointing at a deleted file,
# `type=` naming a class that does not exist, malformed .tscn syntax.
run_scene() {
  hr "LAYER 2/3  scenes (headless load)"
  local found=0
  while IFS= read -r f; do
    local rel="res://${f#./}"
    local out
    out="$(timeout 90 "$GODOT" --headless --quit --scene "$rel" 2>&1 | strip_noise)"
    # Only genuine load failures matter. "Cannot get class" is reported as an
    # ERROR but is recoverable (placeholder node) so it is deliberately included
    # — it is exactly the yellow "!" an agent otherwise never sees.
    if grep -qE 'Failed loading|Parse Error|Cannot get class|non-existent resource|Cannot open file' <<<"$out"; then
      add_finding "scene:$rel" "$(grep -E 'ERROR|WARNING|Parse Error|cannot be created|at:' <<<"$out" | head -40)"
      found=$((found + 1))
    fi
  done < <(find . -name '*.tscn' -not -path './.godot/*' -not -path './addons/*' | sort)
  [[ $found -eq 0 ]] && printf 'OK — all scenes load clean.\n'
}

# --- Layer 3: boot the actual game -------------------------------------------
# The only layer that catches logic errors, null derefs and push_warning.
run_runtime() {
  hr "LAYER 3/3  runtime (boot main scene, $FRAMES frames)"
  local out
  out="$(timeout 180 "$GODOT" --headless --quit-after "$FRAMES" 2>&1 | strip_noise)"
  # WARNING is included on purpose: renpy2godot's own push_warning calls are
  # fidelity gaps the agent should know about.
  if grep -qE 'ERROR|WARNING|SCRIPT ERROR|Parse Error' <<<"$out"; then
    add_finding "runtime" "$(grep -E 'ERROR|WARNING|SCRIPT ERROR|GDScript backtrace|\[[0-9]+\] ' <<<"$out" | head -60)"
  else
    printf 'OK — %s frames with no errors or warnings.\n' "$FRAMES"
  fi
}

case "$LAYER" in
  static)  run_static ;;
  scene)   run_scene ;;
  runtime) run_runtime ;;
  all)     run_static; run_scene; run_runtime ;;
  *) echo "bad --layer: $LAYER" >&2; exit 2 ;;
esac

if [[ $JSON -eq 1 ]]; then
  # Structured output: one JSON object per finding, then a summary object.
  # Agents can consume these directly instead of regex-scraping Godot's log.
  printf '\n===== JSON FINDINGS =====\n'
  if [[ -s "$JSONL" ]]; then
    cat "$JSONL"
  fi
  printf '{"summary":true,"layer":"%s","findings":%d,"clean":%s}\n' \
    "$LAYER" "$FINDINGS" "$([[ $FINDINGS -eq 0 ]] && echo true || echo false)"
fi

hr "RESULT"
if [[ $FINDINGS -eq 0 ]]; then
  echo "CLEAN — no findings."
  exit 0
fi
echo "$FINDINGS finding group(s). Fix these before declaring the task done."
exit 1
