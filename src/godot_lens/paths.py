"""Where everything lives. One place, so the portability bugs stay fixed.

The first version of this tooling hard-coded the project directory in six places
and only worked for the one project it was written for (LESSONS.md §11). Moving
it surfaced four defects at once. Every path decision now goes through this
module, and every failure is *reported with the fix* rather than raised as a
mystery.

Two layouts are supported:

* **Kit layout** (the normal case): a clone of this repository, with the
  interface in `src/godot_lens/` and the collectors in `tools/`. This is what
  `git clone && ./setup.sh` produces.
* **Installed layout**: `src/godot_lens/` copied into site-packages while
  `tools/` stays wherever the kit was checked out. Reachable by pointing
  `GODOT_LENS_KIT` at the kit root.

The collectors are located rather than imported. They are the parts whose
correctness was established by measurement, and the package must not require them
to be rewritten in order to exist.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

# Names of children that must exist for a directory to be the kit root. Checking
# for these rather than trusting a path turns a silent empty result into a loud,
# specific error.
_REQUIRED = (
    os.path.join("tools", "godot_validate.sh"),
    os.path.join("tools", "godot_context.py"),
)


def _is_kit_root(path):
    return bool(path) and all(
        os.path.exists(os.path.join(path, rel)) for rel in _REQUIRED)


def _candidates():
    """Kit roots to try, best first.

    1. `$GODOT_LENS_KIT` explicit override.
    2. Repo-local: `<kit>/src/godot_lens/paths.py` -> `<kit>`.
    3. Walk up from CWD looking for a kit (covers running from a subdirectory).
    """
    env = os.environ.get("GODOT_LENS_KIT")
    if env:
        yield os.path.abspath(env)

    # <kit>/src/godot_lens/paths.py -> up three levels is <kit>.
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.dirname(os.path.dirname(here))

    # Installed into site-packages, kit somewhere above CWD.
    cur = os.path.abspath(os.getcwd())
    while True:
        yield cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent


def kit_root():
    """Absolute path to the kit root, or None if it cannot be found.

    Returns None rather than a guessed path so callers can explain the fix. A
    wrong-but-plausible path is the worst outcome: every tool then reports
    confidently about a project nobody asked about.
    """
    for cand in _candidates():
        if _is_kit_root(cand):
            return cand
    return None


def require_kit():
    root = kit_root()
    if root is None:
        sys.exit(
            "godot-lens: could not locate the kit's tools/ directory.\n"
            "\n"
            "The Python package was found but the collectors were not. If you\n"
            "installed the package separately, point it at the checkout:\n"
            "\n"
            "    export GODOT_LENS_KIT=/path/to/godot-lens\n"
            "\n"
            "If you have not cloned the repository yet, do that first — the\n"
            "package alone cannot observe a project."
        )
    return root


def tool_path(name):
    """Absolute path to a collector, e.g. 'godot_validate.sh'."""
    return os.path.join(require_kit(), "tools", name)


def dumper_path():
    """The GDScript scene dumper handed to the engine."""
    return os.path.join(require_kit(), "tools", "godot", "scene_tree_dump.gd")


def benchmarks_dir():
    return os.path.join(require_kit(), "benchmarks")


def godot_binary():
    """The engine binary, or an error naming the fix.

    Two details matter here, both learned from the same class of bug:

    * A bare name is resolved with `shutil.which` so the failure is a clear
      message rather than a FileNotFoundError from deep inside a subprocess.
    * An explicit path is **not** pre-checked with `os.access(..., X_OK)`. A
      permission probe is not trustworthy on overlay mounts and `noexec`
      filesystems — the same trap that made `[[ -w ]]` report writable for a
      directory that rejected writes (LESSONS.md §4). `godot_version()` runs the
      binary, and a real execution either works or does not.
    """
    configured = os.environ.get("GODOT", "godot")
    if os.sep in configured or (os.altsep and os.altsep in configured):
        return configured
    found = shutil.which(configured)
    if found is None:
        sys.exit(
            f"godot-lens: engine binary not found (looked for {configured!r} on PATH).\n"
            "\n"
            "Install Godot 4.x, or point at a specific binary:\n"
            "\n"
            "    export GODOT=/path/to/godot\n"
        )
    return found


def godot_version():
    """Version string, or None when the engine cannot be run.

    Returning None rather than exiting keeps `godot-lens version` usable as the
    command that diagnoses an environment problem instead of being defeated by it.
    """
    try:
        p = subprocess.run([godot_binary(), "--version"],
                           capture_output=True, text=True, timeout=30)
    except PermissionError:
        sys.exit(f"godot-lens: {godot_binary()!r} is not executable.")
    except (OSError, subprocess.SubprocessError):
        return None
    first = (p.stdout + p.stderr).strip().splitlines()
    return first[0].strip() if first else None


def resolver():
    """Path to the shared project-discovery helper.

    It lives beside the collectors rather than in this package because the shell
    collectors call it as a subprocess. One implementation, two callers.
    """
    return os.path.join(require_kit(), "tools", "project_path.py")


def project(required=True):
    """Resolve the Godot project under test.

    Delegates to the shared resolver so the CLI and the collectors can never
    disagree about which project is being inspected — the failure mode that made
    every tool report confidently about a leftover scratch project.
    """
    p = subprocess.run([sys.executable, resolver()],
                       capture_output=True, text=True)
    found = p.stdout.strip()
    if required and not os.path.isfile(os.path.join(found, "project.godot")):
        sys.exit(
            f"godot-lens: no Godot project found (resolved to {found}).\n"
            "\n"
            "Run from inside a project, or point at one:\n"
            "\n"
            "    export GODOT_PROJECT=/path/to/project\n"
        )
    return found
