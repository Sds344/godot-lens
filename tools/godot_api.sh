#!/usr/bin/env bash
# godot_api.sh — manage the offline Godot API reference.
#
# `dump`  regenerates the API JSON from the installed Godot binary. This is the
#         ground-truth source: the engine's own reflection data, so it can never
#         disagree with the engine the project actually runs on.
# `check` reports whether the dump matches the installed Godot version.
#
# Query it with: tools/godot_api.py <class|method|property|signal|search|enum>
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GODOT="${GODOT:-godot}"

# Writable state dir. The kit directory may be read-only (installed, shared,
# sandboxed), so do not assume it: override with GODOT_LENS_HOME, else use the
# kit dir when writable, else a cache dir. The API dump is ~11 MB, so it lives
# here too rather than being regenerated per run.
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
  if _is_writable "$ROOT/.tooling"; then
    STATE="$ROOT/.tooling"
  else
    STATE="${XDG_CACHE_HOME:-$HOME/.cache}/godot-lens"
  fi
fi
if ! mkdir -p "$STATE" 2>/dev/null || [[ ! -w "$STATE" ]]; then
  echo "godot_api: no writable state directory (tried $ROOT/.tooling and $STATE)." >&2
  echo "Set GODOT_LENS_HOME to a writable path and retry." >&2
  exit 3
fi
DUMP_DIR="$STATE/api-dump"

export XDG_CONFIG_HOME="$STATE/godot_home/config"
export XDG_DATA_HOME="$STATE/godot_home/data"
export XDG_CACHE_HOME="$STATE/godot_home/cache"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$DUMP_DIR"

installed_version() { "$GODOT" --version 2>/dev/null | head -1; }

cmd_dump() {
  echo "Dumping API from: $(installed_version)"
  # Must run inside DUMP_DIR: Godot writes extension_api.json to CWD.
  ( cd "$DUMP_DIR" && timeout 600 "$GODOT" --headless \
      --dump-extension-api-with-docs 2>&1 | grep -vE 'Cannot (create|save)|at: save|at: _gen_doc' )
  local f="$DUMP_DIR/extension_api.json"
  [[ -f "$f" ]] || { echo "FAILED: $f was not produced" >&2; exit 1; }
  local v
  v="$(python3 -c "import json;print(json.load(open('$f'))['header']['version_full_name'])")"
  echo "OK: $f"
  echo "    version: $v  size: $(du -h "$f" | cut -f1)"
  echo "$v" > "$DUMP_DIR/.version"
}

cmd_check() {
  local f="$DUMP_DIR/extension_api.json"
  [[ -f "$f" ]] || { echo "no dump yet — run: tools/godot_api.sh dump"; exit 1; }
  local dump_v installed_v
  dump_v="$(python3 -c "import json;print(json.load(open('$f'))['header']['version_full_name'])")"
  installed_v="$(installed_version)"
  echo "dump:      $dump_v"
  echo "installed: $installed_v"
  # Compare the version triple only; build hash/status wording may differ.
  local a b
  a="$(python3 -c "import json;h=json.load(open('$f'))['header'];print(f\"{h['version_major']}.{h['version_minor']}.{h['version_patch']}\")")"
  b="$(sed -E 's/^([0-9]+\.[0-9]+\.[0-9]+).*/\1/' <<<"$installed_v")"
  if [[ "$a" == "$b" ]]; then
    echo "MATCH — API reference is authoritative for this engine."
    exit 0
  fi
  echo "MISMATCH — regenerate with: tools/godot_api.sh dump" >&2
  exit 1
}

case "${1:-}" in
  dump)  cmd_dump ;;
  check) cmd_check ;;
  *) echo "usage: godot_api.sh {dump|check}"; exit 2 ;;
esac
