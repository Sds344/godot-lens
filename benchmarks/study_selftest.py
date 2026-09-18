#!/usr/bin/env python3
"""study_selftest.py — verify the four fidelity instruments, in both directions.

Why this exists
---------------
A check that has never been shown to fail is not evidence of anything. Each
instrument here is asserted twice: it must accept a correct IR, and it must
reject the specific defect class it exists to catch.

This is not hypothetical diligence. The four instruments were developed against
the *real* broken IR from the converter's history (commit `9b5dd95`), and three
of them reported agreement on it:

    instrument 1  executed spans vs IR       AGREE   (the IR is a superset)
    instrument 2  display order              AGREE   (same reason)
    instrument 3  IR gaps                    AGREE   (nothing was deleted)
    instrument 4  IR placement               DIVERGED <- the only one that saw it

The defect was not a mismatch between the IR and the runtime at all: the
converter emitted the top-level declaration

    define narrator = Character(None)      # script.rpy line 2

as a node inside the body of `start`, whose statements begin at line 11. The IR
therefore *contained* the declaration while the runtime could never reach it. Any
instrument that compares runtime output against the IR is structurally blind to
that, which is why a purely comparative checker is not sufficient and why
instrument 4 reads the IR alone.

The fixture below is the shape of that artifact, reduced to the minimum that
reproduces it. It is written out rather than read from the converter's repository
so this test cannot silently stop running when that repository moves — a skipped
test that looks green is the failure mode this whole project is about.

Usage:
  python3 benchmarks/study_selftest.py
  python3 benchmarks/study_selftest.py --json
Exit code: 0 = every instrument behaved as specified, 1 = at least one did not.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

from godot_lens import fidelity, study  # noqa: E402


def _node(kind, line, **kw):
    d = {"kind": kind, "mapping": "exact",
         "source": {"path": "script.rpy", "line": line, "column": 5}}
    d.update(kw)
    return d


def correct_story():
    """A correct IR: the declaration is not inside any label body.

    The `define` is modelled the way a correct converter records it — as
    configuration, not as a statement in `start`. That is exactly what changed
    when the defect was fixed.
    """
    return {
        "schema_version": 2,
        "entry_label": "start",
        "config": {"character.narrator": "", "character.e": "Eileen"},
        "labels": [
            {"name": "start", "body": [
                _node("dialogue", 11, speaker="e", text="Hello."),
                _node("narration", 12, text="This is narration."),
                _node("jump", 13, target="ending"),
            ]},
            {"name": "ending", "body": [
                _node("dialogue", 16, speaker="e", text="The end."),
            ]},
        ],
    }


def broken_story():
    """The defect from 9b5dd95: the line-2 declaration folded into `start`.

    Reproduced faithfully, including `unsupported`/`construct: define`, because
    the instrument must reject the real shape and not a convenient paraphrase of
    it.
    """
    story = correct_story()
    start = story["labels"][0]
    start["body"].insert(0, {
        "kind": "unsupported", "mapping": "unsupported", "construct": "define",
        "text": "define narrator = Character(None)",
        "source": {"path": "script.rpy", "line": 2,
                   "column": 1, "end_line": 2, "end_column": 34},
    })
    return story


def trace(displays):
    """Build a minimal runtime trace from (speaker, text, y) tuples.

    Returns normalized display items (via `actual_trace`), because that is what
    `compare` consumes — handing it raw trace JSON would be testing a different
    function than the one used in production.
    """
    events = []
    frame = 0
    for item in displays:
        frame += 1
        if item[0]:
            events.append({"frame": frame, "kind": "text", "type": "Label",
                           "to": item[0], "position": f"(64.0, 370.0)"})
        events.append({"frame": frame, "kind": "text", "type": "Label",
                       "to": item[1], "position": f"(64.0, {item[2]})"})
    return fidelity.actual_trace({"events": events})


def probe(spans):
    return {"spans": [{"kind": k, "path": p, "line": l, "label": "start"}
                      for (k, p, l) in spans]}


def run():
    """Each entry: (name, instrument, story, extra, expected_agree)."""
    correct = correct_story()
    broken = broken_story()

    # The runtime executed lines 11..13 plus 16 (it jumped to `ending`).
    executed = [("dialogue", "script.rpy", 11), ("narration", "script.rpy", 12),
                ("jump", "script.rpy", 13), ("dialogue", "script.rpy", 16)]
    ok_probe = probe(executed)

    # The display the IR promises for that path, and the runtime showing it.
    shown = trace([("Eileen", "Hello.", 420), ("", "This is narration.", 420),
                   ("Eileen", "The end.", 420)])
    reordered = trace([("", "This is narration.", 420), ("Eileen", "Hello.", 420),
                       ("Eileen", "The end.", 420)])
    misattributed = trace([("Narrator", "Hello.", 420),
                           ("", "This is narration.", 420),
                           ("Eileen", "The end.", 420)])

    cases = []

    # Instrument 1: executed spans vs IR. Catches a span the IR lacks.
    cases.append(("1/spans", "correct IR", study.verify_spans(correct, ok_probe), True))
    cases.append(("1/spans", "IR missing an executed span",
                  study.verify_spans(
                      _drop_line(correct, 12), ok_probe), False))

    # Instrument 2: display order and attribution.
    exp_ok = fidelity.executed_expected(correct, ok_probe)
    cases.append(("2/trace", "correct order", fidelity.compare(exp_ok, shown), True))
    cases.append(("2/trace", "two displays swapped",
                  fidelity.compare(exp_ok, reordered), False))
    cases.append(("2/trace", "wrong speaker on a dialogue line",
                  fidelity.compare(exp_ok, misattributed), False))

    # Instrument 3: IR gaps (a line inside the range that no node claims).
    cases.append(("3/gaps", "correct IR", study.ir_gaps(correct, REPO), True))
    cases.append(("3/gaps", "line 12 removed from a contiguous 11..13 run",
                  study.ir_gaps(_drop_line(correct, 12), _SOURCE_DIR), False))

    # Instrument 4: IR placement. The only instrument that sees the real defect.
    cases.append(("4/placement", "correct IR", study.ir_placements(correct), True))
    cases.append(("4/placement", "REAL defect: declaration folded into `start`",
                  study.ir_placements(broken), False))

    return cases


_SOURCE_DIR = None

# The commit in the converter's history whose IR carried the real defect. Named
# explicitly so the claim "this instrument catches the actual bug" can be
# re-checked later rather than taken on trust.
REAL_ARTIFACT_COMMIT = "9b5dd95"
REAL_ARTIFACT_PATH = "godot_project/data/story.json"


def _load_real_artifact():
    """Read the real broken IR out of the converter's git history, if present.

    Returns None when the sibling repository or that commit is unavailable. The
    caller records a failure rather than a skip in that case, because a check
    that quietly stops running is precisely the false-confidence failure this
    project exists to prevent.
    """
    import subprocess
    repo = os.environ.get("GODOT_PROJECT")
    candidates = []
    if repo:
        candidates.append(os.path.dirname(os.path.abspath(repo)))
        candidates.append(os.path.abspath(repo))
    candidates.append(os.path.join(os.path.dirname(REPO), "Renpy2Godot"))
    for cand in candidates:
        if not os.path.isdir(os.path.join(cand, ".git")):
            continue
        try:
            blob = subprocess.run(
                ["git", "-C", cand, "show",
                 f"{REAL_ARTIFACT_COMMIT}:{REAL_ARTIFACT_PATH}"],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if blob.returncode == 0 and blob.stdout.strip():
            try:
                return json.loads(blob.stdout)
            except json.JSONDecodeError:
                continue
    return None


def _drop_line(story, line):
    """Remove the node at a source line, to simulate converter loss."""
    import copy
    out = copy.deepcopy(story)
    for label in out["labels"]:
        label["body"] = [n for n in label.get("body", [])
                         if n.get("source", {}).get("line") != line]
    return out


def main():
    global _SOURCE_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--real-artifact", action="store_true",
                    help="also grade against the ACTUAL broken IR from the "
                         "converter's history, if that repository is available")
    args = ap.parse_args()

    # Instrument 3 needs a source file on disk whose lines the IR can be checked
    # against. A synthetic fixture keeps this test independent of the converter.
    import tempfile
    _SOURCE_DIR = tempfile.mkdtemp(prefix="lens-study-src-")
    with open(os.path.join(_SOURCE_DIR, "script.rpy"), "w", encoding="utf-8") as fh:
        fh.write("\n".join([
            "define e = Character(\"Eileen\")",   # 1
            "",                                   # 2
            "",                                   # 3
            "",                                   # 4
            "",                                   # 5
            "",                                   # 6
            "",                                   # 7
            "",                                   # 8
            "",                                   # 9
            "label start:",                       # 10
            "    e \"Hello.\"",                   # 11
            "    \"This is narration.\"",         # 12
            "    jump ending",                    # 13
            "",                                   # 14
            "label ending:",                      # 15
            "    e \"The end.\"",                 # 16
        ]) + "\n")

    results = []
    for name, desc, report, expected in run():
        actual = bool(report.get("agree"))
        results.append({
            "instrument": name, "scenario": desc,
            "expected_agree": expected, "actual_agree": actual,
            "ok": actual is expected,
        })

    # Optional: grade against the REAL artifact rather than a reconstruction.
    # The reduced fixture above is what keeps this test self-contained and always
    # running; this is what keeps it honest about the defect's true shape.
    if args.real_artifact:
        real = _load_real_artifact()
        if real is None:
            results.append({
                "instrument": "4/placement", "scenario": "REAL artifact (skipped)",
                "expected_agree": False, "actual_agree": None, "ok": False,
                "note": "converter repository or commit not available",
            })
        else:
            report = study.ir_placements(real)
            results.append({
                "instrument": "4/placement",
                "scenario": "REAL artifact from converter history (9b5dd95)",
                "expected_agree": False, "actual_agree": bool(report.get("agree")),
                "ok": report.get("agree") is False,
            })

    failures = [r for r in results if not r["ok"]]

    if args.json:
        print(json.dumps({"results": results, "failures": len(failures)}, indent=2))
    else:
        print("fidelity instrument self-test")
        print("============================")
        current = None
        for r in results:
            if r["instrument"] != current:
                current = r["instrument"]
                print()
                print(current)
            mark = "ok  " if r["ok"] else "FAIL"
            want = "agree" if r["expected_agree"] else "diverge"
            got = "agree" if r["actual_agree"] else "diverge"
            print(f"  {mark} {r['scenario']:<48} expected={want:<8} got={got}")
        print()
        print(f"scenarios: {len(results)}   failures: {len(failures)}")
        if failures:
            print("\nAn instrument that cannot fail cannot be trusted:")
            for r in failures:
                print(f"  {r['instrument']}: {r['scenario']}")
            print("\nNote: instrument 4 is the only one that detects the real "
                  "historical defect.")
        else:
            print("All instruments behaved as specified in both directions.")

    import shutil
    shutil.rmtree(_SOURCE_DIR, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
