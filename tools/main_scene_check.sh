#!/usr/bin/env bash
# main_scene_check.sh — is there a runnable main scene? Never boot the game blindly.
#
# Why this exists
# ---------------
# Godot's "run the project" path, when `run/main_scene` is missing or not a real
# scene, does not merely print an error. On Linux it raises an OS-level alert,
# which falls back to spawning `zenity` and puts a modal dialog on the user's
# desktop:
#
#     Error: Can't run project: no main scene defined in the project.
#     (zenity:31): dconf-CRITICAL **: unable to create file '/run/user/1000/...'
#
# `--headless` does NOT suppress this: headless disables rendering, not
# `OS::alert`. Clearing `DISPLAY` does not help either — zenity is still spawned,
# it simply fails to connect. Verified against Godot 4.5.1 on this machine.
#
# So a tool that boots the project to observe it can put a dialog on someone's
# screen as a side effect of doing its job. The fix is not to suppress the dialog
# but to never enter the state that raises it: check first, and report the reason
# as a finding instead of asking the engine to run something that cannot run.
#
# Note the asymmetry that makes this easy to miss: `godot --editor --quit`
# tolerates a missing main scene silently (exit 0), while `--quit-after` alerts.
# A project with no main scene therefore looks fine to one command and pops a
# dialog under another.
#
# Usage (from inside the project directory):
#   bash main_scene_check.sh          # exit 0 = runnable, prints nothing
#                                     # exit 1 = prints the reason on stdout
set -uo pipefail

cfg="project.godot"
if [[ ! -f "$cfg" ]]; then
  printf 'no project.godot in the current directory\n'
  exit 1
fi

# Parse `run/main_scene` from the [application] section. Tolerates surrounding
# whitespace and either quote style; Godot writes double quotes, but a
# hand-edited file may not.
target="$(python3 - "$cfg" <<'PY' 2>/dev/null
import re, sys
section = ""
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    s = line.strip()
    if s.startswith("[") and s.endswith("]"):
        section = s.strip("[]").strip()
        continue
    if section != "application":
        continue
    m = re.match(r'run/main_scene\s*=\s*["\']([^"\']*)["\']', s)
    if m:
        print(m.group(1))
        break
PY
)"

if [[ -z "$target" ]]; then
  printf 'project.godot has no application/run/main_scene, so there is nothing to run\n'
  exit 1
fi

if [[ "$target" == res://* ]]; then
  rel="${target#res://}"
else
  rel="$target"
fi

if [[ ! -f "$rel" ]]; then
  printf 'run/main_scene points at %s, which does not exist on disk\n' "$target"
  exit 1
fi

# A main scene must be a scene. Pointing `run/main_scene` at a script produces
# Godot's other refusal ("Can't load the script ... as it doesn't inherit from
# SceneTree or MainLoop"), which is equally unreachable and equally worth
# reporting as a project finding rather than triggering.
case "$rel" in
  *.tscn|*.scn) ;;
  *) printf 'run/main_scene points at %s, which is not a .tscn/.scn scene\n' "$target"; exit 1 ;;
esac

exit 0
