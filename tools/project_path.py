#!/usr/bin/env python3
"""project_path.py — find the Godot project, wherever this kit happens to live.

Every tool in this kit needs to locate the Godot project it is inspecting. The
first version hard-coded `<repo>/godot_project`, which made the kit unusable
anywhere else. Resolution order:

  1. $GODOT_PROJECT            — explicit override (a path, or env var)
  2. $GODOT_PROJECT_DIR        — same, kept for familiarity
  3. nearest ancestor of CWD containing project.godot
  4. nearest ancestor of this file containing project.godot
  5. nearest SIBLING directory of the kit containing project.godot
  6. <cwd>/godot_project       — the original convention, as a last resort

Step 5 is what makes the kit work when it is dropped INTO a project as
`<project>/godot-lens/`: the project is one level up and beside it.
"""
from __future__ import annotations

import os
import sys

MARKER = "project.godot"


def _has_project(path):
    return bool(path) and os.path.isfile(os.path.join(path, MARKER))


def _ancestors_with_project(start):
    cur = os.path.abspath(start)
    while True:
        if _has_project(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _ancestor_subdir(start, name):
    """Nearest ancestor containing a subdirectory `name` that is a project."""
    cur = os.path.abspath(start)
    while True:
        cand = os.path.join(cur, name)
        if os.path.isdir(cand) and os.path.abspath(cand) != os.path.abspath(start):
            if _has_project(cand):
                return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def kit_root():
    """Directory containing this file's parent (…/godot-lens)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def state_dir(create=True):
    """A writable directory for Godot's XDG homes and the API dump.

    Godot aborts with a bare signal 11 when its config/data dirs are not
    writable, and the failure looks like an engine bug rather than a permissions
    problem. This directory cannot be assumed writable — it may be installed
    system-wide, inside a container, or under a restrictive sandbox.

    Order: $GODOT_LENS_HOME (or the legacy $GODOT_KIT_HOME), then <lens>/.tooling
    if writable, then a user cache dir. Returns None when none can be created, so
    callers can explain the problem instead of crashing obscurely.
    """
    candidates = []
    for var in ("GODOT_LENS_HOME", "GODOT_KIT_HOME"):
        env = os.environ.get(var)
        if env:
            candidates.append(env)
            break
    candidates.append(os.path.join(kit_root(), ".tooling"))
    candidates.append(os.path.join(
        os.environ.get("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")),
        "godot-lens"))

    for path in candidates:
        if create:
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                continue
        if _is_writable(path):
            return path
    return None


def _is_writable(path):
    """Writability by actually writing a probe file.

    `os.access(path, os.W_OK)` is not trustworthy: on some filesystems
    (overlay mounts, restricted sandboxes) it reports True for a directory that
    rejects writes. Trusting it produced a state dir Godot could not use, and
    the resulting failure was a bare signal 11 — an engine-looking crash for a
    permissions problem. A real write either works or it does not.
    """
    probe = os.path.join(path, f".writable-probe-{os.getpid()}")
    try:
        with open(probe, "w"):
            pass
        os.unlink(probe)
        return True
    except OSError:
        return False


def find_project(start=None):
    """Return the absolute path to the Godot project directory.

    Returns a path even if it does not exist, so callers can produce a helpful
    error instead of a confusing crash.
    """
    for var in ("GODOT_PROJECT", "GODOT_PROJECT_DIR"):
        val = os.environ.get(var)
        if val:
            cand = os.path.abspath(val)
            # Accept the marker file itself: people naturally paste the path to
            # project.godot. Returning that path would break every caller.
            if os.path.basename(cand) == MARKER:
                return os.path.dirname(cand)
            if _has_project(cand):
                return cand
            if _has_project(os.path.join(cand, "godot_project")):
                return os.path.join(cand, "godot_project")
            return cand

    found = _ancestors_with_project(start or os.getcwd())
    if found:
        return found

    found = _ancestors_with_project(kit_root())
    if found:
        return found

    # A kit cloned INSIDE a repo that holds its project in a subdirectory, e.g.
    #   <repo>/godot-lens/          <- this kit
    #   <repo>/godot_project/       <- the project
    # Walking up for project.godot finds nothing (the project is a sibling, not
    # an ancestor), so look for the conventional directory name at each level on
    # the way up. Without this, a clone nested inside a repo silently resolves to
    # a non-existent path and every tool reports an empty project.
    found = _ancestor_subdir(kit_root(), "godot_project")
    if found:
        return found

    # Near the kit. Covers two shapes:
    #   <parent>/<game>/project.godot          — kit beside a project
    #   <parent>/<repo>/<game>/project.godot   — kit beside a REPO holding the
    #                                            project in a subdirectory
    #
    # Several candidates can match, and picking the wrong one is a silent
    # failure: the tools then report confidently about a project nobody asked
    # about. That happened during development, when a leftover scratch project
    # inside a hidden directory won the naive first-match race. So gather every
    # candidate, score them, and return the best rather than the first.
    parent = os.path.dirname(kit_root())
    kit = kit_root()
    found = _best_candidate(parent, kit)
    if found:
        return found

    return os.path.join(os.getcwd(), "godot_project")


# Directories that should never be treated as the project under test, even if
# they contain a project.godot. These are agent scratchpads, caches and vendor
# trees that exist in real repositories.
_SKIP_DIRS = {
    "node_modules", "vendor", "build", "dist", "target", "out",
    "tmp", "temp", "cache", "test", "tests", "fixtures", "samples",
    "examples", "addons",
}


def _candidate_score(path, kit):
    """Higher is a better guess. Name-based, deliberately conservative."""
    name = os.path.basename(os.path.normpath(path))
    score = 0
    # A directory literally named godot_project is the convention this kit grew
    # up with, so it wins ties against arbitrary names.
    if name == "godot_project":
        score += 10
    if name in ("game", "godot", "project"):
        score += 5
    if name.startswith("."):
        score -= 50           # hidden dirs are almost never the real project
    if name.lower() in _SKIP_DIRS:
        score -= 40
    # Prefer a project that does NOT sit inside another git repo's scratch area.
    if os.sep + "." in path.replace(kit, ""):
        score -= 20
    return score


def _best_candidate(parent, kit):
    best, best_score = None, None
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return None
    for name in names:
        cand = os.path.join(parent, name)
        if not os.path.isdir(cand) or os.path.abspath(cand) == kit:
            continue
        candidates = []
        if _has_project(cand):
            candidates.append(cand)
        else:
            try:
                for sub in sorted(os.listdir(cand)):
                    deep = os.path.join(cand, sub)
                    if sub.startswith(".") or not os.path.isdir(deep):
                        continue
                    if os.path.abspath(deep) == kit:
                        continue
                    if _has_project(deep):
                        candidates.append(deep)
            except OSError:
                pass
        for c in candidates:
            s = _candidate_score(c, kit)
            if best_score is None or s > best_score:
                best, best_score = c, s
    return best


def main():
    """CLI so shell tools can share this logic instead of reimplementing it."""
    args = sys.argv[1:]
    if args and args[0] == "--kit-root":
        print(kit_root())
    elif args and args[0] == "--exists":
        p = find_project()
        print(p)
        return 0 if _has_project(p) else 1
    else:
        print(find_project())
    return 0


if __name__ == "__main__":
    sys.exit(main())
