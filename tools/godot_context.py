#!/usr/bin/env python3
"""godot_context.py — one call that returns the whole Godot situation as JSON.

The single entry point an agent should call FIRST when it needs to know what is
going on. It aggregates the other tools rather than replacing them:

  engine    - which Godot, and whether the API reference matches it
  project   - scenes/scripts on disk, plus whether the import cache exists
  api       - a compact index of classes (full detail stays in godot_api.py)
  scenes    - per-scene node trees, declared (static) and post-_ready (runtime)
  runtime   - main-scene boot: errors and warnings
  findings  - the validator's structured summary

Design notes that matter:

* Bounded output. Everything is summarised and truncated; the point is fast
  orientation, not completeness. Drill down with godot_api.py / godot_scene.sh /
  godot_validate.sh once you know where to look.
* Static and runtime scene views are BOTH included, because they genuinely
  differ: a scene can declare no children and build its whole UI in _ready().
* Failures are reported, not raised. A broken project must still produce a
  usable context payload, otherwise the agent is blind exactly when it most
  needs sight.

Usage:
  tools/godot_context.py            # full payload
  tools/godot_context.py --summary  # one-screen human digest
  tools/godot_context.py --fast     # skip runtime boot (no game execution)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_path import find_project, state_dir  # noqa: E402

PROJECT = find_project()
TOOLS = os.path.join(ROOT, "tools")
GODOT = os.environ.get("GODOT", "godot")

# Godot needs writable XDG homes; the kit directory may be read-only.
STATE = state_dir()
if STATE is None:
    sys.exit(
        "godot_context: no writable state directory.\n"
        "Godot aborts with a bare signal 11 when it cannot write its config/data\n"
        "dirs. Set GODOT_LENS_HOME to a writable path and retry."
    )

ENV = {
    **os.environ,
    "XDG_CONFIG_HOME": os.path.join(STATE, "godot_home", "config"),
    "XDG_DATA_HOME": os.path.join(STATE, "godot_home", "data"),
    "XDG_CACHE_HOME": os.path.join(STATE, "godot_home", "cache"),
}
for p in (ENV["XDG_CONFIG_HOME"], ENV["XDG_DATA_HOME"], ENV["XDG_CACHE_HOME"]):
    os.makedirs(p, exist_ok=True)

NOISE = re.compile(
    r"godot2026|dir_access|editor_settings|Error saving editor settings"
    r"|Cannot save file|^Godot Engine v|^$"
)


def run(cmd, timeout=180, cwd=None):
    try:
        p = subprocess.run(cmd, cwd=cwd or PROJECT, env=ENV, timeout=timeout,
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except FileNotFoundError as e:
        return 127, str(e)


def clean(text):
    return "\n".join(l for l in text.splitlines() if not NOISE.search(l)).strip()


def section_engine():
    _, ver = run([GODOT, "--version"], timeout=30)
    installed = ver.strip().splitlines()[0] if ver.strip() else "unknown"
    dump = os.path.join(STATE, "api-dump", "extension_api.json")
    out = {"installed": installed, "api_dump_present": os.path.exists(dump)}
    if out["api_dump_present"]:
        try:
            with open(dump, encoding="utf-8") as fh:
                h = json.load(fh)["header"]
            out["api_dump_version"] = h["version_full_name"]
            a = f"{h['version_major']}.{h['version_minor']}.{h['version_patch']}"
            b = re.match(r"(\d+\.\d+\.\d+)", installed)
            out["api_matches_engine"] = bool(b and a == b.group(1))
        except Exception as e:  # pragma: no cover - defensive
            out["api_dump_error"] = str(e)
    return out


def section_project():
    scenes, scripts, others = [], [], 0
    for dirpath, dirnames, filenames in os.walk(PROJECT):
        dirnames[:] = [d for d in dirnames if d not in (".godot", "addons")]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), PROJECT)
            if fn.endswith(".tscn"):
                scenes.append(rel)
            elif fn.endswith(".gd"):
                scripts.append(rel)
            else:
                others += 1
    return {
        "path": PROJECT,
        "main_scene": _main_scene(),
        "scenes": sorted(scenes),
        "scripts": sorted(scripts),
        "other_file_count": others,
        # A missing import cache causes false "missing resource" errors, so the
        # agent needs to know whether a scan is trustworthy yet.
        "import_cache_present": os.path.isdir(os.path.join(PROJECT, ".godot", "imported")),
    }


def _main_scene():
    cfg = os.path.join(PROJECT, "project.godot")
    try:
        with open(cfg, encoding="utf-8") as fh:
            m = re.search(r'run/main_scene="([^"]+)"', fh.read())
            return m.group(1) if m else None
    except OSError:
        return None


def section_api(limit=8):
    dump = os.path.join(STATE, "api-dump", "extension_api.json")
    if not os.path.exists(dump):
        return {"available": False, "hint": "run: tools/godot_api.sh dump"}
    _, out = run([sys.executable, os.path.join(TOOLS, "godot_api.py"), "stats"], timeout=60)
    info = {"available": True, "stats": clean(out)}
    try:
        with open(dump, encoding="utf-8") as fh:
            db = json.load(fh)
        names = [c["name"] for c in db["classes"]]
        info["class_count"] = len(names)
        info["sample_classes"] = names[:limit]
        info["hint"] = ("query detail with: python3 tools/godot_api.py "
                        "class|method|property|signal|enum|search <q>")
    except Exception as e:  # pragma: no cover
        info["error"] = str(e)
    return info


def _parse_scene_json(text):
    # godot_scene.sh already strips the BEGIN/END markers, but the marker form is
    # accepted too so this keeps working if that wrapper changes.
    m = re.search(r"<<<SCENE_JSON_BEGIN>>>(.*?)<<<SCENE_JSON_END>>>", text, re.S)
    candidate = m.group(1) if m else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None


def _tree_summary(node, depth=0, max_depth=6, budget=None):
    """Flatten a tree into a compact list of "indent+name :Type [notes]"."""
    if budget is None:
        budget = [60]
    lines = []
    if depth > max_depth or budget[0] <= 0:
        return lines
    budget[0] -= 1
    notes = []
    if node.get("script"):
        notes.append("script=" + os.path.basename(node["script"]))
    if "text" in node and node["text"] != "":
        t = str(node["text"])
        notes.append(f'text="{t[:40]}"')
    if "shape" in node:
        notes.append("shape=" + ("OK" if node["shape"] else "NULL"))
    if "collision_layer" in node and node["collision_layer"]:
        notes.append(f"layer={node['collision_layer']}")
    if node.get("warnings"):
        notes.append("WARN=" + "; ".join(str(w)[:60] for w in node["warnings"]))
    suffix = ("  [" + ", ".join(notes) + "]") if notes else ""
    lines.append(f"{'  ' * depth}{node.get('name','?')} :{node.get('type','?')}{suffix}")
    for c in node.get("children", []):
        lines.extend(_tree_summary(c, depth + 1, max_depth, budget))
    return lines


def section_scenes(runtime=True, only_main=False):
    proj = section_project()
    targets = proj["scenes"]
    if only_main and proj["main_scene"]:
        main = os.path.basename(proj["main_scene"])
        targets = [t for t in targets if os.path.basename(t) == main] or targets

    result = []
    for rel in targets:
        entry = {"scene": "res://" + rel.replace(os.sep, "/")}
        for view, extra in (("static", []), ("runtime", ["--runtime"])):
            if view == "runtime" and not runtime:
                continue
            _, out = run(["bash", os.path.join(TOOLS, "godot_scene.sh"), rel, *extra],
                         timeout=200)
            data = _parse_scene_json(out)
            if data is None:
                entry[view] = {"error": clean(out)[:400] or "dump failed"}
                continue
            root = data.get("root", {})
            # Keep BOTH: the structured node dicts (so a machine consumer such
            # as godot_diagnose.py can walk real nodes) and the flattened text
            # (so a model gets a cheap readable view). Storing only the text
            # silently starved the diagnostics layer of all node data.
            entry[view] = {
                "root_type": root.get("type"),
                "child_count": root.get("child_count", 0),
                "root": root,
                "tree": _tree_summary(root),
            }
        result.append(entry)
    return result


def section_runtime(frames=180):
    code, out = run([GODOT, "--headless", f"--quit-after", str(frames)], timeout=240)
    body = clean(out)
    errors = re.findall(r"ERROR:.*", body)
    warnings = re.findall(r"WARNING:.*", body)
    return {
        "frames": frames,
        "errors": errors[:20],
        "warnings": warnings[:20],
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def section_findings():
    _, out = run(["bash", os.path.join(TOOLS, "godot_validate.sh"), "--json"], timeout=600)
    recs = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and '"summary"' not in line:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return {"finding_count": len(recs), "findings": recs[:25]}


def build(fast=False, all_scenes=True):
    ctx = {
        "engine": section_engine(),
        "project": section_project(),
        "api": section_api(),
        # Scan EVERY scene by default. Scanning only the main scene silently
        # blinds both the agent and the diagnostics layer to faults living in
        # any other scene, which is exactly where they like to hide.
        "scenes": section_scenes(runtime=not fast, only_main=not all_scenes),
        "runtime": section_runtime(),
        "findings": section_findings(),
    }
    ctx["ok"] = (
        ctx["runtime"]["error_count"] == 0
        and ctx["findings"]["finding_count"] == 0
    )
    return ctx


def summarise(ctx):
    e, p = ctx["engine"], ctx["project"]
    out = [
        f"engine:   {e.get('installed')}"
        f"   api_dump={'yes' if e.get('api_dump_present') else 'NO'}"
        f"   match={e.get('api_matches_engine')}",
        f"project:  {len(p['scenes'])} scenes, {len(p['scripts'])} scripts"
        f"   main={p['main_scene']}"
        f"   import_cache={'yes' if p['import_cache_present'] else 'NO'}",
        f"api:      {ctx['api'].get('class_count','?')} classes"
        f"   ({ctx['api'].get('hint','')})",
    ]
    for s in ctx["scenes"]:
        st, rt = s.get("static", {}), s.get("runtime", {})
        out.append(f"scene:    {s['scene']}")
        out.append(f"    static : {st.get('child_count', '?')} children"
                   f" (root {st.get('root_type','?')})")
        if "runtime" in s:
            out.append(f"    runtime: {rt.get('child_count', '?')} children")
            for line in (rt.get("tree") or [])[:12]:
                out.append("      " + line)
    r = ctx["runtime"]
    out.append(f"runtime:  {r['error_count']} errors, {r['warning_count']} warnings")
    for w in r["warnings"][:5]:
        out.append("    " + w)
    for er in r["errors"][:5]:
        out.append("    " + er)
    f = ctx["findings"]
    out.append(f"findings: {f['finding_count']}")
    for rec in f["findings"][:10]:
        out.append(f"    [{rec.get('layer')}] {rec.get('file','')} "
                   f"line {rec.get('line','?')}: {str(rec.get('message'))[:110]}")
    out.append(f"ok:       {ctx['ok']}")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    fast = "--fast" in args
    # All scenes by default; --main-only narrows it for a faster, shallower look.
    ctx = build(fast=fast, all_scenes="--main-only" not in args)
    if "--summary" in args:
        print(summarise(ctx))
    else:
        print(json.dumps(ctx, indent=2, ensure_ascii=False))
    return 0 if ctx["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
