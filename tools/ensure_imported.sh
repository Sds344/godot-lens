#!/usr/bin/env bash
# ensure_imported.sh — guarantee Godot's asset import cache exists before observing.
#
# Why this is a shared script and not three copies
# ------------------------------------------------
# In a project whose assets have never been imported, `ResourceLoader.exists()`
# returns FALSE for a file that is plainly present on disk. Measured directly:
#
#     before import:  images/bg_room.svg -> ResourceLoader.exists = false
#     after import:   images/bg_room.svg -> ResourceLoader.exists = true
#
# Every observer that loads a texture by path therefore reports "missing
# resource" for an asset the project actually has. That is a false positive about
# project CONTENT when the real situation is a missing BUILD STEP, and it is the
# worst kind of wrong: it sends an agent to edit files that are already correct.
#
# The import pass was first added to godot_validate.sh alone, which left
# godot_context.py and godot_scene.sh still reporting phantom missing textures —
# and `godot_scene.sh --runtime` was the most misleading of all, because it
# reported every sprite's texture as null. One implementation, called by every
# entry point, is what keeps those three from drifting apart again.
#
# Usage (from inside the project directory):
#   bash tools/ensure_imported.sh [--quiet]
#
# Exit code: 0 when the import cache exists (or was created), 1 when it could not
# be created. A non-zero exit is a warning to the caller, not a project defect.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GODOT="${GODOT:-godot}"
QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

say() { [[ $QUIET -eq 1 ]] || printf '%s\n' "$*"; }

# Already imported: nothing to do, and this is the common case.
if [[ -d ".godot/imported" ]]; then
  exit 0
fi

# --- Writability, checked BEFORE running the engine ---------------------------
# The import pass WRITES INTO THE PROJECT UNDER TEST (`.godot/`, and `.import`
# sidecars). That is a real mutation of something this tooling only claims to
# observe, so two things follow.
#
# First, it must not be attempted blindly. Godot's own failure for an unwritable
# `.godot/` is `ERROR: Cannot create file 'res://.godot/editor/...'. Check user
# write permissions.` — an engine-shaped error for a permissions problem, which
# is the same trap documented for the read-only HOME in LESSONS.md §4. Checking up
# front turns it into a sentence that names the cause.
#
# Second, it must announce itself. An import while the Godot EDITOR has the
# project open makes the editor reload its filesystem state, and if the project
# is being regenerated at the same moment the editor can land on a half-written
# `project.godot` and report "no main scene defined". That failure looks like a
# broken project and is actually a reload race, so the write is now stated rather
# than silent.
if ! mkdir -p .godot 2>/dev/null || ! ( : > .godot/.lens-write-probe ) 2>/dev/null; then
  say "godot-lens: .godot/ is not writable — skipping the asset import."
  say "            Textures loaded by path may report as missing, which is a"
  say "            build-step artefact and NOT evidence of content loss."
  exit 1
fi
rm -f .godot/.lens-write-probe 2>/dev/null

say "godot-lens: no .godot/imported — running one asset import pass."
say "            NOTE: this writes into the project under test (.godot/). If the"
say "            Godot editor has it open, the editor will reload its filesystem"
say "            state. Until this completes, textures loaded by path report as"
say "            missing even though the files exist, which looks like content loss."

# Bounded, and the bound matters: an observation tool that can block for ten
# minutes is unusable in a loop, and a hung import is indistinguishable from a
# hung project. 180s is generous for a project of ordinary size; a larger one
# reports the timeout as a build-step problem rather than hanging forever.
import_timeout="${GODOT_LENS_IMPORT_TIMEOUT:-180}"
import_log="$(timeout "$import_timeout" "$GODOT" --headless --import 2>&1)"
import_rc=$?

if [[ $import_rc -eq 124 ]]; then
  printf 'WARNING: asset import exceeded %ss and was stopped.\n' "$import_timeout" >&2
  printf '         Textures loaded by path may report as missing. This is a\n' >&2
  printf '         build-step result, not evidence about the project.\n' >&2
  printf '         Raise the bound with GODOT_LENS_IMPORT_TIMEOUT if the project\n' >&2
  printf '         is genuinely larger than that.\n' >&2
  exit 1
fi

if [[ ! -d ".godot/imported" ]]; then
  printf 'WARNING: asset import produced no .godot/imported directory.\n' >&2
  printf '         Textures loaded by path may report as missing. This is a\n' >&2
  printf '         build-step problem, not necessarily a project defect.\n' >&2
  printf '         Last import output:\n' >&2
  printf '%s\n' "$import_log" | tail -5 >&2
  exit 1
fi

exit 0
