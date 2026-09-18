#!/usr/bin/env python3
"""coverage.py — measure how large the observability blind spot is.

Answers a question that has to be settled before any agent experiment can be
interpreted: **of these fault classes, how many can the tooling see at all?**

Without this number, an A/B experiment showing "no improvement from using the
tooling" is ambiguous — it could mean the tooling does not help, or that the
injected faults were never visible to it. Those two findings call for opposite
responses, and the measurement removes the ambiguity.

It also reports the **false-positive rate**, which is the other way a checker can
be worthless: one that flags everything detects everything.

Nothing here involves a model. Injection is deterministic code and detection is a
string comparison, so there is no grader to distrust.

Usage:
  python3 benchmarks/coverage.py                 # run everything, report a matrix
  python3 benchmarks/coverage.py --json
  python3 benchmarks/coverage.py --fault NAME     # one class
  python3 benchmarks/coverage.py --keep           # keep the lab for inspection
Exit code: 0 while the measured result matches each class's documented
expectation; 1 when a class behaves differently than the registry says it does.
A `missed` class documented as `missed` is a pass — the measurement agreeing with
the documented limit is the point.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import benchmark  # noqa: E402
import mutations  # noqa: E402

GODOT_TIMEOUT = 240


def _run_detector(name, root, env):
    """Run one tool against the lab and return its combined output.

    The tools are run from the KIT, not from the lab, even though the lab is what
    is being measured. It locates its target through `GODOT_PROJECT`, so it does
    not need to be inside the lab — and running the lab's copied tools measured the
    wrong thing: the copy imported its helper modules relative to its own location,
    so it exercised a snapshot of the tooling rather than the version under test.
    Two newly added detectors reported as blind because of that, and the failure
    looked exactly like a real blind spot.

    Running from the kit also means a lab can never be graded by a stale copy of
    the grader, which is the same reason `selftest.py` imports the module directly
    instead of shelling out to a copy.
    """
    if name == "inspect":
        cmd = [sys.executable, os.path.join(REPO, "tools", "godot_context.py")]
    elif name == "diagnose":
        cmd = [sys.executable, os.path.join(REPO, "tools", "godot_diagnose.py"),
               "--json"]
    else:
        return f"(unknown detector {name})"
    try:
        p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True,
                           text=True, timeout=GODOT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return f"(detector {name} failed: {e})"
    return p.stdout + p.stderr


def _env(root):
    env = dict(os.environ)
    state = os.path.join(root, ".tooling")
    env["GODOT_LENS_HOME"] = state
    env["XDG_CONFIG_HOME"] = os.path.join(state, "godot_home", "config")
    env["XDG_DATA_HOME"] = os.path.join(state, "godot_home", "data")
    env["XDG_CACHE_HOME"] = os.path.join(state, "godot_home", "cache")
    for sub in ("config", "data", "cache"):
        os.makedirs(os.path.join(state, "godot_home", sub), exist_ok=True)
    env["GODOT_PROJECT"] = os.path.join(root, "godot_project")
    return env


def measure_fault(name, spec, lab, project):
    """Inject one fault into a fresh lab and record which detectors saw it."""
    if os.path.exists(lab):
        shutil.rmtree(lab, ignore_errors=True)
    benchmark.make_copy(lab, project)

    note = spec["inject"](lab)
    env = _env(lab)

    # Verify the injection did not break the lab. An invalid `.tscn` produces a
    # dump failure, and a dump failure is indistinguishable from "the tools saw
    # nothing" — the first coverage run reported a working rule as a blind spot
    # for exactly this reason (a `[sub_resource]` block placed after the node that
    # referenced it).
    #
    # The check is for `SCENE_DUMP_FAILED` specifically, not for "Parse Error"
    # anywhere in the output. Some fault classes ARE parse errors — a type
    # mismatch is supposed to fail the script's parse, and that is the fault being
    # measured, not a broken injection. Matching the broader string classified the
    # type-error fault as an injection bug and hid a real detection.
    dump = _run_detector("inspect", lab, env)
    injection_error = None
    if "SCENE_DUMP_FAILED" in dump:
        injection_error = ("the injected project does not load cleanly, so nothing "
                           "could be observed — this is a fault-injection bug, not "
                           "a blind spot")

    hits, misses = [], []
    for det in spec["detect"]:
        out = _run_detector(det["tool"], lab, env)
        if det["expect"] in out:
            hits.append(f"{det['tool']} matched {det['expect']!r}")
        else:
            misses.append(f"{det['tool']} did not match {det['expect']!r}")

    if injection_error:
        verdict = "error"
    else:
        verdict = "detected" if hits else "missed"
    expected = spec.get("expected", "detected")
    return {
        "fault": name,
        "hazard": spec["hazard"],
        "injected": note,
        "verdict": verdict,
        "detectors_hit": hits,
        "detectors_missed": misses,
        "expected": expected,
        "injection_error": injection_error,
        # An injection error can never agree with the registry: the measurement
        # did not happen, so claiming it did would be the worse outcome.
        "as_documented": (verdict != "error") and verdict == expected,
    }


def measure_false_positives(lab, project):
    """Run the detectors against an unmodified lab.

    A checker that reports findings on a healthy project detects everything and is
    worthless. This is measured rather than assumed, because it is the failure
    mode that makes a whole layer get ignored.
    """
    if os.path.exists(lab):
        shutil.rmtree(lab, ignore_errors=True)
    benchmark.make_copy(lab, project)
    env = _env(lab)
    out = _run_detector("diagnose", lab, env)
    try:
        diag = json.loads(out)
        items = diag.get("diagnostics", [])
    except json.JSONDecodeError:
        return {"error": "diagnose did not return JSON",
                "raw": out.strip().splitlines()[-5:]}
    # `SCENE_BUILT_AT_RUNTIME` is expected on the reference project: it is the
    # informational note that the .tscn declares nothing and code builds the tree.
    # It is not a defect, and counting it as a false positive would make the
    # number meaningless.
    benign = {"SCENE_BUILT_AT_RUNTIME"}
    unexpected = [d for d in items
                  if d.get("code") not in benign and d.get("severity") != "info"]
    return {
        "severity_error_or_warning": len(unexpected),
        "codes": sorted({d.get("code") for d in items}),
        "unexpected": [d.get("code") for d in unexpected],
    }


def render(results, fp, documented):
    lines = []
    lines.append("observability coverage — which fault classes can the tooling see?")
    lines.append("=" * 78)
    width = max(len(r["fault"]) for r in results)
    for r in results:
        mark = {"detected": "SEEN", "missed": "BLIND", "error": "ERROR"}[r["verdict"]]
        flag = "" if r["as_documented"] else "   <-- DIFFERS FROM REGISTRY"
        lines.append(f"  [{mark:5s}] {r['fault']:<{width}}  {r['hazard'][:44]}{flag}")
        for h in r["detectors_hit"]:
            lines.append(f"            caught by: {h}")
        if r.get("injection_error"):
            lines.append(f"            INJECTION ERROR: {r['injection_error']}")
    seen = sum(1 for r in results if r["verdict"] == "detected")
    blind = sum(1 for r in results if r["verdict"] == "missed")
    errored = sum(1 for r in results if r["verdict"] == "error")
    lines.append("")
    lines.append(f"  detected: {seen}/{len(results)}   blind: {blind}"
                 + (f"   injection errors: {errored}" if errored else ""))
    lines.append("")
    lines.append("  A 'blind' class is a measurement, not a failure: it names a"
                 " hazard the")
    lines.append("  tooling cannot see, which is information the A/B experiment"
                 " needs in order")
    lines.append("  to be interpretable. What would be a failure is a class"
                 " behaving differently")
    lines.append("  from what the registry documents.")
    lines.append("")
    lines.append(f"  false positives on an unmodified project: "
                 f"{fp.get('severity_error_or_warning', '?')} "
                 f"(a checker that flags everything detects everything)")
    if fp.get("unexpected"):
        lines.append(f"    unexpected codes: {fp['unexpected']}")
    lines.append("")
    lines.append(f"  registry declares {documented['detected']} detectable and "
                 f"{documented['missed']} blind.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fault", action="append")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    project = benchmark.find_project()
    if not os.path.isfile(os.path.join(project, "project.godot")):
        print(f"coverage: no Godot project found (looked at {project}).\n"
              "Set GODOT_PROJECT and retry.", file=sys.stderr)
        return 2

    # A scratch directory OUTSIDE the kit. `make_copy` walks the whole
    # repository and therefore refuses a destination inside it, so the previous
    # `<repo>/.tooling` default made `godot-lens bench --coverage` exit 1 before
    # measuring anything — while `README.md` printed its numbers as evidence.
    # See `LESSONS.md` §24.
    base = os.environ.get("GODOT_LENS_HOME") or os.path.join(
        tempfile.gettempdir(), "godot-lens-coverage")
    root = os.path.join(base, "coverage")
    names = args.fault or list(mutations.FAULTS)
    unknown = [n for n in names if n not in mutations.FAULTS]
    if unknown:
        print(f"coverage: unknown fault(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    results = []
    for name in names:
        results.append(measure_fault(name, mutations.FAULTS[name],
                                     os.path.join(root, name), project))

    fp = measure_false_positives(os.path.join(root, "_baseline"), project)
    documented = {
        "detected": sum(1 for s in mutations.FAULTS.values()
                        if s.get("expected", "detected") == "detected"),
        "missed": sum(1 for s in mutations.FAULTS.values()
                      if s.get("expected", "detected") == "missed"),
    }

    if args.json:
        print(json.dumps({"results": results, "false_positives": fp,
                          "documented": documented,
                          "known_unobservable": mutations.KNOWN_UNOBSERVABLE},
                         indent=2))
    else:
        print(render(results, fp, documented))

    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print(f"\nlab kept: {root}")

    return 0 if all(r["as_documented"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
