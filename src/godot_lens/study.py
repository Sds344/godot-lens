"""Reproducible studies: pin what was tested, then compare source against runtime.

Why this module exists
----------------------
An evaluation whose subject is moving cannot attribute a change in results to
anything. If a benchmark is measured against a project that is being edited
concurrently, a difference between two runs means "the agent improved OR the
project changed", and those are indistinguishable after the fact. This was a live
problem while building this: the sibling project gained three commits and a
doubled runtime script during a single afternoon.

So a study pins a fingerprint first, then measures. The fingerprint is recorded,
never inferred, and it covers all three things that can move independently:

    the project under test   (commit + whether its tree was dirty)
    the observation tooling  (schema version)
    the engine               (version)

The fingerprint records the dirty flag and the dirty file count on purpose. A
"clean" claim on a dirty tree is a false claim about reproducibility, and the
honest version of it is cheap to keep.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from . import fidelity, paths, protocol


# --- fingerprints -------------------------------------------------------------

def _git(project, *args):
    try:
        p = subprocess.run(["git", "-C", project, *args],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def fingerprint(project):
    """Identify the project, the tooling and the engine unambiguously.

    Every field is best-effort: a project that is not a git repository still gets
    a fingerprint, it just has no commit. Reporting a partial fingerprint is
    better than refusing to record one, as long as the gaps are visible rather
    than silently filled with a plausible default.
    """
    commit = _git(project, "rev-parse", "HEAD")
    dirty_out = _git(project, "status", "--porcelain")
    dirty_files = len([l for l in (dirty_out or "").splitlines() if l.strip()])
    return {
        "project_path": os.path.abspath(project),
        "project_commit": (commit or "").strip() or None,
        "project_commit_short": (commit or "").strip()[:7] or None,
        "project_dirty": dirty_files > 0,
        "project_dirty_files": dirty_files,
        "schema": protocol.SCHEMA,
        "schema_version": protocol.VERSION,
        "engine": paths.godot_version(),
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def fingerprint_id(fp):
    """A short stable id for a fingerprint, so results can be labelled by it."""
    material = json.dumps({
        "commit": fp.get("project_commit"),
        "dirty": fp.get("project_dirty_files"),
        "engine": fp.get("engine"),
        "schema": fp.get("schema_version"),
    }, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def fingerprint_line(fp):
    commit = fp.get("project_commit_short") or "(no git)"
    dirty = (f" +{fp['project_dirty_files']} uncommitted"
             if fp.get("project_dirty") else " clean")
    return (f"project {commit}{dirty} | engine {fp.get('engine') or '?'} | "
            f"schema {fp.get('schema_version')} | id {fingerprint_id(fp)}")


# --- baseline snapshots -------------------------------------------------------

def snapshot_dir(kit, name):
    return os.path.join(kit, "experiments", name)


def read_snapshot(kit, name):
    path = os.path.join(snapshot_dir(kit, name), "snapshot.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def capture_snapshot(kit, name, project):
    """Record the current state of the project under test.

    Writes only inside the kit. The sibling project is deliberately not touched —
    not even tagged, because a tag in someone else's repository is still a change
    to it, and the parallel-work boundary says the observation project reads it
    and nothing more. The commit id and the dirty-file count are enough to
    reconstruct which state was measured.
    """
    fp = fingerprint(project)
    out = snapshot_dir(kit, name)
    os.makedirs(out, exist_ok=True)

    story_path = os.path.join(project, "data", "story.json")
    story = None
    if os.path.exists(story_path):
        with open(story_path, encoding="utf-8") as fh:
            story = json.load(fh)
        # The IR is the specification side of the comparison, so a study that
        # pinned the runtime but let the IR drift would compare against a
        # specification it can no longer see. Copying it is what makes the study
        # re-runnable after the project has moved on.
        shutil.copy2(story_path, os.path.join(out, "story.json"))

    snapshot = {
        "study": name,
        "kind": "semantic-fidelity",
        "fingerprint": fp,
        "fingerprint_id": fingerprint_id(fp),
        "story_present": story is not None,
        "story_labels": [l.get("name") for l in (story or {}).get("labels", [])],
        "notes": ("Pinned by godot-lens. The project under test is not modified; "
                  "this file records what was measured so a result can be "
                  "attributed to a version."),
    }
    with open(os.path.join(out, "snapshot.json"), "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return snapshot


# --- trace capture ------------------------------------------------------------

def capture_trace(project, scene, frames=200, inputs=30):
    """Run the game headless and record its display timeline.

    Delegates to `tools/godot/trace_dump.gd`, which observes the scene rather
    than the application. Keeping that boundary means this function never needs
    to know what produced the project.
    """
    # Refuse to boot an unrunnable project. Godot's run path raises an OS alert
    # when there is nothing runnable — on Linux a `zenity` modal dialog, which
    # `--headless` does not suppress. Checked first so observing a project cannot
    # put a dialog on the desktop.
    problem = _main_scene_problem(project)
    if problem:
        raise SystemExit(
            f"study: cannot record a trace — {problem}\n"
            "\n"
            "The game was not started, so no display timeline exists. Reporting an\n"
            "empty trace here would read as 'the game showed nothing', which is a\n"
            "different and much stronger claim than 'the game could not run'.")

    dumper = os.path.join(paths.require_kit(), "tools", "godot", "trace_dump.gd")
    if not os.path.exists(dumper):
        raise SystemExit(f"study: trace dumper missing at {dumper}")

    env = dict(os.environ)
    state = paths.state_dir_env()
    if state:
        env.update(state)

    cmd = [paths.godot_binary(), "--headless", "--script", dumper, "--",
           scene, "--frames", str(frames), "--inputs", str(inputs)]
    try:
        p = subprocess.run(cmd, cwd=project, env=env, capture_output=True,
                           text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise SystemExit("study: the game did not finish within 600s; the trace "
                         "would be truncated, so it is not reported as a result.")
    return parse_trace(p.stdout + p.stderr)


def _main_scene_problem(project):
    """Why the project cannot be booted, or None. Shares the shell checker.

    One implementation, used by `godot_context.py`, `godot_validate.sh` and here,
    so all three refuse to boot for the same stated reason rather than each
    inventing its own.
    """
    checker = os.path.join(paths.require_kit(), "tools", "main_scene_check.sh")
    if not os.path.exists(checker):
        return None
    try:
        p = subprocess.run(["bash", checker], cwd=project, capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode == 0:
        return None
    return (p.stdout or p.stderr).strip() or "main scene is not runnable"


def parse_trace(text):
    begin, end = "<<<TRACE_JSON_BEGIN>>>", "<<<TRACE_JSON_END>>>"
    if begin not in text or end not in text:
        raise SystemExit(
            "study: the trace dumper produced no payload.\n"
            "Godot prints its own noise on the same stream, so a missing payload\n"
            "usually means it failed before observing anything. Raw output tail:\n"
            + "\n".join(text.strip().splitlines()[-15:]))
    body = text.split(begin, 1)[1].split(end, 1)[0]
    trace = json.loads(body)
    return _add_initial_display(trace)


def _add_initial_display(trace):
    """Record what is displayed before anything changes.

    A trace of *changes* structurally cannot report what was already on screen,
    so the first line of a generated game — set during `_ready()` and only ever
    observed, never changed — is missing from the events. That is exactly the
    first line of dialogue, and dropping it produced a false "never displayed at
    runtime" against a correct project.

    The dumper supplies the raw initial nodes; they are turned into ordinary
    `text` events so the whole timeline, including frame 0, is interpreted by the
    one pairing rule in `fidelity.group_by_frame`. Synthesising a pre-paired
    result here instead would give the initial frame different semantics from
    every later frame, and a speaker mismatch on line one is what that looks
    like.
    """
    initial = trace.get("initial_display") or {}
    nodes = initial.get("nodes") or []
    if not nodes:
        return trace
    # Trust the dumper's frame number. Recomputing it from the existing events
    # silently moved frame 0 onto frame 1 — merging the opening line into the
    # first change and losing it — because the first observed change happens at
    # frame 1, not 0.
    frame0 = initial.get("frame", 0)
    events = trace.get("events", []) or []
    synthetic = [{
        "frame": frame0,
        "kind": "text",
        "path": n.get("path"),
        "type": n.get("type"),
        "from": "",
        "to": n.get("text"),
        "position": n.get("position", ""),
        "note": "observed before any input (set during _ready)",
    } for n in nodes if n.get("text")]
    trace["events"] = synthetic + events
    trace["event_count"] = len(trace["events"])
    return trace


# --- instrumented run: which source spans did the game actually execute? ------

# The line the generated runtime prints for every IR node it processes. It is the
# one point where the executed node (with its source span) is in hand, so the
# probe is injected immediately after it.
ANCHOR = '_debug("node kind=" + kind + " label=" + _label_name)'

PROBE_AUTOLOAD = 'LensProbe="*res://_lens_probe.gd"'


def _capture_call(indent):
    """The injected statement, indented to match the anchor line's own indentation.

    GDScript rejects a file that mixes tabs and spaces, and the generated runtime
    may be authored with either. Hard-coding tabs broke the instrumented copy
    outright: the engine failed to parse `story_runtime.gd`, so the game never
    ran and the probe reported zero spans — which is indistinguishable from "the
    game genuinely executed nothing". The injected code is the visitor here, so
    it must match its host.
    """
    unit = "\t" if indent.startswith("\t") else "    "
    return (f"{indent}if has_node(\"/root/LensProbe\"):\n"
            f"{indent}{unit}get_node(\"/root/LensProbe\")._lens_capture(node, _label_name)")


def _indent_of(line):
    return line[:len(line) - len(line.lstrip(" \t"))]


def _instrument(project):
    """Copy the project, inject the probe, and return (copy, out_path).

    A copy is used rather than editing in place: the project under test may be
    someone else's working tree with uncommitted work, and an observation tool
    that mutates what it observes is not an observation tool. The copy excludes
    `.godot/` so Godot re-imports rather than trusting a cache built elsewhere.
    """
    src = os.path.abspath(project)
    dst = os.path.join(os.path.abspath(os.environ.get("GODOT_LENS_HOME")
                                       or os.path.join(paths.require_kit(), ".tooling")),
                       "probe-project")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, symlinks=True,
                    ignore=shutil.ignore_patterns(".godot"))

    # 1. The probe script.
    shutil.copy2(os.path.join(paths.require_kit(), "tools", "godot", "lens_probe.gd"),
                 os.path.join(dst, "_lens_probe.gd"))

    # 2. Register it as an autoload so it is reachable at a fixed path.
    cfg_path = os.path.join(dst, "project.godot")
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = fh.read()
    if "[autoload]" not in cfg:
        cfg = cfg.rstrip("\n") + f"\n\n[autoload]\n{PROBE_AUTOLOAD}\n"
        with open(cfg_path, "w", encoding="utf-8") as fh:
            fh.write(cfg)

    # 3. One-line injection at the anchor.
    targets = []
    for dirpath, dirnames, filenames in os.walk(dst):
        dirnames[:] = [d for d in dirnames if d not in (".godot", "addons")]
        for fn in filenames:
            if fn.endswith(".gd") and fn != "_lens_probe.gd":
                targets.append(os.path.join(dirpath, fn))

    injected = 0
    for path in targets:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if ANCHOR not in text:
            continue
        # Find the anchor's own line so the injected statement inherits its
        # indentation and its indent character.
        for line in text.splitlines():
            if ANCHOR in line:
                call = _capture_call(_indent_of(line))
                break
        text = text.replace(ANCHOR, ANCHOR + "\n" + call, 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        injected += 1

    if injected == 0:
        raise SystemExit(
            "study: could not instrument the project.\n"
            f"Expected the anchor line in some .gd file:\n  {ANCHOR}\n"
            "The generated runtime may have changed. Executed-source coverage "
            "needs that hook, and reporting a result without it would be a "
            "measurement of nothing.")
    return dst, os.path.join(dst, "data", "_lens_spans.json")


def capture_spans(project, scene="res://Main.tscn", frames=400):
    """Run the instrumented copy and return the executed source spans."""
    dst, out_path = _instrument(project)
    env = dict(os.environ)
    state = paths.state_dir_env()
    if state:
        env.update(state)
    env["GODOT_LENS_PROBE_OUT"] = out_path

    cmd = [paths.godot_binary(), "--headless", "--quit-after", str(frames)]
    try:
        p = subprocess.run(cmd, cwd=dst, env=env, capture_output=True,
                           text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise SystemExit("study: the instrumented run did not finish within 600s.")

    if not os.path.exists(out_path):
        raise SystemExit(
            "study: the probe wrote no output, so no source span was recorded.\n"
            "Raw engine output tail:\n"
            + "\n".join((p.stdout + p.stderr).strip().splitlines()[-15:]))
    with open(out_path, encoding="utf-8") as fh:
        return json.load(fh), (p.stdout + p.stderr)


# --- executed-span verification ----------------------------------------------

# Which IR field carries the displayed text for each node kind.
TEXT_FIELD_FOR = {"dialogue": "text", "narration": "text", "menu": "prompt"}


def ir_spans(story):
    """Map every text-bearing IR node to its source span."""
    out = {}
    for label in story.get("labels", []):
        _collect_spans(label.get("body", []), str(label.get("name", "")), out)
    return out


def _collect_spans(body, label_name, out):
    for node in body:
        if not isinstance(node, dict):
            continue
        src = node.get("source")
        if isinstance(src, dict) and src.get("line") is not None:
            try:
                line = int(src["line"])
            except (TypeError, ValueError):
                line = None
            if line is not None and line > 0:
                out[(str(src.get("path", "")), line)] = {
                    "kind": str(node.get("kind", "")),
                    "label": label_name,
                    "text": fidelity.normalise(
                        node.get(TEXT_FIELD_FOR.get(str(node.get("kind", "")), "text"))),
                    "speaker": fidelity.normalise(node.get("speaker")),
                }
        # Recurse through every container shape the IR defines.
        for branch in node.get("branches", []) or []:
            if isinstance(branch, dict):
                _collect_spans(branch.get("body", []) or [], label_name, out)
        _collect_spans(node.get("else_body", []) or [], label_name, out)
        for key in ("choices", "clauses"):
            for choice in node.get(key, []) or []:
                if isinstance(choice, dict):
                    _collect_spans(choice.get("body", []) or [], label_name, out)
        _collect_spans(node.get("body", []) or [], label_name, out)


def verify_spans(story, probe_result):
    """Check that every executed source span has a corresponding IR node.

    This is the authoritative fidelity check, and it is deliberately one-sided:
    each EXECUTED span must resolve to an IR node of a compatible kind. It does
    not require every IR node to be executed, because unexecuted branches are
    normal and flagging them would fail every project with an `if`.

    That asymmetry is the point. When a converter drops a statement, the runtime
    never learns about it, so the statement lands in no branch and produces no
    span — yet the runtime still reports having processed the surrounding
    construct. The divergence shows up as an executed span whose source line has
    no IR node, or whose IR node is of the wrong kind.
    """
    spans = ir_spans(story)
    executed = probe_result.get("spans", []) or []
    unmatched = []
    for mark in executed:
        key = (str(mark.get("path", "")), int(mark.get("line", -1)))
        node = spans.get(key)
        if node is None:
            unmatched.append({
                "span": {"path": key[0], "line": key[1]},
                "executed_kind": mark.get("kind"),
                "label": mark.get("label"),
                "reason": ("the runtime executed a source position that has no IR "
                           "node; a statement the converter did not carry over"),
            })
            continue
        if node["kind"] != mark.get("kind"):
            unmatched.append({
                "span": {"path": key[0], "line": key[1]},
                "executed_kind": mark.get("kind"),
                "ir_kind": node["kind"],
                "label": mark.get("label"),
                "reason": ("the IR classifies this source position differently from "
                           "what the runtime executed"),
            })
    return {
        "agree": not unmatched,
        "executed_span_count": len(executed),
        "ir_span_count": len(spans),
        "uncovered_executions": unmatched,
    }


def render_spans(report, limit=15):
    if report["agree"]:
        return (f"AGREE — all {report['executed_span_count']} executed source "
                f"spans resolve to IR nodes "
                f"({report['ir_span_count']} IR spans total).")
    lines = [f"DIVERGED — {len(report['uncovered_executions'])} of "
             f"{report['executed_span_count']} executed spans do not resolve "
             "to IR nodes."]
    for u in report["uncovered_executions"][:limit]:
        src = u["span"]
        lines.append("")
        lines.append(f"[UNMAPPED] {src['path']}:{src['line']}  "
                     f"executed as {u['executed_kind']!r}"
                     + (f" but IR says {u['ir_kind']!r}" if u.get("ir_kind") else ""))
        lines.append(f"  reason: {u['reason']}")
    extra = len(report["uncovered_executions"]) - limit
    if extra > 0:
        lines.append(f"\n... and {extra} more.")
    return "\n".join(lines)


# --- studies ------------------------------------------------------------------

# --- IR internal consistency: lines transcribed by neither neighbour ----------

# Source lines that legitimately produce no IR node of their own: Ren'Py control
# keywords, declarations, and punctuation. A gap made only of these is not
# evidence of loss, and flagging them would bury the real signal.
TRANSPARENT_TOKENS = (
    "label ", "define ", "default ", "init ", "python:", "transform ",
    "return", "pass", "else:", "elif ", "if ", "menu", "jump ", "call ",
    "$", "image ", "scene ", "show ", "hide ", "play ", "stop ", "with ",
    "nvl ", "window ", "#", '"""', "'''", "-", "...",
)


def ir_gaps(story, project):
    """Source lines covered by the IR's span range but by no IR node.

    This is the check that closes a hole the other two leave open, and the hole
    was found by experiment rather than by reasoning: **removing a node from the
    IR makes the IR-runtime comparison pass**, because the expectation simply
    shrinks to match. Both other instruments compare the runtime against the IR,
    so neither can see content the IR never modelled.

    The gap check looks at the IR alone. The IR records a source line for every
    node, so a line inside that range which no node claims is a line the
    converter walked past. A statement that produces no node at all — which is
    precisely what happened when `define narrator = Character(None)` was folded
    into a label body — shows up here, and nowhere else.

    Deliberately conservative: only the range between the first and last
    transcribed line is examined, blank lines and comment lines are skipped, and a
    line whose text matches a control keyword is skipped. A checker that cries
    wolf gets ignored, and then so does the layer it belongs to.
    """
    if not project:
        return {"agree": True, "gaps": [], "note": "no project given"}
    spans = ir_spans(story)
    by_file = {}
    for (path, line) in spans:
        by_file.setdefault(path, []).append(line)

    gaps = []
    for path, lines in by_file.items():
        if not lines:
            continue
        source = _read_source(project, path)
        if source is None:
            continue
        lo, hi = min(lines), max(lines)
        claimed = set(lines)
        for line_no in range(lo, hi + 1):
            if line_no in claimed:
                continue
            text = source.get(line_no, "").strip()
            if not text:
                continue
            if any(text.startswith(tok) for tok in TRANSPARENT_TOKENS):
                continue
            gaps.append({
                "path": path, "line": line_no, "text": text,
                "reason": ("inside the transcribed range of this file, but no IR "
                           "node claims it and it is not a control keyword"),
            })
    return {
        "agree": not gaps,
        "gap_count": len(gaps),
        "gaps": gaps,
        "files_examined": sorted(by_file),
    }


def _read_source(project, rel_path):
    """Read a source file, tolerating paths that are relative or absent."""
    candidates = [
        os.path.join(project, rel_path),
        os.path.join(project, "game", rel_path),
        os.path.join(project, os.path.basename(rel_path)),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    return {i + 1: l.rstrip("\n") for i, l in enumerate(fh)}
            except OSError:
                continue
    return None


def render_gaps(report, limit=15):
    if report.get("agree"):
        return ("AGREE — every non-trivial source line inside the transcribed "
                "range is claimed by an IR node.")
    lines = [f"DIVERGED — {report['gap_count']} source line(s) inside the "
             "transcribed range belong to no IR node."]
    for g in report["gaps"][:limit]:
        lines.append("")
        lines.append(f"[GAP] {g['path']}:{g['line']}  {g['text'][:80]}")
        lines.append(f"  reason: {g['reason']}")
    extra = report["gap_count"] - limit
    if extra > 0:
        lines.append(f"\n... and {extra} more.")
    return "\n".join(lines)


# --- structural placement: is each body node inside its label's own range? ----

def ir_placements(story):
    """Body nodes whose source line lies outside the label that contains them.

    This closes the gap that defeated every other instrument in this module, and
    it is worth recording *how* it was found. Three checks — executed spans, IR
    gaps, and display order — were all run against the real broken IR from the
    converter's history (commit `9b5dd95`) and **all three reported agreement**,
    while the tooling of the day also reported `0 error, 0 warning` on a project
    that was missing a line of the story.

    The reason none of them could see it is that the defect is not a mismatch
    between the IR and the runtime. The broken IR is a *superset* of what runs: it
    still carries every node the game executes, plus one extra. Comparing runtime
    against IR cannot detect an IR node that is merely misplaced.

    The actual defect was structural. Ren'Py's

        define narrator = Character(None)      # script.rpy line 2

    is a top-level declaration, but the converter emitted it as a node inside the
    `start` label's body:

        "kind": "unsupported", "construct": "define",
        "source": {"path": "script.rpy", "line": 2}

    `start` begins at line 10, so line 2 sits *before the label it was attached
    to*. A body node whose source precedes its own label's first statement cannot
    belong to that label — declarations are hoisted, statements are not. That is a
    structural contradiction inside the IR, checkable without a runtime and
    without parsing Ren'Py.
    """
    problems = []
    for label in story.get("labels", []):
        name = str(label.get("name", ""))
        body = label.get("body", []) or []
        nodes = [n for n in _iter_body(body)]
        # Group by source file: a label's statements all come from one file, and
        # the line range must be computed from the file the statements are in.
        by_path = {}
        for node in nodes:
            src = node.get("source")
            if isinstance(src, dict) and isinstance(src.get("line"), int):
                by_path.setdefault(str(src.get("path", "")), []).append(node)

        for path, group in by_path.items():
            # The transcribed range is delimited by nodes whose kind is a real
            # statement the runtime acts on. An `unsupported` node is by
            # definition one the converter could not map, so letting it define the
            # range would hide exactly the case being looked for — the offending
            # node becomes the minimum and then trivially "inside" its own range.
            transcribed = [n["source"]["line"] for n in group
                           if str(n.get("kind", "")) != "unsupported"
                           and str(n.get("mapping", "")) != "unsupported"]
            if len(transcribed) < 2:
                continue
            first_line = min(transcribed)

            for node in group:
                line = node["source"]["line"]
                if line >= first_line:
                    continue
                # Outside the transcribed range. It is a defect only if it is
                # unattributable content rather than a declaration the converter
                # legitimately hoisted out of the body.
                problems.append({
                    "label": name,
                    "kind": str(node.get("kind", "")),
                    "construct": str(node.get("construct", "")),
                    "path": path,
                    "line": line,
                    "label_first_line": first_line,
                    "text": str(node.get("text", ""))[:90],
                    "reason": (f"this node's source line {line} precedes every "
                               f"statement of label {name!r} (first at line "
                               f"{first_line}) in the same file, so it cannot "
                               "belong to that label's body — a declaration "
                               "appears to have been folded into a statement "
                               "body, where the runtime will never reach it"),
                })
    return {"agree": not problems, "problems": problems}


def _iter_body(body):
    """Yield every node in a body, including nested containers."""
    for node in body:
        if not isinstance(node, dict):
            continue
        yield node
        for branch in node.get("branches", []) or []:
            if isinstance(branch, dict):
                yield from _iter_body(branch.get("body", []) or [])
        yield from _iter_body(node.get("else_body", []) or [])
        for key in ("choices", "clauses"):
            for choice in node.get(key, []) or []:
                if isinstance(choice, dict):
                    yield from _iter_body(choice.get("body", []) or [])
        yield from _iter_body(node.get("body", []) or [])


def render_placements(report, limit=15):
    if report.get("agree"):
        return ("AGREE — every body node's source line falls inside its "
                "label's own range.")
    lines = [f"DIVERGED — {len(report['problems'])} body node(s) lie outside "
             "the label that contains them."]
    for p in report["problems"][:limit]:
        lines.append("")
        lines.append(f"[MISPLACED] {p['path']}:{p['line']} "
                     f"{p['kind']}"
                     + (f"/{p['construct']}" if p["construct"] else "")
                     + f"  inside label {p['label']!r}")
        if p["text"]:
            lines.append(f"  text  : {p['text']}")
        lines.append(f"  reason: {p['reason']}")
    extra = len(report["problems"]) - limit
    if extra > 0:
        lines.append(f"\n... and {extra} more.")
    return "\n".join(lines)


def run_fidelity(kit, name, project, scene="res://Main.tscn",
                 frames=200, inputs=30, mutate=None, keep_trace=True):
    """Run both fidelity instruments and record the result.

    Two instruments, deliberately, because each is blind to what the other sees:

    * **executed-span check** — every source position the runtime executed must
      resolve to an IR node. Catches content the converter DROPPED: the runtime
      still reports processing the surrounding construct, so the missing
      statement shows up as an executed span with no IR node.
    * **display-trace check** — the displays the IR promises for the executed path
      must appear, in order, in what the runtime showed. Catches wrong ORDER and
      wrong SPEAKER, which the span check cannot see because both spans exist.

    The trace check alone is not sufficient, and the reason is worth stating: it
    compares the runtime against the IR, so removing a node from the IR shrinks
    the expectation and the comparison still agrees. Verified directly — a
    dropped narration node passes the trace check and fails the span check. That
    asymmetry is why the span check is authoritative for content loss and the
    trace check is corroborating evidence about presentation.

    `mutate` corrupts the IR copy's expected side, which is how the instruments
    are verified rather than assumed.
    """
    out = snapshot_dir(kit, name)
    os.makedirs(out, exist_ok=True)

    story_path = os.path.join(out, "story.json")
    if not os.path.exists(story_path):
        # Fall back to the live project so a study can run without a prior
        # snapshot capture.
        story_path = os.path.join(project, "data", "story.json")
    if not os.path.exists(story_path):
        raise SystemExit(f"study: no story.json found (looked at {story_path}).")
    with open(story_path, encoding="utf-8") as fh:
        story = json.load(fh)

    mutation_note = None
    if mutate:
        story, mutation_note = _apply_ir_mutation(story, mutate)

    # Instrument 1: did the runtime execute any source position the IR lacks?
    probe, _probe_raw = capture_spans(project, scene=scene,
                                      frames=max(frames, 400))
    span_report = verify_spans(story, probe)

    # Instrument 2: does the display order match, for the path actually taken?
    trace = capture_trace(project, scene, frames=frames, inputs=inputs)
    actual = fidelity.actual_trace(trace)
    expected = fidelity.executed_expected(story, probe)
    trace_report = fidelity.compare(expected, actual)

    # Instruments 3 and 4 read the IR alone. They are not redundant: the two
    # runtime comparisons cannot see content the IR never modelled, and cannot
    # see a node that is present but attached to the wrong construct. Both were
    # shown to matter by running them against the real defect from the
    # converter's history.
    gap_report = ir_gaps(story, project)
    placement_report = ir_placements(story)

    result = {
        "study": name,
        "fingerprint": fingerprint(project),
        "scene": scene,
        "frames_observed": trace.get("frames_observed"),
        "inputs_sent": trace.get("inputs_sent"),
        "trace_stalled": trace.get("stalled"),
        "stall_reason": trace.get("stall_reason"),
        "executed_spans": span_report,
        "display_trace": trace_report,
        "ir_gaps": gap_report,
        "ir_placements": placement_report,
        # Agreement requires ALL FOUR. One boolean over one instrument would let
        # every blind spot documented above silently cover the others.
        "agree": bool(span_report["agree"] and trace_report["agree"]
                      and gap_report["agree"] and placement_report["agree"]),
    }
    if mutation_note:
        result["mutation"] = mutation_note

    if keep_trace:
        with open(os.path.join(out, "trace.json"), "w", encoding="utf-8") as fh:
            json.dump(trace, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        with open(os.path.join(out, "probe.json"), "w", encoding="utf-8") as fh:
            json.dump(probe, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return result


# Mutations of the EXPECTED side. Each models a converter defect class that the
# instruments are required to catch; a check never shown to fail is not evidence.
MUTATIONS = {
    "drop-narration": (
        "Delete every narration node from the IR. Models the "
        "`define narrator = Character(None)` defect, where a narration statement "
        "is folded into another construct and never reaches the player while the "
        "generated Godot project stays perfectly valid."),
    "drop-last": (
        "Delete the final IR display node. Models a truncation defect: control "
        "flow ends early, so a valid project simply stops saying things."),
    "reorder": (
        "Swap two adjacent IR display nodes. Models a control-flow mis-ordering, "
        "where the right lines exist but arrive in the wrong sequence."),
    "wrong-speaker": (
        "Clear a dialogue node's speaker. Models attribution loss: the line is "
        "shown but no longer attributed to the character who said it."),
}


def _apply_ir_mutation(story, mutate):
    """Apply a mutation to a copy of the IR, returning (story, note)."""
    if mutate not in MUTATIONS:
        raise SystemExit(f"study: unknown mutation {mutate!r}. "
                         f"Known: {', '.join(sorted(MUTATIONS))}")
    import copy as _copy
    mutated = _copy.deepcopy(story)
    note = {"mutation": mutate, "why": MUTATIONS[mutate]}

    if mutate in ("drop-narration", "drop-last"):
        kind = "narration" if mutate == "drop-narration" else None
        removed = [0]

        def prune(body):
            keep = []
            for n in body:
                if isinstance(n, dict):
                    if kind is None:
                        # drop-last: handled by the post-pass below
                        pass
                    elif n.get("kind") == kind:
                        removed[0] += 1
                        continue
                    for key in ("branches", "choices", "clauses"):
                        for sub in n.get(key, []) or []:
                            if isinstance(sub, dict):
                                prune(sub.get("body", []) or [])
                    if isinstance(n.get("else_body"), list):
                        prune(n["else_body"])
                    if isinstance(n.get("body"), list):
                        prune(n["body"])
                keep.append(n)
            body[:] = keep

        for label in mutated.get("labels", []):
            prune(label.get("body", []))
        if kind is None:
            displays = _display_nodes(mutated)
            if displays:
                displays[-1].pop("text", None)
                removed[0] = 1
        note["removed"] = removed[0]
        return mutated, note

    if mutate == "reorder":
        displays = _display_nodes(mutated)
        if len(displays) >= 2:
            displays[0]["text"], displays[1]["text"] = (
                displays[1].get("text"), displays[0].get("text"))
            note["swapped"] = 2
        return mutated, note

    if mutate == "wrong-speaker":
        displays = [n for n in _display_nodes(mutated)
                    if n.get("kind") == "dialogue"]
        if displays:
            note["cleared_speaker"] = len(displays)
            for n in displays:
                n["speaker"] = None
        return mutated, note

    return mutated, note


def _display_nodes(story):
    """Every text-bearing node in document order, for mutation purposes."""
    out = []
    for label in story.get("labels", []):
        _collect_display_nodes(label.get("body", []), out)
    return out


def _collect_display_nodes(body, out):
    for n in body:
        if not isinstance(n, dict):
            continue
        if n.get("kind") in TEXT_FIELD_FOR:
            out.append(n)
        for branch in n.get("branches", []) or []:
            if isinstance(branch, dict):
                _collect_display_nodes(branch.get("body", []) or [], out)
        _collect_display_nodes(n.get("else_body", []) or [], out)
        for key in ("choices", "clauses"):
            for choice in n.get(key, []) or []:
                if isinstance(choice, dict):
                    _collect_display_nodes(choice.get("body", []) or [], out)
        _collect_display_nodes(n.get("body", []) or [], out)


def render_result(result):
    """Human report for both instruments, with the blind spot stated."""
    lines = []
    lines.append(f"study:       {result['study']}")
    lines.append(f"fingerprint: {fingerprint_line(result['fingerprint'])}")
    lines.append(f"scene:       {result['scene']}  "
                 f"frames={result['frames_observed']} "
                 f"inputs={result['inputs_sent']}")
    if result.get("mutation"):
        lines.append(f"mutation:    {result['mutation']['mutation']} — "
                     f"{result['mutation']['why']}")
    if result.get("trace_stalled"):
        lines.append(f"note:        the game stopped advancing: "
                     f"{result.get('stall_reason', '')}")
    lines.append("")
    lines.append("-- instrument 1/4: executed source spans vs IR "
                 "(content the runtime ran but the IR lacks) --")
    lines.append(render_spans(result["executed_spans"]))
    lines.append("")
    lines.append("-- instrument 2/4: display order and attribution "
                 "(blind to content the IR already dropped) --")
    lines.append(fidelity.render(result["display_trace"], []))
    lines.append("")
    lines.append("-- instrument 3/4: IR gaps "
                 "(source lines inside the transcribed range with no IR node) --")
    lines.append(render_gaps(result["ir_gaps"]))
    lines.append("")
    lines.append("-- instrument 4/4: IR placement "
                 "(body nodes that contradict their own label's range) --")
    lines.append(render_placements(result["ir_placements"]))
    lines.append("")
    lines.append("AGREE (all four)" if result["agree"] else "DIVERGED")
    return "\n".join(lines)
