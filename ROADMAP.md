# Roadmap

Ordered by what makes the project's *claims* stronger, not by what is most
visible. The governing constraint:

> The first consumer of this tooling is not a person, it is an agent.
> A second consumer is CI. A human-facing report is third.

Anything that only improves how the project looks to a human is deferred behind
anything that improves whether its results can be trusted.

---

## Phase 0 — trustworthy evaluation ✅ complete

The benchmark graded *silenced symptoms* as successful repairs, twice. Until
that was fixed, every result the project published was unreliable.

- [x] `case_001` false positive: a script with no `get_node()` call passed as
      "the invalid call was removed" — so replacing the feature with `print()`
      passed on an untouched project.
- [x] `case_002` false positive: deleting the faulty declaration passed, because
      "static validation is clean" is also true of a project with the code gone.
- [x] `benchmarks/selftest.py` — 12 assertions; unfixed fails, real fix passes,
      and 6 named reward-hacks are rejected.
- [x] `benchmarks/repairs.py` — the attack strategies as named, runnable functions.
- [x] `benchmarks/cases/*/EXPECTED.md` — all three written; `case_001`'s
      contradicted its own grader and was corrected.
- [x] `--setup` refuses a dirty lab, so faults cannot cross-contaminate a grade.

**Verification:** `python3 benchmarks/selftest.py` →
`scenarios: 12   hacks attempted: 6   hacks rejected: 6`.

---

## Phase 1 — schema honesty and the CLI ✅ complete

- [x] `schema` / `version` split in the payload (`godot-lens/observation` `0.1`).
      `0.x` states plainly that the shape is exploratory; the previous `lens/1`
      was an overclaim, since the likely next additions are whole new sections
      (`coverage`, `trace`, `input`, `resources`) rather than single fields.
- [x] `PROTOCOL.md` — the observation model, and the two decisions treated as
      settled *despite* `0.x`: an observation layer never carries intent, and
      absent ≠ null.
- [x] `src/godot_lens/` package with a stable front door: `inspect`, `diagnose`,
      `validate`, `scene`, `api`, `bench`, `setup`, `version`.
- [x] `pyproject.toml` + console script, so `uv tool install .` works, while
      `./godot-lens` runs from a fresh clone with no installation step.
- [x] All existing entry points (`tools/godot_context.py`, `tools/*.sh`) still
      work — the parent project's `AGENTS.md` references them, and a front door
      that breaks existing callers is not a front door.
- [x] `paths.py` consolidates every path decision and reports each failure with
      the fix, so the portability bugs of LESSONS §11 stay fixed.

**Deliberately not done here:** relocating the collectors into the package. The
target layout (`core/ collectors/ diagnostics/ exporters/`) is right, but the
collectors are the parts whose correctness was established by measurement.
Rewriting them for tidiness would risk that correctness for no functional gain.
The package locates them instead, and `paths.require_kit()` is the single place
that would change when they do move.

### What "package" means here

One package, no domain split yet. With a single engine there is nothing for
`collectors/` to collect *from*, so it would be an empty abstraction. The domain
split waits until a second engine is real.

---

## Phase 2 — evaluation strength (the payoff)

This is where the project earns the right to make a claim. Without it, the
tooling is a set of useful commands with no evidence they help.

**Phase 2A done:** the semantic-fidelity study (§ below) — four instruments, both
directions, verified against the real historical defect.

- [x] **Semantic/conversion faults** — the case where the project is
      engine-healthy and semantically wrong. `experiments/renpy-fidelity-001/`,
      driven by `godot-lens study`.
- [ ] **Extend the task suite** beyond three hand-written cases, using the
      GameDevBench failure taxonomy as the fault list
      (`docs/gamedevbench-failure-modes.md`). The structural half of that taxonomy
      is now measurable: coverage reports 8/10 classes detected.
- [x] **Mutation testing to quantify observability coverage.** Inject N fault
      classes, report how many the tooling detects. This turns "we know there
      are blind spots" into "the blind spot is these specific classes", which is
      the difference between an admission and a measurement. This is the
      *inverse* of the previously-declined mutation idea: measuring detection, not
      accelerating damage.

      **Measured: 8/10 fault classes detected, 2 blind, 0 false positives** on the
      reference project. `godot-lens bench --coverage`.

      | Fault class | Verdict |
      |---|---|
      | collision-shape-missing | SEEN (`COLLISION_SHAPE_MISSING`) |
      | collision-shape-missing-3d | SEEN (via `inspect` — the 2D rule does not cover it) |
      | collision-layer-zero | SEEN (`COLLISION_LAYER_ZERO`) |
      | sprite-texture-missing | SEEN |
      | node-path-invalid | SEEN (`inspect` runtime) |
      | type-error | SEEN (static, both layers) |
      | property-on-wrong-class | SEEN (`PROPERTY_ON_WRONG_CLASS`) — GameDevBench §G.1 |
      | unset-exported-reference | SEEN (`UNSET_EXPORTED_REFERENCE`) — GameDevBench 35.9% |
      | **physics-overlap-2d** | **BLIND** — overlapping colliders are legal to Godot |
      | **control-offscreen** | **BLIND** — no viewport-bounds comparison exists |

      The last two classes were added *because of* the GameDevBench failure
      taxonomy (`docs/gamedevbench-failure-modes.md`): they are the two most common
      structural failure modes the benchmark measured (36.2% and 35.9% of
      failures), and neither had a detector here. One of them needed a new
      **observation** first — exported members were not in the runtime dump at all,
      so the fault was invisible for lack of data rather than lack of a rule.

      The two blind classes are **not missing rules**. "Two colliders overlap" is a
      legal scene the engine does not report; finding it needs bounding-box
      intersection, and "a Control is outside the viewport" needs a rect comparison
      against the viewport. Those are **new observation capabilities**, a different
      kind of work from adding a rule — which is why the measurement had to come
      first. Recorded in `benchmarks/mutations.py::KNOWN_UNOBSERVABLE` so the gap
      cannot be mistaken for coverage.

      The first run of this harness reported a wrong number three ways, all of
      which it caught itself. See `LESSONS.md` §18.
- [ ] **A/B experiment:** agent alone vs. agent + `godot-lens`, on the same task
      suite. Report fix rate, iterations, and — the metric that matters most —
      **hack rate** (how often a task is "completed" without being repaired).
      The hack rate is the number no comparable tool reports, and it is the one
      this project is equipped to measure honestly.

### The fidelity study and its central finding

`godot-lens study` compares source → IR → runtime with four instruments, each
blind to what the others see. The reason there are four is the study's main
result: instruments 1–3 were pointed at the **real broken IR** from the converter's
history and **all three reported agreement**, because a broken model can be a
*superset* of the runtime's behaviour. Only instrument 4, which checks the model's
internal consistency instead of comparing it to output, saw the defect. See
`LESSONS.md` §15.

---

## Phase 3 — CI as a first-class consumer ✅ complete

Cheap, and it makes the project usable by people who do not use AI at all.

- [x] A GitHub Action wrapping `godot_validate.sh --json`
      (`.github/workflows/validate.yml`).
- [x] Finding → diff annotation, so results appear on the PR rather than in a log
      (`benchmarks/annotate.py`). Kept as a real file, not an inline YAML heredoc,
      so it can be run and tested locally.
- [x] Documented the two traps: Godot's exit code is not a health signal, and a
      green run is not a correctness proof.
- [x] `godot-lens ci` — a one-line verdict from a payload, for pipeline logs.

---

## Phase 4 — agent context modes ✅ complete

- [x] `inspect --agent` — a compact digest budgeted for a context window
      (~120 tokens for the reference project, against ~4.9k for the full payload).
- [x] `inspect` — human-readable.
- [x] `inspect --json` — the raw schema for programs. `--json-out FILE` freezes a
      payload so the other two modes can be exercised with no engine running.

**The design constraint held, and it forced a real decision.** The digest
summarises observed state and never invents interpretation. It also has **three
runtime states, not two**: a project whose game never booted renders as
`NOT OBSERVED`, never as "0 errors". That case is a supported input precisely
because it is where a digest can do the most damage — a clean-looking summary for
a project nothing was learned about.

---

## Phase 5 — presentation

- [x] Single-file HTML report: inline JSON, vanilla JS, inline SVG, no build step,
      no server, no framework (`godot-lens render`, `src/godot_lens/render.py`).
      Verified self-contained — no external `src`/`href` — so it opens from a
      `file://` URL and can be attached to a PR or dropped into a README.
- [ ] Optionally, an MCP adapter as a **thin** layer over the same schema.
      MCP is an adapter target, never the core — the core must stay usable from
      a plain shell.

---

## Shaders: what is in scope, and what never will be

GameDevBench's failure analysis attributes 22.6% of failures to "incorrect shader
or material assignment" and 22.6% to "incorrect shader, post-processing, or
environment parameters". The first instinct is that shaders are multimodal and
therefore out of scope. Measured, that is only half true, and the half that is
structural is worth doing.

Three experiments decided the split:

| Question | Measured result | Consequence |
|---|---|---|
| Does the engine report a shader compile error? | **Yes** — `SHADER ERROR: Expected a ';'` plus `Shader compilation failed` | Already caught by the runtime layer; needs explaining, not detecting |
| Is a `ShaderMaterial` with no shader assigned observable? | **No** — the dumper emits no material field at all | An observation gap, cheap to close |
| Is a misspelled `shader_parameter/` name reported? | **No** — completely silent | **Structurally checkable anyway**: the shader declares its uniforms, the material sets its parameters |

### In scope

- [ ] `SHADER_COMPILE_FAILED` — turn the engine's raw compile error into cause and
      consequence. The detection already works; the layer that explains `impact`
      and `action` is what is missing.
- [ ] `SHADER_PARAMETER_UNKNOWN` — a `shader_parameter/name` the shader does not
      declare. The same shape as `PROPERTY_ON_WRONG_CLASS`: a correct-looking token
      attached to something that does not define it, accepted in silence. Decidable
      from `Shader.get_shader_uniform_list()` plus the `.tscn` text, so it needs no
      rendering. **This is the largest reachable slice of the 22.6%.**
- [ ] `MATERIAL_MISSING` / shader resource unresolvable — requires first adding
      material information to the runtime dump, which is a genuine observation
      addition rather than a rule.

### Explicitly out of scope, permanently

Whether a shader **looks right** — the visual result of a colour ramp, a
distortion, a blend — is not obtainable without rendering and comparing pixels
against intent. That is `render`-and-compare work, and intent is not in the
protocol. A shader that compiles and whose parameters all exist but which produces
the wrong image is a correct observation of a wrong result, and no check here will
ever close that.

The honest framing for the README: **the structural half of shader failure is
reachable, the perceptual half is not.**

### How the three audiences were separated

The renderers were first tangled into the collectors, so changing an output format
meant booting the engine — and the cases most in need of care (a project that
cannot boot, a malformed payload) were the hardest to reach.

They now live in `src/godot_lens/render.py` as **pure functions of the payload**:

| Audience | Entry point | Shape |
|---|---|---|
| agent | `inspect --agent` | ~120-token digest, three runtime states |
| ci | `godot-lens ci --in payload.json` | one verdict line |
| human | `godot-lens render --in payload.json` | one self-contained HTML file |

`inspect --json-out FILE` freezes a payload, and `experiments/fixtures/` holds real
captured ones, so `benchmarks/render_selftest.py` asserts 32 properties with **no
Godot, no project, no network**. The awkward inputs become ordinary files. This is
the decoupling that makes a UI affordable on a system that is expensive to run —
see `LESSONS.md` §16.

---

## Explicitly rejected, and why

- **Refactoring to `core/ collectors/ diagnostics/ exporters/ cli/` now.** The
  target layout is right, and it is what makes `unity-lens` cheap later. But
  moving files while the data model is still at `0.x` means moving them twice,
  and the reorganisation is a large diff that obscures the evaluation fixes.
  Do it with Phase 1's CLI work, which already touches every entry point — one
  coherent refactor instead of two.
- **A dashboard before the schema settles.** A UI built on a moving schema is
  rebuilt twice, and a *persuasive* UI on a wrong measurement is worse than an
  honest CLI, because it is harder to doubt.
- **Server-based viewer.** The presentation targets are a README, a paper
  figure, a PR artifact and a screenshot. All four want one shareable file;
  none want a process to start.
- **Adding diagnostic rules at scale.** Diagnostic rules change agent behaviour.
  A wrong diagnosis is more dangerous than no diagnosis, because an agent acts
  on it. The rule set stays small and hand-designed.

---

## Parallel work boundaries

Two agents can work concurrently if the seam is a file, not a habit.

| Track | Owns | Must not touch |
|---|---|---|
| **Evaluation** (Phases 2–3) | `benchmarks/`, `PROTOCOL.md`, docs | `tools/godot/scene_tree_dump.gd`, diagnostic rules |
| **Interface** (Phases 1, 4–5) | CLI dispatcher, output modes, report renderer, packaging | diagnostic rules, the dumper, grader logic |

The interface track may only *read* the schema and the finding records. If it
needs a field that does not exist, the change belongs to the evaluation track —
requested, not taken. This keeps the observer and the presenter independently
testable, which is the same reason `PROTOCOL.md` forbids an `intent` field.
