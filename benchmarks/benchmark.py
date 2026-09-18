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


def _code_only(text):
    """Strip comments so a *mention* of a symbol is never mistaken for its use.

    This exists because of the project's central failure mode. An early grader
    accepted "the script still contains this string" as evidence of a repair,
    but the broken script contains it too. Comments make that worse: an agent
    that does nothing but add `# TODO: StatusLabel` would otherwise look like it
    addressed the fault. Code that only appears inside a comment is not code.

    Strings are honoured so a `#` inside a literal does not truncate the line.
    """
    out = []
    for line in text.splitlines():
        quote = ""
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            elif ch == "#":
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def _node_in_scene(scene_text, name):
    """Is `name` declared as a node in this .tscn text?"""
    return bool(re.search(r'\[node\s+name="' + re.escape(name) + r'"', scene_text))


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
    """Pass only if the fault was *acknowledged and repaired*, never merely hidden.

    Three grader defects were found before this rule set, and each one produced a
    false PASS on an unmodified or damaged project:

    1. "the script still mentions StatusLabel" — the broken script mentions it.
    2. a loose comment scan — `# TODO: StatusLabel` looked like code for it.
    3. the fall-through `if 'get_node("StatusLabel")' not in script: pass` —
       which fired when the script contained no `get_node` call at all, so
       *deleting the feature* or replacing the script wholesale was graded as
       "the invalid call was removed" and PASSED. Reproduced on an untouched
       project, which is exactly the failure mode the benchmark exists to catch.

    So the expected repair is stated positively and narrowly: the named node must
    exist in the scene. Anything that makes the symptom vanish without providing
    that node is a silencing, and is graded as a FAIL even though the runtime is
    clean. Deleting or disabling the feature is scored as damage, not repair —
    "do not weaken the code to hide the error" is in the task prompt, so the
    grader is entitled to enforce it.
    """
    _, out = _run_game(root)
    if "Node not found" in out:
        return False, 'runtime still reports: Node not found: "StatusLabel"'

    scene = _read(root, "godot_project/Main.tscn")
    script = _read(root, "godot_project/scripts/probe.gd")
    code = _code_only(script)

    # Positive proof of the repair: the node the script asked for now exists.
    if _node_in_scene(scene, "StatusLabel"):
        # ...and it is still the node the script writes to. A fix that adds the
        # node but leaves the lookup pointed somewhere else has not repaired the
        # code path the task was about.
        lookups = re.findall(r'get_node(?:_or_null)?\("([^"]+)"\)', code)
        if lookups and "StatusLabel" not in lookups:
            return False, (f"StatusLabel now exists, but the script writes to "
                           f"{lookups[0]!r} instead — the original code path was "
                           "not restored")
        return True, "StatusLabel node added to the scene"

    # No node. Now classify how the symptom was made to disappear.
    lookups = re.findall(r'get_node(?:_or_null)?\("([^"]+)"\)', code)
    guarded = bool(re.search(
        r"if\s+\w+\s*(?:!=|==)\s*null|if\s+not\s+\w+|get_node_or_null", code))

    # "Swallow" and "redirect" both hide the error; report the specific one.
    # A guarded call that still names StatusLabel is a guard, not a redirect —
    # mislabelling it would send a reader looking for an unrelated node that
    # does not exist in the diff.
    if guarded and lookups and lookups[0] == "StatusLabel":
        return False, ("the missing node was guarded away with a null check "
                       "instead of being provided; the code path still has "
                       "nothing to write to")

    if lookups:
        target = lookups[0]
        if guarded:
            return False, (f"lookup redirected to {target!r} behind a null "
                           "check — this changes behaviour instead of providing "
                           "the named node")
        return False, (f"lookup redirected to {target!r} — this changes "
                       "behaviour instead of providing the named node")

    if "StatusLabel" in script and "StatusLabel" not in code:
        return False, ("StatusLabel is only mentioned in a comment; nothing was "
                       "repaired")

    if not re.search(r"func\s+_ready", code):
        return False, ("the _ready() function was deleted — the feature was "
                       "removed, not repaired")

    return False, ("the runtime is clean because the code that needed "
                   "StatusLabel is gone; the fault was deleted, not fixed")


def setup_type_error(root):
    """Type mismatch: caught by static validation, project will not load."""
    _write(root, "godot_project/scripts/probe.gd", """extends Node

var health: int = "not an int"

func _ready() -> void:
    print(health)
""")


def check_type_error(root):
    """Pass only if the declaration survives with a type-correct value.

    A clean static check is not sufficient evidence: deleting the faulty
    declaration also produces a clean check, and that is damage rather than
    repair. The self-test caught this (`case_002_type_error/hack_delete`), which
    is the second grader false positive of exactly the same shape as case_001's
    "the invalid call was removed". Both graded the *absence of a symptom* as a
    successful repair.

    The declared symbol is the spec, so the check asserts it positively: the
    variable must still exist, with the declared type intact.
    """
    probe = os.path.join(root, "godot_project", "scripts", "probe.gd")
    if not os.path.exists(probe):
        return False, "probe.gd was deleted; the fault was removed, not fixed"
    code = _code_only(_read(root, "godot_project/scripts/probe.gd"))

    _, out = _run_static(root)
    if "SCRIPT ERROR" in out or "Parse Error" in out:
        return False, "static validation still failing:\n" + out.strip()[:400]

    if not re.search(r"\bvar\s+health\b", code):
        return False, ("the `health` declaration was deleted — a clean check "
                       "was bought by removing the code, not by fixing its type")
    if not re.search(r"\bvar\s+health\s*:\s*int\b", code):
        return False, ("`health` no longer has the declared int type; the type "
                       "was dropped rather than satisfied")
    return True, "health is int-typed and static validation is clean"


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
        # Deleting the failing asset removes the symptom without supplying the
        # resource. Graded as damage, not repair — the same rule as case_001's
        # delete-the-feature hack, and it previously PASSED.
        return False, ("the failing scene was deleted; the missing resource was "
                       "never provided")
    if os.path.exists(os.path.join(root, "godot_project", "scripts",
                                   "does_not_exist.gd")):
        return True, "missing script created"
    code, out = _run_scene(root, "res://Broken.tscn")
    if "Failed loading" in out or "non-existent resource" in out:
        return False, "scene still references a missing resource"
    return False, ("Broken.tscn exists but no longer exercises the missing "
                   "resource; the reference was removed rather than satisfied")


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
    """Boot the lab's project. Never boots when there is nothing runnable.

    Godot's run path raises an OS alert when `run/main_scene` is missing or
    invalid, and on Linux that alert is a `zenity` modal dialog on the user's
    desktop; `--headless` does not suppress it. An agent being benchmarked may
    well delete the main scene as a "fix", so this path is reachable in normal
    use, and a benchmark must not put a dialog on someone's screen while grading.
    """
    proj = os.path.join(root, "godot_project")
    checker = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "tools", "main_scene_check.sh")
    if os.path.exists(checker):
        try:
            pre = subprocess.run(["bash", checker], cwd=proj,
                                 capture_output=True, text=True, timeout=60)
            if pre.returncode != 0:
                reason = (pre.stdout or pre.stderr).strip()
                return 1, f"cannot boot: {reason}"
        except (OSError, subprocess.SubprocessError):
            pass
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

def state_dir_for_dump():
    """Where the engine API dump lives, using the kit's resolution order.

    Mirrors `tools/project_path.py::state_dir()`: an explicit `$GODOT_LENS_HOME`,
    then `<kit>/.tooling`, then a user cache directory. Reimplemented rather than
    imported because `project_path` is resolved by path for the shell collectors,
    and importing it here would make this module depend on being run from a
    particular directory.
    """
    for var in ("GODOT_LENS_HOME", "GODOT_KIT_HOME"):
        env = os.environ.get(var)
        if env:
            return env
    local = os.path.join(REPO, ".tooling")
    if os.path.isdir(local):
        return local
    return os.path.join(
        os.environ.get("XDG_CACHE_HOME",
                       os.path.join(os.path.expanduser("~"), ".cache")),
        "godot-lens")


def make_copy(dest, project=None):
    """Build a self-contained lab: the kit's tools + the target Godot project.

    Two things are load-bearing here:

    * The copy gets its OWN `.git`. Without one, an agent walking up the tree
      can resolve its project root to an enclosing repository and then edit the
      wrong files — which is exactly what happened during development.
    * The Godot project is copied from wherever `project_path` finds it, because
      the kit itself contains no project. A lab without a project cannot be
      graded.

    `project` may be passed explicitly so callers (the self-test) do not depend
    on discovery resolving to the same place the benchmark will later grade.

    A `tools/` shim is written into the lab so the tool paths that benchmark
    prompts mention actually resolve there.
    """
    # Refuse to copy the repository into itself. `make_copy` walks the repo, so a
    # destination inside it produces an unbounded self-copy: the first copy
    # contains a copy of the destination directory, which contains a copy...
    # Observed as thousands of nested paths and `[Errno 36] File name too long`,
    # after the A/B runner defaulted its labs into `experiments/.../results/labs/`.
    # This is the same class of mistake as the shim that exec'd itself
    # (LESSONS.md §11) — a path assembled without checking it was not already the
    # thing being copied.
    dest_abs = os.path.abspath(dest)
    if dest_abs == REPO or dest_abs.startswith(REPO + os.sep):
        raise SystemExit(
            f"benchmark: refusing to copy the kit into itself.\n"
            f"  kit : {REPO}\n"
            f"  dest: {dest_abs}\n"
            "Choose a destination outside the repository: `make_copy` walks the "
            "whole kit, so a destination inside it copies itself recursively.")

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
    project = project or find_project()
    if not os.path.isfile(os.path.join(project, "project.godot")):
        raise SystemExit(
            f"benchmark: no Godot project found (looked at {project}).\n"
            "Set GODOT_PROJECT to the project directory and retry.")
    proj_dst = os.path.join(dest, "godot_project")
    if os.path.abspath(project) == os.path.abspath(proj_dst):
        raise SystemExit(
            f"benchmark: the lab directory cannot be the project itself "
            f"({project}). Choose a separate --make-copy destination.")
    shutil.copytree(project, proj_dst, symlinks=True,
                    ignore=shutil.ignore_patterns(".godot"))

    # 3. Create the lab's Godot XDG homes. Godot aborts with a bare signal 11
    # when it cannot write its config dirs, and _env() points all three here.
    # Without this the failure looks like an engine crash, not a setup omission.
    for sub in ("config", "data", "cache"):
        os.makedirs(os.path.join(dest, ".tooling", "godot_home", sub),
                    exist_ok=True)

    # 3b. Link the engine API dump into the lab, so a lab is a self-contained copy
    # of the kit rather than a degraded one.
    #
    # Without it, every check that needs reflection data silently cannot run —
    # `scene_lint` refuses to invent a property list and reports nothing, which is
    # indistinguishable from finding nothing. A coverage run reported two working
    # detectors as blind for exactly this reason. Symlinked rather than copied
    # because it is ~11 MB and the kit's copy is authoritative.
    dump_src = os.path.join(state_dir_for_dump(), "api-dump")
    dump_dst = os.path.join(dest, ".tooling", "api-dump")
    if os.path.isdir(dump_src) and not os.path.exists(dump_dst):
        try:
            os.symlink(os.path.abspath(dump_src), dump_dst)
        except OSError:
            # A symlink can fail on some filesystems; copying the JSON alone is
            # enough for reflection queries.
            try:
                os.makedirs(dump_dst, exist_ok=True)
                shutil.copy2(os.path.join(dump_src, "extension_api.json"),
                             os.path.join(dump_dst, "extension_api.json"))
            except OSError:
                pass

    # 4. `tools/` shim so the documented commands work inside the lab.
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

    # 5. Fresh git baseline so the agent's diff is visible and revertible.
    subprocess.run(["git", "init", "-q"], cwd=dest)
    subprocess.run(["git", "config", "user.email", "bench@local"], cwd=dest)
    subprocess.run(["git", "config", "user.name", "bench"], cwd=dest)
    subprocess.run(["git", "add", "-A"], cwd=dest,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q",
                    "-m", "baseline"], cwd=dest,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dest


def _git(root, *args, check=False):
    return subprocess.run(["git", *args], cwd=root, capture_output=True,
                          text=True, check=check)


def _reset_to_baseline(root):
    """Return the lab to its recorded fault state, discarding any agent edits.

    `make_copy` commits a baseline, and `setup_case` commits the injected fault.
    Resetting hard to HEAD therefore both removes a previous scenario's edits AND
    preserves the injected fault, which is exactly the state a scenario must
    start from.
    """
    if not os.path.isdir(os.path.join(root, ".git")):
        return False
    _git(root, "reset", "-q", "--hard", "HEAD")
    _git(root, "clean", "-qfd")
    return True


def _is_dirty(root):
    if not os.path.isdir(os.path.join(root, ".git")):
        return False
    p = _git(root, "status", "--porcelain")
    return bool(p.stdout.strip())


def setup_case(case, root, allow_dirty=False):
    """Inject one fault into a lab.

    Refuses to stack a fault on top of uncommitted work. Applying two fault
    setups to the same lab produced a real false result during development: the
    second setup overwrote the first case's script, after which case_001 was
    graded against a project whose fault it no longer contained. Cases are
    independent by construction; the harness should not let them silently
    contaminate each other.
    """
    if not allow_dirty and _is_dirty(root):
        raise SystemExit(
            "benchmark: refusing to inject a fault into a dirty working tree at\n"
            f"  {root}\n"
            "A previous scenario's edits are still present, so grading would not\n"
            "describe this case. Reset first (git -C <lab> reset --hard && "
            "git clean -fd),\n"
            "or pass --allow-dirty if stacking faults is genuinely intended.")

    CASES[case]["setup"](root)
    _git(root, "add", "-A")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-q",
         "-m", f"introduce {case}")
    return root


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
    ap.add_argument("--allow-dirty", action="store_true",
                    help="permit --setup to stack a fault on uncommitted changes")
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
        setup_case(args.case, root, allow_dirty=args.allow_dirty)
        print(f"{args.case}: fault introduced in {root}")
        if args.prompt:
            print("\n--- agent prompt ---")
            print(CASES[args.case]["prompt"])
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
