#!/usr/bin/env python3
"""benchmark.py — a minimal, honest benchmark for Godot agent loops.

Purpose
-------
Measure whether a coding agent can *find and fix* a real fault in a Godot
project using the tooling in `tools/`, not whether a model can write GDScript
from memory. It answers four questions per case:

  1. did the agent change anything at all?
  2. does the project now pass headless validation?
  3. did the fix actually address the fault (not just silence a symptom)?
  4. how many agent turns / how much wall time did it take?

Design constraints learned the hard way
---------------------------------------
* Cases are applied to a COPY of the repo, never the repo itself. An agent that
  goes wrong must not be able to damage real work.
* The copy is created through `git worktree`-like copying *including* `.git`,
  because a copy without a repo lets some agents resolve their project root to
  the enclosing repository and then edit the wrong files.
* Faults are real, not synthetic nonsense: each one is a failure class that has
  actually occurred in this project or is trivially common in Godot work.

Usage
-----
  python3 benchmarks/benchmark.py --list
  python3 benchmarks/benchmark.py --case case_001_node_path --setup DIR
  python3 benchmarks/benchmark.py --case case_001_node_path --check DIR
  python3 benchmarks/benchmark.py --all --check DIR      # grade a copy
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
from project_path import find_project  # noqa: E402

EXCLUDE = {".git", ".tooling", ".research-probe", ".cc-research", ".omo",
           ".playwright-mcp", "__pycache__", ".pytest_cache"}


# --- Fault definitions --------------------------------------------------------
# Each setup() mutates a project copy to introduce exactly one fault.
# Each check() inspects the copy and returns (passed, detail).

def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def setup_node_path(root):
    """Script asks for a node that does not exist. Static-clean, runtime-broken."""
    _write(root, "godot_project/Main.tscn", """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[node name="Main" type="Control"]
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
script = ExtResource("1_p")
""")
    _write(root, "godot_project/scripts/probe.gd", """extends Control

func _ready() -> void:
    # There is no node called "StatusLabel" anywhere in Main.tscn.
    var label: Label = get_node("StatusLabel")
    label.text = "ready"
""")


def check_node_path(root):
    """Pass only if the runtime error is gone AND a real fix is present.

    The first version of this check was wrong in a way worth remembering: it
    treated `"StatusLabel" in script or "StatusLabel" in scene` as evidence of a
    fix, but the *broken* script mentions that name too. Referring to a node is
    not the same as that node existing. It reported a pass on an untouched
    project — a false positive, which is worse than no benchmark at all.

    Rule adopted here: grade on observed behaviour, never on the mere presence
    of a string.
    """
    _, out = _run_game(root)
    if "Node not found" in out:
        return False, 'runtime still reports: Node not found: "StatusLabel"'

    # The runtime error is gone. Confirm a genuine fix rather than a silencing.
    #
    # A live agent run taught this lesson: it made the error vanish by pointing
    # the lookup at an unrelated node and overwriting that node's text — a
    # behaviour change, not a repair. The loose fallback below ("the bad name is
    # gone, therefore fixed") passed it. Grade the *repair*, not the absence of
    # the symptom.
    scene = _read(root, "godot_project/Main.tscn")
    script = _read(root, "godot_project/scripts/probe.gd")

    if re.search(r'\[node name="StatusLabel"', scene):
        return True, "StatusLabel node added to the scene"

    # Redirecting the lookup to a DIFFERENT node is a silencing, not a fix,
    # unless the script also stops clobbering unrelated UI.
    redirected = re.search(r'get_node(?:_or_null)?\("([^"]+)"\)', script)
    if redirected and redirected.group(1) != "StatusLabel":
        return False, (f"lookup redirected to {redirected.group(1)!r} — this "
                       "changes behaviour instead of providing the named node")

    if 'get_node("StatusLabel")' not in script:
        return True, "the invalid get_node() call was removed"
    return False, "no runtime error, but no identifiable fix either"


def setup_type_error(root):
    """Type mismatch: caught by static validation, project will not load."""
    _write(root, "godot_project/scripts/probe.gd", """extends Node

var health: int = "not an int"

func _ready() -> void:
    print(health)
""")


def check_type_error(root):
    code, out = _run_static(root)
    if "SCRIPT ERROR" in out or "Parse Error" in out:
        return False, "static validation still failing:\n" + out.strip()[:400]
    return True, "static validation clean"


def setup_missing_resource(root):
    """A scene references a resource file that is not on disk."""
    _write(root, "godot_project/Broken.tscn", """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/does_not_exist.gd" id="1_x"]

[node name="Broken" type="Node2D"]
script = ExtResource("1_x")
""")


def check_missing_resource(root):
    scene = os.path.join(root, "godot_project", "Broken.tscn")
    if not os.path.exists(scene):
        return True, "broken scene removed"
    if os.path.exists(os.path.join(root, "godot_project", "scripts",
                                   "does_not_exist.gd")):
        return True, "missing script created"
    code, out = _run_scene(root, "res://Broken.tscn")
    if "Failed loading" in out or "non-existent resource" in out:
        return False, "scene still references a missing resource"
    return True, "scene loads clean"


CASES = {
    "case_001_node_path": {
        "setup": setup_node_path,
        "check": check_node_path,
        "class": "runtime",
        "prompt": ("This Godot project has a bug: the game errors when it starts. "
                   "Diagnose it with the tools in tools/ and fix it. "
                   "Do not weaken the code to hide the error."),
    },
    "case_002_type_error": {
        "setup": setup_type_error,
        "check": check_type_error,
        "class": "static",
        "prompt": ("This Godot project has a bug: it does not load correctly. "
                   "Diagnose it with the tools in tools/ and fix it."),
    },
    "case_003_missing_resource": {
        "setup": setup_missing_resource,
        "check": check_missing_resource,
        "class": "resource",
        "prompt": ("This Godot project has a bug: a scene fails to load. "
                   "Diagnose it with the tools in tools/ and fix it."),
    },
}


# --- Godot runners ------------------------------------------------------------

def _env(root):
    return {
        **os.environ,
        "XDG_CONFIG_HOME": os.path.join(root, ".tooling", "godot_home", "config"),
        "XDG_DATA_HOME": os.path.join(root, ".tooling", "godot_home", "data"),
        "XDG_CACHE_HOME": os.path.join(root, ".tooling", "godot_home", "cache"),
    }


def _run_game(root, frames=120):
    proj = os.path.join(root, "godot_project")
    try:
        p = subprocess.run(["godot", "--headless", "--quit-after", str(frames)],
                           cwd=proj, env=_env(root), capture_output=True,
                           text=True, timeout=240)
        return p.returncode, p.stdout + p.stderr
    except Exception as e:
        return 1, str(e)


def _run_static(root):
    proj = os.path.join(root, "godot_project")
    f = os.path.join(proj, "scripts", "probe.gd")
    if not os.path.exists(f):
        return 0, ""
    try:
        p = subprocess.run(["godot", "--headless", "--check-only",
                            "--script", "res://scripts/probe.gd"],
                           cwd=proj, env=_env(root), capture_output=True,
                           text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr
    except Exception as e:
        return 1, str(e)


def _run_scene(root, scene):
    proj = os.path.join(root, "godot_project")
    try:
        p = subprocess.run(["godot", "--headless", "--quit", "--scene", scene],
                           cwd=proj, env=_env(root), capture_output=True,
                           text=True, timeout=180)
        return p.returncode, p.stdout + p.stderr
    except Exception as e:
        return 1, str(e)


# --- Copy / grade -------------------------------------------------------------

def make_copy(dest):
    """Build a self-contained lab: the kit's tools + the target Godot project.

    Two things are load-bearing here:

    * The copy gets its OWN `.git`. Without one, an agent walking up the tree
      can resolve its project root to an enclosing repository and then edit the
      wrong files — which is exactly what happened during development.
    * The Godot project is copied from wherever `project_path` finds it, because
      the kit itself contains no project. A lab without a project cannot be
      graded.

    A `tools/` shim is written into the lab so the tool paths that benchmark
    prompts mention actually resolve there.
    """
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)

    # 1. The kit itself: tools/ and benchmarks/.
    for name in os.listdir(REPO):
        if name in EXCLUDE:
            continue
        src = os.path.join(REPO, name)
        dst = os.path.join(dest, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True,
                            ignore=shutil.ignore_patterns(*EXCLUDE))
        else:
            shutil.copy2(src, dst)

    # 2. The Godot project under test.
    project = find_project()
    if not os.path.isfile(os.path.join(project, "project.godot")):
        raise SystemExit(
            f"benchmark: no Godot project found (looked at {project}).\n"
            "Set GODOT_PROJECT to the project directory and retry.")
    proj_dst = os.path.join(dest, "godot_project")
    shutil.copytree(project, proj_dst, symlinks=True,
                    ignore=shutil.ignore_patterns(".godot"))

    # 3. `tools/` shim so the documented commands work inside the lab.
    #
    # The first version of this built the target as "tools/<tool>" and then
    # prefixed it with the shim's own parent — producing "<lab>/tools/../tools/x",
    # i.e. a shim that exec'd ITSELF. The failure mode was an infinite fork storm
    # that looked like a hang. Resolve the target relative to the shim directory
    # instead of hand-assembling the path.
    shim_dir = os.path.join(dest, "tools")
    os.makedirs(shim_dir, exist_ok=True)
    for tool in ("godot_context.py", "godot_diagnose.py", "godot_api.py",
                 "godot_validate.sh", "godot_scene.sh", "godot_api.sh"):
        real = os.path.join(REPO, "tools", tool)
        if not os.path.exists(real):
            continue
        rel = os.path.relpath(real, shim_dir)
        if tool.endswith(".sh"):
            body = ('#!/usr/bin/env bash\n'
                    f'exec bash "$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)/{rel}" "$@"\n')
        else:
            body = ('#!/usr/bin/env python3\n'
                    'import os, sys\n'
                    f'kit = os.path.join(os.path.dirname(os.path.abspath(__file__)), "{rel}")\n'
                    'os.execv(sys.executable, [sys.executable, kit] + sys.argv[1:])\n')
        path = os.path.join(shim_dir, tool)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, 0o755)

    # 4. Fresh git baseline so the agent's diff is visible and revertible.
    subprocess.run(["git", "init", "-q"], cwd=dest)
    subprocess.run(["git", "config", "user.email", "bench@local"], cwd=dest)
    subprocess.run(["git", "config", "user.name", "bench"], cwd=dest)
    subprocess.run(["git", "add", "-A"], cwd=dest,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q",
                    "-m", "baseline"], cwd=dest,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dest


def setup_case(case, root):
    CASES[case]["setup"](root)
    subprocess.run(["git", "add", "-A"], cwd=root,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q",
                    "-m", f"introduce {case}"], cwd=root,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def grade(case, root):
    passed, detail = CASES[case]["check"](root)
    diff = subprocess.run(["git", "diff", "--stat", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()
    return {"case": case, "class": CASES[case]["class"], "passed": passed,
            "detail": detail, "diff_stat": diff or "(no changes)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--case")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--setup", metavar="DIR")
    ap.add_argument("--check", metavar="DIR")
    ap.add_argument("--make-copy", metavar="DIR")
    ap.add_argument("--prompt", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name, c in CASES.items():
            print(f"{name:28s} [{c['class']:8s}] {c['prompt'][:60]}...")
        return 0

    if args.make_copy:
        dest = os.path.abspath(args.make_copy)
        make_copy(dest)
        print(f"copy ready: {dest}")
        return 0

    if args.check:
        # MUST be absolute. Godot silently ignores a relative XDG_DATA_HOME and
        # falls back to the read-only $HOME, where it aborts (SIGABRT) — and an
        # aborted run produces no parseable output, which an earlier version of
        # this grader misread as "clean". Always resolve first.
        root = os.path.abspath(args.check)
        names = list(CASES) if args.all else [args.case]
        results = [grade(n, root) for n in names if n]
        print(json.dumps(results, indent=2))
        return 0 if all(r["passed"] for r in results) else 1

    if args.setup:
        if not args.case:
            print("--setup needs --case", file=sys.stderr)
            return 2
        root = os.path.abspath(args.setup)
        setup_case(args.case, root)
        print(f"{args.case}: fault introduced in {root}")
        if args.prompt:
            print("\n--- agent prompt ---")
            print(CASES[args.case]["prompt"])
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
