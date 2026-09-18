# GameDevBench failure modes, mapped onto what this tooling can see

Analysis of [GameDevBench](https://waynechi.com/gamedevbench) (Chi et al., ICML
2026, [arXiv:2602.11103](https://arxiv.org/abs/2602.11103)) against this project's
observability coverage. The question this answers: **which of the benchmark's
failure modes is godot-lens even capable of addressing**, and which are
structurally outside its reach?

Recorded because it turns "godot-lens might help agents work on Godot" into a
specific, falsifiable claim with a fault taxonomy attached — and because it
identifies failure classes nobody has built a detector for yet.

## The benchmark in one paragraph

333 Godot tasks distilled from web and video tutorials; 2D graphics 33.3%, 3D
graphics 26.7%, UI 20.1%, gameplay logic 19.8%. Reference solutions average 4.7
files and 114 lines changed. Best agent (GPT-6 Astra, Codex, runtime video) passes
68.8%. Verification is deterministic, via Godot unit tests. Two feedback
mechanisms were added and both helped: an editor-screenshot MCP server, and
runtime video (+10.9 points for GPT-5.4). The authors' headline conclusion is that
**multimodality is the bottleneck** — success drops from 51.4% on gameplay tasks
to 33.0% on 2D graphics.

## The failure taxonomy (Appendix I, Tables 6 and 7)

Failure analysis of the four best configurations, via LLM-as-judge. A task may
exhibit several modes, so percentages are not mutually exclusive.

**Structural — "common game development patterns"**

| Failure type | % |
|---|---|
| Missing or mis-parented nodes | 63.4 |
| Missing method, property, or custom signal | 36.2 |
| Unset exported reference | 35.9 |
| Incorrect physics setup | 32.1 |
| Incorrect scene or asset instantiation | 28.2 |
| Incorrect UI control-tree structure | 25.1 |
| Incorrect node type | 22.6 |
| Incorrect TileMap structure | 1.7 |

**Multimodal — "grounding"**

| Failure type | % |
|---|---|
| Incorrect shader or material assignment | 22.6 |
| Incorrect shader, post-processing, or environment parameters | 22.6 |
| Incorrect UI layout, spacing, sizing, or anchoring | 19.9 |
| Incorrect animation state, direction, or frame sequence | 17.8 |
| Incorrect camera framing, position, or view transform | 17.1 |
| Incorrect spritesheet region or atlas slice | 15.3 |
| Incorrect texture, tile, or visual asset selection | 15.3 |
| Incorrect particle emission, spread, or lifetime parameters | 15.0 |
| Collider placement inconsistent with visible sprite geometry | 2.4 |

The paper's summary of the same data: "agents frequently add nodes to incorrect
levels in the tree, drop necessary signals, or assign resources to the wrong
elements."

## The mapping, which is the point

**Every entry in the structural table is a claim about the node tree, a resource
reference, or a signal — the three things this tooling already observes and
reports.** None of them requires understanding a pixel.

| Benchmark failure | Observable by godot-lens? | How |
|---|---|---|
| Missing or mis-parented nodes | **Yes** | `inspect` runtime tree vs `.tscn`; `study` instrument 4 already checks body-vs-container placement |
| Missing method or property | **Partly** | `api` proves existence against the engine binary. Placement is unchecked — see below |
| Missing custom signal | **Partly** | Node/script introspection is possible; no rule exists |
| Unset exported reference | **Yes, but no rule** | `NodePath`-typed exported vars are `null`/empty at runtime. Unchecked |
| Incorrect physics setup | **Partly** | `COLLISION_SHAPE_MISSING` and `COLLISION_LAYER_ZERO` exist; "incorrect" in general needs geometry (see the coverage report) |
| Incorrect scene or asset instantiation | **Partly** | `SPRITE_TEXTURE_MISSING` exists; a wrong-but-resolvable asset is not detectable |
| Incorrect UI control-tree structure | **Partly** | Structure is observable; "incorrect" is only decidable against stated intent |
| Incorrect node type | **Yes** | `type` is in the runtime dump |
| Incorrect TileMap structure | **Partly** | Observable, no rule |
| All nine multimodal entries | **No** | Requires reading an image or a shader. Out of scope by construction |

This split is the finding. The benchmark's own framing attributes failure to
multimodality, and the multimodal half is genuinely out of reach here — but the
structural half is where this tooling is aimed, and most of it has **no detector
yet**.

## The two failure classes worth building next

Both come from the paper's case study and taxonomy, and both are *silent* — the
engine accepts them.

### 1. Property placed on the wrong object (paper §G.1, Table 6's 36.2%)

The paper's example: GPT-5.4 writes the correct property and the correct value,

```
sub_emitter = NodePath("../Splash")
```

but places it under the `ParticleProcessMaterial` sub-resource instead of on the
`GPUParticles2D` node. `sub_emitter` belongs to `GPUParticles2D` and means nothing
on a material.

This is the **same shape** as the defect that motivated `study` instrument 4 — a
correct token attached to the wrong owner — and it is detectable from data already
available: `godot-lens api` knows which class declares which property, and the
`.tscn` declares which object each property is set on. No runtime, no image, no
geometry. A property set on an object whose class does not declare it is
unambiguously wrong.

**Why it is silent**: a headless load ignores an unknown property. This is already
recorded as a measured boundary in `ARCHITECTURE.md` — "invalid property names are
silently ignored" — but it had no detector, so the boundary was known and
unaddressed.

### 2. Unset exported reference (Table 6's 35.9%)

An exported `NodePath` or resource reference never assigned. The property exists,
the type is right, the value is empty. At runtime the game reads `null` and
usually fails much later and somewhere else.

Detectable: the runtime dump can read exported members, and an exported reference
whose value is empty on a node that declares it is a fact about the scene.

## What this analysis does not claim

- **Not** that godot-lens would raise benchmark scores. That is an experiment, and
  it has not been run. The benchmark's own controls suggest the honest prediction
  is *modest* — the multimodal half is untouched by any structural check.
- **Not** that the taxonomy transfers perfectly. GameDevBench evaluates agents
  editing a project from scratch; godot-lens is strongest on the observe-verify
  loop after an edit. The overlap is the loop, not the initial generation.
- **Not** that Table 6 percentages add to 100. They are per-failure-mode and
  overlapping.

## Practical note on running the benchmark

- It pins **Godot 4.4.1 exactly** and rejects other versions; this kit was verified
  on 4.5.1. Running the official harness needs 4.4.1 installed, and
  `tools/godot_api.sh dump` must be regenerated for that binary or
  `api_matches_engine` will be `false` and API answers will describe the wrong
  engine.
- Tasks ship as **individual zip files** (`unzip_tasks.sh`) specifically to prevent
  data leakage, and ground truth is separated from the solver workspace. Any
  experiment must respect that separation — which is the same injection-versus-
  grading boundary this project already enforces with `benchmarks/`.
- Agents run under **bubblewrap confinement** by default. The lab-copy approach in
  `benchmarks/` is compatible, but the two harnesses should not be merged
  casually.
