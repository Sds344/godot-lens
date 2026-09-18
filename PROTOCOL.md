# Observable-state protocol

**Schema: `godot-lens/observation`, version `0.1`.** See
[Versioning](#versioning) — the version is `0.x` on purpose.

## What this is

A single JSON document that describes **what a game engine actually did**, in a
form an agent or a program can consume. It is the interface between the
*observation* side (this project) and every *consumer*: agents, CI gates, the
benchmark harness, and any user interface.

The point of writing it down is that there is one contract instead of several.
A scene dumper, a CLI digest, a dashboard and a report generator must all read
the same shape, so adding a view cannot silently invent a second source of truth.

## What this is not

This protocol describes **engine state and the consequences of that state**. It
does not describe intent, and it cannot decide whether the observed behaviour is
the *desired* behaviour. That requires a specification — requirements, an IR, or
a test oracle — which lives outside this protocol.

A consumer that needs "did the translation preserve meaning?" must supply the
expected behaviour itself and compare. See `LESSONS.md` ("Semantic fidelity") and
`ARCHITECTURE.md` ("An observability layer cannot judge intent").

**Consequence for implementers:** do not add an `intent`, `expected` or
`should_be` field here. A layer that reports what happened and a layer that knows
what was wanted must be separately testable, or a bug in one becomes
indistinguishable from a bug in the other.

## Observation model

Every fact in this document comes from one of three sources, and each has a
different trust level. A consumer must not treat them as equivalent.

| Source | How it is obtained | Trust |
|---|---|---|
| **declared** | parsing `.tscn`/`.gd` text | weakest — describes intent to the engine, not the result |
| **observed** | instantiating the scene, running `_ready()`, one frame of processing | **strongest available** — a real running process |
| **reported** | matching the engine's own stdout/stderr | strong, but only for what was executed |

Two boundaries follow, and they are properties of the model, not bugs to be
fixed later:

1. **Only executed code is observable.** A fault in an unreached branch appears
   in none of the three sources. A payload with `ok: true` means *the observed
   paths are healthy*, never *the project is correct*.
2. **The runtime view is a single point in time.** It is the tree after `N`
   frames, not a recording. A node created and freed within those frames, or a
   fault that needs a player choice to reach, is invisible.

## Top-level shape

```json
{
  "schema": "godot-lens/observation",
  "version": "0.1",
  "ok": true,
  "engine":   { ... },
  "project":  { ... },
  "api":      { ... },
  "scenes":   [ ... ],
  "runtime":  { ... },
  "findings": { ... }
}
```

| Field | Type | Meaning |
|---|---|---|
| `schema` | string | Contract name. Constant; identifies *what* this document is. |
| `version` | string | Schema version of the shape that follows. |
| `ok` | bool | `true` iff the runtime reported no errors **and** the validator reported no findings. It is a convenience digest, not the specification — a project can be `ok: true` and semantically wrong. |
| `engine` | object | Which engine binary produced the observations. |
| `project` | object | What was on disk when observation started. |
| `api` | object | Whether engine reflection data is available and matches. |
| `scenes` | array | Per-scene trees, in both views. |
| `runtime` | object | What the boot of the main scene emitted. |
| `findings` | object | Structured validator findings. |

`schema` and `version` are deliberately two fields rather than one `"lens/1"`
string: a consumer should branch on `version` while treating `schema` as a
constant, and a compound identifier makes it easy to accidentally compare the
wrong part.

## `engine`

```json
{
  "installed": "4.5.1.stable.official.f62fdbde1",
  "api_dump_present": true,
  "api_dump_version": "Godot Engine v4.5.1.stable.official",
  "api_matches_engine": true
}
```

`api_matches_engine` is the field that makes API answers trustworthy: when it is
`false`, any API lookup describes a *different* engine build than the one being
observed, and a consumer should treat API data as unverified rather than wrong.

## `project`

```json
{
  "path": "/abs/path/to/project",
  "main_scene": "res://Main.tscn",
  "scenes": ["Main.tscn", "screens/hud.tscn"],
  "scripts": ["scripts/story_runtime.gd"],
  "other_file_count": 12,
  "import_cache_present": true,
  "fingerprint": {
    "project_commit": "9b9cdcb91a175395da8180076e597ab67268fb1c",
    "project_commit_short": "9b9cdcb",
    "project_dirty": true,
    "project_dirty_files": 4
  }
}
```

`import_cache_present: false` means Godot has not imported assets yet. Findings
about missing textures or resources are **not trustworthy** in that state — the
resource may exist and simply not be imported. Consumers that gate on findings
must check this flag first.

### `fingerprint`

Which revision the observation describes. A finding is only reproducible against a
specific revision, so a result that does not carry one cannot be compared with any
other result. The reference project was under active development while it was being
measured — `git log` moved several times within one session — which is precisely
the condition this field exists to survive.

| Field | Meaning |
|---|---|
| `project_commit` | full `HEAD` of the project, or `null` when it is not a git repository |
| `project_commit_short` | abbreviated form, for display |
| `project_dirty` | whether the working tree had uncommitted changes |
| `project_dirty_files` | how many |

`project_dirty` is **not** noise to be dropped when convenient. "Clean" and "4
uncommitted files" are different claims about reproducibility, and only one of them
is honest for a given capture. A consumer that reports a result without the dirty
flag is overstating what it can reproduce.

A project without git still produces a payload: the fingerprint is present with
`project_commit: null` and `project_dirty_files: 0`. Best-effort is deliberate —
refusing to emit a payload because a revision cannot be determined would remove the
observation exactly when the environment is unusual.

## `scenes[]`

```json
{
  "scene": "res://Main.tscn",
  "static":  { "root_type": "Control", "child_count": 0, "root": { ... }, "tree": ["..."] },
  "runtime": { "root_type": "Control", "child_count": 4, "root": { ... }, "tree": ["..."] }
}
```

Both views are always present unless `--fast` was used, in which case `runtime`
is absent entirely. **Absent is not empty**: a consumer must distinguish "runtime
view was not requested" from "runtime tree had no children". The `runtime` key's
presence is the signal.

`tree` is a pre-rendered, indented text digest for cheap model consumption.
`root` is the structured tree for programmatic use. They are derived from the
same data; a consumer needing correctness should walk `root`, and a consumer
needing brevity should print `tree`. If they ever disagree, `root` is
authoritative.

### Node object

Only properties relevant to the node's class are present. **A missing key means
"not applicable to this node type", never "unset".** This distinction matters:
`"shape": null` means a `CollisionShape2D` has no shape (a defect), while the
absence of `shape` means the node is not a `CollisionShape2D` (not a defect).

| Key | Applies to | Notes |
|---|---|---|
| `name`, `type`, `child_count`, `children` | all nodes | `name` may be engine-generated (`@Label@2`) when the node was created by code |
| `script` | node with a script attached | resource path of the script |
| `visible` | `CanvasItem` | |
| `position` | `Node2D` | string-encoded vector |
| `size`, `anchors_preset` | `Control` | |
| `text` | `Label`, `BaseButton` | the **rendered** string — the field that shows a file view is insufficient |
| `text_length` | `RichTextLabel` | length only; full text is deliberately not serialised |
| `collision_layer`, `collision_mask` | `CollisionObject2D` | |
| `shape` | `CollisionShape2D` | `null` = unset (defect); path = assigned |
| `texture` | `Sprite2D` | `null` = nothing drawn |
| `stream` | `AudioStreamPlayer` | |
| `warnings` | node exposing `get_configuration_warnings()` | the node's **own** configuration warnings |
| `exported` | node with an attached script | exported members and whether each was assigned |

### `exported`

```json
"exported": {
  "target": {"type": "NodePath", "set": false, "value": ""},
  "speed":  {"type": "float",    "set": true,  "value": "2.0"}
}
```

Present only on nodes whose script declares exported members with storage. `type`
is a **name** (`NodePath`, `Object`, `String`) rather than Godot's integer enum,
because a raw integer would force every consumer to hardcode an enum that shifts
between engine versions.

`set` treats **empty as unset**: an exported `NodePath` left as `""` behaves
identically to one never assigned, and reporting the empty one as set would miss
the fault this field exists to find. An exported reference that is never assigned
reads as `null` at runtime, and the resulting failure typically surfaces much later
and somewhere else — which is why it accounts for 35.9% of structural failures in
GameDevBench's failure analysis (`docs/gamedevbench-failure-modes.md`).

This field exists because its absence was a **measurement** problem, not a rule
problem: the fault was detectable in principle and simply never observed, so a
coverage run reported the corresponding check as a blind spot.

`warnings` is present only for node classes that implement
`get_configuration_warnings()`. It is not defined on every `Node` subclass, and
calling it unguarded aborts the whole dump (`LESSONS.md` §6). The editor's yellow
"!" marks are **not** reachable this way and are not represented here.

## `runtime`

```json
{
  "frames": 180,
  "errors": ["ERROR: Node not found: \"StatusLabel\" ..."],
  "warnings": ["WARNING: ..."],
  "error_count": 2,
  "warning_count": 0
}
```

`errors` and `warnings` are raw engine lines, truncated to 20 each;
`*_count` counts everything seen, not just what was retained. A consumer
comparing counts to list length must account for truncation.

Engine noise (log-file paths, editor settings, the version banner) is filtered
before matching. Filtering is deliberately conservative: over-filtering hides a
real defect, which is worse than showing noise.

**`error_count == 0` does not mean the project is healthy.** Godot exits `0` even
when a scene fails to load fatally, and code that never runs reports nothing.

## `findings`

```json
{
  "finding_count": 0,
  "findings": [
    { "layer": "runtime", "file": "", "line": 5, "message": "ERROR: ..." }
  ]
}
```

`layer` is one of `static`, `scene`, `runtime` and identifies **which question
was asked**, not severity. `line` is absent when the engine did not report one.
Findings are emitted one JSON object per line by the validator and aggregated
here; `finding_count` counts all of them, while `findings` holds the first 25.

## Versioning

**`version` is `0.1`: this schema is explicitly not stable yet.** That is a
statement of fact, not modesty. Recording the shape is worth doing now because
without a written shape every consumer grows its own guess; *promising* the shape
is not, because the likely next additions are whole new sections rather than new
fields:

- `coverage` — which branches were executed, and which were not
- `trace` — a frame-stamped event stream, replacing the single-snapshot view
- `input` — the synthetic input that produced the observed state
- `resources` — the dependency graph, and whether each edge resolved
- `source_map` — engine node → source line, for binding observations back to code

Each of those is additive, but a consumer that started before they exist may be
relying on *their absence* as if it meant "healthy". That is the failure mode the
`0.x` prefix is there to warn about.

While the version is `0.x`, anything may change. The rules below are the intent
once it reaches `1.0`:

- **Adding a field or a key to a node object** — minor bump. Consumers should
  ignore unknown fields *and* should not read absence as a negative.
- **Removing or renaming a field, changing a type or meaning, or changing what
  "absent" implies** — major bump. A consumer pinned to a major may reject a
  different one rather than guess.

Because "absent means not applicable" is load-bearing for node objects,
*tightening* that rule (e.g. making an absent key mean "unset") is a major
change, not a minor one.

### What is already frozen, despite `0.x`

Two decisions are cheap to keep and expensive to reverse, so they are treated as
settled even while the shape moves:

1. **An observation layer never carries intent.** No `expected`, `should_be` or
   `intent` field will be added to this schema. The moment it appears, a bug in
   the observation becomes indistinguishable from a bug in the specification.
2. **Absent ≠ null.** A missing key means "not applicable to this node type"; a
   present-but-null key means "applicable and unset". Collapsing the two would
   turn a real defect (`"shape": null`) into silence.

A version number expresses how much churn a consumer should expect. These two
rules express what will *not* churn, and they do not depend on the number.

## Status

This document describes the shape the current tools already emit, with one
addition: the `schema` and `version` fields themselves. Everything else is
implemented and was verified against Godot 4.5.1. Recording it is a prerequisite
for building any second consumer — a UI, a report, or a non-Godot implementation
— because without a frozen shape each consumer would grow its own guess.

The schema is at `0.1`, so treat this document as a description of the present
rather than a promise about next month. See [Versioning](#versioning) for the two
rules that *are* treated as settled.

## Implementing this for another engine

Nothing above is Godot-specific except the class names in the node-object table
and the content of engine error strings. A port needs to supply:

1. the same three sources (declared / observed / reported);
2. the same absent-vs-null discipline for node properties;
3. the same honesty about coverage — a partial observation must be reported as
   partial, not as clean.

The `scenes` / `runtime` / `findings` split is the reusable part; the Godot
dumper is an implementation of the `observed` row.
