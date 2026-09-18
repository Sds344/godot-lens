# GameDevBench track — external tasks, structurally selected

**Status:** task selection and fetch tooling built; 17 candidates downloaded;
the engine gate (`gate.py`) has not been run yet. No agent has been run against
these tasks.

This is the second track. The first (`experiments/agent-ab/`) uses faults we
inject into a project we control, and its weakness is that injected faults are
readable in the source. This track uses
[GameDevBench](https://waynechi.com/gamedevbench) (Chi et al., ICML 2026,
[arXiv:2602.11103](https://arxiv.org/abs/2602.11103)) — 333 real Godot tasks
distilled from 88 tutorials, with deterministic unit-test grading and a reference
solution for each.

We do **not** attempt to reproduce the paper. We use its tasks as a source of
difficulty we did not author, and we run our own B-versus-C contrast.

## What the benchmark gives us

| | |
|---|---|
| Tasks | 333 (115 base + variants), 4 skill categories |
| Best agent | 68.8% ±5.0 (gpt-6-astra) — so there is real headroom, unlike our run 1 |
| Grading | Godot unit tests, deterministic: `VALIDATION_PASSED` / `VALIDATION_FAILED: <reason>` |
| Isolation | already specified by the authors, and already enforced |

The isolation is the reason this track is attractive, because it is exactly the
property our own run 1 lacked. `gamedevbench/src/benchmark_runner.py`:

> prevents agents from: 1. Reading test files (test.gd, test.tscn) 2. Reading
> task_config.json (contains answers/hints) 3. Running validation commands
> 4. Accessing the original task directory

and the official runs record `confinement: strict-v1` —
`bubblewrap-private-mount-namespace`, `private_tmp`, `private_home`,
`private_display`, network via an isolated provider proxy with an allowlist, and
`denied_connects: raw.githubusercontent.com`. The authors block the answer repo by
name. Two independent implementations converged on the same design, which is
mild evidence it is the right one.

## Why "the hardest tasks" was the wrong rule

The obvious selection is the tasks every model failed. There are 92 of them. They
are not usable as a difficulty signal, and the data says so twice.

**First, they cost the same as the tasks everyone passed.**

```
failed by all three models : 116s / 166k tokens median
passed by all three models : 115s / 153k tokens median
```

Effort is flat across the two populations. Models are not struggling harder on
these tasks; they are failing for a reason effort does not fix.

**Second, they collapse into a handful of variant families.** Grouping the 92 by
final assertion gives 43 families, and the top seven cover 55 of the 92:

| n | family | tasks |
|---|---|---|
| 12 | `SpawnTop must line up with the bottom tip of the branch...` | 0204–0215 |
| 11 | `CoinHighlight must cover the coin, overlap above 90%` | 0216–0226 |
| 11 | `StarHighlight must cover the star, overlap above 85%` | 0227–0237 |
| 11 | `Dragging state must reparent into the <X>_layer group` | 0281–0291 |
| 10 | `DetectRange.base_range_size must be <X>` | 0264–0273 |
| 9 | `Water requires a ShaderMaterial override` | 0069–0078 |

These are about seven base tasks, differing in a pixel threshold, an exact float,
or a magic group name. On the IoU family the models land at 0.75–0.87 against a
0.90 threshold. This is the **multimodal half of the paper's own taxonomy** —
which is precisely the half a structural observer cannot address. Selecting
"hardest" selects the tasks we are least able to help with.

### A correction, kept because it was wrong

The 11-task group-name family first looked *broken*: eleven variants demanding
eleven different `*_layer` group names looked like a specification the instruction
could not convey. Reading `task_0281` showed otherwise. The instruction does name
the group; the test asserts `card.get_parent() == ui_layer` where `ui_layer` is
bound to the `HUD` node, and only its *message* says `overlay_layer`. The task is
solvable and the message is stale. Recorded here rather than deleted: the
mechanical heuristic that produced the wrong answer ("test mentions an identifier
the instruction does not") would have discarded good tasks and kept bad ones
silently.

## The selection rule actually used

By **failure modality**, not by difficulty. `select.py` classifies the final
failure message of each task:

- **SCENE** — node presence, parenting, type, group, exported reference, signal,
  project setting, tile metadata, or a property value the instruction can state.
  This is what a runtime observer reads. **121 tasks** fail this way, of which
  **75 were passed by at least one model** and are therefore proven solvable.
- **CODE** — the assertion is about the text of a script. Not observable.
- **VISUAL** — shader, material, IoU, tint, atlas slice, particle parameters,
  camera framing. Out of reach by construction.
- **SKIPPED** — not attempted (`requires_display=true`).

Classification reads the failure message of a model that *failed*; a passing
model's message is a success summary and would classify everything as unknown.
VISUAL is tested before SCENE, because a message can name a node and still be a
pixel judgement ("StarHighlight must stay inside the platformer image bounds").

Skill category per task is **inferred** from the name and message, not read from
an official label — the public repo publishes the taxonomy only in aggregate.
`data/tasks.txt` records each choice with its reason.

## The engine gate

GameDevBench pins **Godot 4.4.1** and its harness refuses any other version:

```python
SUPPORTED_GODOT_VERSION = "4.4.1"
if detected != SUPPORTED_GODOT_VERSION: raise GodotVersionError(...)
```

We run on **4.5.1** and are deliberately not installing a second engine — the goal
is to establish whether the tooling helps, not to reproduce the leaderboard. An
internal B-versus-C contrast is still valid across a version difference, because
both arms get the same engine and the same tests.

It is not valid if the tasks themselves no longer work. A task whose reference
solution does not pass on 4.5.1 has no reachable success state: both arms fail it,
and a row of `0/0` looks like a result while measuring nothing. So every candidate
must clear one gate first, using the benchmark's own machinery rather than our
opinion:

```
reference solution --(official test scene)--> VALIDATION_PASSED
```

`gate.py` runs `godot [--headless] --import --quit --path <dir>` then
`godot [--headless] --path <dir> res://scenes/test.tscn`, and parses the
`VALIDATION_*` markers that `utils/validation.py` defines. Failures are written to
`data/gate.json` with the reference solution's own message, because those messages
are the only evidence we will ever have about what 4.5.1 changed.

This is §21 applied to a borrowed benchmark: run one thing end-to-end and confirm
the control behaves as expected before spending on a matrix.

## Files

| file | purpose |
|---|---|
| `select.py` | classify all 333 tasks by failure modality; emit `data/task-outcomes.json` |
| `fetch.py` | download chosen task + reference zips, keeping them in separate trees |
| `gate.py` | does the reference solution still pass on *our* Godot? |
| `data/tasks.txt` | the chosen tasks, each with its reason (the real pre-registration) |
| `data/task-outcomes.json` | derived per-task table, so the 1.7 MB of raw official runs need not be committed |

Task zips are not committed: they carry binary assets up to tens of megabytes.
`select.py --runs <dir>` regenerates the derived table from the official
per-model result JSONs, which are fetched from
[`waynchi/gamedevbench`](https://github.com/waynchi/gamedevbench).
