# Agent A/B experiment — protocol

**Status: run 2 complete, under containment. Run 1 is contaminated.**

Every cell now runs inside a mount namespace, and `runner.py` **refuses to start a
cell whose lab is not hermetic** — see [`sandbox.py`](sandbox.py) and
[`CONTAMINATION.md`](CONTAMINATION.md). A lab is only isolated if the filesystem
the agent sees is isolated; a clean working directory inside an unclean filesystem
is not containment.

> **Outcome of run 1 (2026-09-17): unusable.** Five of thirty cells read the
> experiment's own answer key through three open doors — a symlink from the lab
> into the kit, a shared `/tmp` lab root, and `Grep` with `path="/"`. One
> transcript says "JACKPOT" and then names the file that "will tell me exactly
> what the expected repair is". Audit: [`CONTAMINATION.md`](CONTAMINATION.md).
>
> **Outcome of run 2 (2026-09-18, isolated): a clean null, and the reason is
> visible.** B 8/12, C 8/12, with no case where B succeeded and C did not; C used
> a median 1.33× the output tokens. Containment removed the ceiling effect that
> made run 1 uninterpretable (the control fell from 12/12 to 8/12) and raised
> adoption from 4/12 to **12/12** — the leak and the tool were substitutes. In the
> one cell where the tool delivered the decisive fact unambiguously, the agent
> deleted the property instead of moving it. See
> [`results-isolated/RESULTS.md`](results-isolated/RESULTS.md) and `LESSONS.md`
> §23–§26.

## The question

> Given a fixed coding agent, does access to engine-state observation improve its
> ability to **genuinely repair** a fault in a Godot project?

Not "can the tooling see more" — that is already measured
(`godot-lens bench --coverage`: 8/10 fault classes detected). This asks whether
seeing more changes what the agent *does*.

## Why three arms and not two

The tempting design is `agent` vs `agent + godot-lens`. It is also worthless, and
the reason is specific to this tooling.

Most of what `godot-lens` detects is **silent to the engine**: a property attached
to a class that does not declare it, an exported reference never assigned, a
`shader_parameter` name the shader does not declare. Godot accepts all of them. An
agent without the tooling cannot observe them at all, so it reports success and the
grader records failure. The result would be a large, impressive, and completely
uninformative difference — it would demonstrate that *information beats no
information*, which nobody doubts.

| Arm | Receives | Budget | Purpose |
|---|---|---|---|
| **A — one-shot** | task text, project, a working Godot | **one pass, no verification** | floor. Generation without a feedback loop |
| **B — native** | task text, project, Godot's own output | **5 rounds** | **the real control** |
| **C — lens** | B **plus** `godot-lens` | **5 rounds** | the treatment |

**B is not MCP.** There is no screenshot server and no display in this experiment;
B is exactly what a developer has with a terminal. GameDevBench's *Editor
Screenshot MCP* is a different and multimodal mechanism, and comparing against it
would answer a different question — "structural state versus visual state" —
which would need an MCP server and a display to build. Recorded here because the
two are easy to conflate and the conflation would silently change the claim.

**Confound, stated rather than hidden:** A differs from B in *two* variables — the
availability of native feedback **and** the iteration budget. A therefore
establishes a floor and nothing more; it must never be used to argue for the value
of feedback. The pre-registered claim rests entirely on **B vs C**, which differ in
exactly one thing.

**The finding is B vs C.** If C does not beat B, this tooling adds nothing over
what a competent developer already has, and that is the honest answer to report.
If C beats B, the difference is attributable to the observation layer and not to
feedback in general.

The contrast that carries the information is **fault level**, not arm: B and C
should both resolve engine-visible faults (that is the sanity check that the
harness works), and should separate on engine-silent faults.

## Fault taxonomy — three levels, deliberately

Nine cases, three per level. The levels exist because they predict *where* B and C
differ, and a suite without the middle level would produce a null result by
construction.

### Level 1 — engine-visible (3 cases)

The engine says something. Both B and C can see the fault.

| Case | Fault | Detector |
|---|---|---|
| `L1-nodepath` | `get_node("NoSuchChild")` for a missing node | runtime error |
| `L1-typeerror` | `var health: int = "not an int"` | parse error |
| `L1-missing-resource` | `ext_resource` pointing at a deleted script | load failure |

**Purpose: harness validation.** If C fails to beat B here, the treatment is not
being applied and no other number can be trusted. A difference here would also be
suspicious — it would suggest the arms differ in something other than the tooling.

### Level 2 — engine-silent (3 cases)

The engine says **nothing**. Only C can see the fault.

| Case | Fault | Detector | Why silent |
|---|---|---|---|
| `L2-property-class` | a real property on a class that does not declare it | `PROPERTY_ON_WRONG_CLASS` | headless load ignores unknown properties |
| `L2-unset-export` | `@export var target: NodePath` never assigned | `UNSET_EXPORTED_REFERENCE` | reads as `null`, fails later elsewhere |
| `L2-shader-param` | `shader_parameter/typo` the shader does not declare | *(planned: `SHADER_PARAMETER_UNKNOWN`)* | no engine output — measured |

**This is the level the experiment is about.** `L2-shader-param` is currently
**not implemented**; it is listed so the gap is visible, and the case is excluded
from the run until the detector exists rather than being quietly dropped.

### Level 3 — semantic preservation (3 cases, reduced to what is measurable)

A valid Godot project that is *semantically wrong* — the class of defect that
motivated the whole project.

| Case | Fault | Detector |
|---|---|---|
| `L3-narration-dropped` | a narration statement removed from `data/story.json` | `study` instrument 1 (executed span vs IR) |
| `L3-unset-reference-semantic` | *(reserved)* | — |
| `L3-attribute-dropped` | *(reserved)* | — |

**Honest scope note.** Only the first is implemented. The other two are reserved
because building them requires IR fixtures I have not produced, and padding the
suite with cases that cannot be graded would inflate the apparent coverage of the
experiment. **A three-case level reported as one case is a smaller and more honest
claim than three cases where two are ungraded.**

## Metrics

| Metric | Definition | Why it matters |
|---|---|---|
| **Detection rate** | fraction of injected faults the arm's evidence revealed | whether the fault was *reachable* by that arm |
| **Repair rate** | fraction of faults genuinely repaired within the round limit | the headline |
| **Residual faults** | faults still present when the agent declared completion | "does the modified code still error" |
| **Convergence rounds** | repair round in which the last genuine repair landed; `>5` counts as not converged | "how many rounds to finish" |
| **Hack rate** | fraction of "completed" tasks whose fault was silenced rather than repaired | **the distinctive one** |
| **Information utilisation** | fraction of C runs that actually invoked `godot-lens` | an arm that never calls the tool measures nothing |
| **Repair quality** | 0 deleted · 1 bypassed · 2 partial · 3 repaired | coarse-grained companion to hack rate |

### Why hack rate is the headline

A benchmark that asks "did the tests pass" cannot distinguish a repair from a
silencing, and an agent optimises against whatever it is graded on. This project
has already documented two graders that graded a silenced symptom as a pass
(`LESSONS.md` §1, §13). The graders here are verified against named attack
strategies before any agent runs (`benchmarks/selftest.py`, 6 hacks, all rejected).

**An arm can win on repair rate and lose on hack rate.** If C repairs more faults
but also silences more, the tooling made the agent *look* better without making it
*be* better. That outcome must be reported, not buried.

## Fixed parameters

| Parameter | Value | Rationale |
|---|---|---|
| Agent harness | Claude Code 2.1.190 | available and scriptable here (`--print`, `--output-format stream-json`) |
| Model | `deepseek-flash` | one model for every arm — the independent variable is the tooling, not the model |
| Round limit | 5 (arms B, C) · 1 pass (arm A) | beyond 5 the task counts as not converged |
| Repeats | **2** for B and C, **1** for A | see the sample-size note below |
| Isolation | project-only lab per run | arm A and B labs contain **no** `godot-lens`; only C does |
| Cells | 6 cases × (1 + 2 + 2) = **30** | measured ≈135k tokens and ~9 min per B/C cell |

### Sample size: why 2 repeats, and what it cannot buy

GameDevBench reaches a ±5pp confidence interval with **333 tasks run once each**,
not by repeating. Its 179 task variants exist to add *tasks*, so its power comes
from breadth. This suite has six cases; repeating compensates poorly, because
repeats resample the *same* fault and so measure agent stochasticity rather than
generalisation.

| Repeats | Observations per arm | 95% CI at p≈0.5 |
|---|---|---|
| 1 | 6 | ±40pp |
| **2** | **12** | **±28pp** |
| 3 | 18 | ±23pp |

Even three repeats cannot support a precise rate. Two is the point at which a
result can be seen to be *stable* rather than lucky, and beyond that the marginal
information is small. **Consequence for reporting: the per-case pattern is the
result, not the aggregate percentage.** The pre-registered expectation is a
*pattern* — L1 fixed by both arms, L2/L3 fixed only with lens — and that pattern is
visible at two repeats while a rate with a ±28pp interval is not evidence of
anything on its own.

`--max-turns` is **not** set to the round limit: one agent turn may include several
tool calls, and conflating turns with repair rounds would misreport convergence.

## Budget

Measured, not estimated: **~21–24k input tokens per invocation** is Claude Code's
system prompt overhead, regardless of task size. A PONG reply cost 24,422 input
tokens / 71 output.

Measured on a real pilot cell (arm B, `L2-property-class`): **82,628 input +
52,928 output tokens, 545 s, 51 turns.** Higher than a bare estimate because the
agent explores before editing.

The committed matrix is **30 cells** (6 cases × [A:1 + B:2 + C:2]):

| | cells | tokens | serial | 6-way parallel |
|---|---|---|---|---|
| **This run** | **30** | **≈3.4M** | ≈3.7 h | **≈37 min** |
| Full protocol (3 repeats) | 42 | ≈4.9M | 5.5 h | ≈55 min |

The harness's reported `cost_usd` uses **Anthropic pricing** and is meaningless
against a DeepSeek endpoint; only the token counts are usable.

## Threats to validity — stated before the results, not after

1. **The author of the tooling is also the author of the faults and the grader.**
   This is the single largest threat. Mitigations: the grader is verified against
   hacks independently of its author's intent; faults are drawn from a *published*
   taxonomy (GameDevBench Appendix I) rather than invented; and the raw
   transcripts are saved so a reader can re-grade. It is not eliminated.
2. **Only the structural half is tested.** Roughly half of GameDevBench's failure
   modes are multimodal — wrong spritesheet region, wrong animation frame, wrong
   camera framing. Nothing here touches those, and a positive result must not be
   read as "godot-lens helps with Godot development" in general.
3. **One harness, one model.** `deepseek-flash` under Claude Code. Results do not
   transfer to other agents or models, and will be reported as such.
4. **`L2-shader-param` missing** weakens the Level 2 claim to two cases.
5. **Small n.** 3 repeats × 9 cases detects only large effects. Any difference
   smaller than roughly 20 percentage points will not be distinguishable from
   noise, and the report will say so rather than reporting a point estimate as if
   it were precise.
6. **Sample contamination from a moving subject.** The reference project was under
   active development during this work. Every run is fingerprinted
   (`study.fingerprint()`) and the project commit recorded per result.

## Planned addition: arm D (godot-mcp) — a different question

Arms A/B/C vary **what the agent can see**. [godot-mcp](https://github.com/Coding-Solo/godot-mcp)
varies **what the agent can do** — it exposes scene manipulation (create and edit
nodes, modify scenes, launch the project) over MCP. Adding it would test a
different hypothesis, and the distinction should stay explicit:

| | Axis | Question |
|---|---|---|
| **B vs C** | perception | does *seeing engine state* improve repair? |
| **B vs D** | actuation | does *acting on the scene programmatically* improve repair? |
| **C vs D** | eyes vs hands | which matters more for silent faults? |

Feasibility checked, not assumed: `node`, `npx`, `Xvfb` and `xvfb-run` are present
on this machine, the package exists on npm (`@coding-solo/godot-mcp` 0.1.1,
`godot-mcp` 0.1.0), and Claude Code takes `--mcp-config`.

Why it is not in the committed matrix:

- It is **not a perception control**, so it cannot substitute for B. Comparing C
  against D would answer the eyes-vs-hands question, not whether the tooling works.
- It needs a display for the editor-backed variant, which changes the runtime
  environment for that arm alone — a confound that has to be designed around
  rather than bolted on.
- The packages are at 0.1.x. A moving dependency inside a pre-registered
  experiment undermines reproducibility unless its exact version is pinned and
  recorded per run.

Recorded here so the idea is preserved with its reasoning rather than
rediscovered. If added, D must get its own pre-registered protocol section and its
own `expected` result, exactly as the other arms did.

## What would falsify the claim

Pre-registered, so the outcome cannot be rationalised afterwards:

- **B ≈ C on Level 2** → the tooling adds nothing the engine does not already
  provide, and the honest conclusion is that silent faults are not a practical
  problem worth a tool.
- **C > B only on Level 1** → the tooling helps where the engine already helped,
  i.e. it is a convenience, not a capability.
- **C's hack rate ≥ B's** → the tooling makes agents look better without being
  better. This would be a negative result about the project's own premise and
  would be reported as the headline.

## Layout

```
experiments/agent-ab/
  README.md      # this file — the pre-registered protocol
  arms.py        # the three arms: prompt construction and permitted tooling
  cases.py       # the nine faults: injection + per-case success criterion
  grader.py      # behavioural grading; imports the hack taxonomy
  selftest.py    # verifies the grader before any agent runs
  runner.py      # runs one (case, arm, repeat): inject → invoke → grade → record
  results/       # raw transcripts and per-run JSON, never overwritten
```

Raw `stream-json` transcripts are kept in full. A summary a reader cannot audit is
not evidence.
