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

- [ ] **Extend the task suite** beyond three hand-written cases: API misuse
      (calling a method that does not exist in the installed engine), scene
      structure faults, dynamic-UI-build failures, and — most importantly —
      **semantic/conversion faults**, where the project is engine-healthy and
      semantically wrong.
- [ ] **Mutation testing to quantify observability coverage.** Inject N fault
      classes, report how many the tooling detects. This turns "we know there
      are blind spots" into "the blind spot is this large", which is the
      difference between an admission and a measurement. Note this is the
      *inverse* of the previously-declined mutation idea: measuring detection,
      not accelerating damage.
- [ ] **A/B experiment:** agent alone vs. agent + `godot-lens`, on the same task
      suite. Report fix rate, iterations, and — the metric that matters most —
      **hack rate** (how often a task is "completed" without being repaired).
      The hack rate is the number no comparable tool reports, and it is the one
      this project is equipped to measure honestly.

---

## Phase 3 — CI as a first-class consumer

Cheap, and it makes the project usable by people who do not use AI at all.

- [ ] A GitHub Action wrapping `godot_validate.sh --json`.
- [ ] Finding → diff annotation, so results appear on the PR rather than in a log.
- [ ] Document the two traps prominently: Godot's exit code is not a health
      signal, and a green run is not a correctness proof.

---

## Phase 4 — agent context modes

Today there are two shapes: a ~500-token digest and a ~4.9k-token full payload.
Make the intent explicit rather than implied.

- [ ] `inspect --agent` — the compact digest, named for its consumer.
- [ ] `inspect` — human-readable.
- [ ] `inspect --json` — the raw schema for programs.

**Design constraint:** the agent digest may summarise observed state, but it must
not *invent* interpretation. A line like "speaker mapping looks wrong" is a
diagnosis (L4), and it belongs to `diagnose`, not to `inspect`. Blurring those
layers is how an observation tool starts asserting things it cannot see.

---

## Phase 5 — presentation (deliberately last)

- [ ] Single-file HTML report: inline JSON, vanilla JS, inline SVG, no build step,
      no server, no framework. Renders the frozen schema; touches no core logic.
- [ ] Optionally, an MCP adapter as a **thin** layer over the same schema.
      MCP is an adapter target, never the core — the core must stay usable from
      a plain shell.

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
