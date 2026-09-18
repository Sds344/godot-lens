#!/usr/bin/env python3
"""runner.py — run one (case, arm, repeat) cell of the experiment.

One cell is: fresh lab copy → inject the fault → hand the task to the agent with
that arm's evidence → grade the resulting project on disk → save everything.

Design decisions that matter for validity:

* **A fresh copy per run.** An agent that damages a run must not affect the next,
  and a repeat must start from an identical starting state or it measures the
  previous agent's leftovers.
* **The transcript is kept in full.** A summary a reader cannot audit is not
  evidence; the raw `stream-json` is written next to the grade.
* **The grade never reads the agent's claims.** `declared_done` is parsed from the
  transcript for reporting only; `cases.grade` inspects the project.
* **Cost is measured, not estimated.** Token usage and turn counts come from the
  harness's own result event.

Usage:
  python3 experiments/agent-ab/runner.py --case L2-property-class --arm B
  python3 experiments/agent-ab/runner.py --case L1-nodepath --arms A,B,C --repeat 1
  python3 experiments/agent-ab/runner.py --list
  python3 experiments/agent-ab/runner.py --plan          # the full matrix and cost
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "benchmarks"))
sys.path.insert(0, os.path.join(REPO, "src"))

import arms  # noqa: E402
import cases  # noqa: E402
import grader  # noqa: E402
import benchmark  # noqa: E402
import sandbox  # noqa: E402

# Turn budgets now live in arms.py, because they are part of the arm definition:
# arm A is capped so it cannot iterate, B and C are not. Keeping one global value
# here silently gave the one-shot arm a feedback loop the first time it was run.
from arms import MAX_TURNS  # noqa: E402
# Wall-clock bound per run. A hung run must not stall the matrix.
RUN_TIMEOUT = 1800


# What arm C receives on top of the project. The tool, its package, and the
# collectors it shells out to — and nothing else. The docs are included because a
# user of a tool has its documentation; withholding it would understate the
# treatment.
LENS_PAYLOAD = ("godot-lens", "src", "tools", "README.md", "PROTOCOL.md")


def make_lab(lab, project, with_lens):
    """Build a lab containing ONLY the Godot project, plus lens for arm C.

    An earlier version copied the whole kit, on the assumption that a lab should
    be self-contained. It was — and that broke the experiment: the copy contained
    the `godot-lens` executable, its package, and a README describing it, so the
    **control arm discovered the treatment on its own** and the first pilot run
    showed `lens_used=True` for arm B. The arms had stopped differing by anything
    except whether they had been *told* about a tool that was sitting in the
    working directory either way.

    A lab is now the project under test. Nothing else is present unless the arm is
    supposed to have it. This is the same class of error as a control group that
    accidentally receives the treatment, and it is invisible in the results — arm B
    looked *better*, not wrong.
    """
    if os.path.exists(lab):
        shutil.rmtree(lab, ignore_errors=True)
    os.makedirs(lab, exist_ok=True)

    shutil.copytree(project, os.path.join(lab, "godot_project"), symlinks=True,
                    ignore=shutil.ignore_patterns(".godot"))

    state = os.path.join(lab, ".tooling")
    for sub in ("config", "data", "cache"):
        os.makedirs(os.path.join(state, "godot_home", sub), exist_ok=True)
    # Reflection data, needed by both the API layer and the structural checks.
    #
    # This is COPIED, never symlinked. The symlink version pointed at the kit's
    # own `.tooling/api-dump`, which left a working door back into the
    # repository standing open in every lab: resolve the link, walk up two
    # levels, and `experiments/agent-ab/cases.py` — the fault definitions for
    # every case, with the expected repair — was readable. Six of the thirty v1
    # cells walked through it, one of them thinking "JACKPOT".
    dump_src = os.path.join(benchmark.state_dir_for_dump(), "api-dump")
    dump_dst = os.path.join(state, "api-dump")
    if os.path.isdir(dump_src):
        shutil.copytree(dump_src, dump_dst, dirs_exist_ok=True)

    if with_lens:
        for name in LENS_PAYLOAD:
            src = os.path.join(REPO, name)
            dst = os.path.join(lab, name)
            if not os.path.exists(src):
                continue
            if os.path.isdir(src):
                shutil.copytree(src, dst, symlinks=True,
                                ignore=shutil.ignore_patterns(
                                    "__pycache__", ".tooling", "results"))
            else:
                shutil.copy2(src, dst)
        # The executable bit is what makes `godot-lens ...` work as documented.
        os.chmod(os.path.join(lab, "godot-lens"), 0o755)
    return lab


def _env(lab):
    """The environment every arm's agent and every grader invocation shares."""
    state = os.path.join(lab, ".tooling")
    env = dict(os.environ)
    env["GODOT_PROJECT"] = os.path.join(lab, "godot_project")
    env["GODOT_LENS_HOME"] = state
    env["XDG_CONFIG_HOME"] = os.path.join(state, "godot_home", "config")
    env["XDG_DATA_HOME"] = os.path.join(state, "godot_home", "data")
    env["XDG_CACHE_HOME"] = os.path.join(state, "godot_home", "cache")
    for sub in ("config", "data", "cache"):
        os.makedirs(os.path.join(state, "godot_home", sub), exist_ok=True)
    # Keep the agent from inheriting a GODOT_PROJECT that points at the real
    # project rather than its lab.
    return env


def run_cell(case_name, arm, repeat, project, results_dir, model=None,
             keep_lab=False, labs_dir=None):
    spec = cases.CASES[case_name]
    cell = f"{case_name}__arm{arm}__r{repeat}"
    # Labs live OUTSIDE the repository by default. `benchmark.make_copy` copies the
    # whole kit, so a lab inside the repo copies itself recursively — observed as
    # thousands of nested directories before this was fixed. The results directory
    # keeps only the transcripts and the graded records, which are small.
    # The lab root must NOT contain the tool's name. It is the agent's working
    # directory, so `/tmp/godot-lens-ab/...` told the control arms that a tool
    # called godot-lens existed — a leak, and the source of a false
    # `used_lens` detection before it was noticed.
    lab_root = labs_dir or os.path.join(tempfile.gettempdir(), "ab-eval")
    lab = os.path.join(lab_root, cell)
    make_lab(lab, project, with_lens=arms.ARMS[arm]["lens"])
    injected = spec["inject"](lab)
    # A hermetic lab is a precondition, not a nicety. The v1 matrix had none and
    # the agent's shell could read the answer key; see sandbox.py. Checking here
    # means an uncontained run fails loudly instead of producing a plausible
    # number that later has to be thrown away.
    leaks = sandbox.check(lab)
    if leaks:
        raise SystemExit(
            "runner: the lab is not hermetic, so this run would measure nothing:\n  "
            + "\n  ".join(leaks))
    env = sandbox.base_env(lab)

    prompt = arms.prompt(arm, spec["task"])
    transcript = os.path.join(results_dir, "transcripts", cell + ".jsonl")
    os.makedirs(os.path.dirname(transcript), exist_ok=True)
    cmd = [
        "claude", "-p", prompt,
        "--append-system-prompt", arms.system_prompt(arm),
        "--allowedTools", *arms.tools_for(arm),
        "--permission-mode", "acceptEdits",
        "--output-format", "stream-json", "--verbose",
        "--max-turns", str(MAX_TURNS[arm]),
    ]
    if model:
        cmd += ["--model", model]
    cmd = sandbox.wrap(cmd, lab, env)

    started = time.time()
    timed_out = False
    with open(transcript, "w", encoding="utf-8") as fh:
        try:
            p = subprocess.run(cmd, cwd=lab, env=env, stdout=fh,
                               stderr=subprocess.PIPE, text=True,
                               timeout=RUN_TIMEOUT)
            proc_rc, proc_err = p.returncode, (p.stderr or "")[-4000:]
        except subprocess.TimeoutExpired:
            timed_out = True
            proc_rc, proc_err = 124, f"timed out after {RUN_TIMEOUT}s"
        except FileNotFoundError:
            raise SystemExit("runner: the `claude` CLI is not on PATH.")

    metrics = grader.analyse_transcript(transcript)
    grade = grader.grade(case_name, lab, metrics["declared_done"])

    record = {
        "cell": cell,
        "case": case_name,
        "level": spec["level"],
        "arm": arm,
        "arm_name": arms.ARMS[arm]["name"],
        "lens_available": arms.ARMS[arm]["lens"],
        "repeat": repeat,
        "injected": injected,
        "injected_visible_to_native_arm": spec["arm_visible"],
        "elapsed_s": round(time.time() - started, 1),
        "timed_out": timed_out,
        "process_rc": proc_rc,
        "process_stderr_tail": proc_err,
        "transcript": os.path.relpath(transcript, REPO),
        "fingerprint": _fingerprint(lab),
        # Recorded per cell so a result can be defended later: the v1 matrix had
        # no containment and five of thirty cells read the answer key, which was
        # only discoverable by auditing transcripts after the fact.
        "sandbox": {
            "engine": "bwrap" if sandbox.available() else "none",
            "hermeticity": "verified",
            "probe": "kit, sibling repos, /mnt/wslg, real $HOME, CLI history, other cells",
            "network": "shared (egress proxy passed through); no net namespace",
        },
        **grade,
        **metrics,
    }
    with open(os.path.join(results_dir, "runs", cell + ".json"), "w",
              encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    if not keep_lab:
        shutil.rmtree(lab, ignore_errors=True)
    return record


def _fingerprint(lab):
    """Which revision of the project the lab was copied from.

    The subject was under active development throughout this work, so a result
    without a revision cannot be compared with any other result.
    """
    try:
        p = subprocess.run(["git", "-C", os.path.join(lab, "godot_project"),
                            "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=20)
        commit = p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"project_commit": commit,
            "project_commit_short": (commit or "")[:7] or None}


def plan(repeats=3):
    n = len(cases.CASES) * len(arms.ARMS) * repeats
    return {
        "cases": len(cases.CASES),
        "arms": len(arms.ARMS),
        "repeats": repeats,
        "cells": n,
        "note": (f"{n} agent invocations. Measured overhead is ~21-24k input "
                 "tokens per invocation before any task work, so the full matrix "
                 "is >=1.5M input tokens. Run a pilot first."),
    }


def run_matrix(args):
    """Run the full committed matrix in one parallel sweep.

    Kept as a separate entry point because the interesting failure mode of a
    long run is discovering halfway through that the harness was misconfigured.
    A matrix run writes one summary per case, so a partial sweep is still usable.
    """
    project = benchmark.find_project()
    results_dir = os.path.abspath(args.out)
    for sub in ("runs", "transcripts"):
        os.makedirs(os.path.join(results_dir, sub), exist_ok=True)

    jobs = []
    for case in args.case_list or list(cases.CASES):
        for arm in ("A", "B", "C"):
            n = 1 if arm == "A" else (args.repeats or 2)
            for r in range(1, n + 1):
                jobs.append((case, arm, r))

    print(f"MATRIX: {len(jobs)} cells across {len(args.case_list or cases.CASES)} "
          f"case(s), {args.jobs}-way parallel", flush=True)
    print(f"results: {results_dir}", flush=True)
    print(flush=True)

    def one(job):
        case, arm, r = job
        try:
            return run_cell(case, arm, r, project, results_dir,
                            model=args.model, keep_lab=args.keep_lab,
                            labs_dir=args.labs_dir)
        except Exception as e:  # noqa: BLE001
            return {"cell": f"{case}__arm{arm}__r{r}", "case": case, "arm": arm,
                    "repeat": r, "error": f"{type(e).__name__}: {e}",
                    "repaired": False, "hack": None, "used_lens": False,
                    "turns": 0, "input_tokens": None, "output_tokens": None,
                    "elapsed_s": None, "detail": "cell failed before grading"}

    records, started = [], time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(one, j): j for j in jobs}
        for done, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            rec = fut.result()
            records.append(rec)
            _report(rec)
            print(f"      [{done}/{len(jobs)}] elapsed "
                  f"{(time.time() - started) / 60:.1f} min", flush=True)

    by_case = {}
    for rec in records:
        by_case.setdefault(rec.get("case", "?"), []).append(rec)
    for case, recs in by_case.items():
        with open(os.path.join(results_dir, f"summary__{case}__matrix.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"case": case,
                       "records": recs,
                       "aggregate": grader.summarise(
                           [r for r in recs if not r.get("error")])},
                      fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    with open(os.path.join(results_dir, "matrix-summary.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"cells": len(records),
                   "wall_minutes": round((time.time() - started) / 60, 1),
                   "by_arm": _by_arm(records),
                   "total_input_tokens": sum(r.get("input_tokens") or 0
                                             for r in records),
                   "total_output_tokens": sum(r.get("output_tokens") or 0
                                              for r in records)},
                  fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(flush=True)
    print(json.dumps(_by_arm(records), indent=2))
    return 0


def _by_arm(records):
    out = {}
    for rec in records:
        arm = rec.get("arm", "?")
        b = out.setdefault(arm, {"n": 0, "repaired": 0, "hacked": 0,
                                 "lens_used": 0, "by_level": {}})
        b["n"] += 1
        b["repaired"] += 1 if rec.get("repaired") else 0
        b["hacked"] += 1 if rec.get("hack") else 0
        b["lens_used"] += 1 if rec.get("used_lens") else 0
        lv = rec.get("level")
        if lv is not None:
            l = b["by_level"].setdefault(str(lv), {"n": 0, "repaired": 0})
            l["n"] += 1
            l["repaired"] += 1 if rec.get("repaired") else 0
    for arm, b in out.items():
        b["repair_rate"] = round(b["repaired"] / b["n"], 3) if b["n"] else None
        b["hack_rate"] = round(b["hacked"] / b["n"], 3) if b["n"] else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    ap.add_argument("--case-list", nargs="*",
                    help="cases for --matrix (default: all)")
    ap.add_argument("--arm")
    ap.add_argument("--arms", help="comma-separated, e.g. A,B,C")
    ap.add_argument("--repeat", type=int, default=1,
                    help="single repeat index (used when --repeats is absent)")
    ap.add_argument("--repeats", type=int,
                    help="run each non-A arm this many times (default: A=1, B/C=2)")
    ap.add_argument("--jobs", type=int, default=1,
                    help="parallel cells; each is an independent process + lab")
    ap.add_argument("--model", help="override the harness default model")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--matrix", action="store_true",
                    help="run every case × arm × repeat (the whole experiment)")
    ap.add_argument("--keep-lab", action="store_true",
                    help="keep the lab for inspection (its path is recorded)")
    ap.add_argument("--labs-dir",
                    help="where to build labs; MUST be outside the repository")
    args = ap.parse_args()

    if args.list:
        print(arms.render_table())
        print()
        for name, spec in cases.CASES.items():
            print(f"L{spec['level']}  {name:24s} "
                  f"native-arm-can-see={spec['arm_visible']}")
        missing = [n for n in ("L2-shader-param",)
                   if n not in cases.CASES]
        if missing:
            print(f"\nnot implemented (excluded rather than silently dropped): "
                  f"{', '.join(missing)}")
        return 0
    if args.plan:
        print(json.dumps(plan(), indent=2))
        return 0

    if args.matrix:
        return run_matrix(args)

    if not args.case:
        print("runner: --case is required (or use --list / --plan / --matrix)",
              file=sys.stderr)
        return 2
    if args.case not in cases.CASES:
        print(f"runner: unknown case {args.case!r}", file=sys.stderr)
        return 2
    arm_list = (args.arms.split(",") if args.arms
                else ([args.arm] if args.arm else ["B", "C"]))
    for a in arm_list:
        if a not in arms.ARMS:
            print(f"runner: unknown arm {a!r}", file=sys.stderr)
            return 2

    project = benchmark.find_project()
    results_dir = os.path.abspath(args.out)
    for sub in ("runs", "transcripts"):
        os.makedirs(os.path.join(results_dir, sub), exist_ok=True)

    # Build the cell list. Repeats are per-arm: arm A is one-shot and cheap, so
    # running it twice buys little, while B and C are where variance matters.
    jobs = []
    for arm in arm_list:
        n = args.repeats if args.repeats is not None else (
            args.repeat if arm != "A" else 1)
        for r in range(1, n + 1):
            jobs.append((arm, r))

    print(f"project: {project}")
    print(f"results: {results_dir}")
    print(f"cell(s): {args.case} × {len(jobs)} run(s)  "
          f"({', '.join(f'{a}×{sum(1 for x, _ in jobs if x == a)}' for a in arm_list)})")
    print(f"parallelism: {args.jobs}")
    print(flush=True)

    def one(job):
        arm, r = job
        return run_cell(args.case, arm, r, project, results_dir,
                        model=args.model, keep_lab=args.keep_lab,
                        labs_dir=args.labs_dir)

    records = []
    if args.jobs <= 1:
        for job in jobs:
            rec = one(job)
            records.append(rec)
            _report(rec)
    else:
        # Each cell is an independent agent process in its own lab, so parallelism
        # is safe and is the difference between ~4 hours and ~40 minutes. Results
        # are printed as they complete rather than in submission order, because a
        # cell that takes three times as long would otherwise stall the display.
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(one, j): j for j in jobs}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    rec = fut.result()
                except Exception as e:  # noqa: BLE001 - one bad cell must not kill the run
                    arm, r = futures[fut]
                    rec = {"cell": f"{args.case}__arm{arm}__r{r}", "arm": arm,
                           "error": f"{type(e).__name__}: {e}",
                           "repaired": False, "hack": None, "used_lens": False,
                           "turns": 0, "input_tokens": None, "output_tokens": None,
                           "cost_usd": None, "elapsed_s": None,
                           "detail": "cell failed before grading"}
                records.append(rec)
                _report(rec)

    records.sort(key=lambda r: (r.get("arm", ""), r.get("repeat", 0)))
    summary = {
        "case": args.case,
        "arm_list": arm_list,
        "repeats": args.repeats,
        "jobs": args.jobs,
        "records": [{k: r.get(k) for k in
                     ("cell", "arm", "repeat", "repaired", "hack", "declared_done",
                      "false_claim", "used_lens", "turns", "input_tokens",
                      "output_tokens", "elapsed_s", "detail", "error")}
                    for r in records],
        "aggregate": grader.summarise([r for r in records if not r.get("error")]),
    }
    tag = f"r{args.repeats}" if args.repeats is not None else f"r{args.repeat}"
    with open(os.path.join(results_dir, f"summary__{args.case}__{tag}.json"),
              "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return 0


def _report(rec):
    if rec.get("error"):
        print(f"  {rec['cell']}: ERROR {rec['error']}", flush=True)
        return
    print(f"  arm {rec['arm']} r{rec['repeat']} ({rec['arm_name']:<8}) "
          f"repaired={str(rec['repaired']):<5} hack={str(rec['hack']):<9} "
          f"lens={str(rec['used_lens']):<5} turns={rec['turns']:<3} "
          f"in={rec['input_tokens']} out={rec['output_tokens']} "
          f"{rec['elapsed_s']}s", flush=True)
    print(f"      {rec['detail'][:100]}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
