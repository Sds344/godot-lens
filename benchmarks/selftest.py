#!/usr/bin/env python3
"""selftest.py — verify the graders, because a grader that can pass a broken
project is worse than no grader at all.

The benchmark is only worth the confidence it manufactures. LESSONS.md §10 and
§1 record two real grader defects that produced false PASSES, one of which
survived until this self-test existed. So the graders are treated as code under
test: for every case and every repair strategy in `repairs.py`, the expected
verdict is asserted.

What this proves, and what it does not
--------------------------------------
It proves the graders are not trivially foolable: a fault is not passed when
untouched, and a symptom cannot be silenced into a pass by redirecting a lookup,
swallowing the error, deleting the feature, or commenting instead of coding.

It does NOT prove the graders encode the right specification. Deciding what
counts as a "correct" repair is a judgement call (see EXPECTED.md per case). A
grader is an oracle, and an oracle can be wrong in the other direction: it can
reject a legitimate fix. That is why each verdict below carries a stated reason
rather than just a boolean.

Usage:
  python3 benchmarks/selftest.py                 # all cases, all scenarios
  python3 benchmarks/selftest.py --case case_001_node_path
  python3 benchmarks/selftest.py --keep          # keep the lab for inspection
  python3 benchmarks/selftest.py --json
Exit code: 0 = every scenario behaved as specified, 1 = at least one did not.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import benchmark  # noqa: E402
import repairs  # noqa: E402


def _lab_root():
    """A scratch directory OUTSIDE the kit.

    This used to be `<repo>/.tooling/selftest`, which was fine until
    `make_copy` learned to refuse a destination inside the repository — it walks
    the whole kit, so copying into itself recurses without bound. The refusal
    was correct and this default was not updated with it, so the selftest exited
    1 before running a single scenario.

    That matters more than a broken script usually would: `README.md` shows this
    command as the evidence that the graders reject hacks. A trust signal that
    cannot be executed is worse than none, because a reader sees the command,
    the output, and no reason to doubt either. See `LESSONS.md` §24.
    """
    base = os.environ.get("GODOT_LENS_HOME") or os.path.join(
        tempfile.gettempdir(), "godot-lens-selftest")
    return os.path.join(base, "selftest")


def run_case(case, case_dir, project):
    """Run every scenario for one case. Returns a list of result dicts."""
    if os.path.exists(case_dir):
        shutil.rmtree(case_dir, ignore_errors=True)
    benchmark.make_copy(case_dir, project)

    results = []
    for name, spec in repairs.SCENARIOS[case].items():
        # Start from a clean, fault-injected baseline for EVERY scenario, so a
        # scenario can never be graded against another scenario's edits.
        if not benchmark._reset_to_baseline(case_dir):
            raise SystemExit(f"selftest: {case_dir} is not a git repo")
        benchmark.setup_case(case, case_dir)

        # The guard must let a freshly reset lab through.
        if spec["fn"] is not None:
            spec["fn"](case_dir)

        graded = benchmark.grade(case, case_dir)
        ok = graded["passed"] is spec["expected"]
        results.append({
            "case": case,
            "scenario": name,
            "expected_pass": spec["expected"],
            "actual_pass": graded["passed"],
            "ok": ok,
            "hack_class": spec["class"],
            "why": spec["why"],
            "detail": graded["detail"],
            "diff_stat": graded["diff_stat"],
        })
    return results


def render(results):
    lines = []
    pad = max(len(r["scenario"]) for r in results) + 2
    case = None
    for r in results:
        if r["case"] != case:
            case = r["case"]
            lines.append(f"\n{case}")
            lines.append("-" * (len(case) + 2))
        mark = "ok  " if r["ok"] else "FAIL"
        want = "pass" if r["expected_pass"] else "fail"
        got = "pass" if r["actual_pass"] else "fail"
        lines.append(f"  {mark} {r['scenario']:<{pad}} expected={want:<4} "
                     f"got={got:<4} {r['detail'][:70]}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # The project under test is the one the benchmark would use; a lab without a
    # project cannot be graded.
    project = benchmark.find_project()
    if not os.path.isfile(os.path.join(project, "project.godot")):
        print(f"selftest: no Godot project found (looked at {project}).\n"
              "Set GODOT_PROJECT to the project directory and retry.",
              file=sys.stderr)
        return 2

    cases = args.case or list(repairs.SCENARIOS)
    unknown = [c for c in cases if c not in repairs.SCENARIOS]
    if unknown:
        print(f"selftest: unknown case(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    root = _lab_root()
    all_results = []
    for case in cases:
        all_results.extend(run_case(case, os.path.join(root, case), project))

    failures = [r for r in all_results if not r["ok"]]

    if args.json:
        print(json.dumps({"scenarios": all_results,
                          "failures": len(failures)}, indent=2))
    else:
        print("grader self-test")
        print("================")
        print(render(all_results))
        print()
        hacks = [r for r in all_results if r["hack_class"]]
        rejected = [r for r in hacks if not r["actual_pass"]]
        print(f"scenarios: {len(all_results)}   "
              f"hacks attempted: {len(hacks)}   "
              f"hacks rejected: {len(rejected)}")
        if failures:
            print(f"\n{len(failures)} scenario(s) behaved incorrectly:")
            for r in failures:
                print(f"  {r['case']}/{r['scenario']}: expected "
                      f"{'pass' if r['expected_pass'] else 'fail'}, "
                      f"got {'pass' if r['actual_pass'] else 'fail'}")
                print(f"    reason: {r['detail']}")
            print("\nA grader that passes a broken project manufactures false "
                  "confidence. Fix this before trusting any result.")
        else:
            print("\nAll graders behaved as specified.")

    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print(f"\nlab kept: {root}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
