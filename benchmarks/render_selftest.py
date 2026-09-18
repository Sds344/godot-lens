#!/usr/bin/env python3
"""render_selftest.py — verify the three renderers against frozen payloads.

Runs with **no Godot, no project, and no network**, which is the point: the
presentation layer must be verifiable on its own, otherwise every layout change
costs an engine run and the un-runnable cases never get tested at all.

The assertions are not "the output looks right" — that cannot be checked. They are
the properties a renderer must never violate:

* a payload whose game never booted renders as UNKNOWN, never as clean
* an observation-layer renderer never asserts anything a diagnostic rule did not
  explicitly state
* the HTML is self-contained (no external src/href), so it opens from file://
* the agent digest stays inside its declared token budget
* a renderer never crashes on a partial payload

Usage:
  python3 benchmarks/render_selftest.py
  python3 benchmarks/render_selftest.py --json
Exit code: 0 = every property held, 1 = at least one did not.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

from godot_lens import render  # noqa: E402

FIXTURES = os.path.join(REPO, "experiments", "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def run():
    """Returns a list of (property, ok, detail)."""
    results = []
    ok_p = load("inspect-ok.json")
    broken = load("inspect-broken.json")
    noboot = load("inspect-noboot.json")

    # --- the three-state rule -------------------------------------------------
    # This is the single most important property in the module. Collapsing
    # "could not run" into "no errors" tells an agent a project is healthy when
    # nothing at all was learned about it.
    results.append(("runtime_status: booted+clean -> clean",
                    render.runtime_status(ok_p) == "clean",
                    render.runtime_status(ok_p)))
    results.append(("runtime_status: booted+errors -> errors",
                    render.runtime_status(broken) == "errors",
                    render.runtime_status(broken)))
    results.append(("runtime_status: booted=false -> unknown",
                    render.runtime_status(noboot) == "unknown",
                    render.runtime_status(noboot)))

    d = render.agent_digest(noboot)
    results.append(("digest says NOT OBSERVED for an unbooted project",
                    "NOT OBSERVED" in d, ""))
    results.append(("digest does not report a clean runtime for it",
                    "0 errors" not in d, ""))
    results.append(("digest states the limit of what is known",
                    "not evidence" in d.lower(), ""))

    c = render.ci_summary(noboot)
    results.append(("ci verdict is UNKNOWN, not CLEAN",
                    "UNKNOWN" in c and "CLEAN" not in c, c))

    # --- observation must not become interpretation ---------------------------
    # A digest may compress what the payload says. It may not add a judgement of
    # its own.
    #
    # This checks the renderer's OWN summary lines, not the whole digest, because
    # the digest legitimately quotes engine output and game content. An earlier
    # version of this test scanned everything and flagged the dialogue line
    # "What should we do next?" as invented advice — the check was wrong, not the
    # renderer. Engine-reported lines are in the payload and are quoted, so they
    # are excluded by the same rule.
    ADVICE = ("we recommend", "you should", "consider ", "it is best to",
              "make sure to", "fix this by", "try to", "please ")
    for name, payload in (("ok", ok_p), ("broken", broken), ("noboot", noboot)):
        digest = render.agent_digest(payload)
        own_lines = []
        in_content = False
        for line in digest.splitlines():
            # A content line is an indented engine/game string; renderer prose is
            # never indented under a display entry.
            stripped = line.strip()
            if stripped.startswith("f") and ":" in stripped and line.startswith("  f"):
                in_content = True
                continue
            if line and not line.startswith(" "):
                in_content = False
            if not in_content:
                own_lines.append(line)
        own = "\n".join(own_lines).lower()
        found = [w for w in ADVICE if w in own]
        results.append((f"digest[{name}] adds no advice of its own",
                        not found, f"found {found}" if found else ""))
        # And it must not smuggle a judgement in as a bare imperative either.
        results.append((f"digest[{name}] has no bare imperative marker",
                        "must " not in own or "not evidence" in own,
                        ""))

    # --- self-containment of the HTML ----------------------------------------
    for name, payload in (("ok", ok_p), ("broken", broken), ("noboot", noboot)):
        page = render.html_report(payload)
        ext = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', page)
        results.append((f"html[{name}] has no external resources", not ext,
                        f"found {ext}" if ext else ""))
        results.append((f"html[{name}] is non-trivial",
                        len(page) > 2000, f"{len(page)} bytes"))
    page = render.html_report(noboot)
    results.append(("html warns that runtime was not observed",
                    "NOT observed" in page, ""))
    results.append(("html separates diagnosis from observation",
                    "rule-bound interpretation" in
                    render.html_report(broken, diagnostics={"diagnostics": [
                        {"severity": "error", "code": "X", "meaning": "m",
                         "impact": "i", "action": "a"}]}),
                    ""))

    # --- diagnosis is labelled when present -----------------------------------
    with_diag = render.html_report(broken, diagnostics={
        "diagnostics": [{"severity": "error", "code": "COLLISION_SHAPE_MISSING",
                         "meaning": "no shape", "impact": "no collision",
                         "action": "assign a Shape2D"}]})
    results.append(("html includes rule-bound action when diagnosis given",
                    "assign a Shape2D" in with_diag, ""))
    without = render.html_report(broken)
    results.append(("html omits diagnosis section when not given",
                    "rule-bound interpretation" not in without, ""))

    # --- budget ---------------------------------------------------------------
    for name, payload in (("ok", ok_p), ("broken", broken), ("noboot", noboot)):
        t = render.agent_digest(payload, budget_tokens=700)
        est = render.estimate_tokens(t)
        results.append((f"digest[{name}] within a 700-token budget",
                        est <= 700, f"~{est} tokens"))
    # A large scene list must be trimmed rather than allowed to grow unbounded,
    # and trimming must not produce a half-written line.
    huge = json.loads(json.dumps(ok_p))
    huge["runtime"]["observed_displays"] = [
        {"frame": i, "speaker": "S", "text": "x" * 120} for i in range(400)]
    t = render.agent_digest(huge, budget_tokens=700)
    results.append(("digest truncates a huge timeline rather than growing",
                    render.estimate_tokens(t) <= 700,
                    f"~{render.estimate_tokens(t)} tokens"))

    # --- robustness on partial payloads ---------------------------------------
    partials = [
        ("empty object", {}),
        ("no runtime", {"project": {}, "scenes": []}),
        ("null runtime", {"runtime": None}),
        ("scenes not a list", {"scenes": "nope"}),
        ("nested node missing children", {"scenes": [
            {"scene": "r", "static": {"root": {"name": "n", "type": "Node"}}}]}),
    ]
    for label, payload in partials:
        try:
            render.agent_digest(payload)
            render.ci_summary(payload)
            render.html_report(payload)
            results.append((f"partial payload renders: {label}", True, ""))
        except Exception as e:  # noqa: BLE001 - the point is to catch anything
            results.append((f"partial payload renders: {label}", False,
                            f"{type(e).__name__}: {e}"))

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = run()
    failures = [r for r in results if not r[1]]

    if args.json:
        print(json.dumps({"results": [
            {"property": p, "ok": ok, "detail": d} for p, ok, d in results],
            "failures": len(failures)}, indent=2))
    else:
        print("renderer self-test (no Godot, no project, no network)")
        print("=" * 58)
        for prop, ok, detail in results:
            mark = "ok  " if ok else "FAIL"
            tail = f"  ({detail})" if detail else ""
            print(f"  {mark} {prop}{tail}")
        print()
        print(f"properties: {len(results)}   failures: {len(failures)}")
        if failures:
            print("\nA renderer that reports unknown as healthy is worse than no")
            print("renderer: it manufactures confidence.")
        else:
            print("All renderer properties held.")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
