#!/usr/bin/env bash
# godot_scene.sh — inspect a Godot scene's node tree as JSON, headless.
#
# Answers "what is actually in this scene", which is the question static
# analysis of .gd files can never answer.
#
#   tools/godot_scene.sh Main.tscn              # what the .tscn declares
#   tools/godot_scene.sh Main.tscn --runtime    # what exists after _ready()
#   tools/godot_scene.sh --all                  # every scene, static
#   tools/godot_scene.sh --all --runtime        # every scene, runtime
#
# The two views genuinely differ. A scene whose root declares no children may
# build its whole UI in _ready(); a static-only look shows one empty node.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$(python3 "$ROOT/tools/project_path.py")"
SCRIPT="$ROOT/tools/godot/scene_tree_dump.gd"
GODOT="${GODOT:-godot}"

# Godot needs a WRITABLE home. The kit directory may be read-only (installed
# system-wide, shared, in a container, or under a restrictive sandbox), so this
# is overridable and falls back to a user cache dir rather than assuming the
# kit's own directory is writable.
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
  cat >&2 <<EOF
godot_scene: no writable state directory.

Godot must be able to write its config/data dirs or it aborts with a bare
signal 11 that looks like an engine bug. Tried:
  kit dir : $ROOT/.tooling
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

RUNTIME_FLAG=()
SCENES=()
ALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime) RUNTIME_FLAG=(--runtime); shift ;;
    --all)     ALL=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *)         SCENES+=("$1"); shift ;;
  esac
done

if [[ $ALL -eq 1 ]]; then
  while IFS= read -r f; do
    SCENES+=("res://${f#./}")
  done < <(cd "$PROJECT" && find . -name '*.tscn' -not -path './.godot/*' -not -path './addons/*' | sort)
fi

[[ ${#SCENES[@]} -eq 0 ]] && { echo "usage: godot_scene.sh <scene.tscn|--all> [--runtime]" >&2; exit 2; }

# Godot prints warnings to the same stream as our JSON; only take the payload.
extract_json() {
  sed -n '/<<<SCENE_JSON_BEGIN>>>/,/<<<SCENE_JSON_END>>>/p' \
    | grep -vE '<<<SCENE_JSON_(BEGIN|END)>>>'
}

FAILED=0
for s in "${SCENES[@]}"; do
  rel="${s#res://}"
  [[ "$s" == res://* ]] || rel="$s"
  # Normalise a bare filename or path into a res:// path.
  if [[ "$s" != res://* ]]; then
    s="res://${rel#./}"
  fi

  out="$(cd "$PROJECT" && timeout 120 "$GODOT" --headless --script "$SCRIPT" -- "$s" "${RUNTIME_FLAG[@]}" 2>&1)"
  body="$(extract_json <<<"$out")"

  if [[ -z "$body" ]]; then
    FAILED=1
    echo "### $s  -- FAILED TO DUMP"
    grep -E 'SCRIPT ERROR|Parse Error|could not|ERROR' <<<"$out" | head -10
    continue
  fi

  echo "### $s"
  printf '%s\n' "$body"
done

exit $FAILED
