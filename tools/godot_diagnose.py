#!/usr/bin/env python3
"""godot_diagnose.py — turn raw Godot state into engineering explanations.

The gap this closes: `godot_context.py` can say `CollisionShape2D.shape = null`,
but "so the player falls through the floor" is domain reasoning that currently
happens (or does not) inside the model's head. This layer encodes that
reasoning as explicit rules so it is deterministic, reviewable and cheap.

Input  : the JSON from `godot_context.py` (pipe it in, or let this call it).
Output : findings, each with severity, meaning, causal impact, and a concrete
         suggested action — ordered by how much they actually matter.

Severity is deliberately NOT the editor's. The editor marks an unused variable
and a shape-less collider the same way (a yellow "!"). For an agent they are
worlds apart, so this ranks by *consequence*:

  error   - the game cannot work as intended
  warning - something is very likely to be wrong at play time
  info    - worth knowing, probably harmless

Usage:
  python3 tools/godot_context.py | python3 tools/godot_diagnose.py
  python3 tools/godot_diagnose.py                 # runs context itself
  python3 tools/godot_diagnose.py --json
  python3 tools/godot_diagnose.py --rules         # list the rule catalog
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT = os.path.join(ROOT, "tools", "godot_context.py")

SEV_ORDER = {"error": 0, "warning": 1, "info": 2}


# --- Rule helpers -------------------------------------------------------------
# A rule is: when(node_or_finding) -> finding | None
# Rules must be conservative. A false positive costs the agent real work, so a
# rule only fires on a condition that is unambiguously wrong.

def _walk(node):
    yield node
    for c in node.get("children", []):
        yield from _walk(c)


def _all_nodes(ctx):
    for scene in ctx.get("scenes", []):
        for view in ("static", "runtime"):
            data = scene.get(view) or {}
            root = data.get("root")
            if isinstance(root, dict):
                yield scene.get("scene", "?"), view, root


def rule_collision_shape_no_shape(scene, view, node):
    """A collider with no shape silently does nothing."""
    if node.get("type") == "CollisionShape2D" and node.get("shape") in (None, ""):
        return {
            "severity": "error",
            "code": "COLLISION_SHAPE_MISSING",
            "scene": scene,
            "view": view,
            "node": node.get("name"),
            "meaning": "CollisionShape2D has no Shape resource assigned.",
            "impact": ("This node contributes no collision at all. Its parent "
                       "physics body cannot detect or block anything, so "
                       "characters pass through geometry or fall out of the world."),
            "action": ("Assign a Shape2D (RectangleShape2D / CircleShape2D / "
                       "CapsuleShape2D) to this node's `shape` property."),
        }
    return None


def rule_sprite_no_texture(scene, view, node):
    if node.get("type") == "Sprite2D" and node.get("texture") in (None, ""):
        return {
            "severity": "warning",
            "code": "SPRITE_TEXTURE_MISSING",
            "scene": scene,
            "view": view,
            "node": node.get("name"),
            "meaning": "Sprite2D has no texture; nothing will be drawn.",
            "impact": "The node is invisible at runtime.",
            "action": "Set the sprite's `texture` to a valid Texture2D resource.",
        }
    return None


def rule_collision_layer_zero(scene, view, node):
    """Body that collides with nothing is a common silent misconfiguration."""
    if node.get("type", "").endswith(("Body2D", "Area2D")) and node.get("collision_layer") == 0:
        return {
            "severity": "warning",
            "code": "COLLISION_LAYER_ZERO",
            "scene": scene,
            "view": view,
            "node": node.get("name"),
            "meaning": "collision_layer is 0, so this body is on no layer.",
            "impact": ("Other bodies cannot detect it, and nothing in the scene "
                       "will report contact with it."),
            "action": ("Set collision_layer (and usually collision_mask) to the "
                       "layers this body should occupy and scan."),
        }
    return None


def rule_empty_control_built_at_runtime(scene, view, node):
    """Static emptiness is only alarming when runtime is also empty."""
    return None  # handled at scene level, kept for documentation value


def rule_node_warnings(scene, view, node):
    warns = node.get("warnings") or []
    if warns:
        return {
            "severity": "warning",
            "code": "NODE_CONFIGURATION_WARNING",
            "scene": scene,
            "view": view,
            "node": node.get("name"),
            "meaning": "Node reported its own configuration warnings: "
                       + "; ".join(str(w) for w in warns),
            "impact": "Behaviour is likely to differ from intent.",
            "action": "Resolve the reported configuration issue.",
        }
    return None


NODE_RULES = [
    rule_collision_shape_no_shape,
    rule_sprite_no_texture,
    rule_collision_layer_zero,
    rule_node_warnings,
]


def scene_rules(ctx):
    """Scene-level (not node-level) checks."""
    out = []
    for scene in ctx.get("scenes", []):
        name = scene.get("scene", "?")
        st = scene.get("static") or {}
        rt = scene.get("runtime") or {}
        if "error" in st or "error" in rt:
            out.append({
                "severity": "error",
                "code": "SCENE_DUMP_FAILED",
                "scene": name,
                "meaning": "The scene could not be inspected.",
                "impact": "Nothing is known about this scene's contents.",
                "action": "Fix the load error before reasoning about this scene.",
                "detail": st.get("error") or rt.get("error"),
            })
            continue
        # A main scene that is empty once running usually means a script never ran.
        if rt and rt.get("child_count") == 0 and st.get("child_count") == 0:
            out.append({
                "severity": "warning",
                "code": "SCENE_EMPTY_RUNTIME",
                "scene": name,
                "meaning": ("Scene declares no children and builds none at "
                            "runtime (checked after _ready())."),
                "impact": "Nothing will be displayed from this scene.",
                "action": ("Check that the attached script's _ready() ran, or "
                           "that the scene file actually contains its nodes."),
            })
        # Worth surfacing: the file and the world disagree, which is the single
        # most misleading thing about game projects.
        if st.get("child_count") == 0 and (rt.get("child_count") or 0) > 0:
            out.append({
                "severity": "info",
                "code": "SCENE_BUILT_AT_RUNTIME",
                "scene": name,
                "meaning": (f"Declares 0 children but has {rt['child_count']} "
                            "at runtime; the tree is built by code."),
                "impact": ("Reading the .tscn alone is misleading here. Any "
                           "analysis based on the scene file will miss these nodes."),
                "action": "Use the runtime view for questions about this scene.",
            })
    return out


def runtime_rules(ctx):
    out = []
    rt = ctx.get("runtime") or {}
    for w in rt.get("warnings", []):
        # The converter's own diagnostics are informational, not defects.
        code = "RUNTIME_WARNING"
        sev = "info"
        if "renpy2godot:" in w:
            code = "CONVERTER_FIDELITY_GAP"
            sev = "info"
        out.append({
            "severity": sev,
            "code": code,
            "meaning": w,
            "impact": ("A construct was not fully translated; behaviour may "
                       "differ from the Ren'Py original."
                       if code == "CONVERTER_FIDELITY_GAP"
                       else "Unexpected runtime condition."),
            "action": ("Inspect the source construct before changing runtime code."
                       if code == "CONVERTER_FIDELITY_GAP"
                       else "Investigate the warning's origin."),
        })
    for e in rt.get("errors", []):
        out.append({
            "severity": "error",
            "code": "RUNTIME_ERROR",
            "meaning": e,
            "impact": "The game hit an error during the observed frames.",
            "action": "Fix the underlying cause, then re-run validation.",
        })
    return out


def validate_rules(ctx):
    """Validator findings, excluding any already explained semantically.

    The validator re-reports runtime warnings verbatim, so without this the same
    `push_warning` appears twice: once as a generic VALIDATION_RUNTIME and once
    as a proper CONVERTER_FIDELITY_GAP. The semantic version is the useful one.
    """
    out = []
    already = {str(w).strip() for w in (ctx.get("runtime") or {}).get("warnings", [])}
    for rec in (ctx.get("findings") or {}).get("findings", []):
        msg = str(rec.get("message", "")).strip()
        if rec.get("layer") == "runtime" and msg in already:
            continue  # a semantic rule already explains this one
        out.append({
            "severity": "error" if rec.get("layer") == "static" else "warning",
            "code": "VALIDATION_" + str(rec.get("layer", "?")).upper(),
            "file": rec.get("file"),
            "line": rec.get("line"),
            "meaning": msg[:300],
            "impact": "The project does not currently pass headless validation.",
            "action": "Fix this before treating the project as healthy.",
        })
    return out


def diagnose(ctx):
    findings = []
    for scene, view, root in _all_nodes(ctx):
        for node in _walk(root):
            for rule in NODE_RULES:
                r = rule(scene, view, node)
                if r:
                    findings.append(r)
    findings.extend(scene_rules(ctx))
    findings.extend(runtime_rules(ctx))
    findings.extend(validate_rules(ctx))

    # De-duplicate: the same node is reported for both views.
    seen, unique = set(), []
    for f in findings:
        key = (f.get("code"), f.get("scene"), f.get("node"), f.get("meaning"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)

    unique.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9),
                               f.get("scene") or "", f.get("node") or ""))
    return {
        "summary": {
            "error": sum(1 for f in unique if f["severity"] == "error"),
            "warning": sum(1 for f in unique if f["severity"] == "warning"),
            "info": sum(1 for f in unique if f["severity"] == "info"),
            "total": len(unique),
        },
        "diagnostics": unique,
    }


def load_context():
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
    p = subprocess.run([sys.executable, CONTEXT], capture_output=True, text=True,
                       timeout=900)
    return json.loads(p.stdout)


RULE_CATALOG = [
    ("COLLISION_SHAPE_MISSING", "error",
     "CollisionShape2D with no shape: contributes no collision; bodies pass through."),
    ("COLLISION_LAYER_ZERO", "warning",
     "Physics body on no layer: nothing can detect it."),
    ("SPRITE_TEXTURE_MISSING", "warning", "Sprite2D draws nothing."),
    ("NODE_CONFIGURATION_WARNING", "warning", "Node's own configuration warnings."),
    ("SCENE_EMPTY_RUNTIME", "warning", "Scene empty both on disk and after _ready()."),
    ("SCENE_BUILT_AT_RUNTIME", "info",
     ".tscn declares nothing but runtime has nodes; file-based analysis misleads."),
    ("SCENE_DUMP_FAILED", "error", "Scene could not be inspected at all."),
    ("RUNTIME_ERROR", "error", "Error emitted while the game ran."),
    ("CONVERTER_FIDELITY_GAP", "info",
     "renpy2godot could not fully translate a construct."),
    ("VALIDATION_*", "error/warning", "Headless validator findings."),
]


def render(diag):
    lines = []
    s = diag["summary"]
    lines.append(f"diagnostics: {s['error']} error, {s['warning']} warning, "
                 f"{s['info']} info  (total {s['total']})")
    for f in diag["diagnostics"]:
        loc = f.get("scene") or f.get("file") or ""
        node = f" node={f['node']}" if f.get("node") else ""
        line = f" line={f['line']}" if f.get("line") else ""
        lines.append("")
        lines.append(f"[{f['severity'].upper()}] {f['code']}  {loc}{node}{line}")
        lines.append(f"  meaning: {f.get('meaning')}")
        lines.append(f"  impact : {f.get('impact')}")
        lines.append(f"  action : {f.get('action')}")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    if "--rules" in args:
        for code, sev, desc in RULE_CATALOG:
            print(f"{sev:8s} {code:28s} {desc}")
        return 0
    ctx = load_context()
    diag = diagnose(ctx)
    if "--json" in args:
        print(json.dumps(diag, indent=2, ensure_ascii=False))
    else:
        print(render(diag))
    return 0 if diag["summary"]["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
