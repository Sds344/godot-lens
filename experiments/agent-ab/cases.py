"""cases.py — the nine faults, their injection, and their success criterion.

Each case is a self-contained experiment cell: *inject a fault into a fresh lab,
state a task, later decide by observation whether it was repaired.*

The success criterion is a **function of the project on disk**, never a string
match on the agent's output. That rule comes from this project's own worst
experience: an early grader accepted "the script still mentions the missing node"
as evidence of a repair, and the broken script mentions it too (`LESSONS.md` §1).
Every criterion here reads the project.

Three levels, because the level predicts where arms B and C differ:

    L1  engine-visible   the engine reports it; B and C can both see it
    L2  engine-silent    only godot-lens reports it; this is the point
    L3  semantic         the project is valid and wrong

See `README.md` for the pre-registered protocol.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "benchmarks"))
sys.path.insert(0, os.path.join(REPO, "src"))

import mutations  # noqa: E402  - reuse the injection definitions

SCENE = "godot_project/Main.tscn"
SCRIPT = "godot_project/scripts/probe.gd"
STORY = "godot_project/data/story.json"


# --- helpers ------------------------------------------------------------------

def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def _exists(root, rel):
    return os.path.exists(os.path.join(root, rel))


def _scene_facts(root):
    """Whatever `godot-lens inspect --json` reports, or {} if it cannot run.

    Grading uses the same observability a consumer would. If the tooling cannot
    see the project, the case cannot be graded as repaired — and that has to be
    the rule, or a broken project could pass by making itself unobservable.
    """
    import subprocess
    env = dict(os.environ)
    env["GODOT_PROJECT"] = os.path.join(root, "godot_project")
    env["GODOT_LENS_HOME"] = os.path.join(root, ".tooling")
    for sub in ("config", "data", "cache"):
        os.makedirs(os.path.join(root, ".tooling", "godot_home", sub), exist_ok=True)
    env["XDG_CONFIG_HOME"] = os.path.join(root, ".tooling", "godot_home", "config")
    env["XDG_DATA_HOME"] = os.path.join(root, ".tooling", "godot_home", "data")
    env["XDG_CACHE_HOME"] = os.path.join(root, ".tooling", "godot_home", "cache")
    try:
        p = subprocess.run([sys.executable,
                            os.path.join(REPO, "tools", "godot_context.py")],
                           cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=600)
    except (OSError, subprocess.SubprocessError):
        return {}
    text = p.stdout
    start, end = text.find("{"), text.rfind("}")
    if start == -1:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def _walk(node):
    if not isinstance(node, dict):
        return
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


def _runtime_nodes(root):
    ctx = _scene_facts(root)
    for scene in ctx.get("scenes") or []:
        rt = (scene.get("runtime") or {}).get("root")
        if rt:
            yield scene.get("scene"), from_root(rt)


def from_root(rt):
    return list(_walk(rt))


def _findings(root):
    ctx = _scene_facts(root)
    return (ctx.get("findings") or {}).get("findings") or []


# --- criteria -----------------------------------------------------------------
# Each returns (repaired: bool, detail: str). `repaired` must mean the fault is
# GONE, not that the symptom vanished — the distinction the whole project exists
# to preserve.

def grade_nodepath(root):
    """The node the code asks for must exist.

    Positive evidence only. Two ways to make the error disappear without repairing
    anything, both graded as failures because the task says the code must keep
    working:

    * deleting the lookup — the feature goes with it;
    * **redirecting** it to a different node that happens to exist. This is the
      attack that beat the first grader this project ever wrote (`LESSONS.md` §1):
      the error vanished, the game changed.

    An earlier version of this function graded a redirect as a repair because it
    accepted "the lookup no longer names a missing node" as proof. That is the same
    negative-check-as-positive-check mistake the whole project is about, so the
    check is now written as the positive requirement it actually is.
    """
    if not _exists(root, SCRIPT):
        return False, "probe.gd was deleted; the fault was removed with the code"
    code = re.sub(r"#.*", "", _read(root, SCRIPT))
    if not re.search(r"func\s+_ready", code):
        return False, "_ready() was removed; the code was deleted, not repaired"

    lookups = re.findall(r'get_node(?:_or_null)?\("([^"]+)"\)', code)
    scene = _read(root, SCENE)

    # A genuine repair: the node the code asks for now exists.
    if looks_like_node(scene, "NoSuchChild"):
        return True, "the missing node was added to the scene"

    if not lookups:
        return False, ("the lookup was deleted rather than satisfied; the code no "
                       "longer does anything")

    target = lookups[0]
    missing = missing_path_segment(scene, target)
    if missing:
        # The lookup names a path segment that does not exist, so nothing was
        # built to satisfy it. If the target is no longer the node the code was
        # written for, this is a redirect rather than an unmet requirement —
        # both are failures, but only one is an attempt to conceal.
        if target != "NoSuchChild/AtAll":
            return False, (f"redirected the lookup to {target!r}, whose segment "
                           f"{missing!r} does not exist either")
        return False, f"the lookup still names a missing node: {missing!r}"

    # Points at a node that exists, but not the one the code was written for.
    return False, (f"redirected the lookup to {target!r} instead of providing "
                   "'NoSuchChild'; the game now reads a different node")


def looks_like_node(scene_text, name):
    import re as _re
    return bool(_re.search(r'\[node\s+name="' + _re.escape(name) + r'"', scene_text))


def _scene_node_names(scene_text):
    import re as _re
    return set(_re.findall(r'\[node\s+name="([^"]*)"', scene_text))


def _parents_of(scene_text, name):
    """The `parent=` of a node, so a path can be checked level by level."""
    import re as _re
    m = _re.search(r'\[node\s+name="' + _re.escape(name) + r'"([^\]]*)\]',
                   scene_text)
    if not m:
        return None
    pm = _re.search(r'parent="([^"]*)"', m.group(1))
    return pm.group(1) if pm else ""


def missing_path_segment(scene_text, path):
    """The first segment of `path` that does not resolve, or None."""
    names = _scene_node_names(scene_text)
    segments = [s for s in path.replace("./", "").split("/") if s not in ("", ".")]
    if not segments:
        return None
    if segments[0] not in names:
        return segments[0]
    if len(segments) > 1 and segments[1] not in names:
        return segments[1]
    return None


def grade_typeerror(root):
    """The declaration must survive with a type-correct value."""
    if not _exists(root, SCRIPT):
        return False, "probe.gd was deleted"
    code = re.sub(r"#.*", "", _read(root, SCRIPT))
    if not re.search(r"\bvar\s+health\b", code):
        return False, "the `health` declaration was deleted, not fixed"
    if not re.search(r"\bvar\s+health\s*:\s*int\b", code):
        return False, "`health` lost its declared int type"
    if re.search(r'var\s+health\s*:\s*int\s*=\s*"', code):
        return False, "the type mismatch is still present"
    return True, "health is int-typed with a compatible value"


def grade_missing_resource(root):
    """The referenced resource must exist, or the reference must point somewhere real."""
    scene_path = os.path.join(root, "godot_project", "Broken.tscn")
    if not os.path.exists(scene_path):
        return False, "Broken.tscn was deleted; the fault was removed with the scene"
    scene = _read(root, "godot_project/Broken.tscn")
    m = re.search(r'ext_resource[^>]*path="res://([^"]+)"', scene)
    if not m:
        return False, "the scene no longer references any resource"
    if _exists(root, os.path.join("godot_project", m.group(1))):
        return True, f"the referenced resource {m.group(1)!r} now exists"
    return False, f"{m.group(1)!r} is still missing"


def grade_property_wrong_class(root):
    """The property must land on an object whose class declares it.

    Anyone can silence this by deleting the line. That is graded as a failure: the
    intent was to set the property, so removing it loses the behaviour.
    """
    scene = _read(root, SCENE)
    if "sub_emitter" not in scene:
        return False, ("the `sub_emitter` assignment was deleted; the behaviour was "
                       "removed rather than placed correctly")
    # Find which object the assignment sits under.
    owner_type = None
    for raw in scene.splitlines():
        s = raw.strip()
        if s.startswith("[sub_resource"):
            m = re.search(r'type="([^"]+)"', s)
            owner_type = m.group(1) if m else None
        elif s.startswith("[node"):
            m = re.search(r'type="([^"]+)"', s)
            owner_type = m.group(1) if m else None
        elif s.startswith("sub_emitter") and owner_type:
            return (owner_type == "GPUParticles2D",
                    f"sub_emitter is set on {owner_type}")
    return False, "sub_emitter is present but not assigned to any object"


def grade_unset_export(root):
    """The exported reference must be assigned to something that exists."""
    if not _exists(root, SCRIPT) or not _exists(root, SCENE):
        return False, "a required file was deleted"
    script = _read(root, SCRIPT)
    if "target" not in script:
        return False, ("the `target` export was removed; that deletes the feature "
                       "rather than wiring it")
    scene = _read(root, SCENE)
    # Either the scene assigns it, or the script stopped needing it.
    m = re.search(r'^target\s*=\s*NodePath\("([^"]*)"\)', scene, re.M)
    if m and m.group(1).strip():
        path = m.group(1).lstrip("./")
        node_name = path.split("/")[-1]
        if re.search(r'\[node\s+name="' + re.escape(node_name) + r'"', scene):
            return True, f"target is assigned to {m.group(1)!r}, which exists"
        return False, f"target points at {m.group(1)!r}, which does not exist"
    if not re.search(r"get_node_or_null\(target\)", script):
        return True, "the script no longer reads the unset reference"
    return False, "target is still unset"


HUD_SCENE = "godot_project/Main.tscn"


def _runtime_tree(root):
    """The runtime root of the lab's main scene, or None."""
    ctx = _scene_facts(root)
    for scene in ctx.get("scenes") or []:
        if str(scene.get("scene", "")).endswith("Main.tscn"):
            return (scene.get("runtime") or {}).get("root")
    return None


def _parent_of(root_node, child_name):
    """The name of the node that owns `child_name`, at any depth."""
    def walk(n, parent):
        if n.get("name") == child_name:
            return parent
        for c in n.get("children") or []:
            found = walk(c, n.get("name"))
            if found is not None:
                return found
        return None
    return walk(root_node, None)


def grade_runtime_misparent(root):
    """The three action buttons must be children of ActionRow.

    Graded by enumerating the RUNTIME tree, because that is the only place the
    fault exists. A source-level grade could be satisfied by editing the table
    while the tree stayed wrong — the negative-check trap this project keeps
    meeting.
    """
    rt = _runtime_tree(root)
    if rt is None:
        return False, ("the project did not run, so its tree could not be "
                       "observed — nothing is known about the layout")
    problems = []
    for name in ("StartButton", "RetryButton", "QuitButton"):
        owner = _parent_of(rt, name)
        if owner is None:
            problems.append(f"{name} is missing from the tree")
        elif owner != "ActionRow":
            problems.append(f"{name} is under {owner!r}, not 'ActionRow'")
    if problems:
        return False, "; ".join(problems)
    return True, "all three buttons are children of ActionRow"


VIEWPORT_WIDTH = 1280.0
VIEWPORT_HEIGHT = 720.0

# The labels the toolbar must still offer after the fix. Kept as data so the
# "do not remove the feature" rule is checkable rather than asserted.
TOOLBAR_LABELS = ["New Game", "Load Game", "Save Game", "Settings", "Credits",
                  "Achievements", "Accessibility Options",
                  "Restore Defaults and Reload", "Export Save Data",
                  "Import Save Data", "Audio", "Video"]


def _subtree(root_node, name):
    if not isinstance(root_node, dict):
        return None
    if root_node.get("name") == name:
        return root_node
    for c in root_node.get("children") or []:
        found = _subtree(c, name)
        if found:
            return found
    return None


def grade_layout_overflow(root):
    """The toolbar must fit inside the viewport, with its buttons intact.

    Both halves are required. Requiring only "it fits" would accept deleting
    buttons, which removes the feature to remove the symptom — the silencing this
    project grades as a failure everywhere else.
    """
    rt = _runtime_tree(root)
    if rt is None:
        return False, ("the project did not run, so the toolbar's real width is "
                       "unknown")
    bar = _subtree(rt, "ToolBar")
    if bar is None:
        return False, "the ToolBar no longer exists in the running tree"

    buttons = [n for n in _walk(bar) if n.get("type") == "Button"]
    present = {str(b.get("text", "")) for b in buttons}
    missing = [l for l in TOOLBAR_LABELS if l not in present]
    if missing:
        return False, (f"{len(missing)} toolbar button(s) were removed "
                       f"({', '.join(missing[:3])}) — the row was shortened by "
                       "deleting functionality")

    rect = _rect(bar)
    if rect is None:
        return False, "the toolbar's rect could not be read from the running tree"

    x, y, w, h = rect
    # Both dimensions, from the container's own computed rectangle.
    #
    # The first version summed the children's widths and compared that to the
    # viewport. It reached the right verdict on the one run it graded and for the
    # wrong reason: a fix that wrapped the row into several lines was rejected for
    # "total width still exceeds the viewport" when the real problem with it was
    # that the wrapped row now ran off the BOTTOM. A criterion that gets the right
    # answer by accident will get the wrong one as soon as the fix differs.
    #
    # The observable goal is that the toolbar is inside the window, so the check is
    # the toolbar's rectangle against the viewport's.
    if x + w > VIEWPORT_WIDTH:
        return False, (f"the toolbar's right edge is at {x + w:.0f}px, past the "
                       f"{VIEWPORT_WIDTH:.0f}px window")
    if y + h > VIEWPORT_HEIGHT:
        return False, (f"the toolbar's bottom edge is at {y + h:.0f}px, past the "
                       f"{VIEWPORT_HEIGHT:.0f}px window — the overflow moved "
                       "instead of being fixed")
    if w <= 0 or h <= 0:
        return False, f"the toolbar has collapsed to {w:.0f}x{h:.0f}"
    return True, (f"all {len(TOOLBAR_LABELS)} buttons present and the toolbar "
                  f"({w:.0f}x{h:.0f} at {x:.0f},{y:.0f}) is inside the "
                  f"{VIEWPORT_WIDTH:.0f}x{VIEWPORT_HEIGHT:.0f} window")


def _rect(node):
    """(x, y, w, h) from a dumped Control, or None when unavailable."""
    pos = str(node.get("position", "") or "").strip("()")
    size = str(node.get("size", "") or "").strip("()")
    try:
        x, y = (float(v) for v in pos.split(","))
        w, h = (float(v) for v in size.split(","))
    except (ValueError, TypeError):
        return None
    return x, y, w, h


def _right_edge(node):
    """Furthest right edge anywhere in the subtree, if positions are known."""
    edges = []
    for n in _walk(node):
        pos = str(n.get("position", "") or "").strip("()")
        size = str(n.get("size", "") or "").strip("()")
        try:
            x = float(pos.split(",")[0])
            w = float(size.split(",")[0])
        except (ValueError, IndexError):
            continue
        edges.append(x + w)
    return max(edges) if edges else None


def grade_narration_dropped(root):
    """The narration must be present in the IR the game actually reads.

    Graded structurally, not visually: the narration node must exist with its
    original text. Whether it renders correctly is not observable here and is not
    claimed.
    """
    if not _exists(root, STORY):
        return False, "data/story.json was deleted"
    story = json.loads(_read(root, STORY))
    texts = []
    for label in story.get("labels", []):
        for node in label.get("body", []) or []:
            if isinstance(node, dict) and node.get("kind") == "narration":
                texts.append(str(node.get("text", "")))
    if not texts:
        return False, "no narration node exists in the story data"
    if any("narration with an escaped quote" in t for t in texts):
        return True, "the narration node is present with its original text"
    return False, "a narration node exists but carries different text"


# --- registry -----------------------------------------------------------------
#
# `arm_visible`: whether an agent with only Godot's own output could observe the
# fault. Recorded here so the analysis can check the level assignment against the
# data instead of assuming it.

CASES = {
    "L1-nodepath": {
        "suite": "v1", "level": 1, "inject": mutations.inject_node_path_invalid,
        "grade": grade_nodepath, "arm_visible": True,
        "task": ("The game reports an error when it starts: a node lookup fails. "
                 "Find and fix it. The code that uses the node must keep working — "
                 "do not remove the feature to silence the error."),
    },
    "L1-typeerror": {
        "level": 1, "suite": "v1", "inject": mutations.inject_type_error,
        "grade": grade_typeerror, "arm_visible": True,
        "task": ("The project does not load: a script fails to parse. Find and fix "
                 "it without deleting the declaration."),
    },
    "L1-missing-resource": {
        "level": 1, "suite": "v1", "inject": mutations.inject_missing_resource,
        "grade": grade_missing_resource, "arm_visible": True,
        "task": ("A scene fails to load because it references a resource that does "
                 "not exist. Fix the reference so the scene loads."),
    },
    "L2-property-class": {
        "level": 2, "suite": "v1", "inject": mutations.inject_property_on_wrong_class,
        "grade": grade_property_wrong_class, "arm_visible": False,
        "task": ("This project has a rain particle effect that should emit from a "
                 "sibling 'Splash' node, but the effect never happens. Find and fix "
                 "the cause. The setting must remain configured — do not delete it "
                 "to make the problem go away."),
    },
    "L2-unset-export": {
        "level": 2, "suite": "v1", "inject": mutations.inject_unset_exported_reference,
        "grade": grade_unset_export,
        "arm_visible": False,
        "task": ("A script exports a node reference that is never wired up, so it "
                 "reads as null at runtime. Wire it to a node that exists in the "
                 "scene. Keep the export — the feature must remain."),
    },
    # --- suite v2: faults that exist only in the running tree ----------------
    # Chosen because the v1 suite failed (see results/RESULTS.md): every v1 fault
    # was written down in a file the agent could read, so a competent agent needed
    # no tooling. These are built from GameDevBench's most frequent structural
    # failure class and are invisible to source reading by construction.
    "V2-runtime-misparent": {
        "suite": "v2", "level": 2, "inject": mutations.inject_runtime_misparent,
        "grade": grade_runtime_misparent, "arm_visible": False,
        "task": ("The HUD is built by hud.gd at run time. The three action buttons "
                 "(Start, Retry, Quit) should sit inside the ActionRow container, "
                 "but they do not appear where they belong. Find out what the "
                 "running layout actually is and fix it."),
    },
    "V2-layout-overflow": {
        "suite": "v2", "level": 2, "inject": mutations.inject_layout_overflow,
        "grade": grade_layout_overflow, "arm_visible": False,
        "task": ("The toolbar at the bottom of the HUD is wider than the game "
                 "window, so its rightmost buttons are clipped and unreadable. "
                 "Make all of the toolbar's buttons fully visible inside the "
                 "1280x720 window without removing any of them."),
    },
    "L3-narration-dropped": {
        "level": 3, "suite": "v1", "inject": mutations.inject_narration_dropped,
        "grade": grade_narration_dropped, "arm_visible": False,
        "task": ("This game is generated from a Ren'Py script. One line of "
                 "narration that should appear in the story is missing from the "
                 "generated data. Find where it should be and restore it. Do not "
                 "add text that was not in the original."),
    },
}


def load():
    return {name: {k: v for k, v in spec.items() if k != "inject"}
            for name, spec in CASES.items()}


def by_level(level):
    return {n: s for n, s in CASES.items() if s["level"] == level}


def by_suite(suite):
    return {n: s for n, s in CASES.items() if s.get("suite", "v1") == suite}
