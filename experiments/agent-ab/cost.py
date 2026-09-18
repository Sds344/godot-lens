"""Re-analyse completed A/B runs under the cost-to-repair metric.

The v1 matrix was designed to answer "does the lens make agents *able* to
repair?"  It answered no-that-is-not-the-axis: a capable agent with a shell
rebuilds whatever observation it needs.  The data we already paid for can
answer the next question for free, provided we are careful about one thing:

    Cost must be conditioned on success.

A cell that fails at the turn cap with 600 output tokens is not "efficient".
So this report separates three populations and never averages across them:

  * both arms repaired   -> the only place a cost ratio means anything
  * C failed, B repaired -> a capability failure, not a cost observation
  * neither repaired     -> said nothing about either arm

Usage:
    python3 experiments/agent-ab/cost.py results/
    python3 experiments/agent-ab/cost.py results/ results-pilot/
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ARMS = ("A", "B", "C")


def load(run_dir: Path) -> list[dict]:
    """Read every per-run record under a results directory."""
    runs = run_dir / "runs"
    if not runs.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(runs.glob("*.json"))]


def tok(r: dict) -> int:
    return int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)


def med(xs: list[float]) -> float:
    return statistics.median(xs) if xs else float("nan")


def by_case(records: list[dict]) -> dict[str, dict[str, list[dict]]]:
    out: dict[str, dict[str, list[dict]]] = {}
    for r in records:
        out.setdefault(r["case"], {}).setdefault(r["arm"], []).append(r)
    return out


def fmt_ratio(c: float, b: float) -> str:
    if not b or b != b:
        return "  n/a"
    return f"{c / b:5.2f}x"


def report(run_dir: Path) -> None:
    records = load(run_dir)
    if not records:
        print(f"no runs under {run_dir}")
        return

    cases = by_case(records)
    print(f"\n{'=' * 78}")
    print(f"COST-TO-REPAIR  ::  {run_dir}")
    print(f"{'=' * 78}")
    print(
        f"{'case':<26} {'arm':<4} {'n':>2} {'fixed':>5} "
        f"{'turns':>6} {'out_tok':>9} {'total_tok':>10} {'sec':>7} {'lens':>5}"
    )
    print("-" * 78)

    paired: list[tuple[str, float, float]] = []   # case, C ratio on output tokens
    paired_turns: list[tuple[str, float, float]] = []
    cap_fail: list[str] = []

    for case in sorted(cases):
        arms = cases[case]
        stats: dict[str, tuple] = {}
        for arm in ARMS:
            rs = arms.get(arm)
            if not rs:
                continue
            fixed = [r for r in rs if r.get("repaired")]
            # Cost is measured on the cells that actually succeeded.
            pop = fixed or rs
            turns = med([r.get("turns") or 0 for r in pop])
            out_tok = med([int(r.get("output_tokens") or 0) for r in pop])
            tot = med([tok(r) for r in pop])
            sec = med([float(r.get("elapsed_s") or 0) for r in pop])
            lens = sum(1 for r in rs if r.get("used_lens"))
            stats[arm] = (turns, out_tok, tot, sec, fixed, rs)
            mark = "" if fixed else "  (all failed - cost not comparable)"
            print(
                f"{case:<26} {arm:<4} {len(rs):>2} {len(fixed):>5} "
                f"{turns:>6.0f} {out_tok:>9,.0f} {tot:>10,.0f} {sec:>7.0f} "
                f"{lens:>5}{mark}"
            )

        b, c = stats.get("B"), stats.get("C")
        if b and c:
            b_fixed, c_fixed = bool(b[4]), bool(c[4])
            if b_fixed and c_fixed:
                paired.append((case, c[1], b[1]))
                paired_turns.append((case, c[0], b[0]))
            elif b_fixed and not c_fixed:
                cap_fail.append(case)
        print()

    print("=" * 78)
    print("PAIRED COST RATIO  (C / B, only cases both arms repaired)")
    print("=" * 78)
    if not paired:
        print("  none - no case had both arms repairing")
    else:
        print(f"  {'case':<26} {'C out_tok':>10} {'B out_tok':>10} {'ratio':>8}")
        for case, c_tok, b_tok in paired:
            print(f"  {case:<26} {c_tok:>10,.0f} {b_tok:>10,.0f} {fmt_ratio(c_tok, b_tok):>8}")
        ratios = [c / b for _, c, b in paired if b]
        turns_ratios = [c / b for _, c, b in paired_turns if b]
        if ratios:
            print(f"\n  median output-token ratio : {med(ratios):.2f}x  (n={len(ratios)})")
            print(f"  median turn ratio         : {med(turns_ratios):.2f}x  (n={len(turns_ratios)})")
        wins = sum(1 for r in ratios if r < 1.0)
        print(f"  C cheaper in {wins}/{len(ratios)} paired cases")

    print("\n" + "=" * 78)
    print("CAPABILITY FAILURES  (B repaired, C did not)")
    print("=" * 78)
    if not cap_fail:
        print("  none")
    for case in cap_fail:
        c_recs = cases[case]["C"]
        for r in c_recs:
            print(f"  {case}: C repeat{r.get('repeat')} -> {r.get('detail')}")
    print()


if __name__ == "__main__":
    targets = [Path(a) for a in sys.argv[1:]] or [Path("experiments/agent-ab/results")]
    for t in targets:
        if t.is_file():
            t = t.parent
        report(t)
