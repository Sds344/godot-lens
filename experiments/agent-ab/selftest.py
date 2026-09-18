#!/usr/bin/env python3
"""selftest.py — verify the experiment's grader before any agent runs.

A benchmark that can pass a broken project manufactures confidence, and this
project has already shipped two such graders (`LESSONS.md` §1, §13). So the grader
is attacked first:

    unfixed  → must NOT be graded repaired
    real fix → must be graded repaired
    hack     → must NOT be repaired, AND must be labelled with the strategy

Three directions per case. A case whose "hack" is graded as a repair means the
experiment would report a fabricated success, and no result from it could be
trusted.

Usage:
  python3 experiments/agent-ab/selftest.py
  python3 experiments/agent-ab/selftest.py --case L2-property-class
  python3 experiments/agent-ab/selftest.py --json
Exit code: 0 = every case behaved as specified, 1 = at least one did not.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "benchmarks"))
sys.path.insert(0, os.path.join(REPO, "src"))

import cases  # noqa: E402
import grader  # noqa: E402
import benchmark  # noqa: E402

SCENE = "godot_project/Main.tscn"
SCRIPT = "godot_project/scripts/probe.gd"
STORY = "godot_project/data/story.json"


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def _append(root, rel, text):
    with open(os.path.join(root, rel), "a", encoding="utf-8") as fh:
        fh.write(text)


# --- repair strategies, per case ---------------------------------------------
# Each entry: (real_fix, hack, expected_hack_label_or_None)

def fix_nodepath(root):
    # Godot's text scene format requires a parent to be declared before its
    # child. Appending the child first produced an unloadable scene, which the
    # grader then correctly reported as unrepaired — the failure was in this
    # fixture, not in the grader.
    _append(root, SCENE,
            '\n[node name="NoSuchChild" type="Node2D" parent="."]\n'
            '[node name="AtAll" type="Node2D" parent="NoSuchChild"]\n')


def hack_nodepath(root):
    _write(root, SCRIPT, """extends Node2D

func _ready() -> void:
	var n: Node = get_node_or_null("OtherNode")
	print(n)
""")


def fix_typeerror(root):
    _write(root, SCRIPT, """extends Node2D

var health: int = 100

func _ready() -> void:
	print(health)
""")


def hack_typeerror(root):
    _write(root, SCRIPT, """extends Node2D

func _ready() -> void:
	print("ok")
""")


def fix_missing_resource(root):
    _write(root, "godot_project/scripts/does_not_exist.gd", "extends Node\n")


def hack_missing_resource(root):
    os.remove(os.path.join(root, "godot_project", "Broken.tscn"))


def fix_property_class(root):
    """Move the assignment onto an object whose class declares it.

    Line-based rather than string-based: the earlier version removed the line and
    then tried to re-insert it after `[node name="Rain" ...]`, but the intervening
    edit had already changed that line's surroundings, so the insertion never
    landed and the fixture looked like a deletion. A repair fixture that silently
    fails makes the grader look wrong when the fixture is.
    """
    lines = _read(root, SCENE).splitlines()
    out, inserted = [], False
    for line in lines:
        if line.strip() == 'sub_emitter = NodePath("../Splash")':
            continue          # drop it from the material
        out.append(line)
        if not inserted and line.strip().startswith(
                '[node name="Rain" type="GPUParticles2D"'):
            out.append('sub_emitter = NodePath("../Splash")')
            inserted = True
    if not inserted:
        raise SystemExit("selftest: could not find the Rain node to place "
                         "sub_emitter on; the injector changed shape")
    _write(root, SCENE, "\n".join(out) + "\n")


def hack_property_class(root):
    """Delete the setting: the symptom goes, the behaviour goes with it."""
    text = _read(root, SCENE)
    _write(root, SCENE, text.replace('sub_emitter = NodePath("../Splash")\n', ""))


def fix_unset_export(root):
    _append(root, SCENE, '\n' if not _read(root, SCENE).endswith("\n") else "")
    text = _read(root, SCENE)
    text = text.replace('[node name="Other" type="Node2D" parent="."]',
                        '[node name="Other" type="Node2D" parent="."]')
    # Assign on the Main node, which owns the script.
    text = text.replace('script = ExtResource("1_p")',
                        'script = ExtResource("1_p")\ntarget = NodePath("Other")')
    _write(root, SCENE, text)


def hack_unset_export(root):
    """Remove the export instead of wiring it."""
    _write(root, SCRIPT, """extends Node2D

func _ready() -> void:
	print("ok")
""")


def fix_narration(root):
    story = json.loads(_read(root, STORY))
    for label in story.get("labels", []):
        for node in label.get("body", []) or []:
            if isinstance(node, dict) and node.get("kind") == "dialogue" \
                    and "Hello" in str(node.get("text", "")):
                idx = label["body"].index(node)
                label["body"].insert(idx + 1, {
                    "kind": "narration", "mapping": "exact",
                    "text": "This is narration with an escaped quote: \"hello\".",
                    "source": {"path": "script.rpy", "line": 15},
                })
                _write(root, STORY,
                       json.dumps(story, indent=2, ensure_ascii=False) + "\n")
                return
    raise SystemExit("selftest: could not find the anchor dialogue to restore after")


def hack_narration(root):
    """Insert unrelated narration: the count changes, the content does not match."""
    story = json.loads(_read(root, STORY))
    for label in story.get("labels", []):
        for node in label.get("body", []) or []:
            if isinstance(node, dict) and node.get("kind") == "dialogue":
                idx = label["body"].index(node)
                label["body"].insert(idx + 1, {
                    "kind": "narration", "mapping": "exact",
                    "text": "A different line entirely.",
                    "source": {"path": "script.rpy", "line": 15},
                })
                _write(root, STORY,
                       json.dumps(story, indent=2, ensure_ascii=False) + "\n")
                return
    raise SystemExit("selftest: no anchor dialogue found")


REPAIRS = {
    "L1-nodepath": (fix_nodepath, hack_nodepath),
    "L1-typeerror": (fix_typeerror, hack_typeerror),
    "L1-missing-resource": (fix_missing_resource, hack_missing_resource),
    "L2-property-class": (fix_property_class, hack_property_class),
    "L2-unset-export": (fix_unset_export, hack_unset_export),
    "L3-narration-dropped": (fix_narration, hack_narration),
}


def run_case(name, project, lab):
    spec = cases.CASES[name]
    fix, hack = REPAIRS[name]
    results = []

    for label, mutate, expect_repaired, expect_hack in (
        ("unfixed", None, False, False),
        ("real fix", fix, True, False),
        ("hack", hack, False, None),   # None = any label is acceptable, but not repaired
    ):
        if os.path.exists(lab):
            shutil.rmtree(lab, ignore_errors=True)
        benchmark.make_copy(lab, project)
        spec["inject"](lab)
        if mutate:
            mutate(lab)
        rec = grader.grade(name, lab, declared_done=True)
        ok = (rec["repaired"] is expect_repaired)
        if expect_repaired is False and expect_hack:
            ok = ok and bool(rec["hack"])
        results.append({
            "case": name, "scenario": label, "ok": ok,
            "repaired": rec["repaired"], "hack": rec["hack"],
            "detail": rec["detail"][:110],
            "false_claim": rec["false_claim"],
        })
    return results


def detection_checks():
    """The lens-invocation detector must not fire on paths or greps.

    Regression guard for the bug where `/tmp/godot-lens-ab/...` in the agent's own
    working directory was read as the control arm invoking the tool.
    """
    from grader import invokes_lens
    cases = [
        ("find /tmp/ab-eval/x/godot_project -type f", False),
        ("ls -la /tmp/ab-eval/L2__armB__r1/", False),
        ("cd /tmp/ab-eval/x && godot --headless --quit-after 60", False),
        ("grep -rn godot-lens README.md", False),
        ("cat godot-lens-ab.md", False),
        ("godot-lens inspect --agent", True),
        ("GODOT_PROJECT=$(pwd)/godot_project godot-lens diagnose", True),
        ("cd x && godot-lens validate", True),
        ("bash tools/godot_validate.sh", True),
        ("/opt/godot-lens/godot-lens study run", True),
    ]
    return [{"case": "detection", "scenario": c[:46],
             "ok": invokes_lens(c) is want,
             "repaired": invokes_lens(c), "hack": None,
             "detail": f"want={want}"}
            for c, want in cases]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    project = benchmark.find_project()
    if not os.path.isfile(os.path.join(project, "project.godot")):
        print(f"selftest: no Godot project found ({project}). Set GODOT_PROJECT.",
              file=sys.stderr)
        return 2

    # Labs must live OUTSIDE the kit: `make_copy` walks the whole repository, so
    # a destination inside it copies itself recursively. This default used to be
    # `<repo>/.tooling`, which `make_copy` now refuses — so the selftest exited 1
    # before running a single check, and the regression guard for the graders was
    # silently dead. A guard that cannot run is worse than no guard, because it
    # is still counted as one.
    base = os.environ.get("GODOT_LENS_HOME") or os.path.join(
        tempfile.gettempdir(), "ab-selftest")
    root = os.path.join(base, "labs")
    names = args.case or list(REPAIRS)

    all_results = detection_checks()
    for name in names:
        all_results.extend(run_case(name, project, os.path.join(root, name)))

    failures = [r for r in all_results if not r["ok"]]
    if args.json:
        print(json.dumps({"results": all_results, "failures": len(failures)},
                         indent=2))
    else:
        print("A/B grader self-test  (unfixed must fail, real fix must pass, "
              "hack must be labelled)")
        print("=" * 78)
        current = None
        for r in all_results:
            if r["case"] != current:
                current = r["case"]
                print(f"\n{current}")
            mark = "ok  " if r["ok"] else "FAIL"
            print(f"  {mark} {r['scenario']:<9} repaired={str(r['repaired']):<5} "
                  f"hack={str(r['hack']):<8} {r['detail'][:58]}")
        print()
        print(f"scenarios: {len(all_results)}   failures: {len(failures)}")
        if failures:
            print("\nA grader that grades a hack as a repair fabricates the result "
                  "the")
            print("experiment is supposed to measure. Fix before running any agent.")
        else:
            print("All cases behaved as specified in all three directions.")

    shutil.rmtree(root, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
