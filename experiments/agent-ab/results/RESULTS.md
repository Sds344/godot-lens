# A/B results — run 1

> ## ⚠ This run is contaminated. Do not cite these numbers.
>
> Five of these thirty cells read the experiment's own answer key through three
> open doors: a symlink from the lab into the kit, a shared `/tmp` lab root, and
> `Grep` with `path="/"`. One transcript's thinking block reads **"JACKPOT"** and
> then names the file that "will tell me exactly what the expected repair is".
>
> Full audit, mechanism and fix: [`../CONTAMINATION.md`](../CONTAMINATION.md).
> The contained re-run is in [`../results-isolated/`](../results-isolated/).
>
> The *direction* below survives — dropping the contaminated cells leaves
> B 9/9 and C 8/10 — but the run has no authority, and a cost re-analysis of the
> same data (`../cost.py`) found arm C using **1.14× the output tokens and 1.28×
> the turns** of arm B, which is the opposite of what the tooling's value
> proposition predicts. The file is kept unedited so the record shows what was
> believed before the audit.

**Date:** 2026-09-17 · **Cells:** 30 (6 cases × [A:1 + B:2 + C:2]) · **Wall clock:**
13.1 min at 6-way parallel · **Tokens:** 3,209,307 in + 471,650 out = **3.68M**

**Verdict: the hypothesis was not supported, and the experiment could not have
supported it.** The control arm solved everything, so there was no headroom in
which an improvement could appear. This is a design failure of the fault suite, not
evidence that the tooling is useless — and the distinction is the whole point of
reporting it.

## Results

| Arm | Repaired | Rate | lens used | by level (L1 / L2 / L3) |
|---|---|---|---|---|
| **A** one-shot | 0/6 | 0% | 0/6 | 0/3 · 0/2 · 0/1 |
| **B** native | **12/12** | **100%** | 0/12 | 6/6 · 4/4 · 2/2 |
| **C** lens | 9/12 | 75% | **4/12** | 4/6 · 4/4 · 1/2 |

Per case (`★` = lens was invoked):

```
case                        A     B r1   B r2    C r1    C r2
L1-missing-resource      FAIL     OK     OK    FAIL★    OK★
L1-nodepath              FAIL     OK     OK      OK     FAIL
L1-typeerror             FAIL     OK     OK      OK      OK
L2-property-class        FAIL     OK     OK      OK      OK★
L2-unset-export          FAIL     OK     OK      OK      OK
L3-narration-dropped     FAIL     OK     OK      OK     FAIL★
```

**Hack rate: 0/30.** No run in any arm silenced a fault. Every failure was an
honest failure. That is a real result, and it says the graders held under live
agents — the discipline built in `LESSONS.md` §1/§13/§18 transferred.

## Why this run cannot answer the question

### 1. Ceiling effect — the control was perfect

B repaired **12/12 with no lens at all**. There is no room for C to improve on a
control that already succeeds everywhere. The pre-registered prediction was that
L2 and L3 would separate B from C; they did not, because **B could already see
those faults.**

### 2. The core assumption was wrong: engine-silent ≠ agent-invisible

The fault taxonomy assumed that a condition Godot does not report is therefore
invisible to an agent. Every L2 case disproved that:

- `L2-property-class` — the agent read `Main.tscn`, saw `sub_emitter` attached to a
  `ParticleProcessMaterial`, and knew from Godot knowledge that the property belongs
  to `GPUParticles2D`. **Reading the source was sufficient.**
- `L2-unset-export` — the unassigned `@export var target: NodePath` is visible in
  the script and its absence in the scene. B fixed it 4/4.

"Silent to the engine" describes the *engine's* reporting, not an agent's ability to
reason. A model with domain knowledge does not need a tool for a fault that is
written down in a file it can read.

### 3. Scale is missing

These labs are one scene and a few dozen lines. The agent can read everything. The
cheapest way for an agent to "observe" a small project is to read it — which is why
B won. GameDevBench's projects average 528 lines across 67 files for exactly this
reason.

### 4. The treatment was under-applied

C invoked `godot-lens` in only **4 of 12** runs. The tool works in the C lab
(verified: `./godot-lens version` and `./godot-lens inspect --agent` both exit 0), so
the agent **had** it and mostly did not reach for it.

Split by usage:

| | repaired |
|---|---|
| lens used (n=4) | 2/4 |
| lens not used (n=8) | 7/8 |

**This is not evidence that the tool harms** — n=4 against n=8 with 1–2 failures
each is noise, and it must not be reported as a harm finding. What it does show is
that the tool did not become part of the agent's working loop, which is a finding
about **discoverability and perceived need**, not about correctness.

### 5. Arm A's 0% is a harness artifact, not a measurement

All six A cells reported `turns=5` — the cap — and none finished. A was cut off
mid-exploration, so it measured the turn budget rather than "one-shot capability".

**Fixed:** A now has **no shell** (`Read/Edit/Write/Glob/Grep/TodoWrite`) with a
20-turn cap. It can read and edit freely but cannot run the project, so it cannot
obtain feedback — which is what the arm is supposed to vary. The previous design
enforced "no feedback" with a turn cap and therefore did not enforce it at all.

## What was fixed as a result of this run

| Defect | Status |
|---|---|
| Lab root `/tmp/godot-lens-ab/` leaked the tool name to the control arms | renamed `/tmp/ab-eval/` |
| `"godot-lens" in cmd` read a path as an invocation | anchored to command position; 10 negatives in `selftest.py` |
| Arm A enforced "no feedback" with a turn cap | arm A has no shell |
| `make_copy` could copy the repo into itself | refuses, `benchmark.py` |

## What the next run needs

Not more repeats. **Different faults.** Specifically, faults that are not written
down in a readable file:

1. **Runtime-only faults** — a node created by code in `_ready()` with a wrong
   parent or missing properties. Nothing in the `.tscn` shows it; a native boot
   prints nothing; only the runtime tree does.
2. **Scale** — a project large enough that reading everything is impractical, so
   observation must be targeted.
3. **Faults whose diagnosis requires a populated tree** — e.g. a node that exists
   with the right name in the wrong subtree, which reads as correct in source and
   is only evidently wrong when the tree is enumerated.

Until the control stops scoring 100%, any difference in C is uninterpretable.

## Honest summary

> Running 30 cells for 3.68M tokens found **no benefit** from the observation layer,
> because the control arm — a competent agent with only Godot's own output — solved
> every fault. The experiment tested whether extra perception helps on faults that
> did not require perception. The measured facts that survive are: the graders held
> against live agents (0/30 hacks), the tooling is under-used when merely available
> (4/12), and engine silence is not agent blindness.

That last sentence is the result worth keeping. It cost 3.68M tokens and it
invalidates a load-bearing assumption that this project had been carrying since the
first fault taxonomy was written.
