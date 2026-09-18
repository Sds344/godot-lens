"""select.py — choose GameDevBench tasks where an *observability* tool could matter.

The naive selection rule is "the tasks every model failed".  It is wrong, and the
data says so in one line:

    failed by all three : 116s / 166k tokens median
    passed by all three : 115s / 153k tokens median

Models do not spend more effort on the tasks they all fail.  They are not
*harder*; they are hard in a different dimension.  Grouping the 92 all-fail
tasks by their final assertion collapses them into 43 message families, and the
top seven families cover 55 of the 92 — they are variants of about seven base
tasks, differing in a pixel threshold, a magic group name, or an exact float:

    12  SpawnTop must line up with the bottom tip of the branch...
    11  CoinHighlight must cover the coin, overlap above 90%   (models reach 0.75-0.87)
    11  StarHighlight must cover the star, overlap above 85%
    11  Dragging state must reparent into the <X>_layer group
    10  DetectRange.base_range_size must be <X>
     9  Water requires a ShaderMaterial override

Those are the multimodal half of the paper's own taxonomy, which is precisely the
half a structural observer cannot address.  Selecting "hardest" selects the tasks
we are least able to help with.

So the selection here is by **failure modality**, not by difficulty:

  SCENE    the assertion is about scene/runtime structure — node presence,
           parenting, type, group membership, an unset exported reference, a
           signal connection, a physics layer.  This is what godot-lens reads.
  CODE     the assertion is about the text of a script (`player.gd must
           reference snappedf`).  The lens does not read source semantics.
  VISUAL   the assertion needs pixels — shader, material, IoU, tint, atlas
           slice, particle parameters, camera framing.  Out of reach.
  UNKNOWN  the message is a summary ("Validation passed") or unintelligible.

Classification reads the *failure* message from a model that failed, never from
a model that passed: a passing model's message is a success summary and would
classify every task as UNKNOWN.

The result is a shortlist, not a verdict.  A message can only say what the last
assertion was; whether the required value is *stated in the instruction* — and
so knowable by the agent and checkable by the tool — needs the task zip, which
`inspect_task.py` reads.  See README.md for the gate.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_RUNS = os.path.join(REPO, ".tooling", "gdb-data")

# Failure modes the paper's structural table names, expressed as message
# patterns.  Ordered: the first bucket that matches wins, and SCENE is tested
# before CODE because "node missing" is a scene fact even when the sentence
# mentions a script.
# Order matters.  VISUAL is tested FIRST, because a message can name a scene
# node and still be a pixel judgement ("StarHighlight must stay inside the
# platformer image bounds" — the node exists, its geometry is wrong).  When a
# message carries an unambiguous pixel/material word we treat it as out of reach
# even if it also mentions a property; over-excluding costs candidates, while
# over-including costs a wasted run.
VISUAL = [
    r"shader", r"material", r"standardmaterial", r"render_mode", r"unshaded",
    r"\biou\b", r"overlap", r"tightly cover", r"image bounds",
    r"tint", r"colou?r", r"purple", r"pink",
    r"animation", r"\bframes?\b", r"atlas", r"slic", r"spritesheet",
    r"particle", r"smoke", r"rain\b", r"sway", r"trail", r"emission", r"lifetime",
    r"camera", r"framing", r"viewport", r"texture", r"\bsky\b", r"glow",
    r"shadow", r"distortion", r"flipped faces", r"visual",
]

# Structural, and therefore readable by a runtime/ scene observer: the things the
# paper's structural table lists — node presence, parenting, type, group
# membership, an unset exported reference, a signal, a physics layer, a project
# setting, a tile's metadata, and property values the instruction can state.
SCENE = [
    # node presence, parenting, naming, type
    r"\bmissing\b", r"\bnot found\b", r"must exist", r"needs? (?:an?|at least)\b",
    r"\bunder\b", r"\bnamed\b", r"\bchild\b", r"\bparent", r"\breparent",
    r"\bnode\b", r"\bscene\b", r"\binstance", r"\bis not an?\b", r"wrong type",
    r"must be an? \w+",
    # exported references and signals — the paper's 35.9% and 36.2%
    r"must resolve", r"\bexport", r"\bunassigned\b", r"\bunset\b", r"\bsignal\b",
    # groups, collision, project settings
    r"\bgroup\b", r"collision (?:layer|mask|shape|mode)", r"\blayer\b",
    r"must be bound", r"main scene", r"\btile\b", r"peering",
    # property values, which the instruction can state and the observer can read
    r"must be \d", r'must be "', r"must be [A-Z]", r"must remain", r"must stay",
    r"should be", r"should keep", r"should start", r"must have", r"must not be",
    r"incorrect", r"mismatch", r"\btext\b", r"\btitle\b", r"grow\b",
    # runtime state transitions, observable as state rather than as pixels
    r"must (?:enter|transition|react|be destroyed)", r"on ready",
]

# Source-level assertions: the lens reads scenes and the running tree, not the
# semantics of a script's text.
CODE = [
    r"\.gd must", r"\bmust (?:define|call|use|reference|emit|connect)\b",
    r"must (?:override|implement)", r"missing (?:method|function|signal)",
    r"debug dictionary", r"\bdictionary\b",
]


def classify(message: str) -> str:
    m = (message or "").strip()
    if not m:
        return "UNKNOWN"
    if "requires display" in m or m.lower().startswith("task skipped"):
        return "SKIPPED"
    for bucket, patterns in (("VISUAL", VISUAL), ("SCENE", SCENE), ("CODE", CODE)):
        for pat in patterns:
            if re.search(pat, m, re.I):
                return bucket
    return "UNKNOWN"


def load_runs(run_dir: str) -> dict[str, dict]:
    runs = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            runs[os.path.basename(path)[:-5]] = json.load(fh)
    if not runs:
        raise SystemExit(f"select: no run JSONs under {run_dir}. See README.md.")
    return runs


def build_table(runs: dict[str, dict]) -> list[dict]:
    names = list(runs)
    by_run = {n: {t["task_name"]: t for t in d["tasks"]} for n, d in runs.items()}
    common = sorted(set.intersection(*[set(v) for v in by_run.values()]))
    rows = []
    for task in common:
        cells = [by_run[n][task] for n in names]
        passes = sum(1 for c in cells if c["success"])
        # The failure message must come from a model that failed.
        msg = ""
        for c in cells:
            if not c["success"]:
                msg = (c.get("message") or "").strip()
                break
        rows.append({
            "task": task,
            "passed_by": passes,
            "of": len(names),
            "mode": classify(msg) if passes < len(names) else "PASSED",
            "message": msg,
            "tokens": int(cells[0].get("total_tokens") or 0),
            "duration_s": float(cells[0].get("solver_duration") or 0),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", default=DEFAULT_RUNS,
                    help="directory of official per-model result JSONs")
    ap.add_argument("--out", default=os.path.join(HERE, "data", "task-outcomes.json"),
                    help="where to write the derived per-task table")
    ap.add_argument("--mode", help="print the shortlist for one mode (SCENE, CODE, VISUAL)")
    ap.add_argument("--max-passed-by", type=int,
                    help="with --mode, only tasks passed by at most N models")
    args = ap.parse_args()

    runs = load_runs(args.runs)
    rows = build_table(runs)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    payload = {
        "source_runs": sorted(runs),
        "total_tasks": len(rows),
        "note": ("Derived from the official per-model results JSONs so the 1.7 MB of "
                 "raw runs need not be committed. Regenerate with select.py."),
        "tasks": rows,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)

    counts = Counter(r["mode"] for r in rows)
    print(f"tasks: {len(rows)}   written: {os.path.relpath(args.out, REPO)}")
    print("\nfailure modality (one message per task):")
    for mode in ("SCENE", "CODE", "VISUAL", "UNKNOWN", "PASSED"):
        n = counts.get(mode, 0)
        print(f"  {mode:<8} {n:>3}  {'#' * (n // 4)}")

    print("\nSCENE failures by how many models passed (solvability probe):")
    scene = [r for r in rows if r["mode"] == "SCENE"]
    for k in range(0, len(runs) + 1):
        n = sum(1 for r in scene if r["passed_by"] == k)
        if n:
            label = "none passed  (may be under-specified)" if k == 0 else \
                    "passed by some (proven solvable)" if k < len(runs) else "all passed"
            print(f"  {k}/{len(runs)} passed : {n:>3}   {label}")

    if args.mode:
        pool = [r for r in rows if r["mode"] == args.mode]
        if args.max_passed_by is not None:
            pool = [r for r in pool if r["passed_by"] <= args.max_passed_by]
        print(f"\n=== {args.mode} shortlist (n={len(pool)}) ===")
        for r in pool:
            print(f"  {r['task']}  passed {r['passed_by']}/{r['of']}  {r['message'][:78]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
