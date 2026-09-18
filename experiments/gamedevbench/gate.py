"""gate.py — can we trust a task on *our* Godot before we spend anything on it?

GameDevBench pins Godot 4.4.1 and its harness refuses any other version:

    SUPPORTED_GODOT_VERSION = "4.4.1"
    if detected != SUPPORTED_GODOT_VERSION: raise GodotVersionError(...)

We are on 4.5.1.  Running the benchmark across versions is defensible for an
*internal* B-versus-C comparison — both arms get the same engine and the same
tests, so the contrast still means something — but only if the tasks still
work.  A task whose reference solution no longer passes on 4.5.1 has no valid
success state at all: both arms would fail it, and a row of 0/0 measures
nothing while looking like a result.

So every candidate must clear one gate first, and the gate uses the benchmark's
own machinery rather than our opinion:

    reference solution  --(official test scene)-->  VALIDATION_PASSED

The invocation and the pass/fail contract are copied from
`gamedevbench/src/benchmark_runner.py` and `.../utils/validation.py`:

    godot [--headless] --import --quit --path <dir>
    godot [--headless] --path <dir> res://scenes/test.tscn
    ... output contains VALIDATION_PASSED or VALIDATION_FAILED: <reason>

A task that fails this gate is reported as unusable, with the reference
solution's own failure message as the reason.  Those messages are also the only
evidence we will ever have about what 4.5.1 changed, so they are written to
`data/gate.json` rather than discarded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TASKS_DIR = os.path.join(REPO, ".tooling", "gdb-tasks")
OUT = os.path.join(HERE, "data", "gate.json")

TEST_SCENE = "res://scenes/test.tscn"
PASSED = re.compile(r"VALIDATION_PASSED(?::\s*(.+))?")
FAILED = re.compile(r"VALIDATION_FAILED(?::\s*(.+))?")

IMPORT_TIMEOUT = 300
TEST_TIMEOUT = 300


def _env(workdir: str) -> dict:
    """Godot aborts (SIGABRT, signal 11) without absolute writable XDG paths."""
    state = os.path.join(workdir, ".xdg")
    env = dict(os.environ)
    for key, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                     ("XDG_CACHE_HOME", "cache")):
        path = os.path.join(state, sub)
        os.makedirs(path, exist_ok=True)
        env[key] = path
    env["XDG_RUNTIME_DIR"] = os.path.join(state, "run")
    os.makedirs(env["XDG_RUNTIME_DIR"], exist_ok=True)
    return env


def _run(cmd: list[str], workdir: str, timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=workdir, env=_env(workdir), text=True,
                           capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 127, str(exc)


def parse(output: str) -> tuple[bool, str]:
    for line in output.splitlines():
        m = PASSED.search(line.strip())
        if m:
            return True, (m.group(1) or "Validation passed").strip()
        m = FAILED.search(line.strip())
        if m:
            return False, (m.group(1) or "Validation failed").strip()
    return False, "no validation marker in output"


def gate_one(task: str, godot: str) -> dict:
    d = os.path.join(TASKS_DIR, task, "gt")
    if not os.path.isdir(d):
        return {"task": task, "gate": "NOT-FETCHED", "reason": f"no reference at {d}"}

    requires_display = False
    cfg = os.path.join(d, "task_config.json")
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8") as fh:
                requires_display = bool(json.load(fh).get("requires_display", False))
        except (OSError, ValueError):
            pass
    head = [] if requires_display else ["--headless"]

    rc, out = _run([godot] + head + ["--import", "--quit", "--path", d],
                   d, IMPORT_TIMEOUT)
    if rc not in (0, 1):                     # Godot's --import is noisy about rc
        return {"task": task, "gate": "IMPORT-FAILED", "reason": out.strip()[-300:]}

    rc, out = _run([godot] + head + ["--path", d, TEST_SCENE], d, TEST_TIMEOUT)
    ok, reason = parse(out)
    return {
        "task": task,
        "gate": "PASS" if ok else "FAIL",
        "reason": reason,
        "requires_display": requires_display,
        "tail": "" if ok else out.strip()[-400:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tasks", nargs="*")
    ap.add_argument("--godot", default=os.environ.get("GODOT_EXEC_PATH", "godot"))
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    tasks = args.tasks or sorted(os.listdir(TASKS_DIR)) if os.path.isdir(TASKS_DIR) else args.tasks
    if not tasks:
        raise SystemExit("gate: nothing to check. Run fetch.py first.")

    ver = subprocess.run([args.godot, "--version"], capture_output=True,
                         text=True).stdout.strip()
    print(f"engine: {ver}   (GameDevBench pins 4.4.1; this is an internal-contrast run)\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for r in pool.map(lambda t: gate_one(t, args.godot), tasks):
            results.append(r)
            flag = {"PASS": "  ok ", "FAIL": " FAIL", "NOT-FETCHED": "  -- "}.get(r["gate"], " ?? ")
            print(f"{flag} {r['task']:<12} {r['reason'][:88]}")

    passed = [r for r in results if r["gate"] == "PASS"]
    print(f"\ngate: {len(passed)}/{len(results)} usable on {ver}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"engine": ver, "expected_engine": "4.4.1", "results": results},
                  fh, indent=1)
    print(f"written: {os.path.relpath(OUT, REPO)}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
