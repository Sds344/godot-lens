"""mutations.py — fault classes, as detectable units rather than anecdotes.

The purpose is **coverage measurement**, not benchmark cases. A benchmark asks
"can an agent fix this?"; this asks a prior question: **can the tooling see it at
all?** Without that number, an A/B experiment that shows no improvement cannot be
interpreted — it could mean the tooling does not help, or that the injected faults
were never visible to it, and those two findings call for opposite responses.

Two properties make this measurable without an agent in the loop:

* **Injection is code.** A fault is a deterministic edit to a lab copy, so the
  same fault is produced identically every run.
* **Detection is code.** A detector is one or more tool invocations with an
  expected outcome, so "detected" is a comparison, not a judgement.

That separation is what keeps this measurement free of the grader-reliability
problem: no model grades anything here.

## The three verdicts, and why the third is the point

Each fault is classified by what the measurement says, not by what was hoped:

    detected      at least one detector found it
    missed        the fault was injected and nothing found it  <- a blind spot
    inapplicable  the fault could not be injected here

`missed` is a result, not a failure. A fault class in this registry is either one
the tooling can see, or a **documented blind spot with a number attached**. The
point of the exercise is to replace "we know there are limits" with "the blind
spot is these specific classes", which is the difference between an admission and
a measurement.

## Why some hazards are deliberately absent

"Two colliders overlap in space" (physics clipping) is the obvious fault to want,
and it is **not** expressible as an injection here, because the engine does not
consider it a fault. Two overlapping `StaticBody2D` colliders are perfectly legal;
detecting the overlap needs computational geometry the tooling does not have. That
is not a missing rule — it is a missing *observation capability*, and adding it is
a different kind of work from adding a rule. It is recorded in
`KNOWN_UNOBSERVABLE` so the gap stays visible instead of being quietly skipped.
"""
from __future__ import annotations

import json
import os

# Faults that no amount of diagnostic rules can reach, because the engine has no
# notion of them being wrong. Listed explicitly so that "we did not test this"
# cannot be mistaken for "this is covered".
KNOWN_UNOBSERVABLE = {
    "physics-overlap-2d": (
        "Two StaticBody2D colliders overlapping in space. Godot accepts this as a "
        "legal scene; nothing reports it. Detecting it needs bounding-box "
        "intersection — a new observation capability, not a new rule."),
    "control-offscreen": (
        "A Control placed outside the viewport. Valid scene, no engine warning. "
        "Needs a viewport-bounds comparison against each Control's rect."),
    "collision-visual-mismatch": (
        "A sprite and its collider disagreeing about extent — the visual and the "
        "physical shapes differ. Both are individually valid; the disagreement is "
        "only meaningful relative to intent, which is not observable."),
}


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


SCENE = "godot_project/Main.tscn"
SCRIPT = "godot_project/scripts/probe.gd"


# --- injections ---------------------------------------------------------------
# Each returns a short note describing what was changed, or raises for a lab that
# cannot host the fault.

def inject_collision_shape_missing(root):
    """A 2D collider with no Shape resource. Syntactically perfect, no collision."""
    _write(root, SCENE, """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[node name="Main" type="Node2D"]
script = ExtResource("1_p")

[node name="Body" type="StaticBody2D" parent="."]

[node name="Shape" type="CollisionShape2D" parent="Body"]
""")
    _write(root, SCRIPT, "extends Node2D\n")
    return "CollisionShape2D with no shape assigned"


def inject_collision_shape_missing_3d(root):
    """The 3D form of the same hazard, to check the rule is not 2D-only.

    Godot's `CollisionShape2D` and `CollisionShape3D` are unrelated classes; a
    diagnostic written against the 2D node silently covers nothing in a 3D
    project. Including both is how that asymmetry becomes a measurement rather
    than an assumption.
    """
    _write(root, SCENE, """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[node name="Main" type="Node3D"]
script = ExtResource("1_p")

[node name="Body" type="StaticBody3D" parent="."]

[node name="Shape" type="CollisionShape3D" parent="Body"]
""")
    _write(root, SCRIPT, "extends Node3D\n")
    return "CollisionShape3D with no shape assigned"


def inject_collision_layer_zero(root):
    """A physics body on no layer: other bodies cannot detect it.

    The `[sub_resource]` block must precede the node that references it. Godot's
    text scene parser resolves `SubResource(...)` by id while reading and fails
    the whole file if the id is not yet declared — with a parse error, not a
    dangling-reference warning. Getting this order wrong made the injection
    produce an *unloadable scene*, which the first coverage run reported as a
    blind spot for a rule that actually worked. The harness now reports detector
    errors explicitly so that class of mistake cannot hide again.
    """
    _write(root, SCENE, """[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[sub_resource type="RectangleShape2D" id="RectangleShape2D_1"]
size = Vector2(32, 32)

[node name="Main" type="Node2D"]
script = ExtResource("1_p")

[node name="Body" type="StaticBody2D" parent="."]
collision_layer = 0
collision_mask = 1

[node name="Shape" type="CollisionShape2D" parent="Body"]
shape = SubResource("RectangleShape2D_1")
""")
    _write(root, SCRIPT, "extends Node2D\n")
    return "StaticBody2D with collision_layer = 0"


def inject_sprite_texture_missing(root):
    """A Sprite2D with no texture: draws nothing, no engine warning."""
    _write(root, SCENE, """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[node name="Main" type="Node2D"]
script = ExtResource("1_p")

[node name="Art" type="Sprite2D" parent="."]
position = Vector2(100, 100)
""")
    _write(root, SCRIPT, "extends Node2D\n")
    return "Sprite2D with no texture"


def inject_node_path_invalid(root):
    """A node path that does not exist. Static-clean, runtime-broken."""
    _write(root, SCENE, """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[node name="Main" type="Node2D"]
script = ExtResource("1_p")
""")
    _write(root, SCRIPT, """extends Node2D

func _ready() -> void:
	var n: Node = get_node("NoSuchChild/AtAll")
	print(n)
""")
    return 'get_node("NoSuchChild/AtAll") with no such node'


def inject_type_error(root):
    """A type mismatch: the one class static checking can see."""
    _write(root, SCENE, """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[node name="Main" type="Node2D"]
script = ExtResource("1_p")
""")
    _write(root, SCRIPT, """extends Node2D

var health: int = "not an int"

func _ready() -> void:
	print(health)
""")
    return 'var health: int = "not an int"'


def inject_property_on_wrong_class(root):
    """The GameDevBench §G.1 case: a valid property on the wrong object.

    `sub_emitter` belongs to `GPUParticles2D`; the paper's model wrote the correct
    property and the correct value but attached it to the `ParticleProcessMaterial`
    sub-resource. Godot ignores an unknown property on a headless load, so the
    scene loads, the game runs, and the setting is silently absent.
    """
    _write(root, SCENE, """[gd_scene load_steps=4 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[sub_resource type="ParticleProcessMaterial" id="ParticleProcessMaterial_1"]
sub_emitter = NodePath("../Splash")
emission_shape = 1

[node name="Main" type="Node2D"]
script = ExtResource("1_p")

[node name="Rain" type="GPUParticles2D" parent="."]
amount = 100
process_material = SubResource("ParticleProcessMaterial_1")

[node name="Splash" type="GPUParticles2D" parent="."]
emitting = false
""")
    _write(root, SCRIPT, "extends Node2D\n")
    return "sub_emitter placed on ParticleProcessMaterial instead of GPUParticles2D"


def inject_unset_exported_reference(root):
    """An exported NodePath declared but never assigned in the scene."""
    _write(root, SCRIPT, """extends Node2D

@export var target: NodePath
@export var speed: float = 1.0

func _ready() -> void:
	# Reads an empty reference, then usually fails later and elsewhere.
	print(get_node_or_null(target))
""")
    _write(root, SCENE, """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[node name="Main" type="Node2D"]
script = ExtResource("1_p")
speed = 2.0

[node name="Other" type="Node2D" parent="."]
""")
    return "@export var target: NodePath declared with no assignment"


def inject_missing_resource(root):
    """A scene whose `ext_resource` points at a script that does not exist.

    The load-time class. Note that Godot still exits 0 for this — the project
    fails to load a scene and reports success to the shell, which is why the
    validator matches output text rather than the exit code.
    """
    _write(root, "godot_project/Broken.tscn", """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/does_not_exist.gd" id="1_x"]

[node name="Broken" type="Node2D"]
script = ExtResource("1_x")
""")
    return "Broken.tscn references a script that is not on disk"


def inject_narration_dropped(root):
    """Remove a narration node from the generated story data.

    The semantic class: the Godot project remains **perfectly valid**. It loads,
    the game runs, nothing is reported — and a line of the story is gone. This is
    the `define narrator = Character(None)` defect reproduced deliberately, and it
    is the case the whole `study` apparatus was built for.

    Mutating `data/story.json` rather than a script is the point: an agent that
    only reads `.gd` files has nothing to go on, because the wrong thing is not in
    the code.
    """
    story_path = os.path.join(root, "godot_project", "data", "story.json")
    if not os.path.exists(story_path):
        raise SystemExit("mutations: data/story.json is required for this fault; "
                         "the selected project does not contain one")
    with open(story_path, encoding="utf-8") as fh:
        story = json.load(fh)

    removed = []

    def prune(body, label_name):
        keep = []
        for node in body:
            if isinstance(node, dict):
                if node.get("kind") == "narration" and not removed:
                    removed.append((label_name, str(node.get("text", ""))[:60]))
                    continue
                for branch in node.get("branches", []) or []:
                    if isinstance(branch, dict):
                        prune(branch.get("body", []) or [], label_name)
                if isinstance(node.get("else_body"), list):
                    prune(node["else_body"], label_name)
                for key in ("choices", "clauses"):
                    for choice in node.get(key, []) or []:
                        if isinstance(choice, dict):
                            prune(choice.get("body", []) or [], label_name)
                if isinstance(node.get("body"), list):
                    prune(node["body"], label_name)
            keep.append(node)
        body[:] = keep

    for label in story.get("labels", []):
        prune(label.get("body", []) or [], str(label.get("name", "")))
        if removed:
            break

    if not removed:
        raise SystemExit("mutations: no narration node found to remove")

    with open(story_path, "w", encoding="utf-8") as fh:
        json.dump(story, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return (f"removed the narration in label {removed[0][0]!r}: "
            f"{removed[0][1]!r}")


def inject_runtime_misparent(root):
    """A node built at run time lands in the wrong parent, because of ORDER.

    The layout table in `hud.gd` is complete and every entry names the correct
    parent. The defect is that one entry — the container — is declared *after* the
    children that ask for it, and `_resolve_parent` falls back to the root when a
    lookup misses. So:

      * the `.tscn` declares a single childless `Control` and shows nothing;
      * the engine reports nothing, because attaching to the root is legal;
      * reading the table shows correct parent names on all fourteen entries.

    The wrong tree exists only at run time. This is the fault class GameDevBench
    measures as its most common ("missing or mis-parented nodes", 63.4%), and the
    one an agent cannot resolve by reading source, because there is nothing wrong
    in the source to read.
    """
    path = os.path.join(root, "godot_project", "hud.gd")
    if not os.path.exists(path):
        raise SystemExit("mutations: hud.gd is required for this fault")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    row = '\t{"kind": "hbox",   "name": "ActionRow",    "parent": "ActionPanel"},\n'
    if row not in text:
        raise SystemExit("mutations: the ActionRow entry was not found; the lab "
                         "project changed shape")
    text = text.replace(row, "", 1)
    anchor = ('\t{"kind": "button", "name": "QuitButton",   "parent": "ActionRow",'
              '   "text": "Quit"},\n')
    text = text.replace(anchor, anchor + row, 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return ("ActionRow is declared after its own children, so the three buttons "
            "attach to the root instead")


def inject_layout_overflow(root):
    """A toolbar whose width exceeds the viewport by a margin too small to guess.

    Each button is sized by the layout engine to fit its label at the current font
    size. The total is therefore **not written down anywhere**: reading the source
    gives the labels, never the pixels. The added labels push the row to roughly
    1300px against a 1280px viewport — an overflow of about 20px.

    The margin is deliberate. A large overflow could be estimated from label length
    and fixed by reasoning alone, which would make the fault readable and the test
    meaningless. At ~20px, estimation cannot resolve it and exact numbers can, so
    the case discriminates between reading and observing.

    The engine reports nothing: a Control extending past the viewport is legal, and
    a clipping container does not warn.
    """
    path = os.path.join(root, "godot_project", "hud.gd")
    if not os.path.exists(path):
        raise SystemExit("mutations: hud.gd is required for this fault")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    anchor = ('\t{"kind": "button", "name": "Tool6", "parent": "ToolBar", '
              '"text": "Achievements"},\n')
    if anchor not in text:
        raise SystemExit("mutations: the Tool6 entry was not found")
    extra = (
        '\t{"kind": "button", "name": "Tool7", "parent": "ToolBar", '
        '"text": "Accessibility Options"},\n'
        '\t{"kind": "button", "name": "Tool8", "parent": "ToolBar", '
        '"text": "Restore Defaults and Reload"},\n'
        '\t{"kind": "button", "name": "Tool9", "parent": "ToolBar", '
        '"text": "Export Save Data"},\n'
        '\t{"kind": "button", "name": "Tool10", "parent": "ToolBar", '
        '"text": "Import Save Data"},\n'
        '\t{"kind": "button", "name": "Tool11", "parent": "ToolBar", '
        '"text": "Audio"},\n'
        '\t{"kind": "button", "name": "Tool12", "parent": "ToolBar", '
        '"text": "Video"},\n')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(anchor, anchor + extra, 1))
    return ("four extra toolbar buttons push the row past the viewport width; the "
            "resulting width exists only in the running layout")


def inject_overlap_2d(root):
    """Two overlapping StaticBody2D colliders — the hazard the engine accepts.

    Kept in the registry precisely so the measurement reports it as *missed*. It
    is the clearest case of a fault that is real to a player and invisible to both
    the engine and the tooling.
    """
    _write(root, SCENE, """[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[sub_resource type="RectangleShape2D" id="RectangleShape2D_1"]
size = Vector2(64, 64)

[node name="Main" type="Node2D"]
script = ExtResource("1_p")

[node name="BodyA" type="StaticBody2D" parent="."]
position = Vector2(100, 100)
collision_layer = 1
collision_mask = 1

[node name="ShapeA" type="CollisionShape2D" parent="BodyA"]
shape = SubResource("RectangleShape2D_1")

[node name="BodyB" type="StaticBody2D" parent="."]
position = Vector2(110, 110)
collision_layer = 1
collision_mask = 1

[node name="ShapeB" type="CollisionShape2D" parent="BodyB"]
shape = SubResource("RectangleShape2D_1")
""")
    _write(root, SCRIPT, "extends Node2D\n")
    return "two 64x64 colliders 10px apart on the same layer (heavily overlapping)"


def inject_control_offscreen(root):
    """A Control far outside the viewport: valid scene, invisible UI."""
    _write(root, SCENE, """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/probe.gd" id="1_p"]

[node name="Main" type="Control"]
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
script = ExtResource("1_p")

[node name="Hud" type="Label" parent="."]
offset_left = 5000.0
offset_top = 5000.0
offset_right = 5200.0
offset_bottom = 5040.0
text = "Score"
""")
    _write(root, SCRIPT, "extends Control\n")
    return "Label positioned at (5000,5000) in a 1280x720 viewport"


# --- registry -----------------------------------------------------------------
# `detect` is a list of tool invocations whose output must contain `expect`.
# Multiple detectors are allowed: a fault that any one of them finds is detected,
# and knowing *which* found it is part of the result.

FAULTS = {
    "collision-shape-missing": {
        "inject": inject_collision_shape_missing,
        "hazard": "A collider that contributes no collision; bodies pass through.",
        "detect": [
            {"tool": "diagnose", "expect": "COLLISION_SHAPE_MISSING"},
            {"tool": "inspect", "expect": "shape"},
        ],
    },
    "collision-shape-missing-3d": {
        "inject": inject_collision_shape_missing_3d,
        "hazard": "The 3D form of the same hazard.",
        "detect": [
            {"tool": "diagnose", "expect": "COLLISION_SHAPE_MISSING"},
            {"tool": "inspect", "expect": "CollisionShape3D"},
        ],
    },
    "collision-layer-zero": {
        "inject": inject_collision_layer_zero,
        "hazard": "A body on no layer: nothing can detect contact with it.",
        "detect": [{"tool": "diagnose", "expect": "COLLISION_LAYER_ZERO"}],
    },
    "sprite-texture-missing": {
        "inject": inject_sprite_texture_missing,
        "hazard": "A sprite that draws nothing.",
        "detect": [{"tool": "diagnose", "expect": "SPRITE_TEXTURE_MISSING"}],
    },
    "node-path-invalid": {
        "inject": inject_node_path_invalid,
        "hazard": "A lookup for a node that does not exist.",
        "detect": [{"tool": "inspect", "expect": "Node not found"}],
    },
    "type-error": {
        "inject": inject_type_error,
        "hazard": "A declaration whose type contradicts its value.",
        "detect": [
            {"tool": "inspect", "expect": "Parse Error"},
            {"tool": "diagnose", "expect": "VALIDATION_STATIC"},
        ],
    },
    # From GameDevBench's failure taxonomy, which measured these as the two most
    # common structural failure modes (36.2% and 35.9% of failures). Both are
    # invisible to the engine, which is why they need a checker rather than a
    # validator.
    "property-on-wrong-class": {
        "inject": inject_property_on_wrong_class,
        "hazard": "A real property attached to a class that does not declare it "
                  "(the paper's sub_emitter case).",
        "detect": [{"tool": "diagnose", "expect": "PROPERTY_ON_WRONG_CLASS"}],
    },
    "unset-exported-reference": {
        "inject": inject_unset_exported_reference,
        "hazard": "An exported NodePath never assigned; reads null at runtime.",
        "detect": [{"tool": "diagnose", "expect": "UNSET_EXPORTED_REFERENCE"}],
    },
    # Real hazards that are expected to be MISSED. Including them is the whole
    # point: a registry of only-detectable faults would report 100% coverage and
    # mean nothing.
    "physics-overlap-2d": {
        "inject": inject_overlap_2d,
        "hazard": "Heavily overlapping colliders (clipping).",
        "detect": [{"tool": "diagnose", "expect": "never-matches-this"}],
        "expected": "missed",
    },
    "control-offscreen": {
        "inject": inject_control_offscreen,
        "hazard": "UI drawn outside the viewport.",
        "detect": [{"tool": "diagnose", "expect": "never-matches-this"}],
        "expected": "missed",
    },
}


def load():
    """The registry as data, for a report that does not execute anything."""
    return {
        name: {
            "hazard": spec["hazard"],
            "detectors": [d["expect"] for d in spec["detect"]],
            "expected": spec.get("expected", "detected"),
        }
        for name, spec in FAULTS.items()
    }


def render_registry():
    lines = [f"{'fault class':30s} {'expectation':12s} hazard", "-" * 96]
    for name, spec in FAULTS.items():
        lines.append(f"{name:30s} {spec.get('expected', 'detected'):12s} "
                     f"{spec['hazard'][:52]}")
    lines.append("")
    lines.append("Known unobservable (no rule can reach these; listed so the gap stays visible):")
    for name, why in KNOWN_UNOBSERVABLE.items():
        lines.append(f"  {name:30s} {why}")
    return "\n".join(lines)
