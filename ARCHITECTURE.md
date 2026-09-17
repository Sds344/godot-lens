# Architecture

## The problem

A coding agent working on a Godot project reads `.gd` and `.tscn` files and
infers the rest. That inference is wrong often enough to matter, because in
Godot **the files are not the game**:

```gdscript
# Main.tscn declares this:
[node name="Main" type="Control"]
script = ExtResource("story_runtime.gd")
```

```gdscript
# ...and the actual UI only exists after this runs:
func _ready() -> void:
    add_child(Label.new())
    add_child(Button.new())
```

| View | What it reports |
|---|---|
| `.tscn` text | `Main` with **0 children** |
| runtime state | `Main` with **4 children** |

An agent reasoning from files concludes the scene is empty. A `CollisionShape2D`
with no `Shape` resource is *syntactically perfect* and contributes no collision.
Godot returns **exit code 0 even when a scene fails to load fatally**, so an
agent that trusts exit codes believes a broken project is healthy.

This is not a knowledge gap — a strong model knows the Godot API well. It is an
**observation gap**. `godot-lens` closes it by converting engine state into text
and JSON that an agent can read, act on, and verify against.

Nothing here needs a GUI, an editor plugin, an MCP server, or screenshots.

## Layers

Each layer answers a different question. Keeping them separate is what keeps the
output small enough to be useful (~550 tokens for a full situation report).

```
                    AI agent
                        |
        +---------------+---------------+
        |               |               |
   L0 API          L1/L2 State      L4 Diagnosis
   knowledge       project+runtime   meaning/impact
        |               |               |
        +-------+-------+-------+-------+
                |               |
           L3 Validation    Benchmark
           is it broken?    did the fix work?
                |               |
                +-------+-------+
                        |
                   Godot engine
```

### L0 — Engine knowledge (`godot_api.py`)

Generated from the installed engine binary via
`godot --dump-extension-api-with-docs`. 971 classes with signatures,
properties, signals, enums and the engine's own documentation.

The point is not that it contains documentation — models already have that. The
point is that it **cannot disagree with the engine you actually run**. No
version skew, no invented methods.

Queried on demand; the 11 MB dump is never loaded into context. Lookups resolve
the full inheritance chain (`connect` is declared on `Object`, so
`godot_api.py method Timer.connect` finds it).

### L1 — Project graph (`godot_scene.sh` static view)

Scenes, scripts and resource references as declared on disk. Note the word
*declared*: this layer is the weakest source of truth and is included mainly so
the contrast with L2 is visible.

### L2 — Runtime world state (`godot_scene.sh --runtime`)

The only layer that shows what the player's game actually contains. The scene is
instantiated, `_ready()` runs, one frame elapses, and the resulting tree is
serialised: node names, types, attached scripts, label/button text, sprite
textures, collision layers, and `shape` on `CollisionShape2D`.

This is the highest-fidelity source available without a GUI, because it reads a
game that is genuinely running rather than the editor's opinion about the game.

### L3 — Behaviour feedback (`godot_validate.sh`)

Three layers, cheapest first:

| Layer | Catches | Blind to |
|---|---|---|
| static | parse errors, type mismatches, unknown identifiers/functions — **all of them in one pass** | warnings, node paths, anything not written in the file |
| scene | missing `ext_resource`, unknown `type=` classes, malformed `.tscn` | bad property names, bad `$NodePath`, logic |
| runtime | real errors, `push_error`/`push_warning`, invalid node paths, null derefs | code not reached within N frames |

Godot's exit code is not trusted anywhere. Findings are matched from output text.

### L4 — Diagnosis (`godot_diagnose.py`)

State alone leaves the causal step to the model. `shape = null` is a fact;
"so characters fall through the floor" is domain reasoning that this layer makes
explicit and reviewable:

```
[ERROR] COLLISION_SHAPE_MISSING  res://Player.tscn node=Shape
  meaning: CollisionShape2D has no Shape resource assigned.
  impact : This node contributes no collision at all. Its parent physics body
           cannot detect or block anything, so characters pass through
           geometry or fall out of the world.
  action : Assign a Shape2D (RectangleShape2D / CircleShape2D / CapsuleShape2D).
```

Severity is ranked by **consequence**, not by the editor's icon colour. The
editor marks an unused variable and a shape-less collider identically (a yellow
"!"); for an agent these are not remotely equal.

## Measured boundaries

These are limits found by testing, not assumed. They are the most important part
of this document.

### Only executed code is observable

A faulty node path inside an unreachable branch is invisible to *both* static
analysis and a headless boot:

```gdscript
if false:
    var x = get_node("NoSuchNode/AtAll")   # 0 findings, static and runtime
```

This is the classic path-coverage problem, not a defect specific to this tooling.
The consequence for an agent is concrete: **a green validation run proves the
executed paths are healthy, not that the project is correct.** For a branching
game most content may sit outside the boot path.

Mitigations, in increasing order of cost:

1. Run longer (`--quit-after N`) — linear cost, still misses player-choice branches.
2. Write tests that execute the branches deliberately.
3. Drive the game down multiple paths automatically (goal-directed playtest).

### Editor-side state is unreachable headlessly

`--check-only` performs no flow analysis and says nothing about warnings. Editor
warnings such as unused or shadowed variables are produced by the editor's UI
layer and are not observable from a headless run. There is no known way to
obtain them without running the editor.

`get_configuration_warnings()` is also **not defined on every Node subclass**.
Calling it unguarded raises at runtime and can abort the calling function — this
produced a silent empty result during development. It is guarded in
`scene_tree_dump.gd`.

### Invalid property names are silently ignored

`nonexistent_property_xyz = 123` in a `.tscn` is accepted without complaint by a
headless load. Only the editor UI surfaces it.

### An observability layer cannot judge intent

This tooling reports **what happened**. It cannot report whether what happened
is what was wanted. That requires a specification: requirements, a domain model,
or a test oracle. For a converter, that means comparing source, intermediate
representation and runtime against each other — which is a different tool for a
different problem.

## Design principles

**Grade on behaviour, never on the presence of a string.** The bundled benchmark
originally checked whether a script still mentioned a missing node's name as
evidence of a repair. The *broken* script mentions it too, so the grader
reported a pass on an untouched project — and later accepted a "fix" that merely
redirected a lookup to an unrelated node. Both are worse than no benchmark.

**A false positive costs real work.** Diagnostic rules only fire on conditions
that are unambiguously wrong. A rule that cries wolf gets ignored, and then so
does the whole layer.

**Report the failure, do not raise it.** A broken project must still produce a
usable payload, because that is exactly when the agent most needs to see
something.

**Never silently resolve to the wrong thing.** Project discovery scores
candidates instead of taking the first match. During development a leftover
scratch project inside a hidden directory won that race, so every tool reported
confidently about a project nobody had asked about.

**Fail loudly on environment problems.** Godot aborts with a bare `signal 11`
when it cannot write its config directory — an engine-looking crash for a
permissions problem. Every entry point checks for a writable state directory and
explains the fix instead.

## Extending

- **A diagnostic rule**: add a function taking `(scene, view, node)` returning a
  finding dict to `NODE_RULES` in `godot_diagnose.py`.
- **A benchmark case**: add a `setup`/`check` pair and an entry in `CASES`.
  The check must observe behaviour, not text.
- **Another engine**: the layering is not Godot-specific. `project_path.py`,
  `godot_scene.sh` and the GDScript dumper are the only engine-shaped parts; the
  state → diagnosis → validation → benchmark structure carries over.
