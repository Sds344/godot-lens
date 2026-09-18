"""scene_lint.py — structural faults that Godot accepts silently.

Every check here targets a failure mode from GameDevBench's failure taxonomy
(`docs/gamedevbench-failure-modes.md`), and every one of them shares a property
that makes it worth having: **the engine does not complain.** Godot loads the
scene, the project runs, and the game is quietly wrong.

That is the gap this module exists for. `godot_validate.sh` reports what the
engine *says*; this reports what the engine *accepts*. And because the checks are
decidable from the engine's own reflection data plus the scene text, none of them
needs a runtime, an image, or geometry.

Two checks, both from the paper's measured failure distribution:

`PROPERTY_ON_WRONG_CLASS`
    The paper's own case study (§G.1). GPT-5.4 wrote the correct property and the
    correct value — `sub_emitter = NodePath("../Splash")` — but attached it to the
    `ParticleProcessMaterial` sub-resource instead of the `GPUParticles2D` node.
    `sub_emitter` belongs to `GPUParticles2D` and means nothing on a material. The
    fix is decidable without running anything: the API dump knows which class
    declares which property.

`UNSET_EXPORTED_REFERENCE`
    An exported `NodePath` or resource reference that is never assigned. The
    runtime reads `null` and typically fails later, somewhere else, with a message
    that points at the symptom rather than the missing assignment.

Both are reported with the severity the *consequence* deserves, following the
existing rule that severity is ranked by impact rather than by how the engine
labels things.
"""
from __future__ import annotations

import json
import os
import re

# Properties Godot's scene format sets on every node regardless of class, or that
# are engine bookkeeping rather than part of the class's API. Flagging these would
# make the checker cry wolf on every scene, and a checker that cries wolf gets
# ignored — along with the layer it belongs to.
UNIVERSAL_PROPERTIES = {
    "script", "metadata", "owner", "parent", "index", "name", "type",
    "instance", "groups", "node_paths", "connection", "editable_children",
    "unique_name_in_owner", "process_mode", "process_priority", "process_thread_group",
    "process_thread_group_order", "process_thread_messages", "physics_interpolation_mode",
    "auto_translate_mode", "editor_description", "scene_file_path", "multiplayer",
    "rotation_edit_mode", "rotation_order", "layout_mode", "layout_direction",
    "anchors_preset", "anchor_*", "offset_*", "grow_horizontal", "grow_vertical",
    "theme_override_*",
}


def _is_universal(prop):
    if prop in UNIVERSAL_PROPERTIES:
        return True
    # Godot writes per-axis and per-theme variants with wildcards, e.g.
    # `anchor_left`, `theme_override_font_sizes/font_size`.
    for pat in ("anchor_", "offset_", "theme_override_", "metadata/"):
        if prop.startswith(pat):
            return True
    return False


class ApiIndex:
    """Which class declares which property, resolved through inheritance.

    Reflection data is the only honest source here: a hand-maintained list of
    Godot properties would be wrong within a release, and the whole point is to
    check against the engine actually installed.
    """

    def __init__(self, dump_path):
        with open(dump_path, encoding="utf-8") as fh:
            db = json.load(fh)
        self.parents = {}
        self.props = {}
        for cls in db.get("classes", []):
            name = cls.get("name")
            self.parents[name] = cls.get("inherits")
            self.props[name] = {p.get("name") for p in cls.get("properties", [])
                                if p.get("name")}
        # Members can also be exposed as methods with getters; a property set in
        # a .tscn for a member declared only as a method getter/setter would
        # otherwise be reported as unknown.
        for cls in db.get("classes", []):
            name = cls.get("name")
            for m in cls.get("methods", []):
                mn = str(m.get("name", ""))
                if mn.startswith("set_") and len(mn) > 4:
                    self.props[name].add(mn[4:])

    def chain(self, cls):
        seen, cur = [], cls
        while cur and cur not in seen:
            seen.append(cur)
            cur = self.parents.get(cur)
        return seen

    def knows_class(self, cls):
        return cls in self.props

    def declares(self, cls, prop):
        """Is `prop` a member of `cls` or any ancestor?"""
        for c in self.chain(cls):
            if prop in self.props.get(c, ()):
                return True
        return False

    def owner_of(self, cls, prop):
        """The class in the chain that first declares `prop`."""
        for c in self.chain(cls):
            if prop in self.props.get(c, ()):
                return c
        return None


# --- scene parsing ------------------------------------------------------------

_NODE_RE = re.compile(r'^\[node\s+name="([^"]*)"(?:\s+type="([^"]*)")?'
                      r'(?:\s+parent="([^"]*)")?(?:\s+instance="([^"]*)")?')
_SUBRES_RE = re.compile(r'^\[sub_resource\s+type="([^"]*)"\s+id="([^"]*)"')
_EXT_RES_RE = re.compile(r'^\[ext_resource\s+type="([^"]*)"[^\]]*?path="([^"]*)"'
                         r'[^\]]*id="([^"]*)"')
_ASSIGN_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_/]*)\s*=\s*(.*)$')
# `@export var name`, `@export var name: T`, `@export_range(...) var name`,
# and plain `var name`. All of these become settable properties on the node.
_SCRIPT_VAR_RE = re.compile(
    r'^\s*(?:@export[^\n]*?\s+)?var\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]*)?(?:=|$)')


def script_declared_members(script_text):
    """Property names a GDScript file declares.

    Needed because a `var` declared in the attached script is a legitimate
    property of the node. Without this, every script-declared property set in a
    `.tscn` looks like a property attached to the wrong class — a false positive
    on ordinary, correct scenes, which is the failure mode that gets a checker
    ignored.
    """
    names = set()
    for line in script_text.splitlines():
        m = _SCRIPT_VAR_RE.match(line)
        if m:
            names.add(m.group(1))
    return names


def _read_script(project_dir, res_path):
    if not project_dir or not res_path:
        return set()
    rel = res_path[6:] if res_path.startswith("res://") else res_path
    path = os.path.join(project_dir, rel)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return script_declared_members(fh.read())
    except OSError:
        return set()


def parse_scene(text):
    """Extract every object in a `.tscn` and the properties assigned to it.

    Returns a list of `{kind, name, type, props, instanced}` where kind is `node`,
    `sub_resource` or `ext_resource`. Parsed leniently on purpose: an unparsable
    line is skipped rather than fatal, because a lint that refuses to run on a
    slightly unusual scene helps nobody.

    `instanced` marks a node that comes from another scene file. Its properties
    were chosen there, against a class this file does not state, so they cannot be
    validated from here and are skipped rather than guessed at.
    """
    objects = []
    current = None
    inherited = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("["):
            m = _NODE_RE.match(stripped)
            if m:
                parent = m.group(3)
                # A node with no parent begins a new root-level subtree; an
                # instanced node hands its whole subtree to another scene file.
                if parent is None:
                    inherited = False
                if m.group(4):
                    inherited = True
                current = {"kind": "node", "name": m.group(1),
                           "type": m.group(2) or "", "parent": parent,
                           "instanced": inherited, "props": {}}
                objects.append(current)
                continue
            m = _SUBRES_RE.match(stripped)
            if m:
                current = {"kind": "sub_resource", "name": m.group(2),
                           "type": m.group(1), "instanced": False, "props": {}}
                objects.append(current)
                continue
            m = _EXT_RES_RE.match(stripped)
            if m:
                current = {"kind": "ext_resource", "name": m.group(3),
                           "type": m.group(1), "path": m.group(2),
                           "instanced": False, "props": {}}
                objects.append(current)
                continue
            current = None
            continue
        if current is None or not stripped or stripped.startswith(";"):
            continue
        m = _ASSIGN_RE.match(stripped)
        if m:
            current["props"][m.group(1)] = m.group(2).strip()
    return objects


# --- checks -------------------------------------------------------------------

def check_property_on_wrong_class(scene_text, api, scene_name="?",
                                  project_dir=None):
    """A property assigned to an object whose class does not declare it.

    This is the paper's case study turned into a rule. A `.tscn` names its own
    types, so the declared class of each object is known without instantiating
    anything; the API dump says which class owns which property.

    Three ways a property can be legitimate without being in the API dump, all of
    which must be honoured or the check reports faults on correct scenes:

    * declared by the node's attached GDScript;
    * declared by the script on a sub-resource;
    * attached to a node that comes from an instanced scene, where this file does
      not state the class.
    """
    objects = parse_scene(scene_text)
    # res id -> script path, so a node's `script = ExtResource("1_p")` resolves.
    script_paths = {o["name"]: o.get("path", "") for o in objects
                    if o["kind"] == "ext_resource" and o["type"] == "Script"}
    script_members = {}
    for res_id, path in script_paths.items():
        script_members[res_id] = _read_script(project_dir, path)

    findings = []
    for obj in objects:
        cls = obj["type"]
        if not cls or not api.knows_class(cls):
            continue
        if obj.get("instanced"):
            # Inherited from another scene; its properties belong to a definition
            # this file does not contain.
            continue
        allowed = set()
        script_ref = obj["props"].get("script", "")
        m = re.search(r'ExtResource\("([^"]+)"\)', script_ref or "")
        if m:
            allowed = script_members.get(m.group(1), set())

        for prop, value in obj["props"].items():
            if _is_universal(prop) or prop in UNIVERSAL_PROPERTIES:
                continue
            if prop in allowed:
                continue
            if api.declares(cls, prop):
                continue
            findings.append({
                "severity": "error",
                "code": "PROPERTY_ON_WRONG_CLASS",
                "scene": scene_name,
                "object": obj["name"],
                "object_kind": obj["kind"],
                "class": cls,
                "property": prop,
                "value": value[:80],
                "meaning": (f"`{prop}` is not a member of `{cls}` "
                            f"({obj['kind']} `{obj['name']}`), nor is it declared "
                            "by an attached script."),
                "impact": ("Godot ignores an unknown property on a headless load, "
                           "so the scene loads and the game runs with this setting "
                           "silently absent. The intended behaviour does not "
                           "happen and nothing reports it."),
                "action": (f"Move `{prop}` onto the object whose class declares it, "
                           f"or verify the class of `{obj['name']}` is correct."),
            })
    return findings


def check_unset_exported_reference(scene_text, api, runtime_root=None,
                                   scene_name="?"):
    """An exported reference that is declared but never assigned.

    Checked against the RUNTIME dump when available, because an exported member
    only exists after the script has run — the `.tscn` alone cannot tell a
    deliberately-empty export from a forgotten one. Without a runtime view this
    returns nothing rather than guessing, because guessing here produces exactly
    the false positives that get a checker ignored.
    """
    if runtime_root is None:
        return []
    findings = []
    for node in _walk(runtime_root):
        script = node.get("script") or ""
        if not script:
            continue
        for member, info in (node.get("exported") or {}).items():
            if not isinstance(info, dict):
                continue
            if info.get("type") not in ("NodePath", "Object", "Resource"):
                continue
            if info.get("set"):
                continue
            findings.append({
                "severity": "warning",
                "code": "UNSET_EXPORTED_REFERENCE",
                "scene": scene_name,
                "node": node.get("name"),
                "script": script,
                "property": member,
                "meaning": (f"`{member}` is exported with type {info.get('type')} "
                            f"on `{script}` but was never assigned."),
                "impact": ("The script reads an empty reference at runtime. The "
                           "failure usually surfaces later and elsewhere, as a null "
                           "dereference that does not name the missing assignment."),
                "action": (f"Assign `{member}` in the scene, or remove the export "
                           "if it is genuinely optional."),
            })
    return findings


def _walk(node):
    if not isinstance(node, dict):
        return
    yield node
    for child in node.get("children", []) or []:
        yield from _walk(child)


def lint_scene(scene_path, api, scene_name=None, runtime_root=None):
    """Run every check against one scene file."""
    try:
        with open(scene_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return [{
            "severity": "error", "code": "SCENE_UNREADABLE",
            "scene": scene_name or scene_path,
            "meaning": f"could not read the scene: {e}",
            "impact": "Nothing is known about this scene's contents.",
            "action": "Fix the file path or permissions.",
        }]
    name = scene_name or os.path.basename(scene_path)
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(scene_path)))
    findings = check_property_on_wrong_class(text, api, name,
                                             project_dir=project_dir)
    findings.extend(check_unset_exported_reference(text, api, runtime_root, name))
    return findings


def render(findings):
    if not findings:
        return "no structural faults found"
    lines = [f"{len(findings)} structural fault(s):", ""]
    for f in findings:
        where = f.get("scene", "?")
        obj = f.get("node") or f.get("object")
        if obj:
            where += f" object={obj}"
        lines.append(f"[{f['severity'].upper()}] {f['code']}  {where}")
        lines.append(f"  meaning: {f.get('meaning')}")
        lines.append(f"  impact : {f.get('impact')}")
        lines.append(f"  action : {f.get('action')}")
        lines.append("")
    return "\n".join(lines)
